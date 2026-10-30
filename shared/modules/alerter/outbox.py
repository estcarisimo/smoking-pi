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

Nothing is said twice (ledger.py). A notification the OpenClaw gateway
answered, or may have received, is with the gateway, which delivers it from
its own queue: it is kept here only to hold the queue shut while delivery
is down (``handed``), and is neither resent nor summarized. A summary that
is delivered marks everything it told as told.
"""

from __future__ import annotations

import json
import logging
import time

import clock
import ledger
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
# Refusals with the network up before the summary is replaced by a plain one.
FALLBACK_AFTER = 3


def pending(state: dict) -> bool:
    return bool((state.get("outbox") or {}).get("items"))


def plain(payload: dict) -> dict:
    """A JSON-safe copy: the state file must always be writable."""
    return json.loads(json.dumps(payload, default=str))


def enqueue(state: dict, payload: dict, now: float | None = None,
            handed: bool = False) -> None:
    """Keep ``payload`` for later. The chart is not kept: a summary has none,
    and a resend as-is goes out as text. ``handed``: the gateway has it, and
    delivers it itself; kept only so what follows waits too."""
    if now is None:
        now = time.time()
    box = state.setdefault("outbox", {})
    if not box.get("items"):
        # Not retried in the same cycle: the send that just failed was the
        # try, and an immediate second one would only fail the same way.
        box.update({"items": [], "since": now, "attempts": 0,
                    "next_try": now + RETRY_MIN_S, "dropped": 0})
    item = {"queued_at": now, "payload": plain(payload)}
    if handed:
        item["handed"] = True
    box["items"].append(item)
    overflow = len(box["items"]) - MAX_ITEMS
    if overflow > 0:
        del box["items"][:overflow]
        box["dropped"] = int(box.get("dropped", 0)) + overflow
    log.warning("Notification %s (%s) not delivered; %d waiting in the outbox",
                payload.get("key"), payload.get("type"), len(box["items"]))


def _to_tell(box: dict) -> list[dict]:
    """The items this outbox still has to deliver: not those the gateway
    holds."""
    return [item for item in box.get("items", []) if not item.get("handed")]


def due(state: dict, now: float) -> bool:
    if not pending(state):
        return False
    next_try = float(state["outbox"].get("next_try", 0))
    # A next_try further out than any backoff is a clock that went back
    # (a Pi has no RTC): try now rather than wait for the clock to catch up.
    return now >= next_try or next_try > now + RETRY_MAX_S


def failed(state: dict, now: float) -> None:
    """Back off: 60 s, doubling, at most 10 min between tries."""
    box = state["outbox"]
    box["attempts"] = int(box.get("attempts", 0)) + 1
    delay = min(RETRY_MAX_S, RETRY_MIN_S * 2 ** (box["attempts"] - 1))
    box["next_try"] = now + delay


def clear(state: dict) -> None:
    state.pop("outbox", None)


def _span(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _incidents(items: list[dict]) -> list[dict]:
    """One entry per incident key, in the order they started.

    A key whose first queued item is a recovery was announced before the
    outage: its start is the recovery's duration back, when it has one, or
    unknown (None), never the moment the recovery was queued.
    """
    by_key: dict[str, dict] = {}
    # A quiet-hours summary that could not be sent carries what it held:
    # those are the incidents, not the summary (review of #343).
    flat = []
    for item in items:
        if item["payload"].get("type") == "held" and item["payload"].get("items"):
            flat += item["payload"]["items"]
        else:
            flat.append(item)
    for item in flat:
        payload = item["payload"]
        key = payload.get("key") or payload.get("type") or "?"
        if key not in by_key:
            started = item["queued_at"]
            if payload.get("type") == "recovery":
                duration = payload.get("duration_s")
                started = started - float(duration) if duration else None
            by_key[key] = {
                "key": key,
                "rule": payload.get("rule"),
                "severity": payload.get("severity"),
                "target": payload.get("target"),
                "message": payload.get("message"),
                "started": started,
                "order": item["queued_at"],
                "recovered": None,
            }
        entry = by_key[key]
        if payload.get("type") == "recovery":
            entry["recovered"] = item["queued_at"]
        else:
            # A re-notification carries the newer numbers; a later alert
            # after a recovery means it came back.
            entry["message"] = payload.get("message") or entry["message"]
            entry["severity"] = payload.get("severity") or entry["severity"]
            entry["recovered"] = None
    return sorted(by_key.values(),
                  key=lambda e: e["started"] if e["started"] is not None else e["order"])


def now_line(state: dict, now: float) -> str:
    esc = templates.esc
    still_open = sorted(
        (record.get("first_seen") or 0, key, record)
        for key, record in (state.get("incidents") or {}).items()
        if not record.get("transient")
    )
    if not still_open:
        return f"{templates.STATUS_EMOJI['ok']} Nothing open now."
    return "Now open: " + ", ".join(
        f"{templates.SEVERITY_EMOJI.get(r.get('severity'), '•')} "
        f"{esc(r.get('rule') or key)}"
        f"{' ' + esc(r['target']) if r.get('target') else ''}"
        f" (since {clock.at(first, now)})"
        for first, key, r in still_open
    )


def summary_lines(state: dict, items: list[dict], now: float,
                  dropped: int = 0) -> list[str]:
    """One line per incident in ``items`` (each once, when it started,
    whether it recovered), then "…and N more". Shared by this summary and
    the quiet-hours one (quiet.py), times in the digest's zone (clock.py)."""
    esc = templates.esc
    open_keys = set(state.get("incidents") or {})
    lines = []
    entries = _incidents(items)
    for entry in entries[:SUMMARY_LINES]:
        emoji = templates.SEVERITY_EMOJI.get(entry["severity"], "•")
        name = entry["rule"] or entry["key"]
        if entry["target"]:
            name = f"{name} {entry['target']}"
        when = clock.at(entry["started"], now) if entry["started"] is not None else ""
        if entry["recovered"] is not None:
            when = f"{when} → ✅ {clock.at(entry['recovered'], now)}".strip(" →")
            if entry["started"] is None:
                when = f"✅ {clock.at(entry['recovered'], now)}"
        elif entry["key"] not in open_keys:
            when += " (over)"
        lines.append(f"{emoji} {esc(name)} · {when} — {esc(entry['message'] or '')}")
    more = len(entries) - SUMMARY_LINES + dropped
    if more > 0:
        lines.append(f"…and {more} more")
    return lines


