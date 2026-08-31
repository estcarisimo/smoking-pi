"""Tests for wall-clock scheduling on a sleep loop.

The property under test is idempotence: a job that should fire once a day
must fire once a day regardless of how often it is asked, how the clock
moves, or how long the machine was off. Everything else here is a corollary.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import schedule

UTC = ZoneInfo("UTC")
LONDON = ZoneInfo("Europe/London")
CHICAGO = ZoneInfo("America/Chicago")


def _epoch(year, month, day, hour, minute, zone=UTC) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=zone).timestamp()


# ---------------------------------------------------------------------------
# Parsing: a typo disables the digest, it does not crash the alert loop
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["08:30", "00:00", "23:59", " 8:05 "])
def test_valid_times_parse(value):
    assert schedule.parse_hhmm(value) is not None


@pytest.mark.parametrize(
    "value", ["25:00", "08:60", "-1:00", "0830", "8", "", None, "eight", "08:30:00"]
)
def test_unusable_times_return_none_rather_than_raising(value):
    assert schedule.parse_hhmm(value) is None


def test_bad_time_disables_without_touching_the_slot():
    slot, reason = schedule.due_slot(None, _epoch(2026, 8, 30, 12, 0), "25:00", "UTC")
    assert reason == "disabled"
    assert slot is None


def test_unknown_timezone_falls_back_to_utc():
    assert schedule.resolve_zone("Mars/Olympus") == UTC
    assert schedule.resolve_zone(None) == UTC
    assert schedule.resolve_zone("America/Chicago") == CHICAGO


# ---------------------------------------------------------------------------
# Firing once, and only once
# ---------------------------------------------------------------------------


def test_fires_at_the_slot():
    slot, reason = schedule.due_slot(
        None, _epoch(2026, 8, 30, 8, 30), "08:30", "UTC"
    )
    assert reason == "due"
    assert slot == _epoch(2026, 8, 30, 8, 30)


def test_not_due_before_the_slot():
    """08:29 resolves to YESTERDAY's slot, which a fresh state has not fired."""
    slot, reason = schedule.due_slot(
        _epoch(2026, 8, 29, 8, 30), _epoch(2026, 8, 30, 8, 29), "08:30", "UTC"
    )
    assert reason == "not_due"
    assert slot == _epoch(2026, 8, 29, 8, 30)


def test_a_second_call_in_the_same_minute_does_not_refire():
    now = _epoch(2026, 8, 30, 8, 30)
    slot, reason = schedule.due_slot(None, now, "08:30", "UTC")
    assert reason == "due"
    # The caller records the slot, then the loop ticks again 20 seconds later.
    again, reason2 = schedule.due_slot(slot, now + 20, "08:30", "UTC")
    assert reason2 == "not_due"
    assert again == slot


def test_a_restart_three_minutes_later_does_not_refire():
    """The container dying and coming back must not re-deliver."""
    now = _epoch(2026, 8, 30, 8, 30)
    slot, _ = schedule.due_slot(None, now, "08:30", "UTC")
    _, reason = schedule.due_slot(slot, now + 180, "08:30", "UTC")
    assert reason == "not_due"


def test_persisting_the_send_time_instead_of_the_slot_double_sends():
    """Reintroduction guard for the core design decision.

    Recording `now` rather than the slot is the tempting simplification. This
    shows what it costs: a clock that steps backwards (NTP correction, a
    container starting with a bad RTC) re-fires the same slot.
    """
    now = _epoch(2026, 8, 30, 8, 31)
    slot, reason = schedule.due_slot(None, now, "08:30", "UTC")
    assert reason == "due"
    assert slot < now, "the slot precedes the send, which is the whole problem"

    # Correct: record the SLOT. Still not due even if the clock steps back
    # past the moment we sent.
    _, with_slot = schedule.due_slot(slot, now - 45, "08:30", "UTC")
    assert with_slot == "not_due"

    # Wrong: record a value earlier than the slot instant — which is what any
    # send-time-like quantity becomes after an NTP correction backwards, or on
    # a container whose RTC starts behind. It re-fires the same day.
    _, with_send_time = schedule.due_slot(slot - 1, now, "08:30", "UTC")
    assert with_send_time == "due", (
        "reintroduction guard: anything recorded before the slot instant "
        "re-fires it; only the slot itself is stable"
    )


def test_the_next_day_fires_again():
    slot, _ = schedule.due_slot(None, _epoch(2026, 8, 30, 8, 30), "08:30", "UTC")
    tomorrow, reason = schedule.due_slot(
        slot, _epoch(2026, 8, 31, 8, 30), "08:30", "UTC"
    )
    assert reason == "due"
    assert tomorrow == _epoch(2026, 8, 31, 8, 30)


# ---------------------------------------------------------------------------
# Lateness: a machine that was off must not flood
# ---------------------------------------------------------------------------


def test_a_ten_hour_gap_skips_rather_than_delivering_stale_news():
    slot, reason = schedule.due_slot(
        None, _epoch(2026, 8, 30, 18, 30), "08:30", "UTC", max_lateness_s=4 * 3600
    )
    assert reason == "skipped_stale"
    # The slot still comes back, because the caller must RECORD it -- that is
    # what stops it firing on the next tick a minute later.
    assert slot == _epoch(2026, 8, 30, 8, 30)


