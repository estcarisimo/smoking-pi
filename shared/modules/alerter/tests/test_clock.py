"""One zone for every time the alerter prints (clock.py).

October 2026: an outage at 20:40 on Wednesday in Chicago read "01:40 UTC" in
the alert, "Thu 01:40" in the digest (the container's Europe/London clock),
and "20:40 CDT" in the quiet-hours bundle. Every surface now reads the same
clock: DIGEST_TZ, with the date when it is not today.
"""

import logging
from datetime import datetime, timezone

import pytest

import clock
import digest
import evaluator
import outbox
import quiet
import watchdog_events

OUTAGE = datetime(2026, 10, 29, 1, 40, tzinfo=timezone.utc).timestamp()
# 08:00 CDT on Thursday the 29th: the outage was yesterday evening there.
MORNING = datetime(2026, 10, 29, 13, 0, tzinfo=timezone.utc).timestamp()
# 22:00 CDT on Wednesday the 28th: the same evening.
EVENING = datetime(2026, 10, 29, 3, 0, tzinfo=timezone.utc).timestamp()


@pytest.fixture(autouse=True)
def fresh_cache():
    clock._resolve.cache_clear()
    yield
    clock._resolve.cache_clear()


@pytest.fixture
def chicago(monkeypatch):
    monkeypatch.setenv("DIGEST_TZ", "America/Chicago")
    # The container's own clock, as on the reference Pi: it must not leak.
    monkeypatch.setenv("TZ", "Europe/London")


def _alert(**kw):
    return {"type": "alert", "key": "outage:1", "rule": "outage",
            "severity": "critical", "target": None,
            "message": "10 of 10 targets at 100% loss", **kw}


def test_today_is_the_time_alone_another_day_carries_its_date(chicago):
    assert clock.at(OUTAGE, EVENING) == "20:40"
    assert clock.at(OUTAGE, MORNING) == "28 Oct 20:40"
    assert clock.at(OUTAGE, MORNING, weekday=True) == "Wed 28 Oct 20:40"
    aware = datetime(2026, 10, 29, 1, 40, tzinfo=timezone.utc)
    assert clock.at(aware, MORNING) == "28 Oct 20:40"


def test_an_alert_reads_the_digest_zone(chicago):
    assert evaluator._hhmm(int(OUTAGE), MORNING) == "28 Oct 20:40"
    alive = [m for m in evaluator.LIVENESS_FIELDS if m != "dns_latency"]
    [inc] = evaluator.rule_measurement_stale(
        [{"_measurement": m, "_value": 10} for m in alive],
        [{"_measurement": "dns_latency", "kind": "newest",
          "_time": datetime.fromtimestamp(OUTAGE, tz=timezone.utc)}],
        MORNING, 900)
    assert "no dns_latency points written since 28 Oct 20:40 — " in inc["message"]


def test_the_offline_summary_reads_the_digest_zone(chicago):
    state = {}
    outbox.enqueue(state, _alert(), now=OUTAGE)
    outbox.enqueue(state, _alert(key="outage:2"), now=OUTAGE + 60)
    text = outbox.build(state, MORNING)["message"]
    assert "Not delivered from 28 Oct 20:40 to 08:00 (11h 20m)" in text
    assert "outage · 28 Oct 20:40" in text
    assert "UTC" not in text
    plain = outbox.fallback(state, MORNING)["message"]
    assert "since 28 Oct 20:40 could not be delivered" in plain


def test_a_quiet_hours_bundle_reads_the_digest_zone(chicago, monkeypatch):
    monkeypatch.setenv("QUIET_FROM", "20:00")
    monkeypatch.setenv("QUIET_UNTIL", "08:00")
    state = {}
    quiet.hold(state, _alert(), OUTAGE)
    text = quiet.build(state, MORNING)["message"]
    assert "Quiet hours, 28 Oct 20:40 to 08:00" in text
    assert "outage · 28 Oct 20:40" in text


def test_a_watchdog_event_reads_the_digest_zone(chicago):
    state = {"watchdog": {"since": OUTAGE - 60}}
    rows = [{"_time": datetime.fromtimestamp(OUTAGE, tz=timezone.utc),
             "_value": "wifi: no packet received for 15 min; rebooting",
             "area": "wifi"}]
    [payload] = watchdog_events.collect(state, lambda src: rows, now=MORNING)
    assert payload["message"].startswith(
        "the watchdog on this host, at 28 Oct 20:40: wifi:")


def test_the_digest_reads_its_own_zone_not_the_containers(chicago):
    """The digest said "Thu 01:40" (Europe/London) for Wednesday 20:40."""
    assert digest._clock("2026-10-29T01:40:00+00:00", MORNING) == "Wed 28 Oct 20:40"
    assert digest._clock("2026-10-29T01:40:00+00:00", EVENING) == "20:40"
    # State is UTC ISO; a naive one is still UTC, not the host's clock.
    assert digest._clock("2026-10-29T01:40:00", MORNING) == "Wed 28 Oct 20:40"
    assert digest._clock(None) == "?"


def test_unset_falls_back_to_utc_and_says_so(monkeypatch):
    monkeypatch.delenv("DIGEST_TZ", raising=False)
    monkeypatch.delenv("TZ", raising=False)
    assert clock.zone().key == "UTC"
    assert clock.at(OUTAGE, MORNING) == "01:40 UTC"
    assert clock.at(OUTAGE, MORNING + 86_400) == "29 Oct 01:40 UTC"


def test_an_unknown_zone_falls_back_to_utc_logged_once(monkeypatch, caplog):
    monkeypatch.setenv("DIGEST_TZ", "Mars/Olympus")
    monkeypatch.delenv("TZ", raising=False)
    with caplog.at_level(logging.WARNING):
        assert clock.at(OUTAGE, MORNING) == "01:40 UTC"
        assert clock.at(OUTAGE, MORNING) == "01:40 UTC"
        assert quiet._zone().key == "UTC"
    assert sum("Mars/Olympus" in r.getMessage() for r in caplog.records) == 1


def test_without_digest_tz_the_schedules_zone_is_named(monkeypatch):
    """No DIGEST_TZ: the digest and quiet hours run on TZ, so the times are
    printed in it too, with its abbreviation since nobody chose it here."""
    monkeypatch.delenv("DIGEST_TZ", raising=False)
    monkeypatch.setenv("TZ", "America/Chicago")
    assert clock.at(OUTAGE, MORNING) == "28 Oct 20:40 CDT"
