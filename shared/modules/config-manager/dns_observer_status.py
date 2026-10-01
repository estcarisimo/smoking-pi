"""The DNS observer's state, for the web admin's dashboard card.

The observer (dns-observer/health.py) writes ``status.json`` into its state
volume, which Pro mounts read-only at ``/dns-observer``. This mirrors
``health.read_status`` -- a stale heartbeat reads as ``down`` -- and adds
the two answers a reader outside Pro needs:

- no ``/dns-observer`` directory: this edition has no observer
  (``available: False``), so the card is not shown;
- the directory but no ``status.json``: never enabled (``enabled: False``),
  so the card offers ``smoking-pi dns enable``.

Pure apart from reading the file; ``now`` is injectable for tests.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

STATE_DIR = Path(os.environ.get("DNS_OBSERVER_DIR", "/dns-observer"))

# health.LIVE_STATES: the observer is getting information.
LIVE_STATES = frozenset(
    {"observing", "quiet", "partial", "upstream_fallback", "upstream_failing"}
)

DOWN_FIX = "smoking-pi up; then smoking-pi logs dns-observer"
# The card's text is fixed: the exception (a path, an errno, a JSON parse
# position) goes to the config-manager log, not to the API response.
UNREADABLE = "The DNS observer's status file is unreadable."
UNREADABLE_FIX = "smoking-pi logs config-manager; then smoking-pi logs dns-observer"


def read(state_dir: Path = STATE_DIR, now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    if not state_dir.is_dir():
        return {"available": False}
    try:
        status = json.loads((state_dir / "status.json").read_text())
    except FileNotFoundError:
        return {"available": True, "enabled": False}
    except (OSError, ValueError):
        logger.exception("Unreadable DNS observer status file in %s", state_dir)
        return _card(
            {"state": "down", "reason": UNREADABLE, "fix": UNREADABLE_FIX}, live=False
        )
    if status.get("state") == "stopped":
        return _card(status, live=False)
    if now > (status.get("stale_after") or 0):
        last = status.get("heartbeat")
        when = f" (last heartbeat {time.strftime('%Y-%m-%d %H:%M', time.localtime(last))})" if last else ""
        return _card(
            {**status, "state": "down",
             "reason": f"The DNS observer is not running{when}.", "fix": DOWN_FIX},
            live=False,
        )
    return _card(status, live=status.get("state") in LIVE_STATES)


def _card(status: Dict[str, Any], live: bool) -> Dict[str, Any]:
    canary = status.get("canary") or {}
    return {
        "available": True,
        "enabled": True,
        "state": status.get("state", "down"),
        "live": live,
        "reason": status.get("reason") or "",
        "fix": status.get("fix") or "",
        "observed_until": status.get("observed_until"),
        "coverage": status.get("coverage") or "",
        "canary": {
            "enabled": bool(canary.get("enabled")),
            "seen_24h": canary.get("seen_24h", 0),
            "sent_24h": canary.get("sent_24h", 0),
        },
    }
