"""What the host's watchdog said, read the same way everywhere.

``smoking-pi watchdog`` writes each event into InfluxDB (``watchdog_event``:
tag ``area``, field ``message``). The alerter turns them into signals
(alerter/watchdog_events.py), its weekly summary counts the actions, and the
MCP server's ``get_updates_since`` and ``get_weekly_summary`` report them:
one reading of the watchdog's words for all of them.
"""

from __future__ import annotations

# The watchdog's own bookkeeping, and the start of an episode that its
# action (or the uplink alert) will report: not news on their own.
SKIP = ("watchdog enabled", "watchdog disabled", "; watching", "nothing to watch")
# It could not act, or what it found will not be fixed by acting.
WARN = ("failed", "not rebooting", "not restarting", "keeps restarting",
        "cannot read", "nothing to reload")


def severity(message: str) -> str | None:
    """info, warning, or None for an event that is not news."""
    text = message.lower()
    if any(s in text for s in SKIP):
        return None
    if any(w in text for w in WARN):
        return "warning"
    return "info"


def action(message: str) -> str | None:
    """reload, reboot or restart for an action the watchdog took; None for
    its bookkeeping, a dry run, a refusal or a failure (which is no action)."""
    text = message.lower()
    if severity(message) != "info" or "would " in text:
        return None
    if "reloading the" in text:
        return "reload"
    if text.endswith("rebooting") or "; rebooting" in text:
        return "reboot"
    if "restarting it" in text:
        return "restart"
    return None
