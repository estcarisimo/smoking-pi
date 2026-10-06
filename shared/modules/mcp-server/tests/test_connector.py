"""Remote connectors: pairing-code sign-in, read-only scope, revocation.

The whole browser flow runs against the real app (the MCP SDK's OAuth
endpoints plus connector.py), in process: register, authorize, the pairing
page, the token, then MCP calls with that token. Nothing leaves the test.
"""

import base64
import html
import hashlib
import importlib
import json
import re
import secrets
import time
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

import connector

PUBLIC = "https://mcp.example.com"
REDIRECT = "https://assistant.example.net/callback"


# ---------------------------------------------------------------------------
# Pairing codes and the store
# ---------------------------------------------------------------------------


@pytest.fixture()
def state(tmp_path, monkeypatch):
    path = str(tmp_path / "connector.json")
    monkeypatch.setenv("MCP_CONNECTOR_STATE", path)
    return path


def test_a_pairing_code_works_once(state):
    code = connector.new_pairing("grok", now=1000)
    assert connector.check_pairing(code.lower(), now=1001) == "grok"
    assert connector.check_pairing(code, now=1002) is None


def test_a_pairing_code_expires(state):
    code = connector.new_pairing("grok", now=1000)
    assert connector.check_pairing(code, now=1000 + connector.PAIRING_TTL_S + 1) is None


def test_five_wrong_tries_burn_the_code(state):
    code = connector.new_pairing("grok", now=1000)
    for _ in range(connector.PAIRING_TRIES):
        assert connector.check_pairing("WRONG234", now=1001) is None
    assert connector.check_pairing(code, now=1002) is None


def test_the_code_accepts_the_dash_it_is_shown_with(state):
    code = connector.new_pairing("grok", now=1000)
    assert connector.check_pairing(f"{code[:4]}-{code[4:]}", now=1001) == "grok"


def test_tokens_are_stored_hashed(state):
    connector.new_pairing("grok")
    raw = open(state).read()
    assert "hash" in raw
    assert connector.load()["pairing"]["hash"] != ""


def test_a_corrupt_state_file_starts_empty(state):
    with open(state, "w") as fh:
        fh.write("{not json")
    assert connector.load()["clients"] == {}


def test_pair_refuses_when_connectors_are_off(state, monkeypatch, capsys):
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    assert connector.main(["pair", "grok"]) == 1
    assert "MCP_PUBLIC_URL" in capsys.readouterr().err


def test_pair_prints_the_url_and_a_code(state, monkeypatch, capsys):
    monkeypatch.setenv("MCP_PUBLIC_URL", PUBLIC + "/")
    assert connector.main(["pair", "grok"]) == 0
    out = capsys.readouterr().out
    assert f"{PUBLIC}/mcp" in out and "Pairing code:" in out


@pytest.mark.parametrize("url", ["dummy", "http://mcp.example.com",
                                 "https://mcp.example.com/mcp", "https://"])
def test_an_unusable_public_url_leaves_connectors_off(url, monkeypatch):
    monkeypatch.setenv("MCP_PUBLIC_URL", url)
    assert connector.enabled() is False
    assert connector.url_problem(url)


def test_a_plain_https_address_turns_them_on(monkeypatch):
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mcp.example.com/")
    assert connector.enabled() is True


def test_allowed_hosts_add_the_public_name_only():
    hosts = connector.allowed_hosts()
    assert "127.0.0.1:*" in hosts


# ---------------------------------------------------------------------------
# The whole flow, through the app
# ---------------------------------------------------------------------------


