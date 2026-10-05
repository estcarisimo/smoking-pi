"""Remote connectors: a cloud assistant signs in with a pairing code.

Claude, ChatGPT, Grok and the rest add a remote MCP server the same way: a
URL, then a sign-in in the browser (OAuth 2.1, as the MCP spec requires).
This module is the authorization server behind that sign-in, and nothing
more. The MCP SDK serves the endpoints (metadata, client registration,
/authorize, /token, /revoke) and checks the bearer on /mcp; this provider
decides who gets a token.

The sign-in page asks for a **pairing code**, which only someone on the Pi
can make (``smoking-pi connector pair``, which runs ``python connector.py
pair`` in this container). There are no accounts and no passwords: the
code is short-lived (10 minutes), single-use, and burned after five wrong
tries.

Every connector gets the ``read`` scope only: the tools that change
anything (targets, probes, mutes, a restart) refuse it (server.py,
``_writes``). ``MCP_API_TOKEN``, the local OpenClaw's token, keeps
``read`` and ``write``. Each connector has its own tokens, and ``revoke``
removes one without touching the others.

Off unless ``MCP_PUBLIC_URL`` is set: the HTTPS address a tunnel publishes
this server at (docs/remote-connector.md). Tokens are stored hashed, in
``MCP_CONNECTOR_STATE`` (a volume), and re-read when the CLI changes it.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import sys
import tempfile
import time
from typing import Any
from urllib.parse import urlparse

READ = "read"
WRITE = "write"
DEFAULT_STATE = "/var/lib/mcp-connector/connector.json"
PAIRING_TTL_S = 600
PAIRING_TRIES = 5
REQUEST_TTL_S = 600
CODE_TTL_S = 300
ACCESS_TTL_S = 3600
REFRESH_TTL_S = 90 * 86400
# Registration is open to anyone who can reach the URL (the MCP spec's
# dynamic client registration); a registration that never pairs is dropped
# after a day, and at most this many may wait at once.
UNPAIRED_TTL_S = 86400
MAX_UNPAIRED = 50
MAX_PENDING = 100
# Pairing codes: 8 characters from an alphabet with no 0/O or 1/I/L.
_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def public_url() -> str:
    """``MCP_PUBLIC_URL`` without a trailing slash, or empty (off)."""
    return (os.environ.get("MCP_PUBLIC_URL") or "").strip().rstrip("/")


def url_problem(url: str) -> str | None:
    """Why ``url`` cannot be the public address, or None. HTTPS, a host,
    no path: the sign-in metadata and every redirect are built from it."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return "it must be an https:// address"
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        return "it must have no path (the server adds /mcp)"
    return None


def enabled() -> bool:
    """Connectors are on when MCP_PUBLIC_URL is a usable address. A wrong
    one leaves them off with a warning instead of a server that cannot
    start: the local assistant keeps working either way."""
    url = public_url()
    if not url:
        return False
    problem = url_problem(url)
    if problem:
        import logging
        logging.getLogger("mcp.connector").warning(
            "MCP_PUBLIC_URL is not usable (%s): remote assistants stay off", problem)
        return False
    return True


def state_path() -> str:
    return os.environ.get("MCP_CONNECTOR_STATE") or DEFAULT_STATE


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _code(n: int = 8) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def normalize_code(value: str) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


# ---------------------------------------------------------------------------
# State: one JSON file, written atomically. The server and the CLI (another
# process in the same container) both write it; each write re-reads first.
# ---------------------------------------------------------------------------


def _empty() -> dict:
    return {"clients": {}, "pairing": None, "access": {}, "refresh": {}}


def load(path: str | None = None) -> dict:
    path = path or state_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError):
        # A corrupt file must not lock everyone in or out silently: start
        # empty (every connector signs in again) and say so.
        import logging
        logging.getLogger("mcp.connector").warning(
            "connector state unreadable; starting empty")
        return _empty()
    base = _empty()
    base.update({k: data.get(k, base[k]) for k in base})
    return base


def save(state: dict, path: str | None = None) -> None:
    path = path or state_path()
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".connector-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _prune(state: dict, now: float) -> None:
    for kind in ("access", "refresh"):
        state[kind] = {h: t for h, t in state[kind].items()
                       if not t.get("expires_at") or t["expires_at"] > now}
    state["clients"] = {cid: c for cid, c in state["clients"].items()
                        if c.get("label") or c.get("registered_at", now) > now - UNPAIRED_TTL_S}
    pairing = state.get("pairing")
    if pairing and (pairing["expires_at"] <= now or pairing["tries_left"] <= 0):
        state["pairing"] = None


