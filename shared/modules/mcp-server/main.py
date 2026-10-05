#!/usr/bin/env python3
"""Entrypoint for the SmokePing MCP server.

Transport selection via environment:

- default (unset / ``MCP_TRANSPORT=stdio``): stdio transport, for
  ``claude mcp add smokeping -- python3 /path/to/main.py``.
- ``MCP_TRANSPORT=http`` (or ``streamable-http``): streamable-http transport
  bound to 0.0.0.0 on ``MCP_PORT`` (default 8090), for running as a Docker
  Compose service.

Setting ``MCP_API_TOKEN`` requires a bearer token on HTTP requests (see
auth.py); unset leaves the transport exactly as it was. stdio is unaffected.
"""

import logging
import os

import auth
import connector
from server import mcp

logger = logging.getLogger("mcp")

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _serve_http_with_auth(host: str, port: int, token: str) -> None:
    """Serve the MCP ASGI app behind bearer auth.

    ``streamable_http_app()`` exists on both mcp 1.x FastMCP and 2.x
    MCPServer, so the wrapped app is built the same way on either SDK; we
    then run it ourselves rather than through mcp.run(), which has no hook
    for middleware.
    """
    import uvicorn

    app = auth.BearerAuthMiddleware(mcp.streamable_http_app(), token)
    logger.info("MCP bearer auth enabled (MCP_API_TOKEN set)")
    uvicorn.run(app, host=host, port=port, log_level="info")


def _serve_http_with_connectors(host: str, port: int) -> None:
    """Serve with remote connectors on (connector.py): the SDK's OAuth
    endpoints and bearer check, MCP_API_TOKEN accepted as the local client.
    DNS-rebinding protection stays on, with the public name allowed."""
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=connector.allowed_hosts(),
        allowed_origins=[f"https://{h}" for h in connector.allowed_hosts()
                         if not h.endswith(":*")]
        + ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    )
    app = mcp.streamable_http_app(transport_security=security)
    if not auth.configured_token():
        logger.warning("MCP_API_TOKEN is not set: only remote connectors can sign in")
    logger.info("Remote connectors on at %s/mcp (read-only, pairing code sign-in)",
                connector.public_url())
    uvicorn.run(app, host=host, port=port, log_level="info", proxy_headers=True,
                forwarded_allow_ips="127.0.0.1")


def main() -> None:
    # force: importing the SDK already gave the root logger a bare
    # "%(message)s" handler, so a plain basicConfig did nothing and tool=
    # lines lost the "mcp.tools:" prefix the web tour's assistant step reads.
    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
        force=True,
    )
    transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
    if transport in ("http", "streamable-http", "streamable_http"):
        host = os.environ.get("MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("MCP_PORT", "8090"))
        token = auth.configured_token()
        if connector.enabled():
            _serve_http_with_connectors(host, port)
        elif token:
            _serve_http_with_auth(host, port, token)
        elif hasattr(mcp, "settings"):
            # mcp 1.x FastMCP: host/port live on the settings object
            logger.warning(
                "MCP_API_TOKEN is not set: the HTTP transport is unauthenticated"
            )
            mcp.settings.host = host
            mcp.settings.port = port
            mcp.run(transport="streamable-http")
        else:
            # mcp >= 2.0 MCPServer: host/port are run() kwargs
            logger.warning(
                "MCP_API_TOKEN is not set: the HTTP transport is unauthenticated"
            )
            mcp.run(transport="streamable-http", host=host, port=port)
    elif transport in ("", "stdio"):
        mcp.run(transport="stdio")
    else:
        raise SystemExit(
            f"Unknown MCP_TRANSPORT={transport!r} (expected 'stdio' or 'http')"
        )


if __name__ == "__main__":
    main()
