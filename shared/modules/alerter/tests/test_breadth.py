"""common.breadth: the breadth-only targets' queries and voting, for every
reader of breadth (the alerter, the diagnosis, the MCP tools)."""

from datetime import datetime, timezone

import pytest

from common import breadth, cadence

T0 = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)


def test_every_shape_asks_for_http_tcp_and_the_wizard():
    for src in (breadth.points_flux(1200), breadth.cadence_flux(),
                breadth.voters_flux(24, {}),
                breadth.steps_flux(24, {}, {"svc"})):
        assert 'r._measurement == "http_latency"' in src
        assert 'r._measurement == "tcp_latency"' in src
        assert 'r.category == "dns_wizard"' in src
        # base_flux's wizard exclusion is off for these queries.
        assert 'r.category != "dns_wizard"' not in src


def test_the_voter_query_answers_one_row_per_target():
    src = breadth.voters_flux(168, {})
    assert "range(start: -168h)" in src
    assert "if r._value >= 0.15 then 0.0 else 1.0" in src
    assert src.endswith('|> group(columns: ["target"]) |> mean() '
                        '|> keep(columns: ["target", "_value"])')


def test_the_voter_query_uses_each_targets_own_pings():
    src = breadth.voters_flux(24, {"head": cadence.Cadence(300, 3)})
    assert src.startswith('import "dict"\n')
    assert '{key: "head", value: 0.5}' in src


def test_the_step_query_counts_only_voters_one_row_per_time():
    src = breadth.steps_flux(24, {"head": cadence.Cadence(300, 3)}, {"a", "head"})
    assert src.count('import "dict"') == 1
    assert '{key: "a", value: true}, {key: "head", value: true}' in src
    assert "dict.get(dict: voters, key: r.target, default: false)" in src
    assert 'group(columns: ["_time"]) |> reduce(' in src
    assert "lost: if r._value >= 0.999 then 1" in src


def test_a_voter_flux_cannot_quote_is_left_out():
    assert breadth.steps_flux(24, {}, {'bad"name'}) is None
    src = breadth.steps_flux(24, {}, {'bad"name', "ok"})
    assert "bad" not in src and '"ok"' in src


def test_voters_need_half_their_window_clean_and_the_breadths_step():
    rows = [{"target": "half", "_value": 0.5}, {"target": "lossy", "_value": 0.49},
            {"target": "slow", "_value": 1.0}, {"target": None, "_value": 1.0},
            {"target": "none", "_value": None}]
    cad = {"slow": cadence.Cadence(600, 3)}
    assert breadth.voters(rows, cad, 300) == {"half"}
    assert breadth.voters(rows, cad, 600) == {"half", "slow"}


def test_per_step_takes_the_largest_count_of_a_cycle():
    rows = [{"_time": T0, "reporting": 30, "lossy": 2, "lost": 0},
            {"_time": T0.replace(second=20), "reporting": 4, "lossy": 3, "lost": 1},
            {"_time": None, "reporting": 99}]
    step = int(T0.timestamp())
    assert breadth.per_step(rows, 300) == {
        step: {"reporting": 30, "lossy": 3, "lost": 1}}


def test_fetch_asks_no_step_query_without_a_voter():
    asked = []

    def query(src):
        asked.append(src)
        return [{"target": "dead", "_value": 0.0}] if "mean()" in src else []

    assert breadth.fetch(query, 24, 300) == {}
    assert len(asked) == 2 and not any("reduce(" in s for s in asked)


def test_fetch_raises_what_the_query_raises():
    def query(src):
        raise RuntimeError("influx hiccup")

    with pytest.raises(RuntimeError):
        breadth.fetch(query, 24, 300)
