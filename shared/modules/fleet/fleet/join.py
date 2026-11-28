"""``python -m fleet.join``: enroll this node in a fleet.

Run by ``smoking-pi fleet join`` inside the fleet image (``docker compose
run --rm -T fleet-agent python -m fleet.join``), never by hand:

* **inside the image**, because that is where ``cryptography`` is and where
  the agent's volume is mounted: the key is generated in the process that
  writes it to ``node/key.pem`` (0600, the agent's own user), and the host
  needs no Python package;
* **the join string on stdin**, never in an argument or the environment:
  it carries a token, and arguments are visible to every user in ``ps``.
  The token is held in memory, sent once to ``/fleet/enroll``, and never
  written, logged or printed.

Steps: decode the join string; refuse if this node is already enrolled (or
its state is corrupt, revoked or another node's); fetch ``GET /fleet/root``
from the listed leads in order and refuse, loudly and before sending
anything, a root whose SHA-256 is not the join string's; generate the P-256
key and its CSR; ``POST /fleet/enroll`` over TLS verified against that root
alone; check the answer (same fleet and node, the chain up to the pinned
root, the leaf for this key and this node id); then write root, key, chain,
store token if any, and ``enrollment.json`` last. Any failure leaves the
node as it was: nothing of a failed join is kept.

Output: human sentences on stderr; one JSON object on stdout for the
command (``{"joined": true, "fleet_id", "site_id", "lead_urls", ...}`` or
``{"joined": false, "code"}``), with no secret in it. Exit 0 joined, 1
refused or failed, 2 the join string is not one.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from . import chain, nodekeys, nodestate, protocol, settings, statefile
from .agent import EDITION, software_version
from .layout import NodePaths, node_paths
from .transport import BadAnswer, HttpsTransport, LeadError, Tls, Transport, TransportError
from .transport import call as lead_call


class JoinFailed(Exception):
    """``code`` for the command, ``message`` for the person: both literal
    (plus ids and URLs from the join string or settings, never the token)."""

    def __init__(self, code: str, message: str, exit_code: int = 1):
        self.code, self.message, self.exit_code = code, message, exit_code
        super().__init__(code)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_local(cfg: settings.Settings, paths: NodePaths) -> None:
    """Refuse to enroll over anything: joining twice, a corrupt state, an
    enrollment of another node id."""
    read = statefile.read_object(paths.enrollment)
    if read == statefile.CORRUPT:
        raise JoinFailed("state_corrupt",
                         "This node's enrollment file is corrupt, and a join never writes "
                         "over it. 'smoking-pi fleet leave --force' clears it; then join again.")
    if isinstance(read, dict):
        fleet = read.get("fleet_id", "?")
        if read.get("node_id") != cfg.node_id:
            raise JoinFailed("identity_mismatch",
                             "This node's volume holds the enrollment of another node id "
                             "(an older env file restored?). See docs/fleet.md, \"Two "
                             "installations, one node id\"; 'smoking-pi fleet leave --force' "
                             "clears it.")
        if isinstance(read.get("revoked"), dict):
            raise JoinFailed("revoked",
                             f"This node was revoked from fleet {fleet}. 'smoking-pi fleet "
                             "leave' clears that enrollment; then join again.")
        raise JoinFailed("already_joined",
                         f"This node is already in fleet {fleet}. To join another (or this "
                         "one again), 'smoking-pi fleet leave' first.")
    if not cfg.has_identity:
        raise JoinFailed("no_identity",
                         "This installation has no node id: run 'smoking-pi fleet status' "
                         "once, then join.")


def fetch_root(js: protocol.JoinString, transport: Transport) -> tuple[str, object]:
    """(the lead that served it, the pinned root). Refuses a root
    that is not the pinned one, whichever lead served it."""
    unreachable: list[str] = []
    for url in js.lead_urls:
        try:
            resp = lead_call(transport, url, "GET", "/fleet/root", None,
                             protocol.RootResponse, Tls(root=None))
        except TransportError:
            unreachable.append(url)
            continue
        except (LeadError, BadAnswer):
            unreachable.append(url)
            continue
        if protocol.negotiate(resp.protocol_min, resp.protocol_max) is None:
            raise JoinFailed("protocol_unsupported",
                             f"The lead at {url} speaks protocol versions {resp.protocol_min} "
                             f"to {resp.protocol_max}; this node speaks {protocol.PROTOCOL_MIN} "
                             f"to {protocol.PROTOCOL_MAX}. Upgrade the older side.")
        try:
            root = chain.pinned_root(resp.root_pem, js.root_sha256)
        except chain.ChainError as err:
            raise JoinFailed(
                "root_mismatch",
                f"REFUSED: {err.reason} (lead {url}). Nothing was sent to it. Either the join "
                "string belongs to another fleet, or something between this node and the "
                "lead is not the lead. Check the URL and ask for a new join string.") from None
        return url, root
    raise JoinFailed("unreachable",
                     "No lead in the join string answered (" + ", ".join(unreachable) + "). "
                     "Nothing was changed. Check that this host reaches it, then run the same "
                     "join again: the token is still unused until it expires.")


_ENROLL_REFUSALS = {
    "token_invalid": ("The lead refused the join token: unknown, already used, or expired. "
                      "Ask for a new join string ('smoking-pi fleet token' on the lead)."),
    "node_exists": ("The lead already has a node with this node id ({node}). If it is this "
                    "installation from an earlier join, remove it on the lead first, then "
                    "join with a new join string."),
    "duplicate_node": ("The lead sees two installations using this node id ({node}); see "
                       "docs/fleet.md, \"Two installations, one node id\"."),
    "revoked": ("The lead has this node id ({node}) as revoked; its operator must remove "
                "it before it can join again."),
    "protocol_unsupported": ("The lead speaks no protocol version in common with this node. "
                             "Upgrade the older side."),
    "bad_request": "The lead did not understand the request (a bug: see the lead's log).",
}


def enroll(js: protocol.JoinString, cfg: settings.Settings, paths: NodePaths,
           transport: Transport, now: datetime | None = None) -> dict:
    """Enroll; return the command's result. Raises JoinFailed."""
    check_local(cfg, paths)
    first, root = fetch_root(js, transport)
    key = nodekeys.new_key()
    request = protocol.EnrollRequest(
        protocol=protocol.PROTOCOL_MAX, protocol_min=protocol.PROTOCOL_MIN,
        join_token=js.token, fleet_id=js.fleet_id, node_id=cfg.node_id,
        instance_id=protocol.new_instance_id(), node_name=cfg.node_name,
        site_name=cfg.site_name, csr_pem=nodekeys.csr_pem(key, cfg.node_id),
        software=protocol.Software(version=software_version(), edition=EDITION))
    # The root goes on disk first: TLS to the lead is verified against that
    # file alone. It is removed again if the join fails.
    joined = False
    try:
        statefile.write_bytes(paths.root_cert, chain.pem(root).encode(), 0o644)
        resp, url = _post_enroll(js, first, request, paths, transport, cfg)
        result = _accept(js, cfg, paths, key, root, resp, url, now)
        joined = True
        return result
    except OSError as err:
        raise JoinFailed("write_failed",
                         f"The node could not write its enrollment ({type(err).__name__}). "
                         "Nothing was kept. If the lead enrolled it already, ask its "
                         "operator to remove this node before joining again.") from None
    finally:
        if not joined:
            nodestate.remove_all(paths)


