"""The lead's administration: ``python -m fleet.admin <command>``.

Run by ``smoking-pi fleet init|token|nodes|revoke|ca`` (cli/lib/fleet.sh)
inside the fleet-lead container (``compose exec``, or ``compose run`` when
it is not running), because that is where the lead's volume and
``cryptography`` are. Not meant to be typed: the commands are the
interface.

Output contract: what the command prints for a person goes to stdout; the
one exception is ``token``, whose stdout is the join string alone (the
command prints it once and it is never stored) with everything else on
stderr. Exit 0 done, 1 refused (with a sentence on stderr), 2 usage.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

from . import pki, protocol, settings
from .layout import LeadPaths, lead_paths
from .lead import open_state, read_fleet
from .registry import TOKEN_TTL_DEFAULT, TOKEN_TTL_MAX, Registry
from .statefile import ABSENT, CORRUPT


class Refusal(Exception):
    """A refusal with the sentence to print."""


def _err(text: str) -> None:
    print(text, file=sys.stderr)


def _when(ts) -> str:
    if ts is None:
        return "never"
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _rfc(ts) -> str | None:
    return None if ts is None else pki.rfc3339(dt.datetime.fromtimestamp(int(ts),
                                                                          dt.timezone.utc))


def _ago(ts, now: float) -> str:
    if ts is None:
        return "never"
    s = max(0, int(now - ts))
    if s < 120:
        return f"{s} s ago"
    if s < 7200:
        return f"{s // 60} min ago"
    if s < 172800:
        return f"{s // 3600} h ago"
    return f"{s // 86400} d ago"


def _url_problems(cfg: settings.Settings) -> list[str]:
    return [p for p in cfg.problems if p.startswith("FLEET_LEAD_URLS")]


def _loaded(paths: LeadPaths):
    fleet = read_fleet(paths)
    if fleet == ABSENT:
        raise Refusal("No fleet is initialized on this lead: smoking-pi fleet init")
    if fleet == CORRUPT:
        raise Refusal("This lead's fleet.json is corrupt; nothing is changed until a person "
                      "looks at it (docs/fleet.md).")
    ca, registry, problem = open_state(paths)
    if problem is not None:
        raise Refusal("This lead's state is broken: " + problem + ".")
    return fleet, ca, registry


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


INIT_PREFIX = ".init-"


def remove_init_leftovers(paths: LeadPaths) -> int:
    """An init killed before its rename leaves its scratch directory, with a
    copy of a root key that belongs to no fleet: removed, and said."""
    removed = 0
    for leftover in sorted(paths.root.glob(INIT_PREFIX + "*")):
        if leftover.is_dir() and not leftover.is_symlink():
            shutil.rmtree(leftover, ignore_errors=True)
            removed += 1
    if removed:
        _err(f"Removed {removed} unfinished fleet creation(s) left by an interrupted "
             "'fleet init' (keys of a fleet that never existed).")
    return removed


def init(paths: LeadPaths, cfg: settings.Settings, name: str) -> dict:
    """Create the fleet: its id, the root, this lead's intermediate, the
    registry and the server certificate, then fleet.json, whose presence
    means "initialized". Built in a scratch directory and renamed into
    place, so an interrupted init leaves nothing half made."""
    if not protocol.valid_name(name):
        raise Refusal("The fleet name: 1 to 64 letters, digits, spaces and . _ - ' ( )")
    if _url_problems(cfg):
        raise Refusal("FLEET_LEAD_URLS: " + "; ".join(_url_problems(cfg)))
    if not cfg.lead_urls:
        raise Refusal("A lead needs at least one URL nodes reach it at (FLEET_LEAD_URLS).")
    existing = read_fleet(paths)
    if existing == CORRUPT:
        raise Refusal("This lead's fleet.json is corrupt; no fleet is created over it.")
    if existing != ABSENT:
        raise Refusal(f"A fleet is already initialized here ({existing['fleet_id']}).")
    target = paths.fleet.parent
    if target.exists():
        raise Refusal(f"{target} exists without fleet.json: a person should look at it "
                      "before a new fleet is created over it.")
    paths.root.mkdir(parents=True, exist_ok=True)
    remove_init_leftovers(paths)
    scratch = Path(tempfile.mkdtemp(prefix=INIT_PREFIX, dir=paths.root))
    try:
        staged = LeadPaths(scratch)
        fleet_id, lead_id = str(uuid.uuid4()), str(uuid.uuid4())
        ca = pki.create(staged, fleet_id, lead_id)
        Registry(staged.registry).create()
        Registry(staged.registry).lead_epoch(cfg.lead_urls, cfg.lead_mode)
        pki.ensure_server_cert(staged, ca, lead_id, pki.url_hosts(cfg.lead_urls))
        fleet = {"fleet_id": fleet_id, "lead_id": lead_id, "name": name,
                 "created_at": protocol.now_rfc3339()}
        pki.write_public(staged.fleet, (json.dumps(fleet, indent=1) + "\n").encode())
        os.rename(staged.fleet.parent, target)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return dict(fleet, root_sha256=ca.root_sha256)


# ---------------------------------------------------------------------------
# token
# ---------------------------------------------------------------------------


def parse_ttl(text: str) -> int:
    m = re.fullmatch(r"([0-9]{1,5})([smh]?)", text.strip(), re.ASCII)
    if not m:
        raise Refusal("--ttl: a number of minutes, or with a unit: 90s, 30m, 2h")
    n, unit = int(m.group(1)), m.group(2) or "m"
    seconds = n * {"s": 1, "m": 60, "h": 3600}[unit]
    if not 60 <= seconds <= TOKEN_TTL_MAX:
        raise Refusal("--ttl: from 1 minute to 24 hours")
    return seconds


def token(paths: LeadPaths, cfg: settings.Settings, ttl: int, name: str | None,
          site: str | None) -> tuple[str, dict]:
    """A join string (returned, never stored) and what to say about it."""
    for label, value in (("--name", name), ("--site", site)):
        if value is not None and not protocol.valid_name(value):
            raise Refusal(f"{label}: 1 to 64 letters, digits, spaces and . _ - ' ( )")
    fleet, ca, registry = _loaded(paths)
    if not cfg.lead_urls:
        raise Refusal("FLEET_LEAD_URLS is empty: a join string must say where the lead is.")
    secret, record = registry.add_token(ttl, name, site)
    join = protocol.JoinString(
        fleet_id=fleet["fleet_id"], lead_urls=tuple(cfg.lead_urls[:8]),
        token=protocol.Secret(secret), root_sha256=ca.root_sha256,
        expires_at=_rfc(record.expires_at))
    return join.to_string(), {"expires_at": _rfc(record.expires_at), "node_name": name,
                              "site_name": site}


# ---------------------------------------------------------------------------
# nodes, revoke
# ---------------------------------------------------------------------------


def node_view(node: dict, now: float) -> dict:
    try:
        last = json.loads(node["last_heartbeat"]) if node.get("last_heartbeat") else None
    except ValueError:
        last = None
    state = node["state"]
    stale = (state == "active" and node.get("last_seen") is not None
             and now - node["last_seen"] > 5 * protocol.HEARTBEAT_DEFAULT)
    return {
        "node_id": node["node_id"], "node_name": node["node_name"],
        "site_id": node["site_id"], "site_name": node["site_name"],
        "state": state, "state_reason": node.get("state_reason"), "stale": stale,
        "enrolled_at": _rfc(node["enrolled_at"]), "last_seen": _rfc(node.get("last_seen")),
        "version": node.get("version"), "edition": node.get("edition"),
        "cert_not_after": _rfc(node.get("cert_not_after")),
        "instance_id": node["instance_id"], "last_heartbeat": last,
    }


def nodes_text(views: list[dict], now: float) -> str:
    if not views:
        return "No nodes yet: smoking-pi fleet token prints a join string for one."
    rows = [("NODE", "NAME", "SITE", "STATE", "LAST SEEN", "VERSION", "CERTIFICATE UNTIL")]
    for v in views:
        state = v["state"] + (" (silent)" if v["stale"] else "")
        last = v["last_seen"]
        seen = _ago(protocol.parse_time(last).timestamp(), now) if last else "never"
        rows.append((v["node_id"][:8], v["node_name"], v["site_name"], state, seen,
                     v["version"] or "?", (v["cert_not_after"] or "-")[:10]))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows)


def revoke(paths: LeadPaths, ref: str) -> str:
    _fleet, _ca, registry = _loaded(paths)
    found = registry.find(ref)
    if not found:
        raise Refusal(f"No node matches '{ref}' (its id, 8 or more of its first characters, "
                      "or its exact name: smoking-pi fleet nodes).")
    if len(found) > 1:
        raise Refusal(f"'{ref}' matches {len(found)} nodes; use more of the id.")
    node = found[0]
    if node["state"] == "revoked":
        return f"Node {node['node_id']} ({node['node_name']}) was already revoked."
    registry.set_state(node["node_id"], "revoked", "operator")
    return (f"Revoked node {node['node_id']} ({node['node_name']}). Its next heartbeat is "
            "told to stop; only a new join string brings it back.")


# ---------------------------------------------------------------------------
# ca
# ---------------------------------------------------------------------------


def ca_status(paths: LeadPaths) -> dict:
    fleet, ca, _registry = _loaded(paths)
    return {
        "fleet_id": fleet["fleet_id"], "lead_id": fleet.get("lead_id"),
        "root_sha256": ca.root_sha256,
        "root_not_after": pki.rfc3339(pki.not_after(ca.root)),
        "root_key_online": paths.root_key.exists(),
        "intermediate_sha256": pki.fingerprint(ca.intermediate),
        "intermediate_not_after": pki.rfc3339(pki.not_after(ca.intermediate)),
    }


def _read_key_from_stdin() -> bytes:
    data = sys.stdin.buffer.read(16 * 1024)
    if not data.strip():
        raise Refusal("No key on standard input.")
    return data


def export_root_key(paths: LeadPaths) -> bytes:
    _fleet, ca, _registry = _loaded(paths)
    if not paths.root_key.exists():
        raise Refusal("The root key is not on this lead (it was taken offline).")
    data = paths.root_key.read_bytes()
    if not pki.root_key_matches(ca, data):
        raise Refusal("The root key file on this lead is not the root's; nothing exported.")
    return data


def remove_root_key(paths: LeadPaths, offered: bytes) -> str:
    """Delete the root key from the volume, but only when ``offered`` (the
    copy the operator kept) is the same key: never delete the last copy."""
    _fleet, ca, _registry = _loaded(paths)
    if not paths.root_key.exists():
        return "The root key was already offline."
    if not pki.root_key_matches(ca, offered):
        raise Refusal("The key offered is not this fleet's root key; nothing was removed.")
    paths.root_key.unlink()
    return "The root key is no longer on this lead. Keep the exported copy safe and offline."


def restore_root_key(paths: LeadPaths, offered: bytes) -> str:
    _fleet, ca, _registry = _loaded(paths)
    if not pki.root_key_matches(ca, offered):
        raise Refusal("The key offered is not this fleet's root key; nothing was written.")
    pki.write_private(paths.root_key, offered)
    return "The root key is back on this lead (mode 0600)."


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m fleet.admin")
    sub = p.add_subparsers(dest="command", required=True)
    i = sub.add_parser("init")
    i.add_argument("--name", default="fleet")
    t = sub.add_parser("token")
    t.add_argument("--name")
    t.add_argument("--site")
    t.add_argument("--ttl", default=str(TOKEN_TTL_DEFAULT // 60))
    n = sub.add_parser("nodes")
    n.add_argument("--json", action="store_true")
    r = sub.add_parser("revoke")
    r.add_argument("node")
    c = sub.add_parser("ca")
    c.add_argument("action", choices=("status", "export-root-key", "remove-root-key",
                                      "restore-root-key"))
    c.add_argument("--json", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    paths = lead_paths()
    cfg = settings.load()
    try:
        if args.command == "init":
            made = init(paths, cfg, args.name)
            print(json.dumps(made))
        elif args.command == "token":
            join, about = token(paths, cfg, parse_ttl(args.ttl), args.name, args.site)
            _err(json.dumps(about))
            print(join)
        elif args.command == "nodes":
            _fleet, _ca, registry = _loaded(paths)
            now = time.time()
            views = [node_view(n, now) for n in registry.nodes()]
            print(json.dumps(views, indent=1) if args.json else nodes_text(views, now))
        elif args.command == "revoke":
            print(revoke(paths, args.node))
        elif args.action == "status":
            print(json.dumps(ca_status(paths), indent=1))
        elif args.action == "export-root-key":
            sys.stdout.buffer.write(export_root_key(paths))
        elif args.action == "remove-root-key":
            print(remove_root_key(paths, _read_key_from_stdin()))
        else:
            print(restore_root_key(paths, _read_key_from_stdin()))
    except Refusal as refusal:
        _err(str(refusal))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
