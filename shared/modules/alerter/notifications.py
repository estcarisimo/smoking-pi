"""The alerter's side of the notification ledger: writing it.

Not ledger.py: that is the per-key record of what was handed to a
transport, which keeps a notification from being said twice; this is the
30-day record of what was said, for people and assistants to read.

One entry per notification decision (common/notification_ledger.py says
what a line holds and how it is read): an alert or a recovery, sent, queued
in the outbox or held for quiet hours; the summary the outbox or quiet hours
later sent in their place; the weekly summary; a report. The hooks are where a
message is handed to a transport and where its outcome is known
(main._deliver, outbox.flush, quiet.release, weekly.check,
reports_watcher.check); none of them changes what is sent or when, and
none of them can cost a notification: every function here logs and moves
on.

Writes are appends of one whole line; a line torn by a power cut is skipped
by every reader and dropped at the next compaction, which rewrites the file
(tmp file + fsync + os.replace) with the last ``RETAIN_DAYS`` days and at
most ``MAX_ENTRIES`` entries, at most once an hour or when the file passes
``MAX_BYTES``.

``python notifications.py show [--days N] [--kind K] [--json]`` prints it: what
``smoking-pi alerts history`` runs in the container.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime

import clock
import notifier
import state
import templates
from common import notification_ledger

log = logging.getLogger("alerter.notifications")

RETAIN_DAYS = notification_ledger.RETAIN_DAYS
# Thirty days of a bad month is a few hundred entries; this is the ceiling
# for a month nobody would want, not a size to plan for.
MAX_ENTRIES = 5000
MAX_BYTES = 8 * 1024 * 1024
COMPACT_EVERY_S = 3600
# The text is the message as sent, already within Telegram's 4096; the cap
# is for a transport with no limit of its own (a webhook, a report).
MAX_TEXT = 8192

_last_compact: float | None = None


def ledger_file() -> str:
    return notification_ledger.path(state.state_file())


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def kind_of(payload: dict) -> str:
    """The ledger's name for what this payload is."""
    kind = payload.get("type") or "alert"
    rule = payload.get("rule")
    if kind == "held":
        return "quiet_bundle"
    if kind == "undelivered":
        return "outbox_summary"
    if kind == "alert" and rule == "watchdog":
        return "watchdog"
    # Imported here: quiet imports outbox, which writes here.
    import quiet

    if kind == "alert" and rule in quiet.MONITOR_RULES:
        return "monitor"
    return kind if kind in notification_ledger.KINDS else "alert"


def _targets(payload: dict) -> list[str]:
    """The targets the message names: a digest's are the lossy ones, not
    every target it summed up."""
    named = []
    listed = payload.get("lossy") if payload.get("type") == "digest" \
        else payload.get("targets")
    for value in [payload.get("target"), *(listed or [])]:
        if isinstance(value, dict):
            value = value.get("target") or value.get("name")
        if value and isinstance(value, str) and value not in named:
            named.append(value)
    return named


def _diagnosis(payload: dict) -> dict | None:
    found = payload.get("diagnosis")
    if not isinstance(found, dict):
        return None
    return {"class": found.get("class"), "cause": found.get("summary"),
            "detail": found.get("detail"), "confidence": found.get("confidence")}


def _render(payload: dict) -> str:
    """The text a payload that was not handed to a transport would read as."""
    try:
        return templates.format_message(payload, templates.TG_TEXT_LIMIT)
    except Exception:  # noqa: BLE001 - the record must never cost the alert
        log.warning("Ledger: could not render %s", payload.get("key"), exc_info=True)
        return str(payload.get("message") or "")


def _append(line: dict) -> bool:
    path = ledger_file()
    data = json.dumps(line, default=str, ensure_ascii=False) + "\n"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "ab+") as fh:
            # A torn last line (power cut mid-write) must not swallow this one.
            if fh.seek(0, os.SEEK_END) > 0:
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) != b"\n":
                    data = "\n" + data
            fh.write(data.encode("utf-8"))
            if fh.tell() == len(data.encode("utf-8")):
                # Just created: readable by the web admin, whatever the umask.
                os.chmod(path, 0o644)
    except OSError:
        log.warning("Ledger: could not write %s", path, exc_info=True)
        return False
    return True


def record(payload: dict, outcome: str, now: float | None = None,
           receipt: dict | None = None, entry_id: str | None = None) -> str:
    """Write one decision; returns its id ("" when it could not be written).

    ``outcome``: sent, handed (the gateway has it), queued (joined the
    outbox untried), held (quiet hours), failed (tried; waits in the
    outbox) or skipped-duplicate (already said: ledger.py).
    ``receipt`` is notifier.take_receipt() after a send: the text as handed
    to the transport, and its message id when it gave one. Without one the
    text is rendered as it would have been sent.
    """
    if now is None:
        now = time.time()
    entry_id = entry_id or new_id()
    try:
        receipt = receipt or {}
        text = receipt.get("text")
        if text is None:
            text = _render(payload)
        entry = {
            "id": entry_id,
            "decided_at": now,
            "kind": kind_of(payload),
            # The payload's own type (weekly, alert, recovery...).
            "type": payload.get("type") or "alert",
            "key": payload.get("key"),
            "rule": payload.get("rule"),
            "severity": payload.get("severity"),
            "targets": _targets(payload),
            "text": str(text)[:MAX_TEXT],
            "markup": templates.markup(),
            "outcome": outcome,
            "transport": receipt.get("mode") or notifier.notify_mode(),
            "message_id": receipt.get("message_id"),
            # Handed to the gateway counts as delivered: it holds it and
            # delivers it from its own queue (ledger.py).
            "delivered_at": now if outcome in ("sent", "handed") else None,
            "diagnosis": _diagnosis(payload),
        }
        if payload.get("items"):
            entry["carries"] = carried_ids(payload["items"])
        _append(entry)
        maybe_compact(now)
    except Exception:  # noqa: BLE001 - the record must never cost the alert
        log.exception("Ledger: could not record %s", payload.get("key"))
        return ""
    return entry_id


