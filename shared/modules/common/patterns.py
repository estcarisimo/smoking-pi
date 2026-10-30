"""Recurring windows: loss that comes back at the same hours, as one fact.

From 2026-08-30 to 2026-10-06 most destinations on the reference Pi lost
2-8 % of their pings from 19:30 to 23:00 America/Chicago, every evening:
the provider at peak hour, the most useful thing the monitor could have
told its owner. No message named it. Before the episodes (PR #436) no rule
reached it at all; after, each evening would be its own alert, the 38th as
loud as the first. A recurring window is a different fact from any of its
evenings, and is said differently (decision of 2026-10-30, front 8):

- **announced once when it appears**: on the RECURRING_MIN_DAYS-th day out
  of the last WINDOW_DAYS with a material episode of the same class in the
  same hours (``new``);
- **counted, not pushed, while it lasts**: an episode inside an active
  pattern's hours is not news (``definitions.materiality``'s novelty), so
  it is recorded as part of the pattern, and the weekly summary counts the
  days ("17 evenings of 21");
- **announced once when it disappears**: after GONE_DAYS observed days in a
  row without it (``gone``).

**The hours**, a pattern's band: the episode's loss span, in the messages'
zone (DIGEST_TZ), widened to the half hour (BAND_ALIGN_MIN). An episode is
in a band when at least BAND_OVERLAP of its span falls inside it, on its
own day or (a band past midnight) the day before. A band may run past
midnight; the day it belongs to is the one it starts on.

**Only what the monitor could hear counts, either way.** Each cycle the
alerter marks the half hour it measured (:func:`mark_heard`); a day whose
band was not heard for at least half its length (a deaf radio, the Pi off,
the database unreadable, the record not yet begun) is ``blind`` for that
pattern: neither a day seen nor a day without it. A pattern cannot form
from, nor end because of, days nobody measured -- and the monitor's own
episodes (class ``monitor``) are never evidence.

The inference's shared degradation periods (profile ``inference``, off by
default) are supporting evidence only: counted on the pattern when the
alerter could read them, never needed for one to form.

Shared because the alerter (which keeps it in ``state["patterns"]``), the
weekly summary and the MCP server's ``list_alert_state`` must read the
same thing. Pure: the zone and the clock are the caller's.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import definitions

# The episode classes a pattern can be made of: the network's, broad ones
# (the gate already asks for breadth), never the monitor's own.
CLASSES = ("upstream", "local_link", "destination")
# Days looked back for a band to recur, and how many of them it needs.
WINDOW_DAYS = 7
MIN_DAYS = definitions.RECURRING_MIN_DAYS
# Observed days in a row without it before a pattern is gone.
GONE_DAYS = 3
BAND_ALIGN_MIN = 30
BAND_OVERLAP = 0.5
# A band heard for less than this share of its half hours is not observed.
HEARD_SHARE = 0.5
SLOTS = 24 * 60 // BAND_ALIGN_MIN
# Bounds of state["patterns"].
HEARD_DAYS = 14
DAYS_KEPT = 90
EVIDENCE_MAX = 60
MAX_PATTERNS = 20
GONE_KEPT_DAYS = 30
# The hours a provider's network is busiest at home: the evening.
PEAK_FROM_MIN, PEAK_TO_MIN = 19 * 60, 23 * 60

CAUSE = {
    "upstream": "your provider",
    "local_link": "your line",
    "destination": "the destinations themselves",
}


def book(state: dict) -> dict:
    box = state.setdefault("patterns", {})
    box.setdefault("items", [])
    box.setdefault("heard", {})
    return box


# ---------------------------------------------------------------------------
# Wall clock
# ---------------------------------------------------------------------------

def _local(ts: float, tz: ZoneInfo) -> datetime:
    return datetime.fromtimestamp(float(ts), tz=tz)


def _minutes(when: datetime, day: date) -> float:
    """Minutes of wall clock from ``day``'s midnight to ``when``."""
    return ((when.date() - day).days * 1440 + when.hour * 60 + when.minute
            + when.second / 60.0)


def band_span(day: date, lo_min: int, hi_min: int, tz: ZoneInfo) -> tuple[float, float]:
    """The band [lo_min, hi_min) (minutes of wall clock, ``hi_min`` past
    1440 for a band past midnight) on ``day``, as epochs."""
    midnight = datetime(day.year, day.month, day.day)
    lo = (midnight + timedelta(minutes=lo_min)).replace(tzinfo=tz).timestamp()
    hi = (midnight + timedelta(minutes=hi_min)).replace(tzinfo=tz).timestamp()
    return lo, hi


