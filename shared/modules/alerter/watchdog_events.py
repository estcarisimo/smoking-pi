"""What the host's watchdog did, told once.

``smoking-pi watchdog`` (cli/lib/watchdog.sh) runs on the host: it reloads a
hung Wi-Fi driver, reboots the Pi, restarts SmokePing, and says when it
cannot. It acts exactly when the network is down, so until now its actions
reached its own log and the journal and nobody else: a reboot at 3 a.m.
left no trace a person would see. It now also writes each event into
InfluxDB (``watchdog_event``: tag ``area``, field ``message``), and this
module turns the new ones into notifications. They go through the outbox
like any alert, so a reboot is told once the Pi is back.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import flux
from common import mutes

log = logging.getLogger("alerter.watchdog_events")

MEASUREMENT = "watchdog_event"
# How far back a first look, or a long outage, reads.
LOOKBACK_S = 24 * 3600

# The watchdog's own bookkeeping, and the start of an episode that its
# action (or the uplink alert) will report: not news on their own.
_SKIP = ("watchdog enabled", "watchdog disabled", "; watching", "nothing to watch")
# It could not act, or what it found will not be fixed by acting.
_WARN = ("failed", "not rebooting", "not restarting", "keeps restarting",
         "cannot read", "nothing to reload")


def events_flux(since: float) -> str:
    start = datetime.fromtimestamp(since, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    return (
        f"from(bucket: {flux.flux_str(flux.influx_bucket())}) "
        f"|> range(start: {start}) "
        f'|> filter(fn: (r) => r._measurement == "{MEASUREMENT}" '
        'and r._field == "message") '
        "|> group() "
        '|> sort(columns: ["_time"]) '
        '|> keep(columns: ["_time", "_value", "area"])'
    )


def severity(message: str) -> str | None:
    """info, warning, or None for an event that is not news."""
    text = message.lower()
    if any(s in text for s in _SKIP):
        return None
    if any(w in text for w in _WARN):
        return "warning"
    return "info"


def _epoch(value) -> float | None:
    if value is None:
        return None
    if hasattr(value, "timestamp"):
        return float(value.timestamp())
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def collect(state: dict, query, now: float | None = None) -> list[dict]:
    """New watchdog events as alert payloads. Never raises.

    The first look only marks where to start: events from before the
    alerter knew about them are history, not news.
    """
    if now is None:
        now = time.time()
    book = state.get("watchdog")
    if not isinstance(book, dict) or "seen_until" not in book:
        state["watchdog"] = {"seen_until": now}
        return []
    seen = float(book["seen_until"])
    try:
        rows = query(events_flux(max(seen, now - LOOKBACK_S)))
    except Exception:  # influx client raises many exception types
        log.warning("Watchdog events query failed; read next cycle", exc_info=True)
        return []
    entries = mutes.load()
    payloads = []
    newest = seen
    for row in rows:
        at = _epoch(row.get("_time"))
        if at is None or at <= seen:
            continue
        newest = max(newest, at)
        message = str(row.get("_value") or "")
        level = severity(message)
        if level is None:
            continue
        payload = {
            "type": "alert",
            "rule": "watchdog",
            "severity": level,
            "key": f"watchdog:{int(at)}",
            "target": None,
            "message": (
                "the watchdog on this host, at "
                f"{datetime.fromtimestamp(at, tz=timezone.utc):%H:%M UTC}: "
                f"{message}"
            ),
        }
        if mutes.find(entries, payload, now) is not None:
            log.info("Watchdog event %s muted", payload["key"])
            continue
        payloads.append(payload)
    book["seen_until"] = newest
    return payloads
