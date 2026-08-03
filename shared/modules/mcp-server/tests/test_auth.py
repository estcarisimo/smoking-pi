"""Bearer auth for the MCP streamable-http transport."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auth  # noqa: E402

TOKEN = "s3cret-token-value"


class TestExtractBearer:
    def test_missing_header(self):
        assert auth.extract_bearer(None) is None
        assert auth.extract_bearer("") is None

    def test_plain_bearer(self):
        assert auth.extract_bearer(f"Bearer {TOKEN}") == TOKEN

    def test_scheme_is_case_insensitive(self):
        # RFC 7235: the scheme is case-insensitive, the credential is not.
        assert auth.extract_bearer(f"bearer {TOKEN}") == TOKEN
        assert auth.extract_bearer(f"BEARER {TOKEN}") == TOKEN

    def test_other_schemes_rejected(self):
        assert auth.extract_bearer(f"Basic {TOKEN}") is None
        assert auth.extract_bearer(TOKEN) is None

    def test_extra_whitespace(self):
        assert auth.extract_bearer(f"Bearer   {TOKEN}  ") == TOKEN

    def test_empty_credential(self):
        assert auth.extract_bearer("Bearer ") is None


class TestTokenMatches:
    def test_exact_match(self):
        assert auth.token_matches(TOKEN, TOKEN) is True

    def test_mismatch(self):
        assert auth.token_matches("wrong", TOKEN) is False

    def test_case_sensitive(self):
        assert auth.token_matches(TOKEN.upper(), TOKEN) is False

    def test_prefix_is_not_enough(self):
        assert auth.token_matches(TOKEN[:-1], TOKEN) is False

    def test_empty_values_never_match(self):
        assert auth.token_matches("", TOKEN) is False
        assert auth.token_matches(None, TOKEN) is False
        assert auth.token_matches(TOKEN, "") is False


class TestIsAuthorized:
    def test_no_token_configured_allows_everything(self):
        # Unset MCP_API_TOKEN must preserve the previous open behaviour.
        assert auth.is_authorized({}, "") is True

    def test_valid_token(self):
        assert auth.is_authorized({"authorization": f"Bearer {TOKEN}"}, TOKEN) is True

    def test_missing_header_rejected(self):
        assert auth.is_authorized({}, TOKEN) is False

    def test_wrong_token_rejected(self):
        assert auth.is_authorized({"authorization": "Bearer nope"}, TOKEN) is False

    def test_health_path_stays_open(self):
        assert auth.is_authorized({}, TOKEN, path="/health") is True

    def test_other_paths_still_gated(self):
        assert auth.is_authorized({}, TOKEN, path="/mcp") is False


class TestDecodeHeaders:
    def test_lowercases_names(self):
        scope = {"headers": [(b"Authorization", b"Bearer x"), (b"HOST", b"pi")]}
        assert auth.decode_headers(scope) == {
            "authorization": "Bearer x", "host": "pi"}

    def test_missing_headers_key(self):
        assert auth.decode_headers({}) == {}


class RecordingApp:
    """Downstream ASGI app that records whether it was reached."""

    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _run(middleware, scope):
    """Drive an ASGI call to completion and return the messages it sent."""
    sent = []

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    asyncio.run(middleware(scope, receive, send))
    return sent


def _http_scope(headers=None, path="/mcp"):
    return {"type": "http", "path": path, "headers": headers or []}


class TestMiddleware:
    def test_valid_token_passes_through(self):
        app = RecordingApp()
        mw = auth.BearerAuthMiddleware(app, TOKEN)
        scope = _http_scope([(b"authorization", f"Bearer {TOKEN}".encode())])
        sent = _run(mw, scope)
        assert app.called is True
        assert sent[0]["status"] == 200

    def test_missing_token_returns_401(self):
        app = RecordingApp()
        mw = auth.BearerAuthMiddleware(app, TOKEN)
        sent = _run(mw, _http_scope())
        # The request must never reach the tool surface.
        assert app.called is False
        assert sent[0]["status"] == 401
        body = json.loads(sent[1]["body"])
        assert body["error"] == "unauthorized"

    def test_401_advertises_bearer_scheme(self):
        mw = auth.BearerAuthMiddleware(RecordingApp(), TOKEN)
        sent = _run(mw, _http_scope())
        headers = dict(sent[0]["headers"])
        assert headers[b"www-authenticate"] == b'Bearer realm="smokeping-mcp"'

    def test_wrong_token_returns_401(self):
        app = RecordingApp()
        mw = auth.BearerAuthMiddleware(app, TOKEN)
        scope = _http_scope([(b"authorization", b"Bearer wrong")])
        sent = _run(mw, scope)
        assert app.called is False
        assert sent[0]["status"] == 401

    def test_lifespan_events_are_not_gated(self):
        # A gated lifespan scope would stop the server from starting at all.
        app = RecordingApp()
        mw = auth.BearerAuthMiddleware(app, TOKEN)
        _run(mw, {"type": "lifespan"})
        assert app.called is True


class TestConfiguredToken:
    def test_unset_disables_auth(self, monkeypatch):
        monkeypatch.delenv("MCP_API_TOKEN", raising=False)
        assert auth.configured_token() == ""
        assert auth.auth_enabled() is False

    def test_blank_is_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv("MCP_API_TOKEN", "   ")
        assert auth.auth_enabled() is False

    def test_set_enables_auth(self, monkeypatch):
        monkeypatch.setenv("MCP_API_TOKEN", TOKEN)
        assert auth.configured_token() == TOKEN
        assert auth.auth_enabled() is True
