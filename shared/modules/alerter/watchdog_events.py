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

import clock
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


def _key(row: dict, at: float) -> str:
    # The area and the instant to the microsecond: two events in the same
    # second (a reload, then "reloading failed") are two notifications.
    return f"watchdog:{row.get('area') or 'watchdog'}:{at:.6f}"


def collect(state: dict, query, now: float | None = None) -> list[dict]:
    """New watchdog events as alert payloads. Never raises.

    The first look only marks where to start: events from before the
    alerter knew about them are history, not news. After that, every event
    in the last LOOKBACK_S is read and those already told are skipped by
    their key -- not by a high-water time mark, which a Pi's clock (no
    RTC; NTP steps it after a boot) can move backward, losing the events
    that follow, or forward, hiding them for hours (review of #335).
    """
    if now is None:
        now = time.time()
    try:
        return _collect(state, query, now)
    except Exception:  # noqa: BLE001 - must never cost an alert or the cycle
        log.exception("Watchdog events could not be read; next cycle")
        return []


def _collect(state: dict, query, now: float) -> list[dict]:
    book = state.get("watchdog")
    if not isinstance(book, dict) or "since" not in book:
        state["watchdog"] = {"since": now, "told": {}}
        return []
    since = float(book["since"])
    told = book.setdefault("told", {})
    try:
        rows = query(events_flux(min(now, max(since, now - LOOKBACK_S))))
    except Exception:  # influx client raises many exception types
        log.warning("Watchdog events query failed; read next cycle", exc_info=True)
        return []
    entries = mutes.load()
    payloads = []
    for row in rows:
        at = _epoch(row.get("_time"))
        if at is None or at < since:
            continue
        key = _key(row, at)
        if key in told:
            continue
        told[key] = now
        message = str(row.get("_value") or "")
        level = severity(message)
        if level is None:
            continue
        payload = {
            "type": "alert",
            "rule": "watchdog",
            "severity": level,
            "key": key,
            "target": None,
            "message": (
                "the watchdog on this host, at "
                f"{clock.at(at, now)}: "
                f"{message}"
            ),
        }
        if mutes.find(entries, payload, now) is not None:
            log.info("Watchdog event %s muted", key)
            continue
        payloads.append(payload)
    # Remembered as long as the query can return them, then forgotten.
    for key in [k for k, t in told.items() if now - float(t) > LOOKBACK_S + 3600]:
        del told[key]
    return payloads