# ---------------------------------------------------------------------------
# Pairing, listing, revoking: what `smoking-pi connector` does
# ---------------------------------------------------------------------------


def new_pairing(label: str, path: str | None = None, now: float | None = None) -> str:
    """A fresh pairing code (replacing any earlier one), for ``label``."""
    now = time.time() if now is None else now
    state = load(path)
    _prune(state, now)
    code = _code()
    state["pairing"] = {"hash": _hash(code), "label": label[:40] or "assistant",
                        "expires_at": now + PAIRING_TTL_S, "tries_left": PAIRING_TRIES}
    save(state, path)
    return code


def check_pairing(code: str, path: str | None = None,
                  now: float | None = None) -> str | None:
    """The pairing's label when ``code`` is right; None otherwise. A right
    code is used up; a wrong one costs a try."""
    now = time.time() if now is None else now
    state = load(path)
    _prune(state, now)
    pairing = state.get("pairing")
    if not pairing:
        save(state, path)
        return None
    if hmac.compare_digest(_hash(normalize_code(code)), pairing["hash"]):
        state["pairing"] = None
        save(state, path)
        return pairing["label"]
    pairing["tries_left"] -= 1
    if pairing["tries_left"] <= 0:
        state["pairing"] = None
    save(state, path)
    return None


def connectors(path: str | None = None, now: float | None = None) -> list[dict]:
    """Every paired connector: label, client id, when, whether it holds a
    live token, and when it last got one."""
    now = time.time() if now is None else now
    state = load(path)
    _prune(state, now)
    out = []
    for cid, c in state["clients"].items():
        if not c.get("label"):
            continue  # registered, never paired: nothing to show or revoke
        live = [t for t in list(state["access"].values()) + list(state["refresh"].values())
                if t["client_id"] == cid]
        out.append({"label": c["label"], "client_id": cid,
                    "client_name": c.get("info", {}).get("client_name"),
                    "paired_at": c.get("paired_at"), "last_token_at": c.get("last_token_at"),
                    "connected": bool(live)})
    return sorted(out, key=lambda c: c.get("paired_at") or 0)


def revoke(name: str, path: str | None = None) -> list[str]:
    """Remove every connector whose label or client id is ``name``, with its
    tokens. Returns the labels removed."""
    state = load(path)
    gone = [cid for cid, c in state["clients"].items()
            if name in (cid, c.get("label"))]
    for cid in gone:
        state["clients"].pop(cid, None)
    for kind in ("access", "refresh"):
        state[kind] = {h: t for h, t in state[kind].items() if t["client_id"] not in gone}
    save(state, path)
    return gone


# ---------------------------------------------------------------------------
# The OAuth provider the MCP SDK calls
# ---------------------------------------------------------------------------


