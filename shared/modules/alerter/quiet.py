"""Quiet hours: nothing sounds, and the one message that replaces the night.

Between QUIET_FROM and QUIET_UNTIL (HH:MM, in the digest's zone: DIGEST_TZ,
then TZ, then UTC) nothing sounds (decision of 2026-10-30, front 8): the
push policy (policy.py) keeps an episode that becomes material in the night
for the end of quiet hours -- still open then, it is alerted as by day --
and a material episode that began and ended in the night is held here,
with anything else that would sound. A silent message (the end of an
episode told by day, the monitor's one message) wakes nobody and is sent
as it is. When quiet hours end everything held goes out as ONE silent
message: each episode once, when it started, whether it is over, and what
is open now -- the outbox's summary (outbox.py), under its own header.

Until front 8 a total loss and the monitor going blind passed through
(``TOTAL_RULES``, ``MONITOR_RULES``); now nothing does: a total loss in the
night is alerted at the end of quiet hours if it is still going on, and the
dead-man check-in (healthchecks.io) is what covers the monitor going blind.
The outbox is not held: a notification already late for a failed delivery
goes when it can.

Off unless both times are set and differ. A time that does not read as
HH:MM turns quiet hours off with a warning, never the alert loop.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

import clock
import ledger
import notifications
import outbox
import schedule
import templates
log = logging.getLogger("alerter.quiet")

# As the outbox: a held queue nobody empties must not grow the state file.
MAX_ITEMS = outbox.MAX_ITEMS


def window() -> tuple[tuple[int, int], tuple[int, int]] | None:
    """((from_h, from_m), (until_h, until_m)), or None when off."""
    raw_from = os.environ.get("QUIET_FROM") or ""
    raw_until = os.environ.get("QUIET_UNTIL") or ""
    if not raw_from.strip() and not raw_until.strip():
        return None
    start, end = schedule.parse_hhmm(raw_from), schedule.parse_hhmm(raw_until)
    if start is None or end is None or start == end:
        log.warning("QUIET_FROM=%r / QUIET_UNTIL=%r is not two different HH:MM "
                    "times; quiet hours are off", raw_from, raw_until)
        return None
    return start, end


def _zone():
    return clock.zone()


def active(now: float) -> bool:
    """Whether ``now`` falls in quiet hours (which may span midnight)."""
    span = window()
    if span is None:
        return False
    local = datetime.fromtimestamp(now, tz=_zone())
    minute = local.hour * 60 + local.minute
    start = span[0][0] * 60 + span[0][1]
    end = span[1][0] * 60 + span[1][1]
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def holds(payload: dict, now: float) -> bool:
    """Whether this notification waits for the end of quiet hours: any that
    would sound, and the night's material episodes (policy.py ``night``),
    which are told together at the end. A silent one (the end of an
    episode told by day, the monitor's one message) goes as it is: it
    wakes nobody."""
    if not active(now):
        return False
    return not payload.get("silent") or payload.get("kind") == "night"


def hold(state: dict, payload: dict, now: float) -> None:
    box = state.setdefault("held", {})
    if not box.get("items"):
        box.update({"items": [], "since": now, "dropped": 0})
    # For the assistant (MCP list_alert_state), which has no QUIET_* of its own.
    box["until"] = f"{os.environ.get('QUIET_UNTIL', '').strip()} {_zone().key}"
    box["items"].append({"queued_at": now, "payload": outbox.plain(payload)})
    overflow = len(box["items"]) - MAX_ITEMS
    if overflow > 0:
        del box["items"][:overflow]
        box["dropped"] = int(box.get("dropped", 0)) + overflow
    log.info("Quiet hours: %s (%s) held; %d waiting", payload.get("key"),
             payload.get("type"), len(box["items"]))


def build(state: dict, now: float) -> dict:
    """The one message for everything held, in the outbox's shape and in
    the quiet hours' zone. Carries the held items, so an outbox summary
    that has to take it later lists the incidents, not this message."""
    box = state["held"]
    items = box["items"]
    since = float(box.get("since", items[0]["queued_at"]))
    lines = [
        templates._b(f"🌙 Quiet hours, {clock.at(since, now)} to {clock.at(now, now)}")
        + " — held overnight:",
        outbox.now_line(state, now),
        *outbox.summary_lines(state, items, now, int(box.get("dropped", 0))),
    ]
    return {
        "type": "held",
        "key": "held",
        "severity": "info",
        # A morning summary is read, not woken up to.
        "silent": True,
        "message": "\n".join(lines),
        "items": items,
    }


def release(state: dict, now: float, deliver) -> bool:
    """Once quiet hours are over, hand what was held to ``deliver`` as one
    message (through the outbox if the network is down). Never raises.

    Once that message is delivered, everything it carried is told
    (ledger.told): an alert it summarized does not arrive again on its own.
    Through the outbox, the outbox's summary does that when it goes."""
    if not (state.get("held") or {}).get("items") or active(now):
        return False
    try:
        event = build(state, now)
    except Exception:  # noqa: BLE001 - a summary must never cost the loop
        log.exception("Quiet hours summary failed; sending a plain one")
        held = state["held"]
        event = {"type": "held", "key": "held", "severity": "info", "silent": True,
                 "message": f"Smoking Pi: {len(held['items'])} notification(s) "
                            "held during quiet hours. Ask the assistant for the "
                            "incident history for the details."}
    items = state["held"]["items"]
    state.pop("held", None)
    deliver(event)
    if event.get("delivery") in ("sent", "handed"):
        ledger.told(state, items, now)
        # And in the notification record, which message carried each one.
        # One that went to the outbox instead is said when the outbox
        # delivers it.
        if event.get("notification_id"):
            notifications.delivered_in(notifications.carried_ids(items),
                                       event["notification_id"], now)
    count = len(items)
    log.info("Quiet hours over: %d held notification(s) sent as one", count)
    return True