def build(state: dict, now: float) -> dict:
    """The one event that delivers everything waiting.

    ``state["incidents"]`` (already reconciled this cycle) supplies the
    "now" line, second after the header: it is the line that matters most,
    and a message over budget is cut from the end.
    """
    box = state["outbox"]
    items = _to_tell(box)
    age = now - items[0]["queued_at"]
    # A negative age is a clock that went back (no RTC on a Pi): not fresh.
    if len(items) == 1 and not box.get("dropped") and 0 <= age < RESEND_AS_IS_S:
        return dict(items[0]["payload"])

    since = float(box.get("since", items[0]["queued_at"]))
    lines = [
        templates._b(f"📭 Not delivered from {clock.at(since, now)} to "
                     f"{clock.at(now, now)} ({_span(max(0.0, now - since))})")
        + " — this is what happened meanwhile:",
        now_line(state, now),
    ]
    lines += summary_lines(state, items, now, int(box.get("dropped", 0)))
    return {
        "type": "undelivered",
        "key": "undelivered",
        "severity": "info",
        "message": "\n".join(lines),
    }


def fallback(state: dict, now: float) -> dict:
    """A summary with no markup and no operator-written text.

    Sent instead of :func:`build` once the receiver has refused the summary
    ``FALLBACK_AFTER`` times while the network was up: the text itself may
    be what it refuses (a name that breaks the markup), and a summary that
    can never be sent would hold every later alert behind it. Only counts
    and rule names, which the code writes.
    """
    box = state["outbox"]
    since = float(box.get("since", now))
    waiting = len(_to_tell(box)) + int(box.get("dropped", 0))
    still_open = sorted({(r.get("rule") or "incident")
                         for r in (state.get("incidents") or {}).values()
                         if not r.get("transient")})
    now_part = ("open now: " + ", ".join(still_open)) if still_open else "nothing open now"
    return {
        "type": "undelivered",
        "key": "undelivered",
        "severity": "info",
        "message": (f"Smoking Pi: {waiting} notification(s) since "
                    f"{clock.at(since, now)} could not be delivered; "
                    f"{now_part}. Ask the assistant for list_alert_state "
                    "or the incident history for the details."),
    }


def flush(state: dict, now: float, send, reachable) -> bool | None:
    """Try to deliver what is waiting, if it is time. Never raises.

    ``send(event)`` and ``reachable() -> bool`` are the notifier's; passed
    in so this module needs no network. ``send`` returns True/False, or the
    notifier's outcome: "sent", "handed" (the gateway has it: never sent
    again) or "failed". Returns True when the outbox was emptied by a
    message, False when a try failed, None when nothing was sent.
    """
    if not due(state, now):
        return None
    box = state["outbox"]
    try:
        if not reachable():
            failed(state, now)
            log.info("Delivery still unreachable; %d notification(s) waiting, "
                     "next try in %ds", len(box["items"]),
                     int(box["next_try"] - now))
            return False
        # What another path already said (a quiet-hours summary that told
        # the same incident) is not said again here.
        box["items"] = [item for item in box["items"] if item.get("handed")
                        or not ledger.done(state, ledger.key_of(item["payload"]))]
        items = _to_tell(box)
        if not items:
            # Only what the gateway holds: it delivers that itself.
            log.info("Delivery is back; the gateway holds what was waiting")
            clear(state)
            return None
        refused = int(box.get("refused", 0))
        event = build(state, now) if refused < FALLBACK_AFTER else fallback(state, now)
        outcome = send(event)
        if outcome is True or outcome in (ledger.SENT, "handed"):
            log.info("Delivered %d waiting notification(s) as one %s",
                     len(items), event.get("type"))
            if event.get("type") == "undelivered":
                ledger.told(state, items, now)
            else:  # the one item, as it was
                ledger.record(state, ledger.key_of(event),
                              ledger.QUEUED if outcome == "handed" else ledger.SENT,
                              now)
            clear(state)
            return True
        # Reachable, and still refused: the channel or the message itself.
        box["refused"] = refused + 1
        if box["refused"] == FALLBACK_AFTER:
            log.error("The receiver refused the outbox summary %d times with "
                      "the network up; sending a plain fallback from now on",
                      FALLBACK_AFTER)
    except Exception:  # noqa: BLE001 - the outbox must never cost the loop
        log.exception("Outbox delivery failed")
    failed(state, now)
    return False