def _post_enroll(js, first, request, paths, transport, cfg):
    order = [first] + [u for u in js.lead_urls if u != first]
    busy: list[str] = []
    for url in order:
        try:
            resp = lead_call(transport, url, "POST", "/fleet/enroll", request,
                             protocol.EnrollResponse, Tls(root=paths.root_cert))
            return resp, url
        except TransportError:
            continue
        except BadAnswer:
            raise JoinFailed("bad_answer",
                             f"The lead at {url} answered the enrollment outside the protocol. "
                             "Nothing was kept here; it may have used the token. Check the "
                             "lead's version and log.") from None
        except LeadError as err:
            if err.code in _ENROLL_REFUSALS:
                raise JoinFailed(err.code,
                                 _ENROLL_REFUSALS[err.code].format(node=cfg.node_id)) from None
            busy.append(f"{url}: {err.code}")
    if busy:
        raise JoinFailed("lead_unavailable",
                         "The lead could not enroll this node now (" + "; ".join(busy) + "). "
                         "Nothing was changed; try the same join again in a minute.")
    raise JoinFailed("unreachable",
                     "The lead served its root but did not answer the enrollment. Nothing "
                     "was changed; run the same join again.")


def _accept(js, cfg, paths, key, root, resp: protocol.EnrollResponse, url, now) -> dict:
    now = now or datetime.now(timezone.utc)
    if resp.fleet_id != js.fleet_id or resp.node_id != cfg.node_id:
        raise JoinFailed("bad_answer",
                         "The lead's answer is for another fleet or node. Nothing was kept.")
    try:
        issued = chain.verify(resp.cert_chain, root, now)
        if not nodekeys.matches(issued[0], key):
            raise chain.ChainError("the certificate the lead issued is not for this node's key")
        if not chain.node_identity_ok(issued[0], cfg.node_id):
            raise chain.ChainError("the certificate the lead issued names another node")
    except chain.ChainError as err:
        raise JoinFailed("chain_invalid",
                         f"REFUSED: {err.reason}. Nothing was kept. The lead has probably "
                         "recorded this node: ask its operator to remove it, and for a new "
                         "join string.") from None
    endpoints = resp.lead_endpoints
    enrollment = {
        "node_id": cfg.node_id, "fleet_id": resp.fleet_id, "site_id": resp.site_id,
        "root_sha256": js.root_sha256.lower(), "enrolled_at": _iso(now),
        "not_after": _iso(issued[0].not_valid_after_utc),
        "lead_endpoints": nodestate.endpoints_dict(endpoints),
        "heartbeat_interval": resp.heartbeat_interval,
    }
    statefile.write_bytes(paths.key, nodekeys.key_pem(key), 0o600)
    statefile.write_bytes(paths.chain,
                          nodestate.chain_text(chain.pem(c) for c in issued).encode(), 0o644)
    if resp.store is not None:
        enrollment = nodestate.save_store(paths, enrollment, resp.store, _iso(now))
    nodestate.write_enrollment(paths, enrollment)  # last: "enrolled" from here on
    return {"joined": True, "fleet_id": resp.fleet_id, "site_id": resp.site_id,
            "node_id": cfg.node_id, "lead_urls": list(endpoints.urls), "lead_in_use": url,
            "not_after": enrollment["not_after"], "store_received": resp.store is not None}


def main(argv: list[str] | None = None, *, stdin=None, stdout=None, stderr=None,
         transport: Transport | None = None, paths: NodePaths | None = None) -> int:
    stdin, stdout, stderr = stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr
    cfg = settings.load()
    try:
        # One line, bounded: a join string is a few hundred characters.
        text = stdin.readline(8192).strip()
        try:
            js = protocol.JoinString.parse(text)
        except protocol.ValidationError:
            raise JoinFailed("bad_join_string",
                             "That is not a join string (spfleet1_...). Copy it again from "
                             "'smoking-pi fleet token' on the lead.", exit_code=2) from None
        finally:
            text = ""
        result = enroll(js, cfg, paths or node_paths(), transport or HttpsTransport())
    except JoinFailed as err:
        print(err.message, file=stderr)
        print(json.dumps({"joined": False, "code": err.code}), file=stdout)
        return err.exit_code
    except Exception as err:  # never a traceback that could quote a value
        error_id = protocol.new_error_id()
        print(f"The join failed unexpectedly ({type(err).__name__}, error id {error_id}). "
              "Nothing was kept.", file=stderr)
        print(json.dumps({"joined": False, "code": "internal"}), file=stdout)
        return 1
    print(json.dumps(result), file=stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
