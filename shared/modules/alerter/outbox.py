"""Undelivered notifications, and the one message that replaces them.

The reference Pi's radio hung for 11 hours on 2026-10-11 and for five and a
half on 2026-10-12. Every alert the alerter decided on in that time failed
to send, and nothing ever said so: a failed alert waited for the next
cooldown, a failed outage warning was never retried, and a failed recovery
was gone (reconcile had already dropped its record). When the network came
back, what arrived was whatever happened to be decided at that moment.

So a notification that cannot be delivered goes here instead (``state
["outbox"]``, persisted with the incidents), and while anything is waiting:

- new notifications join it rather than being sent into a dead link, where
  each attempt costs up to half a minute of the loop -- and, with OpenClaw,
  leaves a copy in the gateway's own durable queue that it replays later;
- once per retry interval (60 s, doubling to 10 min) the alerter asks
  whether the network is back (``reachable``, a TCP connect: nothing is
  queued anywhere by asking), and only then tries to deliver;
- what it delivers is ONE message. A single notification younger than
  ``RESEND_AS_IS_S`` is sent as it was; anything more is a summary of the
  whole stretch -- each incident once, when it started, whether it
  recovered -- and what is open now. Ten stale messages in a row would
  describe the past in the voice of the present.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import templates

log = logging.getLogger("alerter.outbox")

# Bounded: an outbox nobody can empty (a revoked token) must not grow the
# state file forever. The oldest go first, and the summary says how many.
MAX_ITEMS = 100
RETRY_MIN_S = 60
RETRY_MAX_S = 600
# One undelivered notification this recent is still news as it stands.
RESEND_AS_IS_S = 600
# Lines in a summary before "and N more".
SUMMARY_LINES = 15


def pending(state: dict) -> bool:
    return bool((state.get("outbox") or {}).get("items"))


def _plain(payload: dict) -> dict:
    """A JSON-safe copy: the state file must always be writable."""
    return json.loads(json.dumps(payload, default=str))


def enqueue(state: dict, payload: dict, now: float | None = None) -> None:
    """Keep ``payload`` for later. The chart is not kept: a summary has none,
    and a resend as-is goes out as text."""
    if now is None:
        now = time.time()
    box = state.setdefault("outbox", {})
    if not box.get("items"):
        # Not retried in the same cycle: the send that just failed was the
        # try, and an immediate second one would only fail the same way.
        box.update({"items": [], "since": now, "attempts": 0,
                    "next_try": now + RETRY_MIN_S, "dropped": 0})
    box["items"].append({"queued_at": now, "payload": _plain(payload)})
    overflow = len(box["items"]) - MAX_ITEMS
    if overflow > 0:
        del box["items"][:overflow]
        box["dropped"] = int(box.get("dropped", 0)) + overflow
    log.warning("Notification %s (%s) not delivered; %d waiting in the outbox",
                payload.get("key"), payload.get("type"), len(box["items"]))


def due(state: dict, now: float) -> bool:
    return pending(state) and now >= float(state["outbox"].get("next_try", 0))


def failed(state: dict, now: float) -> None:
    """Back off: 60 s, doubling, at most 10 min between tries."""
    box = state["outbox"]
    box["attempts"] = int(box.get("attempts", 0)) + 1
    delay = min(RETRY_MAX_S, RETRY_MIN_S * 2 ** (box["attempts"] - 1))
    box["next_try"] = now + delay


def clear(state: dict) -> None:
    state.pop("outbox", None)


def _clock(ts: float, now: float) -> str:
    at = datetime.fromtimestamp(ts, tz=timezone.utc)
    today = datetime.fromtimestamp(now, tz=timezone.utc).date()
    return at.strftime("%H:%M" if at.date() == today else "%d/%m %H:%M")


def _span(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _incidents(items: list[dict]) -> list[dict]:
    """One entry per incident key, in the order they started."""
    by_key: dict[str, dict] = {}
    for item in items:
        payload = item["payload"]
        key = payload.get("key") or payload.get("type") or "?"
        entry = by_key.setdefault(key, {
            "key": key,
            "rule": payload.get("rule"),
            "severity": payload.get("severity"),
            "target": payload.get("target"),
            "message": payload.get("message"),
            "started": item["queued_at"],
            "recovered": None,
        })
        if payload.get("type") == "recovery":
            entry["recovered"] = item["queued_at"]
        else:
            # A re-notification carries the newer numbers; a later alert
            # after a recovery means it came back.
            entry["message"] = payload.get("message") or entry["message"]
            entry["severity"] = payload.get("severity") or entry["severity"]
            entry["recovered"] = None
    return sorted(by_key.values(), key=lambda e: e["started"])


def build(state: dict, now: float) -> dict:
    """The one event that delivers everything waiting.

    ``state["incidents"]`` (already reconciled this cycle) supplies the
    "now" line, so the summary ends on the present, not on the last thing
    that failed to send.
    """
    box = state["outbox"]
    items = box["items"]
    if len(items) == 1 and not box.get("dropped") and (
            now - items[0]["queued_at"] < RESEND_AS_IS_S):
        return dict(items[0]["payload"])

    since = float(box.get("since", items[0]["queued_at"]))
    esc = templates.esc
    lines = [
        templates._b(f"📭 Not delivered from {_clock(since, now)} to "
                     f"{_clock(now, now)} UTC ({_span(now - since)})")
        + " — this is what happened meanwhile:"
    ]
    entries = _incidents(items)
    for entry in entries[:SUMMARY_LINES]:
        emoji = templates.SEVERITY_EMOJI.get(entry["severity"], "•")
        name = entry["rule"] or entry["key"]
        if entry["target"]:
            name = f"{name} {entry['target']}"
        when = _clock(entry["started"], now)
        if entry["recovered"] is not None:
            when += f" → ✅ {_clock(entry['recovered'], now)}"
        lines.append(f"{emoji} {esc(name)} · {when} — {esc(entry['message'] or '')}")
    more = len(entries) - SUMMARY_LINES + int(box.get("dropped", 0))
    if more > 0:
        lines.append(f"…and {more} more")

    still_open = sorted(
        (record.get("first_seen") or 0, key, record)
        for key, record in (state.get("incidents") or {}).items()
        if not record.get("transient")
    )
    if still_open:
        now_line = ", ".join(
            f"{templates.SEVERITY_EMOJI.get(r.get('severity'), '•')} "
            f"{esc(r.get('rule') or key)}"
            f"{' ' + esc(r['target']) if r.get('target') else ''}"
            f" (since {_clock(first, now)})"
            for first, key, r in still_open
        )
        lines.append(f"Now open: {now_line}")
    else:
        lines.append(f"{templates.STATUS_EMOJI['ok']} Nothing open now.")
    return {
        "type": "undelivered",
        "key": "undelivered",
        "severity": "info",
        "message": "\n".join(lines),
    }


def flush(state: dict, now: float, send, reachable) -> bool | None:
    """Try to deliver what is waiting, if it is time. Never raises.

    ``send(event) -> bool`` and ``reachable() -> bool`` are the notifier's;
    passed in so this module needs no network. Returns True when the outbox
    was emptied, False when a try failed, None when nothing was tried.
    """
    if not due(state, now):
        return None
    try:
        if not reachable():
            failed(state, now)
            log.info("Delivery still unreachable; %d notification(s) waiting, "
                     "next try in %ds", len(state["outbox"]["items"]),
                     int(state["outbox"]["next_try"] - now))
            return False
        event = build(state, now)
        if send(event):
            log.info("Delivered %d waiting notification(s) as one %s",
                     len(state["outbox"]["items"]), event.get("type"))
            clear(state)
            return True
    except Exception:  # noqa: BLE001 - the outbox must never cost the loop
        log.exception("Outbox delivery failed")
    failed(state, now)
    return False
