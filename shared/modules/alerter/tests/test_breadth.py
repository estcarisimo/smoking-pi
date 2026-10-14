"""common.breadth: the breadth-only targets' queries and voting, for every
reader of breadth (the alerter, the diagnosis, the MCP tools)."""

from datetime import datetime, timedelta, timezone

import pytest

from common import breadth, cadence

T0 = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
STEP0 = int(T0.timestamp()) // 300 * 300


def _long_window_shapes():
    return [breadth.cadence_flux(), breadth.count_flux(168),
            breadth.count_flux(168, 0.5), breadth.span_flux(168, "first"),
            breadth.span_flux(168, "last"),
            breadth.lossy_flux(168, {"a", "head"}, 0.15)]


def test_every_shape_asks_for_http_tcp_and_the_wizard():
    for src in [breadth.points_flux(1200), *_long_window_shapes()]:
        assert 'r._measurement == "http_latency"' in src
        assert 'r._measurement == "tcp_latency"' in src
        assert 'r.category == "dns_wizard"' in src
        # base_flux's wizard exclusion is off for these queries.
        assert 'r.category != "dns_wizard"' not in src


def test_every_shape_is_balanced():
    """v2.33's first version shipped a doubled brace that InfluxDB refused
    to compile; the mocked tests never noticed."""
    for src in [breadth.points_flux(1200), *_long_window_shapes()]:
        for opening, closing in ("()", "{}", "[]"):
            assert src.count(opening) == src.count(closing), src


def test_long_windows_ask_only_what_the_storage_engine_answers():
    """A per-row Flux function (map, reduce) or a group by time made a week
    of 63 targets take 108 s on the staging Pi, and 30 days froze it. Only
    bare aggregates and filters on the value and the tags."""
    for src in _long_window_shapes():
        assert "map(" not in src and "reduce(" not in src
        assert 'group(columns: ["_time"])' not in src
        assert 'import "dict"' not in src


def test_count_flux_filters_on_the_value_only_when_asked():
    assert "r._value >=" not in breadth.count_flux(24)
    src = breadth.count_flux(24, 0.5)
    assert "range(start: -24h)" in src
    assert "|> filter(fn: (r) => r._value >= 0.5) |> count()" in src


def test_lossy_flux_reads_only_the_voters_and_is_bounded():
    src = breadth.lossy_flux(24, {"b", "a"}, 0.15)
    assert 'r._value >= 0.15 and (r.target == "a" or r.target == "b")' in src
    assert src.endswith(f"|> limit(n: {breadth.MAX_LOSSY_ROWS + 1})")


def test_voters_need_half_their_points_clean_and_the_breadths_step():
    totals = {"half": 10, "lossy": 10, "slow": 10, "silent": 0, 'bad"name': 10}
    events = {"half": 5, "lossy": 6}
    cad = {"slow": cadence.Cadence(600, 3)}
    assert breadth.voters(totals, events, cad, 300) == {"half"}
    assert breadth.voters(totals, events, cad, 600) == {"half", "slow"}


def test_per_step_counts_reporting_over_each_span_and_lossy_once_per_cycle():
    spans = {"a": (STEP0, STEP0 + 900), "b": (STEP0 + 600, STEP0 + 600)}
    rows = [
        {"target": "a", "_time": T0 + timedelta(seconds=300), "_value": 0.5},
        # A second point of a in the same cycle: still one lossy target.
        {"target": "a", "_time": T0 + timedelta(seconds=420), "_value": 1.0},
        # Above the lowest bar, under b's own: not lossy.
        {"target": "b", "_time": T0 + timedelta(seconds=600), "_value": 0.2},
        {"target": "stranger", "_time": T0, "_value": 1.0},
    ]
    out = breadth.per_step(spans, rows, {"a": 0.15, "b": 0.5}, 300)
    assert out == {
        STEP0: {"reporting": 1, "lossy": 0, "lost": 0},
        STEP0 + 300: {"reporting": 1, "lossy": 1, "lost": 1},
        STEP0 + 600: {"reporting": 2, "lossy": 0, "lost": 0},
        STEP0 + 900: {"reporting": 1, "lossy": 0, "lost": 0},
    }