@pytest.fixture()
def app(state, monkeypatch):
    import backends
    import server

    monkeypatch.setenv("MCP_PUBLIC_URL", PUBLIC)
    monkeypatch.setenv("MCP_API_TOKEN", "local-secret")
    module = importlib.reload(server)
    monkeypatch.setattr(module, "query_influx", lambda flux: [])
    monkeypatch.setattr(backends, "query_influx", lambda flux: [])
    monkeypatch.setattr(module, "_cadences", lambda: {})
    from test_tools import ExplodingConfigAPI
    monkeypatch.setattr(backends, "get_config_api", lambda: ExplodingConfigAPI())
    from mcp.server.transport_security import TransportSecuritySettings
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                         allowed_hosts=connector.allowed_hosts())
    yield module, connector.wrap(module.mcp.streamable_http_app(transport_security=security))
    monkeypatch.delenv("MCP_PUBLIC_URL")
    monkeypatch.delenv("MCP_API_TOKEN")
    importlib.reload(server)


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _sign_in(client, code_for_pairing):
    reg = client.post("/register", json={
        "redirect_uris": [REDIRECT], "client_name": "Grok",
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"]})
    assert reg.status_code == 201, reg.text
    client_id = reg.json()["client_id"]
    verifier, challenge = _pkce()
    auth = client.get("/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz",
        "resource": f"{PUBLIC}/mcp"}, follow_redirects=False)
    assert auth.status_code == 302, auth.text
    pair_url = urlparse(auth.headers["location"])
    assert pair_url.path == "/connector/pair"
    request_id = parse_qs(pair_url.query)["request"][0]
    page = client.get("/connector/pair", params={"request": request_id})
    assert page.status_code == 200 and "Connect Grok to Smoking Pi" in page.text
    done = client.post("/connector/pair", data={"request": request_id,
                                                "code": code_for_pairing},
                       follow_redirects=False)
    return client_id, verifier, request_id, done


def _back(client, done):
    """Follow the sign-in the way a browser does: the POST goes to the
    continue page on the Pi, which sends the browser back to the assistant."""
    assert done.status_code == 303, done.text
    assert urlparse(done.headers["location"]).path == "/connector/pair"
    page = client.get(done.headers["location"], follow_redirects=False)
    assert page.status_code == 200 and "Connected" in page.text, page.text
    found = re.search(r'http-equiv="refresh" content="0;url=([^"]+)"', page.text)
    assert found, page.text
    return html.unescape(found.group(1))


def _token(client, client_id, verifier, done):
    back = parse_qs(urlparse(_back(client, done)).query)
    assert back["state"] == ["xyz"]
    tok = client.post("/token", data={
        "grant_type": "authorization_code", "code": back["code"][0],
        "redirect_uri": REDIRECT, "client_id": client_id, "code_verifier": verifier,
        "resource": f"{PUBLIC}/mcp"})
    assert tok.status_code == 200, tok.text
    return tok.json()


def _call(client, token, tool, arguments, host="mcp.example.com"):
    headers = {"Authorization": f"Bearer {token}", "Accept":
               "application/json, text/event-stream", "Host": host}
    init = client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"}}})
    assert init.status_code == 200, init.text
    session = init.headers.get("mcp-session-id")
    if session:
        headers["mcp-session-id"] = session
    client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "method": "notifications/initialized"})
    resp = client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": tool, "arguments": arguments}})
    assert resp.status_code == 200, resp.text
    body = resp.text
    if body.lstrip().startswith("{"):
        return json.loads(body)
    data = [line[5:] for line in body.splitlines() if line.startswith("data:")]
    return json.loads(data[-1])


def _text(result):
    return " ".join(c.get("text", "") for c in result["result"]["content"])


def test_mcp_without_a_token_is_refused(app):
    _, asgi = app
    with TestClient(asgi, base_url=PUBLIC) as client:
        resp = client.post("/mcp", json={}, headers={"Host": "mcp.example.com"})
        assert resp.status_code == 401
        assert "oauth-protected-resource" in resp.headers.get("www-authenticate", "")


def test_a_wrong_pairing_code_is_refused_and_says_so(app, state):
    _, asgi = app
    connector.new_pairing("grok")
    with TestClient(asgi, base_url=PUBLIC) as client:
        _, _, _, done = _sign_in(client, "WRONG234")
        assert done.status_code == 400 and "not right" in done.text


def test_a_connector_signs_in_reads_and_cannot_write(app, state):
    _, asgi = app
    code = connector.new_pairing("grok")
    with TestClient(asgi, base_url=PUBLIC) as client:
        client_id, verifier, _, done = _sign_in(client, code)
        token = _token(client, client_id, verifier, done)
        assert token["scope"] == "read"
        read = _call(client, token["access_token"], "diagnose_loss", {"hours": 24})
        assert "incidents" in _text(read)
        write = json.loads(_text(_call(client, token["access_token"], "mute_alerts",
                                       {"target": "google", "hours": 1})))
        assert write["reason"] == "read_only"
    listed = connector.connectors()
    assert [c["label"] for c in listed] == ["grok"] and listed[0]["connected"]


def test_the_local_token_keeps_write_on_this_machine(app, state, tmp_path, monkeypatch):
    monkeypatch.setenv("ALERT_MUTES_FILE", str(tmp_path / "mutes.json"))
    _, asgi = app
    with TestClient(asgi, base_url="http://127.0.0.1:8090") as client:
        result = json.loads(_text(_call(client, "local-secret", "unmute_alerts",
                                        {"all": True}, host="127.0.0.1:8090")))
    assert "error" not in result, result
    assert (tmp_path / "mutes.json").exists()


