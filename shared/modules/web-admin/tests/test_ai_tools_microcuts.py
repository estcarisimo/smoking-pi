"""The web assistant's get_microcut_stats reads the shared definition.

Before this, web-admin kept its own copy of the tool: it counted every
window with any loss (98% of them on a rate-limited gateway) and always
returned a top-5, so the in-UI assistant kept reporting "strong microcuts"
about the floor's tail after the MCP tool had stopped. Each test below is
one piece of the evidence in docs/detection-reliability.md.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services import ai_tools
from common import microcuts
from common.aggregates import LOSS_EVENT_PCT

T0 = datetime(2026, 9, 19, 0, 42, 33, tzinfo=timezone.utc)
CPE = {"target": "136.25.220.1", "protocol": "ipv4"}


def _fake_influx(monkeypatch, cut_windows, windows=2880, p50=10.0, p90=16.0,
                 max_loss=None):
    """Answer each of the tool's Flux queries from the spec, so a test reads
    like the day it reproduces: a floor plus a list of windows above the
    threshold."""
    if max_loss is None:
        max_loss = max([w["_value"] for w in cut_windows] + [p90])
    seen = []

    def query(flux):
        seen.append(flux)
        if "quantile(q: 0.5)" in flux:
            return [{**CPE, "_value": p50}]
        if "quantile(q: 0.9)" in flux:
            return [{**CPE, "_value": p90}]
        if "|> max()" in flux:
            return [{**CPE, "_value": max_loss}]
        if "|> median()" in flux:
            return [{**CPE, "_value": 0.044}]
        if "|> count()" in flux:
            return [{**CPE, "_value": windows}]
        if f"r._value > {microcuts.loss_pct()}" in flux:
            return cut_windows
        raise AssertionError(f"unexpected Flux: {flux}")

    monkeypatch.setattr(ai_tools, "query_influx", query)
    return seen


def _windows(spec):
    return [{"_time": T0 + timedelta(seconds=off), **CPE, "_value": loss}
            for off, loss in spec]


def test_quiet_day_has_no_cuts_and_states_the_floor(monkeypatch):
    seen = _fake_influx(monkeypatch, [], p50=10.0, p90=18.0, max_loss=48.0)
    out = ai_tools.execute_tool("get_microcut_stats", {"hours": 24})
    assert out["cuts"] == [] and out["worst_windows"] == []
    assert out["cut_loss_pct"] == 50.0
    entry = out["stats"][0]
    assert entry["windows"] == 2880 and entry["cut_windows"] == 0
    assert entry["confirmed_cuts"] == 0 and entry["possible_cuts"] == 0
    assert entry["p50_loss_pct"] == 10.0 and entry["p90_loss_pct"] == 18.0
    assert "no microcuts" in out["note"] and "p90 18%" in out["note"]
    assert "rate limiting" in out["note"]
    # The old copy's "windows with any loss" query is gone.
    assert not any("r._value > 0.0" in f for f in seen)
    assert "lossy_windows" not in entry


def test_six_windows_at_100_are_one_cut_of_160_s(monkeypatch):
    _fake_influx(monkeypatch, _windows([(30 * i, 100.0) for i in range(6)]))
    out = ai_tools.execute_tool("get_microcut_stats", {"hours": 24})
    assert len(out["cuts"]) == 1
    cut = out["cuts"][0]
    assert cut["seconds"] == 160 and cut["windows"] == 6
    assert cut["confirmed"] and cut["total"]
    assert cut["start"] == "2026-09-19T00:42:33+00:00"
    assert "start_epoch" not in cut
    entry = out["stats"][0]
    assert entry["cut_windows"] == 6 and entry["confirmed_cuts"] == 1
    assert entry["possible_cuts"] == 0
    assert "note" not in out
    assert len(out["worst_windows"]) == 5
    assert all(w["loss_pct"] == 100.0 for w in out["worst_windows"])


def test_two_isolated_windows_are_two_possible_cuts_not_a_burst(monkeypatch):
    # 2026-09-07: two windows above 50% twenty-three minutes apart.
    _fake_influx(monkeypatch, _windows([(0, 62.0), (23 * 60, 54.0)]))
    out = ai_tools.execute_tool("get_microcut_stats", {"hours": 24})
    assert [c["confirmed"] for c in out["cuts"]] == [False, False]
    assert out["cuts"][0]["start"] > out["cuts"][1]["start"]  # newest first
    entry = out["stats"][0]
    assert entry["confirmed_cuts"] == 0 and entry["possible_cuts"] == 2
    assert entry["cut_windows"] == 2
    assert [w["loss_pct"] for w in out["worst_windows"]] == [62.0, 54.0]


def test_threshold_follows_the_shared_env_var(monkeypatch):
    monkeypatch.setenv("MICROCUT_LOSS_PCT", "30")
    seen = _fake_influx(monkeypatch, [])
    out = ai_tools.execute_tool("get_microcut_stats", {"hours": 6})
    assert out["cut_loss_pct"] == 30.0
    assert any("r._value > 30.0" in f for f in seen)
    assert "exceeded 30% loss in the last 6h" in out["note"]


def test_microcut_stats_rejects_bad_hours(monkeypatch):
    _fake_influx(monkeypatch, [])
    assert "error" in ai_tools.execute_tool("get_microcut_stats", {"hours": 0})


def test_loss_events_default_is_the_shared_threshold(monkeypatch):
    seen = []
    monkeypatch.setattr(ai_tools, "query_influx", lambda flux: seen.append(flux) or [])
    out = ai_tools.execute_tool("get_loss_events", {"hours": 24})
    assert out["min_loss_pct"] == LOSS_EVENT_PCT == 15.0
    assert f"r._value >= {LOSS_EVENT_PCT / 100.0}" in seen[0]


@pytest.mark.parametrize("name", ["get_loss_events", "get_microcut_stats"])
def test_tool_descriptions_state_the_definition(name):
    tool = next(t for t in ai_tools.TOOLS if t["name"] == name)
    text = tool["description"]
    if name == "get_loss_events":
        assert "background" in text
        assert f"default {LOSS_EVENT_PCT:g}" in (
            tool["input_schema"]["properties"]["min_loss_pct"]["description"]
        )
    else:
        assert "cuts" in text and "floor" in text and "never as microcuts" in text
