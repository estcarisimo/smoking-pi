"""The reader's zone and calendar days, for every container that prints a time.

The alerter's messages, its weekly summary and the MCP tools that return
the same facts must agree on one clock: ``DIGEST_TZ`` (the zone
``smoking-pi alerts`` sets), then ``TZ``, then UTC. A week is seven
calendar days in that zone -- 167 or 169 hours across a DST change, never
``7 * 86400`` seconds.

Moved here from the alerter's ``schedule.py`` when the MCP server needed the
weekly summary's week (``get_weekly_summary``): containers cannot import
across each other, and a second copy of the DST handling would drift.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger("common.localtime")

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def resolve_zone(tzname: str | None) -> ZoneInfo:
    """Timezone by name; UTC (with a warning) for an unknown one."""
    if not tzname or not str(tzname).strip():
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(str(tzname).strip())
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        log.warning("Unknown timezone %r; using UTC", tzname)
        return ZoneInfo("UTC")


def digest_zone() -> ZoneInfo:
    """The messages' zone: DIGEST_TZ, then TZ, then UTC."""
    return resolve_zone((os.environ.get("DIGEST_TZ") or "").strip()
                        or (os.environ.get("TZ") or "").strip())


def parse_weekday(value: str | None) -> int | None:
    """``mon``..``sun`` (or the full name, any case) as 0..6, else None."""
    text = str(value or "").strip().lower()[:3]
    return WEEKDAYS.index(text) if text in WEEKDAYS else None


def slot_on(day: datetime, hour: int, minute: int, zone: ZoneInfo) -> datetime:
    """The instant ``hour:minute`` on ``day``'s calendar date in ``zone``.

    Spring-forward can make the wall time nonexistent (02:30 where 02:00 jumps
    to 03:00) and autumn can make it ambiguous (01:30 happens twice). ``fold=0``
    picks the first of an ambiguous pair deterministically; a nonexistent time
    is normalized by the round-trip through UTC below, which lands it just
    after the gap. Either way the slot is stable across ticks, which is the
    property the alerter's idempotence depends on.
    """
    naive = datetime(day.year, day.month, day.day, hour, minute, fold=0)
    local = naive.replace(tzinfo=zone)
    # Round-tripping through UTC normalizes a nonexistent wall time onto a real
    # instant, so two ticks either side of the gap agree on the slot.
    return datetime.fromtimestamp(local.timestamp(), tz=zone)


def midnight_before(slot: datetime, zone: ZoneInfo, days_back: int = 0) -> datetime:
    """00:00 of ``slot``'s calendar day in ``zone``, ``days_back`` calendar
    days earlier: a week is seven calendar days, which is 167 or 169 hours
    across a DST change, never ``slot - 7 * 86400``."""
    local = slot.astimezone(zone)
    day = (local - timedelta(days=days_back)).date()
    return slot_on(datetime(day.year, day.month, day.day), 0, 0, zone)
