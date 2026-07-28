#!/usr/bin/env python3
"""Entrypoint for the SmokePing MCP server.

Transport selection via environment:

- default (unset / ``MCP_TRANSPORT=stdio``): stdio transport, for
  ``claude mcp add smokeping -- python3 /path/to/main.py``.
- ``MCP_TRANSPORT=http`` (or ``streamable-http``): streamable-http transport
  bound to 0.0.0.0 on ``MCP_PORT`` (default 8090), for running as a Docker
  Compose service.
"""

import os

from server import mcp


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
    if transport in ("http", "streamable-http", "streamable_http"):
        host = os.environ.get("MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("MCP_PORT", "8090"))
        if hasattr(mcp, "settings"):
            # mcp 1.x FastMCP: host/port live on the settings object
            mcp.settings.host = host
            mcp.settings.port = port
            mcp.run(transport="streamable-http")
        else:
            # mcp >= 2.0 MCPServer: host/port are run() kwargs
            mcp.run(transport="streamable-http", host=host, port=port)
    elif transport in ("", "stdio"):
        mcp.run(transport="stdio")
    else:
        raise SystemExit(
            f"Unknown MCP_TRANSPORT={transport!r} (expected 'stdio' or 'http')"
        )


if __name__ == "__main__":
    main()
