"""get_weekly_summary: the weekly message's week, as data.

The week's reading is common.weekly's, tested in depth by the alerter's
suite (which also checks that this data and the message agree). Here: which
week a reader gets, the tool's shape, its errors and its cache. InfluxDB is
a fake that answers by what the Flux asks for.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import server
from common import aggregates, notification_ledger, weekly

CHI = ZoneInfo("America/Chicago")
# Saturday 7 Nov 2026, 10:00 Chicago.
SAT = datetime(2026, 11, 7, 10, 0, tzinfo=CHI).timestamp()


def _midnight(y, m, d):
    return datetime(y, m, d, tzinfo=CHI).timestamp()


# --- which week ------------------------------------------------------------


def test_last_is_the_last_complete_week():
    chosen, problem = weekly.select("last", SAT, CHI)
    assert problem is None and chosen["complete"] is True
    assert (chosen["start"], chosen["stop"]) == (_midnight(2026, 10, 26),
                                                 _midnight(2026, 11, 2))
    # Across the end of DST (1 Nov): 169 hours, not 168.
    assert chosen["stop"] - chosen["start"] == 169 * 3600
    assert chosen["prev"] == _midnight(2026, 10, 19)
    assert chosen["prev_stop"] == chosen["start"]


def test_current_is_this_week_so_far_against_the_same_span_before():
    chosen, _ = weekly.select("current", SAT, CHI)
    assert chosen["complete"] is False
    assert chosen["start"] == _midnight(2026, 11, 2)
    assert chosen["stop"] <= SAT and SAT - chosen["stop"] < weekly.STEP_S
    assert chosen["prev"] == _midnight(2026, 10, 26)
    before = datetime.fromtimestamp(chosen["prev_stop"], tz=CHI)
    assert (before.weekday(), before.hour) == (5, 10)  # Saturday 10:00, a week before


def test_a_date_is_the_week_that_contains_it_and_the_day_is_weekly_day():
    chosen, _ = weekly.select("2026-10-28", SAT, CHI)
    assert chosen["start"] == _midnight(2026, 10, 26)
    sunday, _ = weekly.select("2026-10-28", SAT, CHI, weekday=6)
    assert sunday["start"] == _midnight(2026, 10, 25)


@pytest.mark.parametrize("spec, problem", [
    ("next", "week must be last, current or an ISO date (YYYY-MM-DD)."),
    ("2026-11-30", "That week has not begun."),
])
def test_bad_weeks_are_literal_errors(spec, problem):
    assert weekly.select(spec, SAT, CHI) == (None, problem)


# --- the tool ---------------------------------------------------------------


class Influx:
    """A full week of points, the categories, nothing else unless asked."""

    def __init__(self, fail=False):
        self.fail, self.asked = fail, []

    def __call__(self, flux):
        self.asked.append(flux)
        if self.fail:
            raise ConnectionError("Authorization: Token secret-value")
        if "aggregateWindow" in flux:
            start = flux.split("range(start: ")[1].split(",")[0]
            stop = flux.split("stop: ")[1].split(")")[0]
            lo = datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp()
            hi = datetime.fromisoformat(stop.replace("Z", "+00:00")).timestamp()
            n = int((hi - lo) // weekly.STEP_S)
            return [{"_time": lo + (i + 1) * weekly.STEP_S, "_value": 10}
                    for i in range(n)]
        if 'keep(columns: ["target", "category"])' in flux:
            return [{"target": f"t{i}", "category": "topsites"} for i in range(30)]
        return []


@pytest.fixture()
def pi(monkeypatch, tmp_path):
    monkeypatch.setenv("DIGEST_TZ", "America/Chicago")
    monkeypatch.setenv("ALERT_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(aggregates, "target_stats", lambda *a, **k: [
        {"target": "uba_ar", "avg_loss_pct_measurable": 12.5}])
    server._weekly_cache.clear()
    influx = Influx()
    monkeypatch.setattr(server, "query_influx", influx)
    last, _ = weekly.select("last", time.time(), CHI)

    def write(closed=(), ledger=(), skipped=False):
        state = {"episodes": {"seq": 9, "open": None, "closed": list(closed),
                              "since": last["prev"] - 86400}}
        if skipped:
            state["weekly"] = {"last_skipped": {"week_start": last["start"]}}
        (tmp_path / "state.json").write_text(json.dumps(state))
        with open(tmp_path / notification_ledger.FILENAME, "w") as fh:
            for line in ledger:
                fh.write(json.dumps(line) + "\n")

    return influx, last, write


def _episode(at, minutes=210, cls="upstream", material=True):
    return {"key": "episode:3", "start": at, "end": at + minutes * 60, "class": cls,
            "detail": None, "material": material, "broad_cycles": 3,
            "affected": 20, "reporting": 30, "loss_pct": 6.7}


def test_a_bad_week_as_data(pi, monkeypatch):
    influx, last, write = pi
    monkeypatch.setenv("PUBLIC_BASE_HOST", "192.168.1.10")
    sent_at = last["stop"] + 8 * 3600
    write([_episode(last["start"] + 2 * 86400)],
          ledger=[{"id": "w1", "kind": "weekly", "type": "weekly", "outcome": "handed",
                   "decided_at": sent_at, "text": "🔴 Bad week"}])
    out = server.get_weekly_summary()
    assert out["has_material"] is True and out["verdict"] == "bad"
    assert out["by_cause"] == [{"cause": "provider", "cause_text": "your provider",
                                "minutes": 210, "minutes_week_before": 0,
                                "trend": "worse"}]
    [ep] = out["episodes"]
    assert ep["cause_text"] == "your provider" and ep["minutes"] == 210
    assert ep["destinations_affected"] == 20 and ep["loss_pct"] == 6.7
    assert ep["link"].startswith("http://192.168.1.10:3000/d/")
    assert out["monitor"]["measured_pct"] == 100.0
    assert out["monitor"]["watchdog_actions"] == {"reload": 0, "reboot": 0, "restart": 0}
    assert out["destinations"] == [{"service": "uba_ar", "loss_pct": 12.5}]
    assert "connectivity-quality-v1" in out["links"]["grafana_week"]
    assert out["week"]["complete"] is True and out["week"]["tz"] == "America/Chicago"
    assert out["weekly_message"]["sent"] is True
    # The tool's log line carries the verdict.
    assert server._summarize_result(out) == "verdict=bad"


def test_a_quiet_week_has_no_material_and_says_it_was_skipped(pi):
    _, _, write = pi
    write(skipped=True)
    out = server.get_weekly_summary(week="last")
    assert out["has_material"] is False and out["verdict"] == "good"
    assert out["episodes"] == [] and out["patterns"] == []
    assert out["weekly_message"] == {"sent": False,
                                     "outcome": "skipped: nothing material"}


def test_a_finished_week_is_cached_and_the_current_one_is_not(pi):
    influx, _, write = pi
    write()
    server.get_weekly_summary(week="last")
    asked = len(influx.asked)
    server.get_weekly_summary(week="last")
    assert len(influx.asked) == asked
    server.get_weekly_summary(week="current")
    server.get_weekly_summary(week="current")
    assert len(influx.asked) > asked


def test_the_cache_keeps_the_reading_but_not_what_the_alerter_did(pi, tmp_path):
    influx, last, write = pi
    write()
    assert server.get_weekly_summary()["weekly_message"] is None
    asked = len(influx.asked)
    write(ledger=[{"id": "w2", "kind": "weekly", "type": "weekly", "outcome": "queued",
                   "decided_at": last["stop"] + 3600, "text": "🟡 Mixed week"}])
    out = server.get_weekly_summary()
    assert len(influx.asked) == asked
    assert out["weekly_message"]["sent"] is False
    assert out["weekly_message"]["pending"] is True


def test_a_week_whose_destinations_were_unread_is_not_cached(pi, monkeypatch):
    influx, _, write = pi
    write()

    def down(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(aggregates, "target_stats", down)
    assert server.get_weekly_summary()["destinations"] is None
    asked = len(influx.asked)
    server.get_weekly_summary()
    assert len(influx.asked) > asked


def test_an_unread_week_is_an_error_with_an_id_and_no_detail(pi, monkeypatch):
    _, _, write = pi
    write()
    monkeypatch.setattr(server, "query_influx", Influx(fail=True))
    out = server.get_weekly_summary()
    assert out["error"].startswith("InfluxDB query failed (log id ")
    assert "secret" not in json.dumps(out) and out["error_id"]


def test_a_bad_week_argument_is_a_plain_error(pi):
    assert server.get_weekly_summary(week="someday") == {
        "error": "week must be last, current or an ISO date (YYYY-MM-DD)."}