def update(entry_id: str | None, **changes) -> None:
    """Change an entry after the fact (its outcome, when it was delivered,
    which summary carried it). Never raises."""
    if not entry_id or not changes:
        return
    _append({"id": entry_id, "update": changes})


def carried_ids(items: list[dict]) -> list[str]:
    """The ledger ids of what an outbox or quiet-hours box holds, a held
    bundle's own items included."""
    ids = []
    for item in items:
        payload = item.get("payload") or {}
        if payload.get("notification_id"):
            ids.append(payload["notification_id"])
        if payload.get("items"):
            ids += carried_ids(payload["items"])
    return ids


def delivered_in(ids: list[str], summary_id: str, now: float) -> None:
    """What a delivered summary carried: each entry says which one, and
    when it went."""
    for entry_id in ids:
        update(entry_id, delivered_in=summary_id, delivered_at=now)


def maybe_compact(now: float | None = None, force: bool = False) -> None:
    global _last_compact
    if now is None:
        now = time.time()
    path = ledger_file()
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if not force and size < MAX_BYTES and _last_compact is not None \
            and 0 <= now - _last_compact < COMPACT_EVERY_S:
        return
    _last_compact = now
    compact(now)


def compact(now: float | None = None) -> int:
    """Rewrite the file: updates folded in, torn lines and entries older
    than RETAIN_DAYS dropped, at most MAX_ENTRIES kept. Returns how many
    entries remain (-1 on failure). Never raises."""
    if now is None:
        now = time.time()
    path = ledger_file()
    try:
        entries, skipped = notification_ledger.read(path)
        kept = notification_ledger.select(entries, RETAIN_DAYS, now=now)[-MAX_ENTRIES:]
        lines = [json.dumps(e, default=str, ensure_ascii=False) + "\n" for e in kept]
        # And by size: the oldest go until it is well under MAX_BYTES, so
        # a file of big entries is not rewritten on every record.
        size = sum(len(line.encode("utf-8")) for line in lines)
        while lines and size > MAX_BYTES * 3 // 4:
            size -= len(lines.pop(0).encode("utf-8"))
        kept = kept[len(kept) - len(lines):]
        directory = os.path.dirname(path) or "."
        fd, tmp_path = tempfile.mkstemp(prefix=".ledger.", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
                fh.flush()
                os.fsync(fh.fileno())
            # mkstemp makes it 0600; the web admin reads it as another user.
            os.chmod(tmp_path, 0o644)
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception:  # noqa: BLE001 - a compaction must never cost the loop
        log.warning("Ledger: could not compact %s", path, exc_info=True)
        return -1
    if skipped or len(kept) < len(entries):
        log.info("Ledger compacted: %d entries kept, %d dropped, %d unreadable "
                 "line(s)", len(kept), len(entries) - len(kept), skipped)
    return len(kept)


# --- show: what `smoking-pi alerts history` prints ----------------------------


def _zone():
    # The one zone every time the alerter prints is in (clock.py).
    return clock.zone()


def show(days: float, kind: str | None, as_json: bool, out=None,
         now: float | None = None) -> int:
    out = out or sys.stdout
    try:
        entries, skipped = notification_ledger.read(ledger_file())
    except OSError:
        print("The notification ledger cannot be read; see the alerter's log.",
              file=sys.stderr)
        return 1
    chosen = notification_ledger.select(entries, days, kind, now)
    if as_json:
        json.dump(chosen, out, default=str, ensure_ascii=False)
        out.write("\n")
        return 0
    if not chosen:
        since = (f" (the record begins {_clock(entries[0]['decided_at'])})"
                 if entries else " (nothing is recorded yet)")
        print(f"No notifications in the last {days:g} day(s){since}.", file=out)
        return 0
    for entry in chosen:
        outcome = str(entry.get("outcome") or "?")
        if entry.get("delivered_in") and entry.get("delivered_at"):
            outcome += "→sent"  # later, inside a summary
        print(f"{_clock(entry.get('decided_at'))}  {entry.get('kind', '?'):<14} "
              f"{outcome:<12} {notification_ledger.headline(entry)}", file=out)
    if skipped:
        print(f"({skipped} unreadable line(s) skipped)", file=sys.stderr)
    return 0


def _clock(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts), tz=_zone()).strftime("%Y-%m-%d %H:%M %Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return "?"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="notifications.py",
                                     description="The notification ledger")
    sub = parser.add_subparsers(dest="command", required=True)
    shown = sub.add_parser("show", help="print the ledger")
    shown.add_argument("--days", type=float, default=7)
    shown.add_argument("--kind", choices=notification_ledger.KINDS)
    shown.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not 0 < args.days <= notification_ledger.MAX_DAYS:
        parser.error(f"--days must be more than 0 and at most "
                     f"{notification_ledger.MAX_DAYS}")
    return show(args.days, args.kind, args.json)


if __name__ == "__main__":
    sys.exit(main())