def test_a_revoked_connector_is_locked_out(app, state):
    _, asgi = app
    code = connector.new_pairing("grok")
    with TestClient(asgi, base_url=PUBLIC) as client:
        client_id, verifier, _, done = _sign_in(client, code)
        token = _token(client, client_id, verifier, done)
        assert connector.revoke("grok") == [client_id]
        resp = client.post("/mcp", json={}, headers={
            "Authorization": f"Bearer {token['access_token']}",
            "Host": "mcp.example.com"})
        assert resp.status_code == 401
        refresh = client.post("/token", data={
            "grant_type": "refresh_token", "refresh_token": token["refresh_token"],
            "client_id": client_id})
        assert refresh.status_code in (400, 401)


def test_a_refresh_never_widens_the_scope(app, state):
    _, asgi = app
    code = connector.new_pairing("grok")
    with TestClient(asgi, base_url=PUBLIC) as client:
        client_id, verifier, _, done = _sign_in(client, code)
        token = _token(client, client_id, verifier, done)
        again = client.post("/token", data={
            "grant_type": "refresh_token", "refresh_token": token["refresh_token"],
            "client_id": client_id, "scope": "read"})
        assert again.status_code == 200, again.text
        assert again.json()["scope"] == "read"
        reused = client.post("/token", data={
            "grant_type": "refresh_token", "refresh_token": token["refresh_token"],
            "client_id": client_id})
        assert reused.status_code in (400, 401)


def test_an_expired_sign_in_page_says_so(app):
    _, asgi = app
    with TestClient(asgi, base_url=PUBLIC) as client:
        resp = client.get("/connector/pair", params={"request": "nope"})
        assert resp.status_code == 410 and "expired" in resp.text


def test_a_foreign_host_header_is_refused(app, state):
    _, asgi = app
    with TestClient(asgi, base_url=PUBLIC) as client:
        resp = client.post("/mcp", json={}, headers={
            "Authorization": "Bearer local-secret", "Host": "evil.example.org",
            "Accept": "application/json, text/event-stream"})
        # Not loopback, so the local token is not honored; and the transport
        # would refuse the host anyway.
        assert resp.status_code in (401, 403, 421)


def test_the_local_token_does_not_work_through_the_tunnel(app, state):
    """A leaked MCP_API_TOKEN must not be a key to the Pi from the internet."""
    _, asgi = app
    with TestClient(asgi, base_url=PUBLIC) as client:
        resp = client.post("/mcp", json={}, headers={
            "Authorization": "Bearer local-secret", "Host": "mcp.example.com",
            "Accept": "application/json, text/event-stream"})
        assert resp.status_code == 401


def test_a_non_ascii_bearer_is_a_401_not_a_crash(app, state):
    _, asgi = app
    with TestClient(asgi, base_url=PUBLIC, raise_server_exceptions=False) as client:
        resp = client.post("/mcp", json={}, headers={
            "Authorization": "Bearer café".encode("latin-1"),
            "Host": "127.0.0.1:8090"})
        assert resp.status_code == 401


def test_the_pairing_page_shows_what_the_owner_controls(app, state):
    _, asgi = app
    connector.new_pairing("grok")
    with TestClient(asgi, base_url=PUBLIC) as client:
        reg = client.post("/register", json={
            "redirect_uris": [REDIRECT], "client_name": "<b>Totally Grok</b>",
            "token_endpoint_auth_method": "none"})
        _, challenge = _pkce()
        auth = client.get("/authorize", params={
            "response_type": "code", "client_id": reg.json()["client_id"],
            "redirect_uri": REDIRECT, "code_challenge": challenge,
            "code_challenge_method": "S256", "state": "s"}, follow_redirects=False)
        page = client.get(auth.headers["location"].replace(PUBLIC, ""))
    assert "for <b>grok</b>" in page.text
    assert page.text.count("assistant.example.net") == 1
    assert "&lt;b&gt;Totally Grok&lt;/b&gt;" in page.text
    assert page.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"
    # The form posts only here; the way back is the continue page.
    assert page.headers["content-security-policy"].count("form-action 'self';") == 1


@pytest.mark.parametrize("uri,source", [
    ("https://www.cursor.com/agents/mcp/oauth/callback", "https://www.cursor.com"),
    ("http://127.0.0.1:33418/callback", "http://127.0.0.1:33418"),
    ("http://[::1]:8080/cb", "http://[::1]:8080"),
    ("cursor://anysphere.cursor-mcp/oauth/callback", "cursor:"),
    ("HTTPS://WWW.Cursor.com/cb", "https://WWW.Cursor.com"),
    ("javascript:alert(1)", ""),
    ("data:text/html,hi", ""),
    ("blob:https://a.example/x", ""),
    ("filesystem:https://a.example/x", ""),
    ("file:///etc/passwd", ""),
    ("about:blank", ""),
    ("vbscript:x", ""),
    ("https://user@evil.example/cb", ""),
    ("https://a.example;script-src */cb", ""),
])
def test_the_return_source_is_one_csp_source_or_nothing(uri, source):
    assert connector.return_source(uri) == source


