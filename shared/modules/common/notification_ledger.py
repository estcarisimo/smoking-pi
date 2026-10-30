"""The notification ledger: what the alerter told the user, and how it went.

Before it, the project could not say what it had told anyone. The alerter's
``history`` in ``state.json`` kept 48 hours of bare keys, its log was wiped by
every upgrade, and the October 2026 audit of 72 Telegram messages had to be
rebuilt from the OpenClaw gateway's own message cache.

So every notification the alerter decides on is one line of
``ledger.jsonl``, next to ``state.json`` in the alerter's state volume, kept
30 days: what it was (``kind``), the text as handed to the transport, the
targets it named, and what became of it (``outcome``). The alerter writes it
(alerter/notifications.py); this module only reads it, for the MCP server's
``get_notification_history``, the web admin's chat (which mounts the volume
read-only, as the MCP server does) and ``smoking-pi alerts history``.

The file is append-only between compactions. A line is either an entry
(``{"id": ..., "kind": ..., ...}``) or a later change to one
(``{"id": ..., "update": {...}}``: the outcome once it is known, the
summary that finally carried it); reading folds the changes into their
entries. A line that does not parse (a write torn by a power cut) is
skipped, never fatal.
"""

from __future__ import annotations

import html
import json
import os
import re
import time
from datetime import datetime, timezone

FILENAME = "ledger.jsonl"
DEFAULT_STATE_FILE = "/var/lib/alerter/state.json"
RETAIN_DAYS = 30
# What a reader may ask for: the whole ledger, never more.
MAX_DAYS = RETAIN_DAYS
# One answer's worth; the newest first when there are more.
MAX_ENTRIES_ANSWER = 200

# update: a follow-up on an open episode (alerter/policy.py); episode: an
# episode that closed with nothing pushed, recorded with why (outcome
# ``recorded``). superseded is named for a notification nothing sends.
KINDS = ("alert", "recovery", "superseded", "update", "outbox_summary",
         "quiet_bundle", "weekly", "digest", "watchdog", "monitor", "report",
         "episode")
# handed: the OpenClaw gateway has it and delivers it from its own queue;
# skipped-duplicate: already said (alerter/ledger.py), not handed again;
# recorded: decided not to push (the push policy), kept for the record.
OUTCOMES = ("sent", "handed", "queued", "held", "failed", "skipped-duplicate",
            "recorded")


def path(state_file: str | None = None) -> str:
    """The ledger next to the alerter's state file."""
    state_file = state_file or os.environ.get("ALERT_STATE_FILE") or DEFAULT_STATE_FILE
    return os.path.join(os.path.dirname(state_file) or ".", FILENAME)


def parse(lines) -> tuple[list[dict], int]:
    """(entries in the order written, with their updates folded in;
    the number of lines that did not parse)."""
    entries: dict[str, dict] = {}
    skipped = 0
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            skipped += 1
            continue
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            skipped += 1
            continue
        change = row.get("update")
        if change is not None:
            entry = entries.get(row["id"])
            if entry is not None and isinstance(change, dict):
                entry.update({k: v for k, v in change.items() if k != "id"})
            continue
        entries[row["id"]] = row
    return list(entries.values()), skipped


def read(ledger_file: str | None = None) -> tuple[list[dict], int]:
    """Every entry in the file, oldest first; ([], 0) when there is none."""
    try:
        with open(ledger_file or path(), encoding="utf-8", errors="replace") as fh:
            entries, skipped = parse(fh)
    except FileNotFoundError:
        return [], 0
    entries.sort(key=lambda e: float(e.get("decided_at") or 0))
    return entries, skipped


def select(entries: list[dict], days: float, kind: str | None = None,
           now: float | None = None) -> list[dict]:
    if now is None:
        now = time.time()
    cutoff = now - float(days) * 86400
    return [e for e in entries
            if float(e.get("decided_at") or 0) >= cutoff
            and (not kind or e.get("kind") == kind)]


def _iso(ts) -> str | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(
            timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def validate(days, kind) -> tuple[int, str | None, str | None]:
    """(days, kind, error): what both assistants' tools check first."""
    try:
        days = int(days if days is not None else 7)
    except (TypeError, ValueError):
        return 0, None, f"Invalid days value {days!r}: must be an integer."
    if not 1 <= days <= MAX_DAYS:
        return days, None, f"days must be between 1 and {MAX_DAYS} (got {days})."
    kind = (str(kind).strip() or None) if kind is not None else None
    if kind is not None and kind not in KINDS:
        return days, None, "kind must be one of: " + ", ".join(KINDS) + "."
    return days, kind, None


def answer(days: int = 7, kind: str | None = None,
           ledger_file: str | None = None, now: float | None = None) -> dict:
    """What ``get_notification_history`` returns, newest first. Validation
    of ``days`` and ``kind`` is the caller's; raises OSError when the file
    exists and cannot be read."""
    entries, skipped = read(ledger_file)
    chosen = select(entries, days, kind, now)
    chosen.reverse()
    out = []
    for entry in chosen[:MAX_ENTRIES_ANSWER]:
        row = dict(entry)
        row["decided"] = _iso(entry.get("decided_at"))
        row["delivered"] = _iso(entry.get("delivered_at"))
        row.pop("decided_at", None)
        row.pop("delivered_at", None)
        out.append(row)
    counts: dict[str, int] = {}
    for entry in chosen:
        name = f"{entry.get('kind')}/{entry.get('outcome')}"
        counts[name] = counts.get(name, 0) + 1
    return {
        "days": days,
        "kind": kind,
        "entries": out,
        "total": len(chosen),
        "truncated": len(chosen) > MAX_ENTRIES_ANSWER,
        "counts": counts,
        # When the record begins: older than this, nothing was recorded --
        # not "nothing was sent". None means nothing is recorded yet.
        "recorded_from": _iso(entries[0].get("decided_at")) if entries else None,
        "unreadable_lines": skipped,
    }


def headline(entry: dict, width: int = 100) -> str:
    """The first line of the text, without markup: what a list shows."""
    text = str(entry.get("text") or "")
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    first = html.unescape(re.sub(r"<[^>]+>", "", first)).strip()
    if not first:
        first = " ".join(str(entry.get(k)) for k in ("rule", "key") if entry.get(k))
    return first if len(first) <= width else first[: width - 1] + "…"
