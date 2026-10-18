"""common.breadth: the breadth-only targets' queries and voting, for every
reader of breadth (the alerter, the diagnosis, the MCP tools)."""

import re
from datetime import datetime, timedelta, timezone

import pytest

from common import breadth, cadence

T0 = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
STEP0 = int(T0.timestamp()) // 300 * 300


def _long_window_shapes():
    return [breadth.cadence_flux(), breadth.count_flux(168),
            breadth.count_flux(168, 0.5), breadth.span_flux(168, "first"),
            breadth.span_flux(168, "last"),
            breadth.cycle_count_flux(168, {"a", "head"}, 0.15, 300)]


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


def test_cycle_count_flux_counts_only_the_targets_per_cycle():
    src = breadth.cycle_count_flux(24, {"b", "a"}, 0.15, 300)
    assert ('|> filter(fn: (r) => r._value >= 0.15) '
            '|> filter(fn: (r) => (r.target == "a" or r.target == "b")) ') in src
    window = ('aggregateWindow(every: 300s, fn: count, createEmpty: false, '
              'timeSrc: "_start")')
    # Per target first, then per cycle: a target counts once per cycle,
    # however many points it has there (review of #349).
    assert f'|> group(columns: ["target"]) |> {window} |> group() |> {window}' in src
    assert src.index("aggregateWindow") > src.index('r.target == "b"')
    assert "keep(" not in src.split("aggregateWindow")[0]


def _depth(src: str) -> int:
    depth = deepest = 0
    for ch in src:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        deepest = max(deepest, depth)
    return deepest


def test_cycle_count_flux_never_nests_with_the_number_of_targets():
    """224 voters as an ``or`` chain were "Program is nested too deep" to
    InfluxDB on the reference Pi (v2.33.1); 63 on staging were not. A
    balanced tree nests as deep as log2 of the names: a 2 000-name one ran
    on the reference Pi (2026-10-15)."""
    one = breadth.cycle_count_flux(168, {"a"}, 0.15, 300)
    src = breadth.cycle_count_flux(168, {f"t{i}" for i in range(2000)}, 0.15, 300)
    assert src.count("r.target == ") == 2000
    assert _depth(src) - _depth(one) <= 11
    for n in (2, 3, 224, 2000):
        names = {f"t{i}" for i in range(n)}
        tree = breadth.cycle_count_flux(168, names, 0.15, 300)
        assert {f"t{i}" for i in range(n)} == set(
            part.split('"')[1] for part in tree.split("r.target == ")[1:])


def test_cycle_count_flux_asks_for_no_list_scan():
    """``contains(set: [...])`` is not pushed down: Flux scanned the 224
    names for every point above the bar, 5.2 s of a week's 5.4 s lost
    count on the reference Pi; the tree took 0.4 s (2026-10-15)."""
    src = breadth.cycle_count_flux(168, {f"t{i}" for i in range(224)}, 0.15, 300)
    assert "contains(" not in src


def test_cycle_count_flux_refuses_no_targets():
    with pytest.raises(ValueError):
        breadth.cycle_count_flux(168, set(), 0.15, 300)


def test_voters_need_half_their_points_clean_and_the_breadths_step():
    totals = {"half": 10, "lossy": 10, "slow": 10, "silent": 0, 'bad"name': 10}
    events = {"half": 5, "lossy": 6}
    cad = {"slow": cadence.Cadence(600, 3)}
    assert breadth.voters(totals, events, cad, 300) == {"half"}
    assert breadth.voters(totals, events, cad, 600) == {"half", "slow"}


def test_per_step_counts_reporting_over_each_span_and_sums_the_bars():
    spans = {"a": (STEP0, STEP0 + 900), "b": (STEP0 + 600, STEP0 + 600)}
    lossy = [{"_time": T0 + timedelta(seconds=300), "_value": 1},   # bar 0.15
             {"_time": T0 + timedelta(seconds=600), "_value": 1},   # bar 0.5
             {"_time": T0 + timedelta(seconds=600), "_value": 1},
             {"_time": None, "_value": 9}]
    lost = [{"_time": T0 + timedelta(seconds=300), "_value": 1}]
    assert breadth.per_step(spans, lossy, lost, 300) == {
        STEP0: {"reporting": 1, "lossy": 0, "lost": 0},
        STEP0 + 300: {"reporting": 1, "lossy": 1, "lost": 1},
        STEP0 + 600: {"reporting": 2, "lossy": 2, "lost": 0},
        STEP0 + 900: {"reporting": 1, "lossy": 0, "lost": 0},
    }


def test_per_step_clamps_a_miscount_down_never_the_denominator_up():
    """A count above the cycle's reporting voters is a miscount; raising
    the denominator to it would read 3 lossy targets of 10 as 12 of 12."""
    spans = {f"t{i}": (STEP0, STEP0) for i in range(10)}
    out = breadth.per_step(spans, [{"_time": T0, "_value": 12}],
                           [{"_time": T0, "_value": 11}], 300)
    assert out == {STEP0: {"reporting": 10, "lossy": 10, "lost": 10}}
    # And a count in a cycle where no voter reported is no cycle at all.
    assert breadth.per_step({}, [{"_time": T0, "_value": 3}], [], 300) == {}


