"""The weekly summary: the week, its comparison, each section, and silence.

Every reader is faked by a query function that answers by what the Flux
asks for; nothing reaches InfluxDB.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import schedule
import weekly
from common import definitions

CHICAGO = ZoneInfo("America/Chicago")
# Monday 2 Nov 2026, 08:00 in Chicago: the slot. Its week is Mon 26 Oct
# 00:00 -> Mon 2 Nov 00:00 local, across the end of DST (1 Nov).
SLOT = datetime(2026, 11, 2, 8, 0, tzinfo=CHICAGO).timestamp()
START = datetime(2026, 10, 26, tzinfo=CHICAGO).timestamp()
STOP = datetime(2026, 11, 2, tzinfo=CHICAGO).timestamp()
PREV = datetime(2026, 10, 19, tzinfo=CHICAGO).timestamp()
FULL_WEEK_BINS = int((STOP - START) // weekly.STEP_S)


def _t(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=CHICAGO)


def _incident(start, minutes, cls="local_link", affected=18, reporting=30,
              targets=None, max_loss=40.0):
    return {"_time": start, "end_epoch": int(start.timestamp() + minutes * 60),
            "minutes": minutes, "class": cls, "summary": "", "targets":
            ",".join(targets or [f"t{i}" for i in range(affected)]),
            "targets_affected": affected, "targets_reporting": reporting,
            "max_loss_pct": max_loss}


class Influx:
    """Answers each Flux by what it reads; ``between`` tells the weeks apart."""

    def __init__(self, incidents=(), before=(), bins=None, before_bins=None,
                 watchdog=(), upgrades=(), shared=(), loss=25.0, medians=None,
                 fail=()):
        self.incidents, self.before = list(incidents), list(before)
        self.bins = FULL_WEEK_BINS if bins is None else bins
        self.before_bins = FULL_WEEK_BINS if before_bins is None else before_bins
        self.watchdog, self.upgrades, self.shared = watchdog, upgrades, shared
        self.loss, self.medians, self.fail = loss, medians or [], list(fail)
        self.asked = []

    def _this_week(self, flux):
        return weekly._rfc3339(START) in flux.split("stop:")[0]

    def __call__(self, flux):
        self.asked.append(flux)
        for word in self.fail:
            if word in flux:
                raise ConnectionError("down")
        if '"loss_incident"' in flux:
            return self.incidents if self._this_week(flux) else self.before
        if "aggregateWindow" in flux:
            this = self._this_week(flux)
            n = self.bins if this else self.before_bins
            first = START if this else PREV
            # The first n bins have a point, stamped at their end.
            return [{"_time": first + (i + 1) * weekly.STEP_S, "_value": 10}
                    for i in range(n)]
        if '"watchdog_event"' in flux:
            return list(self.watchdog) if self._this_week(flux) else []
        if '"upgrade"' in flux:
            return list(self.upgrades)
        if "shared_degradation" in flux:
            return list(self.shared) if self._this_week(flux) else []
        if '"target_labels"' in flux:
            return [{"target": "Netflix_h3", "_value": "Netflix"}]
        if "keep(columns: [\"target\", \"category\"])" in flux:
            return [{"target": f"t{i}", "category": "topsites" if i % 2 else "dns"}
                    for i in range(30)]
        if "contains(value: r.target" in flux:
            return [{"_value": self.loss / 100.0}]
        if '"median"' in flux:
            return [{"_value": self.medians.pop(0)}] if self.medians else []
        return []


@pytest.fixture(autouse=True)
def zone(monkeypatch):
    monkeypatch.setenv("DIGEST_TZ", "America/Chicago")
    monkeypatch.setattr(weekly.aggregates, "target_stats", lambda *a, **k: [])


# --- the week ----------------------------------------------------------------


def test_the_week_is_the_seven_calendar_days_before_the_slots_day():
    prev, start, stop = weekly.week_bounds(SLOT, CHICAGO)
    assert (prev, start, stop) == (PREV, START, STOP)
    # 1 Nov ends DST in Chicago: that week is 169 hours, not 168.
    assert stop - start == 169 * 3600
    assert start - prev == 168 * 3600


def test_the_weekly_slot_is_once_a_week_on_its_day():
    monday = schedule.parse_weekday("Monday")
    assert monday == 0 and schedule.parse_weekday("sun") == 6
    assert schedule.parse_weekday("someday") is None
    slot, reason = schedule.due_slot(None, SLOT + 60, "08:00", "America/Chicago",
                                     weekday=monday)
    assert (slot, reason) == (SLOT, "due")
    # Tuesday: still Monday's slot, already fired.
    slot, reason = schedule.due_slot(SLOT, SLOT + 86400, "08:00", "America/Chicago",
                                     weekday=monday)
    assert (slot, reason) == (SLOT, "not_due")
    # Monday 07:59 the week after: the slot is still this one.
    later = datetime(2026, 11, 9, 7, 59, tzinfo=CHICAGO).timestamp()
    assert schedule.due_slot(SLOT, later, "08:00", "America/Chicago",
                             weekday=monday) == (SLOT, "not_due")


# --- the comparison ----------------------------------------------------------


def _week(episodes=(), deaf=None, **extra):
    week = {"episodes": list(episodes), "deaf_minutes": deaf or {}, "minor": 0,
            "measured_pct": 100.0, "no_data_minutes": 0, "watchdog": {},
            "upgrades": 0, "start": START, "stop": STOP}
    week.update(extra)
    return week


def _ep(cause, minutes, **extra):
    return {"kind": "loss", "cause": cause, "minutes": minutes, "start": START + 3600,
            "end": START + 3600 + minutes * 60, "affected": 18, "reporting": 30,
            "loss_pct": 30.0, **extra}


def test_compare_by_cause():
    verdict, rows = weekly.compare(_week([_ep("line", 47)]), _week())
    assert verdict == "bad" and rows == [("line", 47, 0, "worse")]
    verdict, rows = weekly.compare(_week([_ep("line", 47)], deaf={"wifi": 12}),
                                   _week(deaf={"wifi": 220}))
    # 12 min of a deaf radio is under the bar; 220 the week before was not.
    assert verdict == "mixed"
    assert rows == [("wifi", 0, 220, "better"), ("line", 47, 0, "worse")]
    assert weekly.compare(_week(), _week([_ep("provider", 90)]))[0] == "good"
    assert weekly.compare(_week([_ep("line", 30)]), _week([_ep("line", 35)]))[0] == "mixed"
    assert weekly.compare(_week(), _week()) == ("good", [])


# --- each section, from the readers -------------------------------------------


def test_a_line_cut_is_material_and_named_by_cause_not_rule():
    influx = Influx(incidents=[_incident(_t(28, 20, 40), 31)])
    out = weekly.build(SLOT, influx)
    assert out["send"] is True
    text = out["message"]
    first = text.splitlines()[0]
    assert first.startswith("🔴 Bad week, 26 Oct – 1 Nov: your line worse (31 min")
    assert "local_link" not in text and "t3" not in first
    assert "Wed 28 Oct 20:40, 31 min: your line — 18 of 30 destinations, 25 % loss." in text
    assert "It measured 100.0 % of the week." in text
    assert "The watchdog had nothing to do." in text


@pytest.mark.parametrize("change, why", [
    ({"minutes": 10}, "shorter than three cycles"),
    ({"affected": 2, "targets": ["t0", "t2"]}, "two of thirty, one category"),
    ({"reporting": 2, "affected": 2}, "too few destinations reporting"),
])
def test_below_the_bar_is_not_material(change, why):
    inc = _incident(_t(28, 20), change.pop("minutes", 31), **change)
    out = weekly.build(SLOT, Influx(incidents=[inc]))
    assert out["send"] is False, why


def test_two_categories_are_broad_enough():
    inc = _incident(_t(28, 20), 31, affected=2, targets=["t0", "t1"])
    assert weekly.build(SLOT, Influx(incidents=[inc]))["send"] is True


def test_low_mean_loss_is_not_material_and_a_failed_read_uses_the_maximum():
    inc = _incident(_t(28, 20), 31, max_loss=4.0)
    assert weekly.build(SLOT, Influx(incidents=[inc], loss=3.0))["send"] is False
    # The mean could not be read: the stored maximum (4 %) cannot clear 5 %.
    assert weekly.build(SLOT, Influx(incidents=[inc], fail=["contains("]))["send"] is False
    inc = _incident(_t(28, 20), 31, max_loss=60.0)
    assert weekly.build(SLOT, Influx(incidents=[inc], fail=["contains("]))["send"] is True


def test_minutes_inside_an_upgrade_or_a_watchdog_action_do_not_count():
    inc = _incident(_t(28, 20), 20)
    upgrade = [{"_time": _t(28, 19, 55), "phase": "start"},
               {"_time": _t(28, 20, 10), "phase": "end"}]
    assert weekly.build(SLOT, Influx(incidents=[inc], upgrades=upgrade))["send"] is False
    reboot = [{"_time": _t(28, 20, 5), "_value": "wifi: deaf; rebooting", "area": "wifi"}]
    assert weekly.build(SLOT, Influx(incidents=[inc], watchdog=reboot))["send"] is False


def test_the_monitors_own_deaf_spans_are_its_availability_not_an_episode():
    deaf = _incident(_t(27, 1), 5 * 60 + 40, cls="local_wifi")
    week = weekly.read_week(START, STOP, Influx(incidents=[deaf]))
    assert week["episodes"] == []
    assert week["deaf_minutes"] == {"wifi": 340}
    assert week["measured_pct"] == pytest.approx(100 * (1 - 345 / (169 * 60)), abs=0.1)
    # A gap inside the deaf span (a reboot while the radio heard nothing) is
    # lost once, not twice.
    gap = Influx(incidents=[deaf])
    deaf_start = (_t(27, 1).timestamp() - START) // weekly.STEP_S
    real = gap.__call__

    def with_gap(flux):
        rows = real(flux)
        if "aggregateWindow" in flux and gap._this_week(flux):
            rows = [r for i, r in enumerate(rows)
                    if not deaf_start + 12 <= i < deaf_start + 36]
        return rows

    assert weekly.read_week(START, STOP, with_gap)["measured_pct"] == \
        week["measured_pct"]
    out = weekly.build(SLOT, Influx(incidents=[deaf]))
    assert out["send"] is True  # under 99 %
    text = out["message"]
    assert text.splitlines()[0].startswith(
        "🔴 Bad week, 26 Oct – 1 Nov: the Pi's Wi-Fi worse (5 h 40 min deaf")
    assert "It measured 96.6 % of the week: 5 h 40 min the Pi's Wi-Fi heard nothing." in text


def test_no_data_counts_against_availability():
    influx = Influx(bins=FULL_WEEK_BINS - 36)  # three hours without a point
    out = weekly.build(SLOT, influx)
    assert out["send"] is True
    assert "3 h with no measurement at all" in out["message"]


def test_the_watchdogs_actions_are_counted_and_its_bookkeeping_is_not():
    events = [
        {"_time": _t(27, 3), "_value": "wifi: deaf; reloading the brcmfmac driver"},
        {"_time": _t(29, 3), "_value": "wifi: deaf; reloading the brcmfmac driver"},
        {"_time": _t(30, 4), "_value": "wifi: deaf; rebooting"},
        {"_time": _t(30, 4), "_value": "wifi: deaf; would reboot (dry run)"},
        {"_time": _t(30, 5), "_value": "wifi: nothing answers on wlan0; watching"},
        {"_time": _t(31, 5), "_value": "smokeping: stopped; restarting it"},
        {"_time": _t(31, 6), "_value": "smokeping: the restart failed"},
    ]
    week = weekly.read_week(START, STOP, Influx(watchdog=events))
    assert week["watchdog"] == {"reload": 2, "reboot": 1, "restart": 1}
    text = weekly.render(week, _week(), [], [], [], CHICAGO)
    assert ("The watchdog reloaded the Wi-Fi driver twice, rebooted the Pi once, "
            "restarted SmokePing once.") in text


def test_recurring_windows_from_shared_periods_new_and_gone():
    # Loss the destinations shared, 20:00-22:00 on four evenings.
    shared = [{"_time": _t(d, 20), "end": _t(d, 21, 50).timestamp(),
               "_measurement": "shared_degradation", "class": "loss"}
              for d in (26, 27, 29, 30)]
    week = weekly.read_week(START, STOP, Influx(shared=shared))
    windows = weekly.recurring(week["repeating"], START, STOP, CHICAGO)
    assert windows == [{"from": 20, "to": 22, "days": 4}]
    out = weekly.build(SLOT, Influx(shared=shared))
    assert out["send"] is True
    assert "20:00–22:00: loss or slowdowns on 4 of 7 days (new: not the week before)." \
        in out["message"]
    gone = weekly.render(_week(), _week(), [], windows, [], CHICAGO)
    assert "20:00–22:00, 4 days the week before, did not come back." in gone
    # Two evenings are not a pattern, and a deaf period is the monitor's.
    deaf = [{**s, "class": "monitor_deaf"} for s in shared]
    assert weekly.build(SLOT, Influx(shared=shared[:2] + deaf))["send"] is False


def test_one_flaky_destination_every_evening_is_not_a_recurring_window():
    flaky = [_incident(_t(d, 20), 30, cls="destination", affected=1,
                       targets=["t1"]) for d in (26, 27, 28, 29)]
    week = weekly.read_week(START, STOP, Influx(incidents=flaky))
    assert week["repeating"] == [] and week["minor"] == 4
    assert weekly.build(SLOT, Influx(incidents=flaky))["send"] is False
    # Shared by the destinations, even under the loss bar, it is one.
    shared = [_incident(_t(d, 20), 30, max_loss=3.0) for d in (26, 27, 28, 29)]
    assert weekly.recurring(weekly.read_week(START, STOP, Influx(
        incidents=shared, loss=2.0))["repeating"], START, STOP, CHICAGO) == [
        {"from": 20, "to": 21, "days": 4}]


def test_an_episode_is_counted_for_its_minutes_inside_the_week():
    # Sunday 23:50 to Monday 00:20: ten minutes of it are this week's.
    late = _incident(datetime(2026, 11, 1, 23, 50, tzinfo=CHICAGO), 30)
    assert weekly.read_week(START, STOP, Influx(incidents=[late]))["episodes"] == []


def test_recurring_hours_are_local_in_a_half_hour_zone():
    india = ZoneInfo("Asia/Kolkata")
    days = [datetime(2026, 10, d, 20, 0, tzinfo=india).timestamp() for d in (26, 27, 28)]
    windows = weekly.recurring([(t, t + 1800) for t in days], days[0] - 3600,
                               days[-1] + 86400, india)
    assert windows == [{"from": 20, "to": 21, "days": 3}]


def test_a_slowdown_is_material_at_twice_the_same_hours_a_week_before():
    period = [{"_time": _t(28, 19), "end": _t(28, 20).timestamp(),
               "_measurement": "shared_congestion", "class": ""}]
    week = weekly.read_week(START, STOP, Influx(shared=period, medians=[0.050, 0.020]))
    [ep] = week["episodes"]
    assert ep["kind"] == "slow" and ep["median_ms"] == pytest.approx(50.0)
    text = weekly.render(week, _week(), [], [], [], CHICAGO)
    assert "slowdowns across the connection — latency 50 ms, 2.5× the same hours" in text
    week = weekly.read_week(START, STOP, Influx(shared=period, medians=[0.030, 0.020]))
    assert week["episodes"] == []


def test_destinations_by_service_label_only_above_the_bar(monkeypatch):
    stats = [
        {"target": "Netflix_h3", "avg_loss_pct_measurable": 7.2, "median_ms": 31.0},
        {"target": "Netflix", "avg_loss_pct_measurable": 6.0},
        {"target": "uba_ar", "avg_loss_pct_measurable": 12.5},
        {"target": "Google", "avg_loss_pct_measurable": 4.9},
        {"target": "deaf_only", "avg_loss_pct_measurable": None},
    ]
    seen = {}

    def target_stats(start, stop, spans):
        seen["window"], seen["spans"] = (start, stop), spans
        return stats

    monkeypatch.setattr(weekly.aggregates, "target_stats", target_stats)
    tops = weekly.destinations(START, STOP, [(1.0, 2.0)], Influx())
    assert [(t["label"], t["loss_pct"]) for t in tops] == [("uba_ar", 12.5), ("Netflix", 7.2)]
    assert seen == {"window": (START, STOP), "spans": [(1.0, 2.0)]}
    text = weekly.render(_week(), _week(), [], [], tops, CHICAGO)
    assert "uba_ar: 12.5 % loss of its own." in text
    assert "Destinations" not in weekly.render(_week(), _week(), [], [], [], CHICAGO)
    assert "could not be read" in weekly.render(_week(), _week(), [], [], None, CHICAGO)


def test_one_link_to_exactly_the_week(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_HOST", "192.168.1.10")
    out = weekly.build(SLOT, Influx(incidents=[_incident(_t(28, 20, 40), 31)]))
    [(key, url)] = out["links"].items()
    assert key == "grafana_week"
    assert url.startswith("http://192.168.1.10:3000/d/connectivity-quality-v1?")
    assert f"from={int(START * 1000)}" in url and f"to={int(STOP * 1000)}" in url


# --- sending, and not --------------------------------------------------------


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("WEEKLY_ENABLED", "true")
    sent = []
    monkeypatch.setattr(weekly.notifier, "send",
                        lambda payload, image=None: sent.append(payload) or "sent")
    return sent


def test_an_empty_week_sends_nothing_and_records_the_skipped_slot(on, caplog):
    state = {}
    caplog.set_level("INFO", logger="alerter.weekly")
    assert weekly.check(state, now=SLOT + 60, query=Influx()) is False
    assert on == []
    record = state["weekly"]
    assert record["last_fired_slot"] == SLOT
    assert record["last_skipped"] == {"slot": SLOT, "week_start": START,
                                      "measured_pct": 100.0}
    [line] = [r.message for r in caplog.records if "not sent" in r.message]
    assert line.startswith("Weekly for 26 Oct – 1 Nov not sent")
    # And once: the next tick finds the slot fired.
    assert weekly.check(state, now=SLOT + 120, query=Influx()) is False
    assert on == []


def test_a_week_with_news_is_sent_once_silently(on):
    state = {}
    influx = Influx(incidents=[_incident(_t(28, 20, 40), 31)])
    assert weekly.check(state, now=SLOT + 60, query=influx) is True
    assert weekly.check(state, now=SLOT + 120, query=influx) is False
    [payload] = on
    assert payload["type"] == "weekly" and payload["silent"] is True
    assert state["weekly"]["last_sent_slot"] == SLOT


@pytest.mark.parametrize("unread", ['"loss_incident"', "aggregateWindow",
                                    "shared_degradation", '"category"])'])
def test_an_unread_week_is_never_a_quiet_one_and_is_tried_again(on, unread):
    state = {}
    down = Influx(fail=[unread], incidents=[_incident(_t(28, 20, 40), 31)])
    assert weekly.check(state, now=SLOT + 60, query=down) is False
    record = state["weekly"]
    assert on == [] and record["last_error"] == "no data"
    assert "last_skipped" not in record and "last_fired_slot" not in record
    # Not before RETRY_S; then InfluxDB answers and the week goes out.
    assert weekly.check(state, now=SLOT + 120, query=Influx()) is False
    up = Influx(incidents=[_incident(_t(28, 20, 40), 31)])
    assert weekly.check(state, now=SLOT + 60 + weekly.RETRY_S, query=up) is True
    assert len(on) == 1 and "last_error" not in state["weekly"]


def test_a_week_that_stays_unread_is_retired_after_its_tries(on):
    state = {}
    down = Influx(fail=['"loss_incident"'])
    for i in range(weekly.MAX_ATTEMPTS + 1):
        weekly.check(state, now=SLOT + 60 + i * weekly.RETRY_S, query=down)
    assert state["weekly"]["last_fired_slot"] == SLOT and on == []


def test_a_failed_delivery_retries_the_same_slot_three_times(monkeypatch):
    monkeypatch.setenv("WEEKLY_ENABLED", "true")
    monkeypatch.setattr(weekly.notifier, "send", lambda payload, image=None: "failed")
    state = {}
    influx = Influx(incidents=[_incident(_t(28, 20, 40), 31)])
    for i in range(weekly.MAX_ATTEMPTS):
        now = SLOT + 60 + i * weekly.RETRY_S
        assert weekly.check(state, now=now, query=influx) is False
        assert "last_fired_slot" not in state["weekly"]
        assert state["weekly"]["attempts"] == i + 1
        if i + 1 < weekly.MAX_ATTEMPTS:
            # The next tick is too soon to try again.
            assert weekly.check(state, now=now + 60, query=influx) is False
            assert state["weekly"]["attempts"] == i + 1
    weekly.check(state, now=SLOT + 60 + weekly.MAX_ATTEMPTS * weekly.RETRY_S,
                 query=influx)
    assert state["weekly"]["last_fired_slot"] == SLOT


def test_each_try_is_in_the_notification_ledger_as_a_weekly(on, state_file):
    import notifications
    from common import notification_ledger

    influx = Influx(incidents=[_incident(_t(28, 20, 40), 31)])
    assert weekly.check({}, now=SLOT + 60, query=influx) is True
    entries, _ = notification_ledger.read(notifications.ledger_file())
    [entry] = entries
    assert entry["kind"] == "weekly" and entry["outcome"] == "sent"
    assert entry["text"].startswith("🔴 Bad week, 26 Oct – 1 Nov")


def test_a_weekly_handed_to_the_gateway_is_not_sent_again(monkeypatch):
    monkeypatch.setenv("WEEKLY_ENABLED", "true")
    tries = []
    monkeypatch.setattr(weekly.notifier, "send",
                        lambda payload, image=None: tries.append(1) or "handed")
    state = {}
    influx = Influx(incidents=[_incident(_t(28, 20, 40), 31)])
    assert weekly.check(state, now=SLOT + 60, query=influx) is True
    weekly.check(state, now=SLOT + 60 + weekly.RETRY_S, query=influx)
    assert tries == [1] and state["weekly"]["last_fired_slot"] == SLOT


def test_a_late_slot_still_goes_out_the_same_day_and_a_stale_one_is_retired(on):
    influx = Influx(incidents=[_incident(_t(28, 20, 40), 31)])
    assert weekly.check({}, now=SLOT + 10 * 3600, query=influx) is True
    state = {}
    assert weekly.check(state, now=SLOT + 25 * 3600, query=Influx()) is False
    assert state["weekly"]["last_fired_slot"] == SLOT and len(on) == 1


def test_off_by_default_and_a_bad_day_disables_it(monkeypatch, caplog):
    assert weekly.check({}, now=SLOT + 60, query=Influx()) is False
    monkeypatch.setenv("WEEKLY_ENABLED", "true")
    monkeypatch.setenv("WEEKLY_DAY", "someday")
    state = {}
    assert weekly.check(state, now=SLOT + 60, query=Influx()) is False
    assert "not a day and an HH:MM" in caplog.text


# --- the daily digest's settings, mapped -------------------------------------


def test_an_upgraded_digest_becomes_the_weekly_at_its_time(monkeypatch, caplog):
    monkeypatch.setattr(weekly, "_legacy_logged", False)
    monkeypatch.setenv("DIGEST_ENABLED", "true")
    monkeypatch.setenv("DIGEST_AT", "08:30")
    caplog.set_level("INFO", logger="alerter.weekly")
    conf = weekly.settings()
    assert conf == {"enabled": True, "day": 0, "day_raw": "mon", "at": "08:30",
                    "tz": "America/Chicago"}
    weekly.settings()
    [line] = [r.message for r in caplog.records if "retired daily digest" in r.message]
    assert "mon at 08:30 America/Chicago" in line and "smoking-pi alerts --weekly" in line
    # The new settings win once they exist.
    monkeypatch.setenv("WEEKLY_ENABLED", "false")
    assert weekly.settings()["enabled"] is False
    monkeypatch.setenv("WEEKLY_ENABLED", "true")
    monkeypatch.setenv("WEEKLY_AT", "07:00")
    assert weekly.settings()["at"] == "07:00"


def test_digest_off_or_unset_leaves_the_weekly_off(monkeypatch):
    monkeypatch.setenv("DIGEST_ENABLED", "false")
    monkeypatch.setenv("DIGEST_AT", "08:30")
    conf = weekly.settings()
    assert conf["enabled"] is False and conf["at"] == weekly.DEFAULT_AT


def test_the_thresholds_live_in_common_definitions():
    assert definitions.MATERIAL_MIN_MINUTES == 15
    assert definitions.MATERIAL_BREADTH_PCT == 30.0
    assert definitions.MATERIAL_LOSS_PCT == 5.0
    assert definitions.MATERIAL_LATENCY_RATIO == 2.0
    assert definitions.MATERIAL_AVAILABILITY_PCT == 99.0
