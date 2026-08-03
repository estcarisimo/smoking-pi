"""Optional bearer-token auth for the streamable-http transport.

The MCP server exposes the whole SmokePing tool surface, including mutations
(add/remove targets, restart SmokePing). It binds to 127.0.0.1 in Compose, so
the network boundary is the host — but anything running on that host can reach
it. Setting ``MCP_API_TOKEN`` requires ``Authorization: Bearer <token>`` on
every HTTP request and gives the port its own credential, separate from the
config-manager API token and from OpenClaw's gateway token.

Unset or empty means no auth: existing deployments keep working unchanged.
The stdio transport is never affected — there is no request to authenticate,
and the caller already had to be able to spawn the process.

The middleware is pure ASGI so it wraps whatever app the installed MCP SDK
builds (``FastMCP`` in mcp 1.x, ``MCPServer`` in 2.x) without depending on
Starlette internals.
"""

from __future__ import annotations

import hmac
import json
import logging
import os

logger = logging.getLogger("mcp.auth")

# Probed by liveness checks that have no way to carry a token. It exposes no
# data, so it stays open when auth is on.
PUBLIC_PATHS = frozenset({"/health"})


def configured_token() -> str:
    return (os.environ.get("MCP_API_TOKEN") or "").strip()


def auth_enabled() -> bool:
    return bool(configured_token())


def extract_bearer(header_value: str | None) -> str | None:
    """Pull the token out of an ``Authorization`` header value.

    Returns None when the header is missing or is not a Bearer credential.
    The scheme is matched case-insensitively (RFC 7235); the token is not.
    """
    if not header_value:
        return None
    parts = header_value.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def token_matches(presented: str | None, expected: str) -> bool:
    """Constant-time comparison, so a wrong token leaks no timing signal."""
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented, expected)


def is_authorized(headers: dict, expected: str, path: str = "") -> bool:
    """Whether a request carrying ``headers`` may proceed.

    ``headers`` maps lowercase header names to values.
    """
    if not expected:
        return True
    if path in PUBLIC_PATHS:
        return True
    return token_matches(extract_bearer(headers.get("authorization")), expected)


def decode_headers(scope: dict) -> dict:
    """ASGI scope headers (list of byte pairs) as a lowercase-keyed dict."""
    headers = {}
    for raw_name, raw_value in scope.get("headers") or []:
        try:
            name = raw_name.decode("latin-1").lower()
            headers[name] = raw_value.decode("latin-1")
        except (UnicodeDecodeError, AttributeError):
            continue
    return headers


class BearerAuthMiddleware:
    """ASGI middleware rejecting requests without a valid bearer token."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        # Lifespan/websocket events carry no Authorization header; only HTTP
        # requests are gated.
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if is_authorized(decode_headers(scope), self.token, path):
            await self.app(scope, receive, send)
            return

        logger.warning("Rejected unauthenticated MCP request to %s", path)
        body = json.dumps({
            "error": "unauthorized",
            "detail": "Missing or invalid Authorization: Bearer token",
        }).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                # Tells a client which scheme to retry with (RFC 7235).
                (b"www-authenticate", b'Bearer realm="smokeping-mcp"'),
            ],
        })
        await send({"type": "http.response.body", "body": body})
