"""Wall-clock scheduling for a process that wakes on a sleep loop.

The alerter wakes every ALERT_INTERVAL seconds and has no idea what time it
is between ticks. The weekly summary wants a wall-clock instant ("Monday
08:00 local"),
and the gap between those two models is where double-sends live.

**The slot is the identity, not the send time.** Every tick resolves the most
recent scheduled instant at or before now -- the *slot* -- and the state file
records which slot was last fired. Firing is then idempotent: ten ticks in the
same minute all resolve the same slot, see it already recorded, and do
nothing. Persisting the moment we *sent* instead would re-fire on any clock
skew, on a container restart within the minute, or on an NTP step backwards.

**Being late is not a reason to send.** A Pi that was powered off for two days
comes back with 08:30 long past. Delivering it at 19:00 is misleading, and
delivering two of them is worse. Past ``max_lateness``, the slot is recorded
as fired *without sending* -- the schedule catches up silently rather than
flooding.

No new dependency: ``zoneinfo`` is stdlib and tzdata resolves inside the
alerter image (``TZ`` is already set on the service).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from common import localtime

log = logging.getLogger("alerter.schedule")

# Four hours. Long enough to cover a reboot, a slow start, or a laptop lid;
# short enough that a summary never arrives in a different part of the day
# than the one it describes.
DEFAULT_MAX_LATENESS_S = 4 * 3600

Reason = str  # "due" | "not_due" | "skipped_stale" | "disabled"


def parse_hhmm(value: str | None) -> tuple[int, int] | None:
    """Parse ``HH:MM`` into (hour, minute), or None when unusable.

    Returns None rather than raising: a typo in WEEKLY_AT must disable the
    weekly summary with a loud log, never crash the alert loop that shares
    this process.
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def resolve_zone(tzname: str | None) -> ZoneInfo:
    """Timezone by name, falling back to UTC with a warning.

    An unknown zone must not take the alert loop down with it, and silently
    using the container's local time would put a summary at an hour the
    operator never chose.
    """
    if not tzname or not str(tzname).strip():
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(str(tzname).strip())
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        log.warning(
            "Unknown timezone %r; falling back to UTC for scheduling", tzname
        )
        return ZoneInfo("UTC")


# The calendar-day math is common.localtime's: the MCP server's weekly
# summary reads the same week, so there is one copy of the DST handling.
_slot_on = localtime.slot_on
midnight_before = localtime.midnight_before


WEEKDAYS = localtime.WEEKDAYS
parse_weekday = localtime.parse_weekday


def previous_slot(now: datetime, hour: int, minute: int, zone: ZoneInfo,
                  weekday: int | None = None) -> datetime:
    """The most recent scheduled instant at or before ``now``: daily, or on
    ``weekday`` (0 = Monday) only."""
    local_now = now.astimezone(zone)
    day = local_now
    if weekday is not None:
        day = local_now - timedelta(days=(local_now.weekday() - weekday) % 7)
    candidate = _slot_on(day, hour, minute, zone)
    if candidate > local_now:
        back = 7 if weekday is not None else 1
        candidate = _slot_on(day - timedelta(days=back), hour, minute, zone)
    return candidate


def due_slot(
    last_fired_slot: float | None,
    now: float,
    at_hhmm: str | None,
    tzname: str | None,
    max_lateness_s: int | None = DEFAULT_MAX_LATENESS_S,
    weekday: int | None = None,
) -> tuple[float | None, Reason]:
    """Decide whether a scheduled job is due, and for which slot.

    Returns ``(slot_epoch, reason)``. Recording ``slot_epoch`` is what retires
    a slot, so **when** the caller records it is a real choice:

    - ``skipped_stale`` -- record it immediately. That is how a missed day is
      retired instead of firing late.
    - ``due`` -- record it only once the work has actually succeeded. A caller
      that records the slot before delivering has retired it, and no retry is
      possible for that day.

    ``reason`` is one of:

    - ``due``           -- send it, then record the slot
    - ``not_due``       -- this slot already fired
    - ``skipped_stale`` -- too late to be meaningful; record without sending
    - ``disabled``      -- unusable ``at_hhmm``

    ``weekday`` (0 = Monday): one slot a week, on that day, instead of one
    a day.
    """
    parsed = parse_hhmm(at_hhmm)
    if parsed is None:
        return None, "disabled"
    hour, minute = parsed

    zone = resolve_zone(tzname)
    now_dt = datetime.fromtimestamp(now, tz=zone)
    slot = previous_slot(now_dt, hour, minute, zone, weekday)
    slot_epoch = slot.timestamp()

    # >= not >: the same slot must never fire twice, and float round-tripping
    # through JSON can return a value a hair off the one written.
    if last_fired_slot is not None and float(last_fired_slot) >= slot_epoch:
        return slot_epoch, "not_due"

    if max_lateness_s is not None and (now - slot_epoch) > max_lateness_s:
        return slot_epoch, "skipped_stale"

    return slot_epoch, "due"
