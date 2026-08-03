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
from server import mcp

logger = logging.getLogger("mcp")


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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
    if transport in ("http", "streamable-http", "streamable_http"):
        host = os.environ.get("MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("MCP_PORT", "8090"))
        token = auth.configured_token()
        if token:
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