def test_per_step_buckets_a_clipped_first_window_and_a_boundary_point():
    """aggregateWindow's first window starts at the range start, not on a
    cycle boundary (timeSrc "_start"); a point exactly on a boundary opens
    the next window. Both fall in the cycle Python buckets them in."""
    spans = {"a": (STEP0, STEP0 + 600)}
    clipped = T0 + timedelta(seconds=137)
    boundary = T0 + timedelta(seconds=300)
    out = breadth.per_step(spans, [{"_time": clipped, "_value": 1},
                                   {"_time": boundary, "_value": 1}], [], 300)
    assert out[STEP0]["lossy"] == 1 and out[STEP0 + 300]["lossy"] == 1


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
        if "aggregateWindow(" in src:
            bar = float(src.split("r._value >= ")[1].split(")")[0])
            targets = set(re.findall(r'r\.target == "([^"]+)"', src))
            per_cycle = {}
            for t, vs in self.loss.items():
                if t in targets:
                    for i, v in enumerate(vs):
                        if v >= bar:
                            per_cycle[i] = per_cycle.get(i, 0) + 1
            return [{"_time": T0 + timedelta(seconds=300 * i), "_value": n}
                    for i, n in per_cycle.items()]
        raise AssertionError(src)


def test_fetch_counts_voters_per_cycle_at_their_own_bars():
    fake = Fake({"head": [0.0, 1 / 3, 2 / 3, 0.0], "ping": [0.0, 0.2, 1.0, 0.0],
                 "dead": [1.0] * 4},
                pings={"head": 3})
    out = breadth.fetch(fake, 24, 300)
    assert out[STEP0 + 300] == {"reporting": 2, "lossy": 1, "lost": 0}
    assert out[STEP0 + 600] == {"reporting": 2, "lossy": 2, "lost": 1}
    # One count per distinct bar: 0.5 (three pings) and 0.15 (ten), for the
    # vote and again per cycle; the dead CDN is in no per-cycle count.
    assert sum("r._value >= 0." in s and "|> count()" in s for s in fake.asked) == 2
    cycles = [s for s in fake.asked if "aggregateWindow(" in s]
    assert len(cycles) == 3 and not any('"dead"' in s for s in cycles)


def test_fetch_at_the_callers_bar_counts_lossy_but_never_changes_who_votes():
    fake = Fake({"a": [0.0, 0.3, 0.0, 0.0], "b": [0.0, 0.0, 0.0, 0.0]})
    assert breadth.fetch(fake, 24, 300, bar=0.4)[STEP0 + 300]["lossy"] == 0
    assert breadth.fetch(fake, 24, 300, bar=0.25)[STEP0 + 300]["lossy"] == 1


def test_fetch_floor_raises_the_lossy_bar_but_never_lowers_it():
    """The widespread rule's WIDESPREAD_LOSS_PCT: a voter at 20% (an event on
    ten pings) is not lossy under a 0.3 floor; a three-ping target keeps its
    own higher bar (0.5) over a lower floor."""
    fake = Fake({"a": [0.0, 0.2, 0.0, 0.0], "b": [0.0, 0.0, 0.0, 0.0]})
    assert breadth.fetch(fake, 24, 300)[STEP0 + 300]["lossy"] == 1
    assert breadth.fetch(fake, 24, 300, floor=0.3)[STEP0 + 300]["lossy"] == 0
    head = Fake({"h": [0.0, 1 / 3, 0.0, 0.0], "b": [0.0] * 4}, pings={"h": 3})
    assert breadth.fetch(head, 24, 300, floor=0.3)[STEP0 + 300]["lossy"] == 0


def test_fetch_asks_nothing_more_without_a_voter():
    fake = Fake({"dead": [1.0] * 4})
    assert breadth.fetch(fake, 24, 300) == {}
    assert not any("aggregateWindow(" in s or "first()" in s for s in fake.asked)


def test_fetch_refuses_past_a_week():
    with pytest.raises(ValueError):
        breadth.fetch(Fake({}), breadth.MAX_HOURS + 1, 300)


def test_fetch_counts_a_long_outage_cycle_by_cycle():
    """v2.33.1 fetched every lossy point and gave up past 20 000: a week of
    the reference Pi's 224 voters always did. Now the counts come per
    cycle (the shape is pinned in the cycle_count_flux tests)."""
    fake = Fake({f"t{i}": [0.0] * 20 + [1.0] * 4 for i in range(30)})
    out = breadth.fetch(fake, 168, 300)
    assert out[STEP0 + 300 * 23] == {"reporting": 30, "lossy": 30, "lost": 30}


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
        return [{"_time": T0 + timedelta(seconds=300), "_value": 1}]  # per cycle

    out = breadth.fetch(query, 24, 300)
    # 2 lossy of 6: votes. Reports in all four cycles, once.
    assert sorted(out) == [STEP0 + 300 * i for i in range(4)]
    assert {c["reporting"] for c in out.values()} == {1}
    assert out[STEP0 + 300]["lost"] == 1
