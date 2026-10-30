"""common.patterns on synthetic episodes: what makes a recurring window,
what does not, and the days nobody heard (they count neither way). The
whole cycle, replayed over twelve evenings, is alerter/tests/test_patterns.py."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from common import patterns

CHI = ZoneInfo("America/Chicago")


def when(month: int, day: int, hhmm: str) -> float:
    hour, minute = hhmm.split(":")
    return datetime(2026, month, day, int(hour), int(minute), tzinfo=CHI).timestamp()


def _ep(i: int, day: int, lo="19:30", hi="23:00", cls="upstream", unmet=()):
    return {"id": i, "class": cls, "start": when(10, day, lo), "end": when(10, day, hi),
            "unmet": list(unmet), "microcut": False, "affected": 14, "reporting": 18,
            "loss_pct": 6.0, "confidence": "medium"}


def _heard(box, days, lo="00:00", hi="23:59"):
    for day in days:
        t = when(10, day, lo)
        while t <= when(10, day, hi):
            patterns.mark_heard(box, t + 60, CHI)
            t += 900


def test_two_days_of_seven_are_not_a_pattern():
    box = patterns.book({})
    _heard(box, range(1, 8))
    eps = [_ep(1, 2), _ep(2, 5)]
    assert patterns.update(box, eps, when(10, 7, "23:59"), CHI) == []
    assert box["items"] == []


def test_three_days_of_seven_are_one():
    box = patterns.book({})
    _heard(box, range(1, 8))
    eps = [_ep(1, 2), _ep(2, 4), _ep(3, 6, lo="19:35", hi="22:40")]
    [event] = patterns.update(box, eps, when(10, 7, "23:59"), CHI)
    pattern = event["pattern"]
    assert (event["event"], pattern["from"], pattern["to"]) == ("new", "19:30", "23:00")
    assert pattern["days_seen"] == 3 and pattern["days_observed"] == 6
    # Another class in the same hours is another fact.
    assert patterns.explaining(box, _ep(9, 7, cls="local_link"), CHI) is None
    assert patterns.explaining(box, _ep(9, 7, lo="20:00", hi="21:00"), CHI) == pattern["id"]
    # Mostly outside the band: news.
    assert patterns.explaining(box, _ep(9, 7, lo="14:00", hi="21:00"), CHI) is None


def test_the_monitors_own_episodes_and_non_material_ones_are_no_evidence():
    box = patterns.book({})
    _heard(box, range(1, 8))
    eps = [_ep(1, 2, cls="monitor"), _ep(2, 3, cls="monitor"), _ep(3, 4),
           _ep(4, 5, unmet=["breadth"]), _ep(5, 6, unmet=["novelty"])]
    assert patterns.update(box, eps, when(10, 7, "23:59"), CHI) == []


def test_a_pattern_does_not_end_on_days_nobody_heard():
    box = patterns.book({})
    _heard(box, [1, 2, 3])
    eps = [_ep(1, 1), _ep(2, 2), _ep(3, 3)]
    [new] = patterns.update(box, eps, when(10, 3, "23:59"), CHI)
    pattern = new["pattern"]
    # Four days deaf in the evening (heard only in the morning), then two
    # quiet evenings heard: not gone -- the blind ones count neither way.
    _heard(box, [4, 5, 6, 7], hi="12:00")
    _heard(box, [8, 9])
    assert patterns.update(box, eps, when(10, 9, "23:59"), CHI) == []
    assert pattern["state"] == "active"
    assert [pattern["days"][f"2026-10-0{d}"] for d in range(4, 10)] == [
        "blind", "blind", "blind", "blind", "clear", "clear"]
    assert patterns.absent_days(pattern) == 2
    # The third heard evening without it: gone.
    _heard(box, [10])
    [gone] = patterns.update(box, eps, when(10, 10, "23:59"), CHI)
    assert gone["event"] == "gone" and pattern["state"] == "gone"


def test_a_pattern_does_not_form_across_blind_days():
    """Seen on two evenings, then the monitor blind on the next five: the
    blind days are not a third evening, and nothing forms."""
    box = patterns.book({})
    _heard(box, [1, 2])
    _heard(box, [3, 4, 5, 6, 7], hi="12:00")
    eps = [_ep(1, 1), _ep(2, 2), _ep(3, 5, cls="monitor"), _ep(4, 6, cls="monitor")]
    assert patterns.update(box, eps, when(10, 7, "23:59"), CHI) == []


def test_a_band_past_midnight_belongs_to_the_day_it_starts():
    box = patterns.book({})
    _heard(box, range(1, 8))
    eps = [{**_ep(i, d, lo="22:30", hi="23:59"), "end": when(10, d + 1, "01:00")}
           for i, d in ((1, 1), (2, 2), (3, 3))]
    [event] = patterns.update(box, eps, when(10, 4, "12:00"), CHI)
    pattern = event["pattern"]
    assert (pattern["from"], pattern["to"], pattern["part"]) == ("22:30", "01:00", "night")
    assert pattern["days_seen"] == 3
    late = {**_ep(9, 5, lo="00:00", hi="00:40")}
    assert patterns.match_day(pattern, late["start"], late["end"], CHI) == date(2026, 10, 4)


def test_the_book_is_bounded():
    box = patterns.book({})
    for day in range(1, 31):
        patterns.mark_heard(box, when(10, day, "12:00"), CHI)
    assert len(box["heard"]) == patterns.HEARD_DAYS
