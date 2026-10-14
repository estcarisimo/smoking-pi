"""Quiet hours: what is held overnight, and the one message that replaces it.

Between QUIET_FROM and QUIET_UNTIL (HH:MM, in the digest's zone: DIGEST_TZ,
then TZ, then UTC) an alert, a recovery or a watchdog event is held instead
of sent, and when quiet hours end everything held goes out as ONE message:
each incident once, when it started, whether it recovered, and what is open
now -- the outbox's summary (outbox.py), under its own header. Ten messages
at 07:00 describing the night in the voice of the present would be the
burst quiet hours exist to avoid.

A total loss is never held (common.definitions.TOTAL_RULES: the uplink, the
Internet beyond the house, this host's own DNS): it is the one thing worth
waking up for. When it cannot be delivered either, the outbox keeps it as
it keeps any alert.

Off unless both times are set and differ. A time that does not read as
HH:MM turns quiet hours off with a warning, never the alert loop.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

import outbox
import schedule
import templates
from common import definitions

log = logging.getLogger("alerter.quiet")

PASS_RULES = frozenset(definitions.TOTAL_RULES)
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
    return schedule.resolve_zone(os.environ.get("DIGEST_TZ") or os.environ.get("TZ"))


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
    """Whether this notification waits for the end of quiet hours."""
    return payload.get("rule") not in PASS_RULES and active(now)


def hold(state: dict, payload: dict, now: float) -> None:
    box = state.setdefault("held", {})
    if not box.get("items"):
        box.update({"items": [], "since": now, "dropped": 0})
    box["items"].append({"queued_at": now, "payload": outbox._plain(payload)})
    overflow = len(box["items"]) - MAX_ITEMS
    if overflow > 0:
        del box["items"][:overflow]
        box["dropped"] = int(box.get("dropped", 0)) + overflow
    log.info("Quiet hours: %s (%s) held; %d waiting", payload.get("key"),
             payload.get("type"), len(box["items"]))


def build(state: dict, now: float) -> dict:
    """The one message for everything held, in the outbox's shape."""
    box = state["held"]
    items = box["items"]
    since = float(box.get("since", items[0]["queued_at"]))
    esc = templates.esc
    open_keys = set(state.get("incidents") or {})
    lines = [
        templates._b(f"🌙 Quiet hours, {outbox._clock(since, now)} to "
                     f"{outbox._clock(now, now)} UTC")
        + " — held overnight:",
        outbox._now_line(state, now),
    ]
    entries = outbox._incidents(items)
    for entry in entries[:outbox.SUMMARY_LINES]:
        emoji = templates.SEVERITY_EMOJI.get(entry["severity"], "•")
        name = entry["rule"] or entry["key"]
        if entry["target"]:
            name = f"{name} {entry['target']}"
        when = outbox._clock(entry["started"], now) if entry["started"] is not None else ""
        if entry["recovered"] is not None:
            when = f"{when} → ✅ {outbox._clock(entry['recovered'], now)}".strip(" →")
            if entry["started"] is None:
                when = f"✅ {outbox._clock(entry['recovered'], now)}"
        elif entry["key"] not in open_keys:
            when += " (over)"
        lines.append(f"{emoji} {esc(name)} · {when} — {esc(entry['message'] or '')}")
    more = len(entries) - outbox.SUMMARY_LINES + int(box.get("dropped", 0))
    if more > 0:
        lines.append(f"…and {more} more")
    return {
        "type": "held",
        "key": "held",
        "severity": "info",
        # A morning summary is read, not woken up to.
        "silent": True,
        "message": "\n".join(lines),
    }


def release(state: dict, now: float, deliver) -> bool:
    """Once quiet hours are over, hand what was held to ``deliver`` as one
    message (through the outbox if the network is down). Never raises."""
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
    count = len(state["held"]["items"])
    state.pop("held", None)
    deliver(event)
    log.info("Quiet hours over: %d held notification(s) sent as one", count)
    return True