def test_a_skipped_slot_recorded_does_not_fire_afterwards():
    now = _epoch(2026, 8, 30, 18, 30)
    slot, reason = schedule.due_slot(None, now, "08:30", "UTC")
    assert reason == "skipped_stale"
    _, after = schedule.due_slot(slot, now + 60, "08:30", "UTC")
    assert after == "not_due"


def _replay(start: float, ticks: int, last=None, at="08:30", step=60):
    """Run the loop the way main.py does and count actual deliveries."""
    sent = []
    for i in range(ticks):
        slot, reason = schedule.due_slot(last, start + i * step, at, "UTC")
        if reason in ("due", "skipped_stale"):
            last = slot
        if reason == "due":
            sent.append(slot)
    return sent, last


def test_two_days_off_then_back_inside_the_budget_delivers_exactly_one():
    """Off from the 28th, back at noon: 08:30 is 3.5h ago, still meaningful.

    The point is *one*, not zero — two days of missed slots must not become
    two messages.
    """
    sent, _ = _replay(_epoch(2026, 8, 30, 12, 0), ticks=120)
    assert sent == [_epoch(2026, 8, 30, 8, 30)]


def test_back_outside_the_budget_delivers_none():
    """Back at 14:00: 08:30 is 5.5h ago, past the 4h budget."""
    sent, last = _replay(_epoch(2026, 8, 30, 14, 0), ticks=120)
    assert sent == []
    # The slot was still retired, so it cannot fire later either.
    assert last == _epoch(2026, 8, 30, 8, 30)


def test_a_week_of_ticks_delivers_exactly_one_per_day():
    """The whole point, end to end: 7 days of 5-minute ticks, 7 digests."""
    sent, _ = _replay(
        _epoch(2026, 8, 24, 0, 0), ticks=7 * 24 * 12, step=300
    )
    assert len(sent) == 7, f"expected 7 daily digests, got {len(sent)}"
    assert len(set(sent)) == 7, "the same slot fired more than once"


def test_within_the_lateness_budget_still_fires():
    slot, reason = schedule.due_slot(
        None, _epoch(2026, 8, 30, 11, 0), "08:30", "UTC", max_lateness_s=4 * 3600
    )
    assert reason == "due"
    assert slot == _epoch(2026, 8, 30, 8, 30)


# ---------------------------------------------------------------------------
# Daylight saving: the two days a year a naive implementation misfires
# ---------------------------------------------------------------------------


def test_spring_forward_nonexistent_wall_time_still_yields_one_stable_slot():
    """Europe/London 2026-03-29: 01:00 jumps to 02:00, so 01:30 never happens.

    The slot must still resolve, and must resolve to the SAME instant from
    either side of the gap -- otherwise two ticks disagree and it fires twice.
    """
    zone = LONDON
    before = datetime(2026, 3, 29, 0, 45, tzinfo=zone)
    after = datetime(2026, 3, 29, 3, 15, tzinfo=zone)
    a = schedule.previous_slot(after, 1, 30, zone)
    assert a is not None
    # From before the gap, the most recent 01:30 is the previous day's.
    b = schedule.previous_slot(before, 1, 30, zone)
    assert b < before
    # And asking twice after the gap agrees.
    assert schedule.previous_slot(after + timedelta(minutes=5), 1, 30, zone) == a


def test_autumn_ambiguous_wall_time_picks_one_instant_consistently():
    """Europe/London 2026-10-25: 01:30 occurs twice. Pick one, always the same."""
    zone = LONDON
    later = datetime(2026, 10, 25, 6, 0, tzinfo=zone)
    first = schedule.previous_slot(later, 1, 30, zone)
    second = schedule.previous_slot(later + timedelta(minutes=1), 1, 30, zone)
    assert first == second


def test_dst_day_does_not_double_fire_across_a_day_of_ticks():
    """Tick every 5 minutes through the London spring-forward day."""
    zone = "Europe/London"
    last = None
    fired = []
    start = datetime(2026, 3, 29, 0, 0, tzinfo=LONDON).timestamp()
    for step in range(0, 24 * 12):
        now = start + step * 300
        slot, reason = schedule.due_slot(last, now, "01:30", zone, 4 * 3600)
        if reason in ("due", "skipped_stale"):
            last = slot
        if reason == "due":
            fired.append(slot)
    assert len(fired) <= 1, f"fired {len(fired)} times on a DST day: {fired}"


def test_timezone_actually_shifts_the_slot():
    """08:30 Chicago is not 08:30 UTC; a wrong zone is a wrong hour."""
    utc_slot, _ = schedule.due_slot(
        None, _epoch(2026, 8, 30, 20, 0), "08:30", "UTC"
    )
    chi_slot, _ = schedule.due_slot(
        None, _epoch(2026, 8, 30, 20, 0), "08:30", "America/Chicago"
    )
    assert utc_slot != chi_slot
    assert chi_slot - utc_slot == 5 * 3600  # CDT is UTC-5
