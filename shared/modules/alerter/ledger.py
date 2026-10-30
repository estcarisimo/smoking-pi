"""What has been handed to a transport, so nothing is said twice.

October 2026 on the reference Pi: six identical copies of one microcut
alert in one second (10/09), and twice an outage arriving as a pair of
identical messages one minute apart, minutes after the outbox summary that
had already told it (10/18, 10/28). Each was one decision sent more than
once: the OpenClaw gateway keeps every request it answered in its own
durable queue and replays them, and the alerter sent the same message again
whenever an answer read as a failure.

So every notification has an idempotency key -- the incident (its key and
when it was first seen), and the kind of message: the first alert, the
n-th re-notification of a still-open incident (``update<n>``), or the
recovery -- and ``state["delivered"]`` records, per key, what happened and
when:

- ``sent``: delivered;
- ``queued``: the gateway answered, or may have received it -- it holds it
  in its own retry queue and delivers it itself;
- ``told``: an outbox or quiet-hours summary said it;
- ``held``: waiting for the end of quiet hours;
- ``outbox``: waiting in the outbox, not tried;
- ``failed``: tried, nobody received it; waiting in the outbox.

A key that is ``sent``, ``queued`` or ``told`` is never handed to a
transport again, whichever path tries it (a direct send, an outbox resend,
the end of quiet hours). Kept KEEP_S, in the state file, so a restart
forgets nothing.
"""

from __future__ import annotations

import logging

log = logging.getLogger("alerter.ledger")

KEEP_S = 7 * 24 * 3600
# A second bound, by count: a flapping week must not grow the state file.
MAX_KEYS = 5000
SENT, QUEUED, TOLD, HELD, OUTBOX, FAILED = (
    "sent", "queued", "told", "held", "outbox", "failed")
FINAL = frozenset({SENT, QUEUED, TOLD})
# Notifications about an incident. Summaries, digests and reports are not:
# they are built fresh each time and have no incident to repeat.
_INCIDENT_TYPES = frozenset({"alert", "recovery"})


def key_of(payload: dict) -> str | None:
    """The notification's idempotency key, or None when it has none."""
    kind = payload.get("type") or "alert"
    key = payload.get("key")
    if kind not in _INCIDENT_TYPES or not key:
        return None
    snapshot = payload.get("state")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    # The incident, not just the rule's key: uplink_down next week is
    # another incident with the same key.
    try:
        incident = f"{key}@{int(float(snapshot['first_seen']))}"
    except (KeyError, TypeError, ValueError):
        incident = str(key)
    if kind == "recovery":
        return f"{incident}|recovery"
    try:
        count = int(snapshot.get("notified_count") or 1)
    except (TypeError, ValueError):
        count = 1
    return f"{incident}|{'alert' if count <= 1 else f'update{count}'}"


def family(idem: str) -> str:
    """``incident|alert`` or ``incident|recovery``: an update is an alert."""
    incident, _, kind = idem.rpartition("|")
    return f"{incident}|{'recovery' if kind == 'recovery' else 'alert'}"


def outcome(state: dict, idem: str | None) -> str | None:
    book = state.get("delivered")
    if idem is None or not isinstance(book, dict):
        return None
    entry = book.get(idem)
    return entry.get("outcome") if isinstance(entry, dict) else None


def done(state: dict, idem: str | None) -> bool:
    """Whether this notification has already reached a transport for good."""
    return outcome(state, idem) in FINAL


def record(state: dict, idem: str | None, result: str, now: float) -> None:
    """Note what happened to ``idem``. A final outcome is kept as it is: a
    summary that tells what the gateway already holds stays ``queued``."""
    if idem is None or done(state, idem):
        return
    book = state.setdefault("delivered", {})
    book[idem] = {"outcome": result, "at": now}
    prune(state, now)


def prune(state: dict, now: float) -> None:
    book = state.get("delivered")
    if not isinstance(book, dict):
        state.pop("delivered", None)
        return
    for idem in [k for k, v in book.items()
                 if not isinstance(v, dict) or now - float(v.get("at", 0)) > KEEP_S]:
        del book[idem]
    overflow = len(book) - MAX_KEYS
    if overflow > 0:
        for idem in sorted(book, key=lambda k: float(book[k].get("at", 0)))[:overflow]:
            del book[idem]


def flatten(items: list[dict]) -> list[dict]:
    """Outbox/held items, with an undelivered quiet-hours summary replaced
    by the items it carries."""
    flat = []
    for item in items:
        payload = item.get("payload") or {}
        if payload.get("type") == "held" and payload.get("items"):
            flat += flatten(payload["items"])
        else:
            flat.append(item)
    return flat


def told(state: dict, items: list[dict], now: float) -> None:
    """A summary carrying ``items`` was delivered: each one is told, and so
    is every notification still waiting -- held for quiet hours, or in the
    outbox -- about the same incident with the same kind. The summary said
    it; the waiting copy must not say it again later. A recovery is another
    kind: an incident told only in a summary still recovers, once."""
    families = set()
    for item in flatten(items):
        idem = key_of(item.get("payload") or {})
        if idem is not None:
            record(state, idem, TOLD, now)
            families.add(family(idem))
    if not families:
        return
    for box_name in ("held", "outbox"):
        box = state.get(box_name)
        if not isinstance(box, dict) or not box.get("items"):
            continue
        kept = []
        for item in box["items"]:
            idem = key_of(item.get("payload") or {})
            if idem is not None and family(idem) in families:
                record(state, idem, TOLD, now)
                log.info("%s told by a summary; dropped from %s", idem, box_name)
                continue
            kept.append(item)
        box["items"] = kept
        if not kept:
            state.pop(box_name, None)
