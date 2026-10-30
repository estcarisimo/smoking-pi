"""One zone for every time the alerter prints.

October 2026's Telegram chat read three clocks at once: outage alerts and the
"while offline" summary in UTC, quiet-hours bundles in America/Chicago, and
the digest in the container's own zone with a weekday, so an outage at
20:40 on a Wednesday in Chicago came out as "Thu 01:40" in the digest.

Every time in a message now comes from :func:`at`, in the digest's zone: the
one ``smoking-pi alerts --digest`` sets (``DIGEST_TZ``), resolved exactly as
the digest's schedule and quiet hours resolve it (then ``TZ``, then UTC), so
a printed time and the hour a message fires at never disagree. A time today
is ``20:40``; any other day carries its date, ``28 Oct 20:40``. When
``DIGEST_TZ`` is not the zone in effect (unset or unknown), the zone's
abbreviation follows, since the reader never chose it.

Only what is printed changes: state keeps UTC ISO timestamps and epochs.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

import schedule


@lru_cache(maxsize=8)
def _resolve(digest_tz: str, tz: str) -> tuple[ZoneInfo, bool]:
    """The zone, and whether it is the one DIGEST_TZ names. Cached by the
    settings' values, so an unknown name is logged once, not every cycle."""
    zone = schedule.resolve_zone(digest_tz or tz)
    chosen = bool(digest_tz.strip()) and zone.key == digest_tz.strip()
    return zone, chosen


def _settings() -> tuple[str, str]:
    return os.environ.get("DIGEST_TZ") or "", os.environ.get("TZ") or ""


def zone() -> ZoneInfo:
    """The digest's zone: DIGEST_TZ, then TZ, then UTC."""
    return _resolve(*_settings())[0]


def at(ts: float | datetime, now: float | None = None, weekday: bool = False) -> str:
    """``ts`` (epoch seconds, or an aware datetime) as the reader's clock.

    ``20:40`` when it falls on ``now``'s day in the zone, ``28 Oct 20:40``
    (``Wed 28 Oct 20:40`` with ``weekday``) otherwise; `` CDT`` and the like
    appended when the zone is a fallback rather than DIGEST_TZ.
    """
    tz, chosen = _resolve(*_settings())
    when = (ts.astimezone(tz) if isinstance(ts, datetime)
            else datetime.fromtimestamp(float(ts), tz=tz))
    today = datetime.fromtimestamp(
        time.time() if now is None else float(now), tz=tz).date()
    text = f"{when:%H:%M}"
    if when.date() != today:
        text = f"{when.day} {when:%b} {text}"
        if weekday:
            text = f"{when:%a} {text}"
    return text if chosen else f"{text} {when.strftime('%Z')}"