def band_of(start: float, end: float, tz: ZoneInfo) -> tuple[date, int, int]:
    """The day and the band, aligned to BAND_ALIGN_MIN, of a span."""
    first = _local(start, tz)
    day = first.date()
    lo = int(_minutes(first, day) // BAND_ALIGN_MIN) * BAND_ALIGN_MIN
    hi = math.ceil(_minutes(_local(max(end, start), tz), day) / BAND_ALIGN_MIN) * BAND_ALIGN_MIN
    hi = min(max(hi, lo + BAND_ALIGN_MIN), lo + 1440)
    return day, lo, hi


def hhmm(minutes: int) -> str:
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


def part_of_day(lo_min: int) -> str:
    hour = (lo_min // 60) % 24
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"


def peak_hour(pattern: dict) -> bool:
    return pattern["from_min"] < PEAK_TO_MIN and PEAK_FROM_MIN < pattern["to_min"]


def match_day(pattern: dict, start: float, end: float, tz: ZoneInfo) -> date | None:
    """The day of ``pattern`` whose band holds at least BAND_OVERLAP of the
    span [start, end], or None."""
    end = max(float(end), float(start) + 1.0)
    span = end - float(start)
    first = _local(start, tz).date()
    for day in (first, first - timedelta(days=1)):
        lo, hi = band_span(day, pattern["from_min"], pattern["to_min"], tz)
        if max(0.0, min(hi, end) - max(lo, float(start))) >= BAND_OVERLAP * span:
            return day
    return None


# ---------------------------------------------------------------------------
# What the monitor heard
# ---------------------------------------------------------------------------

def mark_heard(box: dict, now: float, tz: ZoneInfo) -> None:
    """The half hour this cycle measured (the minute before ``now``) was
    heard. The alerter calls it on every cycle it could judge."""
    box.setdefault("since", float(now))
    when = _local(float(now) - 1, tz)
    key = when.date().isoformat()
    slots = box["heard"].setdefault(key, [0] * SLOTS)
    slots[(when.hour * 60 + when.minute) // BAND_ALIGN_MIN] += 1
    for old in sorted(box["heard"])[:-HEARD_DAYS]:
        del box["heard"][old]


def observed(box: dict, pattern: dict, day: date) -> bool:
    """Whether the monitor heard ``pattern``'s band on ``day`` for at least
    HEARD_SHARE of its half hours."""
    total = heard = 0
    for slot in range(pattern["from_min"] // BAND_ALIGN_MIN,
                      -(-pattern["to_min"] // BAND_ALIGN_MIN)):
        on = (day + timedelta(days=slot // SLOTS)).isoformat()
        total += 1
        heard += bool((box["heard"].get(on) or [0] * SLOTS)[slot % SLOTS])
    return total > 0 and heard >= HEARD_SHARE * total


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

def qualifies(ep: dict) -> bool:
    """Whether an episode (episodes.summary) is evidence of a pattern: of a
    network class, material in everything but novelty (an evening already
    inside the pattern is still that pattern), not a microcut."""
    return (ep.get("class") in CLASSES
            and set(ep.get("unmet") or ()) <= {"novelty"}
            and not ep.get("microcut")
            and ep.get("start") is not None and ep.get("end") is not None)


def pattern_id(lo_min: int, hi_min: int, cls: str) -> str:
    return f"{hhmm(lo_min).replace(':', '')}-{hhmm(hi_min).replace(':', '')}-{cls}"


def live(box: dict | None) -> list[dict]:
    """The patterns not gone."""
    return [p for p in (box or {}).get("items") or () if p.get("state") != "gone"]


def explaining(box: dict | None, ep: dict, tz: ZoneInfo) -> str | None:
    """The id of the live pattern ``ep`` (an episode's summary: class,
    start, end) falls in, or None: then it is not news."""
    if ep.get("class") not in CLASSES or ep.get("start") is None:
        return None
    for pattern in live(box):
        if (pattern["class"] == ep["class"]
                and match_day(pattern, ep["start"], ep["end"], tz) is not None):
            return pattern["id"]
    return None


def _median(values: list[float]) -> float:
    values = sorted(values)
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def _band(members: list[dict], tz: ZoneInfo) -> tuple[int, int]:
    """The members' typical band: the median start, the median end."""
    bands = [band_of(m["start"], m["end"], tz)[1:] for m in members]
    lo = int(_median([b[0] for b in bands]) // BAND_ALIGN_MIN) * BAND_ALIGN_MIN
    hi = math.ceil(_median([b[1] for b in bands]) / BAND_ALIGN_MIN) * BAND_ALIGN_MIN
    return lo, max(hi, lo + BAND_ALIGN_MIN)


def _seen(pattern: dict, cands: list[dict], tz: ZoneInfo) -> dict[date, list[dict]]:
    out: dict[date, list[dict]] = {}
    for ep in cands:
        if ep.get("class") != pattern["class"]:
            continue
        day = match_day(pattern, ep["start"], ep["end"], tz)
        if day is not None:
            out.setdefault(day, []).append(ep)
    return out


def _form(cands: list[dict], taken: set, today: date, tz: ZoneInfo,
          now: float) -> list[dict]:
    """New patterns among the episodes no pattern holds: a band, seeded by
    a closed episode, that RECURRING_MIN_DAYS days of the last WINDOW_DAYS
    share."""
    out = []
    first_day = today - timedelta(days=WINDOW_DAYS - 1)
    for seed in sorted(cands, key=lambda e: -float(e["start"])):
        if seed["id"] in taken or seed.get("open"):
            continue
        _, lo, hi = band_of(seed["start"], seed["end"], tz)
        trial = {"from_min": lo, "to_min": hi, "class": seed["class"]}
        days = {d: eps for d, eps in _seen(trial, [c for c in cands
                                                   if c["id"] not in taken], tz).items()
                if d >= first_day}
        if len(days) < MIN_DAYS:
            continue
        members = [e for eps in days.values() for e in eps]
        closed = [e for e in members if not e.get("open")] or members
        lo, hi = _band(closed, tz)
        pattern = {
            "id": pattern_id(lo, hi, seed["class"]),
            "from_min": lo, "to_min": hi,
            "from": hhmm(lo), "to": hhmm(hi), "tz": tz.key,
            "part": part_of_day(lo),
            "class": seed["class"],
            "cause": CAUSE[seed["class"]],
            "state": "new", "created_at": now, "created_day": today.isoformat(),
            "days": {}, "evidence": [],
        }
        taken.update(e["id"] for e in members)
        out.append(pattern)
    return out


def _refresh(box: dict, pattern: dict, cands: list[dict], today: date,
             now: float, tz: ZoneInfo, margin: float) -> list[dict]:
    """Advance one pattern's days and figures; the transition it makes."""
    seen = _seen(pattern, cands, tz)
    members = [e for eps in seen.values() for e in eps]
    days = pattern["days"]
    first = min([date.fromisoformat(d) for d in days] + list(seen) + [today])
    lookback = max(first, today - timedelta(days=WINDOW_DAYS + 1))
    day = lookback
    while day <= today:
        key = day.isoformat()
        _, hi = band_span(day, pattern["from_min"], pattern["to_min"], tz)
        if day in seen:
            days[key] = "seen"
        elif hi + margin <= now and days.get(key) != "seen":
            days[key] = "clear" if observed(box, pattern, day) else "blind"
        day += timedelta(days=1)
    for old in sorted(days)[:-DAYS_KEPT]:
        del days[old]
    known = set(pattern["evidence"])
    pattern["evidence"] += [e["id"] for e in sorted(members, key=lambda e: e["start"])
                            if e["id"] not in known and not e.get("open")]
    del pattern["evidence"][:-EVIDENCE_MAX]
    if members:
        last = max(members, key=lambda e: float(e["start"]))
        pattern["last"] = {"id": last["id"], "start": last["start"], "end": last["end"]}
        pattern["last_seen"] = max(float(pattern.get("last_seen") or 0),
                                   float(last["end"]))
        first_seen = min(float(e["start"]) for e in members)
        pattern["first_seen"] = min(float(pattern.get("first_seen") or first_seen),
                                    first_seen)
        closed = [e for e in members if not e.get("open")] or members
        reporting = [int(e.get("reporting") or 0) for e in closed]
        pattern["affected"] = round(_median([int(e.get("affected") or 0) for e in closed]))
        pattern["reporting"] = round(_median(reporting))
        losses = [float(e["loss_pct"]) for e in closed if e.get("loss_pct") is not None]
        if losses:
            pattern["loss_min"] = round(min(min(losses), pattern.get("loss_min", 1e9)), 1)
            pattern["loss_max"] = round(max(max(losses), pattern.get("loss_max", 0.0)), 1)
        confidences = [e.get("confidence") for e in closed if e.get("confidence")]
        if confidences:
            pattern["confidence"] = max(set(confidences), key=confidences.count)
    pattern["days_seen"] = sum(1 for v in days.values() if v == "seen")
    pattern["days_observed"] = sum(1 for v in days.values() if v in ("seen", "clear"))
    if pattern["state"] == "new" and today.isoformat() != pattern["created_day"]:
        pattern["state"] = "active"
    if pattern["state"] != "gone" and absent_days(pattern) >= GONE_DAYS:
        pattern["state"] = "gone"
        pattern["gone_at"] = now
        return [{"event": "gone", "pattern": pattern}]
    return []


def absent_days(pattern: dict) -> int:
    """Observed days in a row, newest first, without the pattern; the days
    nobody heard are skipped."""
    count = 0
    for key in sorted(pattern["days"], reverse=True):
        status = pattern["days"][key]
        if status == "seen":
            break
        if status == "clear":
            count += 1
    return count


def streak(pattern: dict) -> int:
    """Observed days in a row, newest first, with the pattern."""
    count = 0
    for key in sorted(pattern["days"], reverse=True):
        status = pattern["days"][key]
        if status == "clear":
            break
        if status == "seen":
            count += 1
    return count


def recent_counts(pattern: dict, today: date, days: int = WINDOW_DAYS) -> tuple[int, int]:
    """(seen, observed) over the last ``days`` days."""
    first = (today - timedelta(days=days - 1)).isoformat()
    values = [v for k, v in pattern["days"].items() if k >= first]
    return (sum(v == "seen" for v in values),
            sum(v in ("seen", "clear") for v in values))


def update(box: dict, episodes: list[dict], now: float, tz: ZoneInfo,
           margin: float = 900.0) -> list[dict]:
    """Fold the alerter's episodes (summaries; the open one with ``open``
    set) into the patterns. Returns the transitions, ``[{"event": "new" |
    "gone", "pattern": ...}]``. ``margin``: how long after a band ends its
    day is judged (an episode in it closes ALERT_RESOLVE_AFTER late)."""
    today = _local(now, tz).date()
    keep = now - (WINDOW_DAYS + 2) * 86400
    cands = [e for e in episodes if qualifies(e) and float(e["end"]) >= keep]
    events: list[dict] = []
    # What a pattern holds, or held before it was gone, seeds no new one:
    # a gone window re-forms only from evenings after it.
    taken: set = {i for p in box["items"] for i in p.get("evidence") or ()}
    for pattern in live(box):
        events += _refresh(box, pattern, cands, today, now, tz, margin)
        taken.update(e["id"] for eps in _seen(pattern, cands, tz).values()
                     for e in eps)
    for pattern in _form(cands, taken, today, tz, now):
        _refresh(box, pattern, cands, today, now, tz, margin)
        box["items"] = [p for p in box["items"] if p["id"] != pattern["id"]]
        box["items"].append(pattern)
        events.append({"event": "new", "pattern": pattern})
    gone_keep = now - GONE_KEPT_DAYS * 86400
    box["items"] = [p for p in box["items"]
                    if p.get("state") != "gone" or float(p.get("gone_at") or now) >= gone_keep
                    ][-MAX_PATTERNS:]
    return events


def view(pattern: dict, now: float, tz: ZoneInfo | None = None) -> dict:
    """A pattern as the assistant reads it (MCP ``list_alert_state``)."""
    tz = tz or ZoneInfo(pattern.get("tz") or "UTC")
    seen, heard = recent_counts(pattern, _local(now, tz).date())
    return {
        "id": pattern["id"],
        "state": pattern.get("state"),
        "hours": f"{pattern['from']}-{pattern['to']} {pattern.get('tz')}",
        "class": pattern.get("class"),
        "cause": pattern.get("cause"),
        "confidence": pattern.get("confidence"),
        "days_seen": pattern.get("days_seen", 0),
        "days_observed": pattern.get("days_observed", 0),
        "last_7_days": {"seen": seen, "observed": heard},
        "typical": {"affected": pattern.get("affected"),
                    "reporting": pattern.get("reporting"),
                    "loss_pct": [pattern.get("loss_min"), pattern.get("loss_max")]},
        "first_seen": pattern.get("first_seen"),
        "last_seen": pattern.get("last_seen"),
        "gone_at": pattern.get("gone_at"),
        "inference_days": pattern.get("inference_days"),
        "evidence": [f"episode:{i}" for i in pattern.get("evidence", [])[-10:]],
    }
