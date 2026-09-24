"""Is a chat assistant using this Smoking Pi? The evidence, not the claim.

The MCP server (Pro, profile ``mcp``) is how an assistant such as OpenClaw
reads the measurements. Whether one is *connected* is not something the
assistant can be asked: a well-primed agent answers fluently from its own
shell while the server sits untouched (docs/openclaw-integration.md,
"Verify with evidence, not with the answer"). The server logs one
``tool=<name>`` line per call, and those lines are the only proof --
the same evidence ``smoking-pi openclaw --check`` reads.

Container logs start over when the container is recreated, so "no calls"
means none since the server started, and the report says when that was.

Pure: api.py supplies the container's state and its timestamped log.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

# `docker logs --timestamps` prefixes each line with an RFC 3339 time.
_CALL = re.compile(r"^(\S+)\s.*?\btool=([A-Za-z_][A-Za-z0-9_]*)\b")


def summarize(state: Optional[str], started_at: Optional[str], logs: str) -> Dict[str, Any]:
    """The web tour's assistant step, from the MCP server's container.

    ``state`` is Docker's (``running``, ``exited``, ...) or None when the
    project has no mcp-server container at all (not Pro, or the ``mcp``
    profile was never enabled).
    """
    if state is None:
        return {"mcp": "absent", "connected": False}
    if state != "running":
        return {"mcp": "stopped", "connected": False, "state": state}
    calls = [m.groups() for m in map(_CALL.match, logs.splitlines()) if m]
    body: Dict[str, Any] = {
        "mcp": "running",
        "connected": bool(calls),
        "since": started_at,
        "calls": len(calls),
    }
    if calls:
        body["last_call"], body["last_tool"] = calls[-1]
    return body