class Fake:
    """Answers each long-window shape from per-target loss lists."""

    def __init__(self, loss, pings=None):
        self.loss, self.pings, self.asked = loss, pings or {}, []

    def __call__(self, src):
        self.asked.append(src)
        if 'r._field == "pings"' in src:
            return [{"target": t, "_field": "pings", "_value": p}
                    for t, p in self.pings.items()]
        if "|> count()" in src:
            bar = None
            if "r._value >=" in src:
                bar = float(src.split("r._value >= ")[1].split(")")[0])
            return [{"target": t, "_value": sum(1 for v in vs if bar is None or v >= bar)}
                    for t, vs in self.loss.items()]
        if "|> first()" in src or "|> last()" in src:
            last = "|> last()" in src
            return [{"target": t, "_time": T0 + timedelta(seconds=300 * (len(vs) - 1
                                                                       if last else 0))}
                    for t, vs in self.loss.items()]
        if "limit(n:" in src:
            bar = float(src.split("r._value >= ")[1].split(" ")[0])
            return [{"target": t, "_time": T0 + timedelta(seconds=300 * i), "_value": v}
                    for t, vs in self.loss.items() if f'"{t}"' in src
                    for i, v in enumerate(vs) if v >= bar]
        raise AssertionError(src)


def test_fetch_counts_voters_per_cycle_at_their_own_bars():
    fake = Fake({"head": [0.0, 1 / 3, 2 / 3, 0.0], "ping": [0.0, 0.2, 1.0, 0.0],
                 "dead": [1.0] * 4},
                pings={"head": 3})
    out = breadth.fetch(fake, 24, 300)
    assert out[STEP0 + 300] == {"reporting": 2, "lossy": 1, "lost": 0}
    assert out[STEP0 + 600] == {"reporting": 2, "lossy": 2, "lost": 1}
    # One count per distinct bar: 0.5 (three pings) and 0.15 (ten).
    assert sum("r._value >= 0." in s and "|> count()" in s for s in fake.asked) == 2
    assert '"dead"' not in fake.asked[-1]


def test_fetch_at_the_callers_bar_counts_lossy_but_never_changes_who_votes():
    fake = Fake({"a": [0.0, 0.3, 0.0, 0.0], "b": [0.0, 0.0, 0.0, 0.0]})
    assert breadth.fetch(fake, 24, 300, bar=0.4)[STEP0 + 300]["lossy"] == 0
    assert breadth.fetch(fake, 24, 300, bar=0.25)[STEP0 + 300]["lossy"] == 1


def test_fetch_asks_nothing_more_without_a_voter():
    fake = Fake({"dead": [1.0] * 4})
    assert breadth.fetch(fake, 24, 300) == {}
    assert not any("limit(n:" in s or "first()" in s for s in fake.asked)


def test_fetch_refuses_past_a_week_and_past_the_row_bound(monkeypatch):
    with pytest.raises(ValueError):
        breadth.fetch(Fake({}), breadth.MAX_HOURS + 1, 300)
    monkeypatch.setattr(breadth, "MAX_LOSSY_ROWS", 2)
    with pytest.raises(breadth.TooMany):
        breadth.fetch(Fake({"a": [0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0]}), 24, 300)


def test_fetch_raises_what_the_query_raises():
    def query(src):
        raise RuntimeError("influx hiccup")

    with pytest.raises(RuntimeError):
        breadth.fetch(query, 24, 300)


def test_fetch_sums_a_target_split_over_several_series():
    """One target name in two series (another tag differs): its counts add
    up, and its span runs from the earliest first point to the latest last
    one (review of #347)."""
    def query(src):
        if 'r._field == "pings"' in src:
            return []
        if "|> count()" in src:
            lossy = "r._value >=" in src
            return [{"target": "a", "_value": 1 if lossy else 3},
                    {"target": "a", "_value": 1 if lossy else 3}]
        if "|> first()" in src:
            return [{"target": "a", "_time": T0 + timedelta(seconds=600)},
                    {"target": "a", "_time": T0}]
        if "|> last()" in src:
            return [{"target": "a", "_time": T0 + timedelta(seconds=300)},
                    {"target": "a", "_time": T0 + timedelta(seconds=900)}]
        return [{"target": "a", "_time": T0 + timedelta(seconds=300), "_value": 1.0}]

    out = breadth.fetch(query, 24, 300)
    # 2 lossy of 6: votes. Reports in all four cycles, once.
    assert sorted(out) == [STEP0 + 300 * i for i in range(4)]
    assert {c["reporting"] for c in out.values()} == {1}
    assert out[STEP0 + 300]["lost"] == 1