def make_provider(static_token: str = "", path: str | None = None):
    """The SDK's ``OAuthAuthorizationServerProvider``, built lazily so
    importing this module (the CLI) does not need the SDK's auth package."""
    from mcp.server.auth.provider import (
        AccessToken,
        AuthorizationCode,
        AuthorizeError,
        RefreshToken,
        RegistrationError,
        TokenError,
        construct_redirect_uri,
    )
    from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

    class Provider:
        def __init__(self) -> None:
            self.static_token = static_token
            self.path = path
            # Authorizations waiting for their pairing code, and issued
            # codes waiting for /token: minutes-long, so in memory.
            self.pending: dict[str, dict] = {}
            self.codes: dict[str, AuthorizationCode] = {}

        # -- clients -------------------------------------------------------
        async def get_client(self, client_id: str):
            c = load(self.path)["clients"].get(client_id)
            return OAuthClientInformationFull.model_validate(c["info"]) if c else None

        async def register_client(self, client_info) -> None:
            state = load(self.path)
            _prune(state, time.time())
            if sum(1 for c in state["clients"].values() if not c.get("label")) >= MAX_UNPAIRED:
                raise RegistrationError("invalid_client_metadata",
                                        "too many sign-ins waiting; try again later")
            state["clients"][client_info.client_id] = {
                "info": json.loads(client_info.model_dump_json(exclude_none=True)),
                "registered_at": time.time()}
            save(state, self.path)

        # -- authorize: send the browser to the pairing page ---------------
        async def authorize(self, client, params) -> str:
            now = time.time()
            self.pending = {k: v for k, v in self.pending.items() if v["expires_at"] > now}
            if len(self.pending) >= MAX_PENDING:
                raise AuthorizeError("temporarily_unavailable", "too many sign-ins waiting")
            request_id = secrets.token_urlsafe(24)
            self.pending[request_id] = {"client_id": client.client_id, "params": params,
                                        "client_name": client.client_name or "",
                                        "expires_at": now + REQUEST_TTL_S}
            return f"{public_url()}/connector/pair?request={request_id}"

        def pending_request(self, request_id: str) -> dict | None:
            req = self.pending.get(request_id or "")
            if req and req["expires_at"] > time.time():
                return req
            return None

        def complete(self, request_id: str, label: str) -> str:
            """The pairing code was right: issue the authorization code and
            return where to send the browser."""
            req = self.pending.pop(request_id)
            params = req["params"]
            code = secrets.token_urlsafe(32)
            self.codes[code] = AuthorizationCode(
                code=code, scopes=[READ], expires_at=time.time() + CODE_TTL_S,
                client_id=req["client_id"], code_challenge=params.code_challenge,
                redirect_uri=params.redirect_uri,
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                resource=params.resource)
            state = load(self.path)
            client = state["clients"].get(req["client_id"])
            if client is not None:
                client["label"] = label
                client["paired_at"] = time.time()
                save(state, self.path)
            return construct_redirect_uri(str(params.redirect_uri), code=code,
                                          state=params.state)

        # -- codes and tokens ----------------------------------------------
        async def load_authorization_code(self, client, authorization_code: str):
            code = self.codes.get(authorization_code)
            if code and code.client_id == client.client_id and code.expires_at > time.time():
                return code
            return None

        def _issue(self, client_id: str, scopes: list[str], resource: str | None):
            now = time.time()
            state = load(self.path)
            if client_id not in state["clients"]:
                raise TokenError("invalid_grant", "this connector was revoked")
            _prune(state, now)
            access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            entry = {"client_id": client_id, "scopes": scopes, "resource": resource}
            state["access"][_hash(access)] = {**entry, "expires_at": int(now + ACCESS_TTL_S)}
            state["refresh"][_hash(refresh)] = {**entry, "expires_at": int(now + REFRESH_TTL_S)}
            state["clients"][client_id]["last_token_at"] = now
            save(state, self.path)
            return OAuthToken(access_token=access, token_type="Bearer",
                              expires_in=ACCESS_TTL_S, refresh_token=refresh,
                              scope=" ".join(scopes))

        async def exchange_authorization_code(self, client, authorization_code):
            self.codes.pop(authorization_code.code, None)
            return self._issue(client.client_id, [READ], authorization_code.resource)

        async def load_refresh_token(self, client, refresh_token: str):
            t = load(self.path)["refresh"].get(_hash(refresh_token))
            if not t or t["client_id"] != client.client_id or t["expires_at"] <= time.time():
                return None
            return RefreshToken(token=refresh_token, client_id=t["client_id"],
                                scopes=t["scopes"], expires_at=t["expires_at"],
                                resource=t.get("resource"))

        async def exchange_refresh_token(self, client, refresh_token, scopes):
            state = load(self.path)
            state["refresh"].pop(_hash(refresh_token.token), None)
            save(state, self.path)
            # Never more than READ, whatever the client asks for.
            return self._issue(client.client_id, [READ], refresh_token.resource)

        async def load_access_token(self, token: str):
            if self.static_token and hmac.compare_digest(token, self.static_token):
                return AccessToken(token=token, client_id="local", scopes=[READ, WRITE],
                                   resource=f"{public_url()}/mcp")
            t = load(self.path)["access"].get(_hash(token))
            if not t or t["expires_at"] <= time.time():
                return None
            return AccessToken(token=token, client_id=t["client_id"], scopes=t["scopes"],
                               expires_at=t["expires_at"], resource=t.get("resource"))

        async def revoke_token(self, token) -> None:
            state = load(self.path)
            for kind in ("access", "refresh"):
                state[kind].pop(_hash(token.token), None)
            save(state, self.path)

    return Provider()


def auth_settings():
    """``AuthSettings`` for MCPServer when connectors are on."""
    from mcp.server.auth.settings import (
        AuthSettings,
        ClientRegistrationOptions,
        RevocationOptions,
    )
    base = public_url()
    return AuthSettings(
        issuer_url=base,
        resource_server_url=f"{base}/mcp",
        validate_token_resource=False,
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=[READ], default_scopes=[READ]),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=[READ],
    )


def allowed_hosts() -> list[str]:
    """Host headers the transport accepts: loopback, and the public name the
    tunnel forwards (DNS-rebinding protection stays on)."""
    host = urlparse(public_url()).netloc
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    return hosts + ([host] if host else [])