def test_the_form_never_redirects_off_the_pi(app, state):
    """Browsers apply form-action to every hop after a form POST, and
    Cursor's callback redirects www.cursor.com -> cursor.com: a redirect
    to the assistant from the POST was blocked (v2.24.0 and v2.24.1)."""
    _, asgi = app
    code = connector.new_pairing("grok")
    with TestClient(asgi, base_url=PUBLIC) as client:
        _, _, request_id, done = _sign_in(client, code)
        assert done.status_code == 303
        assert done.headers["location"] == f"/connector/pair?request={request_id}"
        # The token to the code is a cookie, never in the logged URL.
        cookie = done.headers["set-cookie"]
        assert cookie.startswith("smoking_pi_pair_" + request_id[:12] + "=")
        for attr in ("HttpOnly", "Secure", "SameSite=lax", "Path=/connector/pair"):
            assert cookie.count(attr) == 1, cookie
        back = _back(client, done)
    assert back.startswith(REDIRECT + "?")
    assert parse_qs(urlparse(back).query)["state"] == ["xyz"]


def test_the_continue_page_needs_the_cookie(app, state):
    """Knowing the request id (it is in the access log) is not enough."""
    _, asgi = app
    code = connector.new_pairing("grok")
    with TestClient(asgi, base_url=PUBLIC) as client:
        _, _, request_id, done = _sign_in(client, code)
        client.cookies.clear()
        page = client.get(done.headers["location"])
        assert page.status_code == 200 and "already used" in page.text
        assert "url=" not in page.text


def test_the_continue_page_never_links_a_script_scheme(app, state):
    """Defense in depth behind the SDK's redirect_uri check."""
    module, asgi = app
    code = connector.new_pairing("grok")
    with TestClient(asgi, base_url=PUBLIC) as client:
        _, _, request_id, done = _sign_in(client, code)
        module._connector.completed[request_id]["back"] = "javascript:alert(1)"
        page = client.get(done.headers["location"])
    assert page.status_code == 200 and "already used" in page.text
    assert "url=" not in page.text and "<a href" not in page.text


def test_a_second_click_returns_to_the_assistant_again(app, state):
    """The browser re-posted the form 1-3 s after the code was taken and
    showed 'expired': the sign-in had worked, the owner was told it had not."""
    _, asgi = app
    code = connector.new_pairing("grok")
    with TestClient(asgi, base_url=PUBLIC) as client:
        client_id, verifier, request_id, done = _sign_in(client, code)
        again = client.post("/connector/pair", data={"request": request_id,
                                                     "code": code},
                            follow_redirects=False)
        assert again.status_code == 303
        assert again.headers["location"] == done.headers["location"]
        # Only the code that completed it gets the way back again.
        other = client.post("/connector/pair", data={"request": request_id,
                                                     "code": "AAAA-BBBB"},
                            follow_redirects=False)
        assert other.status_code == 200 and "already used" in other.text
        assert "location" not in other.headers
        _token(client, client_id, verifier, again)
        after = client.post("/connector/pair", data={"request": request_id,
                                                     "code": code},
                            follow_redirects=False)
        assert after.status_code == 200 and "already used" in after.text


def test_a_completed_sign_in_is_forgotten_with_its_code(app, state, monkeypatch):
    _, asgi = app
    code = connector.new_pairing("grok")
    with TestClient(asgi, base_url=PUBLIC) as client:
        _, _, request_id, done = _sign_in(client, code)
        assert done.status_code == 303
        later = time.time() + connector.CODE_TTL_S + 1
        monkeypatch.setattr(connector.time, "time", lambda: later)
        resp = client.post("/connector/pair", data={"request": request_id,
                                                    "code": code},
                           follow_redirects=False)
        assert resp.status_code == 410


def test_a_revoke_is_never_undone_by_a_concurrent_write(state):
    """Two processes write the file; each read-modify-write holds the lock."""
    import threading
    connector.new_pairing("grok")
    with connector.transaction() as st:
        st["clients"]["c1"] = {"label": "grok", "info": {}}
    done = threading.Event()

    def revoke_soon():
        connector.revoke("grok")
        done.set()

    with connector.transaction() as st:
        t = threading.Thread(target=revoke_soon)
        t.start()
        assert not done.wait(0.2)  # blocked on the lock
        st["clients"]["c1"]["last_token_at"] = 1.0
    t.join(5)
    assert "c1" not in connector.load()["clients"]