# ---------------------------------------------------------------------------
# The pairing page
# ---------------------------------------------------------------------------

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect to Smoking Pi</title>
<style>body{{font-family:system-ui,sans-serif;max-width:28rem;margin:3rem auto;
padding:0 1rem;color:#1a1a1a;background:#fafafa}}@media(prefers-color-scheme:dark)
{{body{{color:#eee;background:#161616}}input{{background:#222;color:#eee}}}}
h1{{font-size:1.4rem}}input{{font-size:1.4rem;letter-spacing:.2rem;width:100%;
padding:.5rem;box-sizing:border-box;text-transform:uppercase}}button{{font-size:1.1rem;
margin-top:1rem;padding:.6rem 1.2rem}}.err{{color:#b00020}}.note{{opacity:.75}}</style>
</head><body><h1>Connect {client} to Smoking Pi</h1>
<p>It will be able to <b>read</b> this Pi's measurements: latency, loss,
outages, Wi-Fi, charts. It cannot change anything.</p>
<p class="note">On the Pi, run <code>sudo smoking-pi connector pair</code> and
type the code it shows.</p>{error}
<form method="post"><input type="hidden" name="request" value="{request}">
<input name="code" autocomplete="off" autofocus placeholder="ABCD-2345"
 maxlength="12" required><button type="submit">Connect</button></form>
</body></html>"""

_GONE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect to Smoking Pi</title></head><body style="font-family:system-ui,
sans-serif;max-width:28rem;margin:3rem auto;padding:0 1rem"><h1>This sign-in
expired</h1><p>Start again from your assistant: add or reconnect the
Smoking Pi connector.</p></body></html>"""


def page(request_id: str, client_name: str, error: str = "") -> str:
    return _PAGE.format(
        client=html.escape(client_name or "your assistant"),
        request=html.escape(request_id),
        error=f'<p class="err">{html.escape(error)}</p>' if error else "")


def register_routes(mcp: Any, provider: Any) -> None:
    """GET/POST /connector/pair on the MCP app."""
    from starlette.responses import HTMLResponse, RedirectResponse

    @mcp.custom_route("/connector/pair", methods=["GET", "POST"])
    async def pair(request):  # pragma: no cover - exercised through the app
        if request.method == "GET":
            request_id = request.query_params.get("request", "")
            req = provider.pending_request(request_id)
            if not req:
                return HTMLResponse(_GONE, status_code=410)
            return HTMLResponse(page(request_id, req["client_name"]))
        form = await request.form()
        request_id = str(form.get("request", ""))
        req = provider.pending_request(request_id)
        if not req:
            return HTMLResponse(_GONE, status_code=410)
        label = check_pairing(str(form.get("code", "")))
        if not label:
            return HTMLResponse(page(request_id, req["client_name"],
                                     "That code is not right, or it expired. "
                                     "Make a new one on the Pi."), status_code=400)
        return RedirectResponse(provider.complete(request_id, label), status_code=302)


# ---------------------------------------------------------------------------
# CLI: python connector.py pair [LABEL] | list [--json] | revoke NAME
# ---------------------------------------------------------------------------


def _when(epoch: float | None) -> str:
    if not epoch:
        return "never"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("pair", "list", "revoke"):
        print("usage: connector.py pair [LABEL] | list [--json] | revoke NAME",
              file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "pair":
        if not enabled():
            why = url_problem(public_url()) if public_url() else "set MCP_PUBLIC_URL first"
            print(f"Remote assistants are off: {why} (docs/remote-connector.md).",
                  file=sys.stderr)
            return 1
        label = (rest[0] if rest else "assistant").strip()
        code = new_pairing(label)
        print(f"Connector URL:  {public_url()}/mcp")
        print(f"Pairing code:   {code[:4]}-{code[4:]}   (for '{label}', valid 10 minutes, once)")
        print("Add the URL as a custom connector in your assistant; when its sign-in")
        print("page asks, type the code. It gets read-only access.")
        return 0
    if cmd == "list":
        items = connectors()
        if rest[:1] == ["--json"]:
            print(json.dumps(items, indent=1))
            return 0
        if not items:
            print("No connectors.")
            return 0
        for c in items:
            print(f"{c['label']:<16} {'connected' if c['connected'] else 'signed out':<11}"
                  f" paired {_when(c['paired_at'])}, last token {_when(c['last_token_at'])}"
                  f"  ({c['client_name'] or c['client_id']})")
        return 0
    if not rest:
        print("usage: connector.py revoke NAME", file=sys.stderr)
        return 2
    gone = revoke(rest[0])
    if not gone:
        print(f"No connector named {rest[0]!r}.", file=sys.stderr)
        return 1
    print(f"Revoked {rest[0]}: its tokens no longer work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
