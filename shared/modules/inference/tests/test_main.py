"""One pass: windows, the start date, and a failing target."""

import pandas as pd

import deaf
import main
import status
import store

NOW = 1_790_000_000
DAY = 86400


def test_windows_default_to_fourteen_and_seven_days():
    assert main.windows(NOW) == (NOW - 14 * DAY, NOW - 7 * DAY)


def test_the_start_date_bounds_both_windows(monkeypatch):
    monkeypatch.setenv("INFERENCE_SINCE", "2026-09-25")
    since = main.since()
    assert since == 1_790_294_400  # 2026-09-25T00:00:00Z
    c, d = main.windows(NOW)
    assert c == max(NOW - 14 * DAY, since)
    assert d == max(NOW - 7 * DAY, since)


def test_a_start_date_that_is_not_a_date_is_ignored(monkeypatch):
    monkeypatch.setenv("INFERENCE_SINCE", "last summer")
    assert main.since() is None


def test_each_detector_reads_only_its_own_window_and_writes_its_own_measurement():
    c_start, d_start = main.windows(NOW)
    pings = pd.DataFrame({"epoch": [c_start - 10, c_start + 10], "values": [7.0, 7.1]})
    loss = pd.DataFrame({"epoch": [d_start - 10, d_start + 10], "loss_pct": [0.0, 0.0]})
    seen, written = {}, []

    def congestion(p):
        seen["c"] = list(p["epoch"])
        return {"periods": [{"start": 1, "end": 2}], "change_points": 3}

    def degradation(lo):
        seen["d"] = list(lo["epoch"])
        return {"periods": [], "change_points": 0}

    report = main.run_once(
        now=NOW, fetch=lambda t, s: (pings, loss),
        list_targets=lambda s: [("Google", "topsites")],
        find_congestion=congestion, find_degradation=degradation,
        replace=lambda m, t, c, periods, start, now, edge: written.append((m, t, start)))
    assert seen == {"c": [c_start + 10], "d": [d_start + 10]}
    assert written == [(store.CONGESTION, "Google", c_start),
                       (store.DEGRADATION, "Google", d_start)]
    assert report["targets"]["Google"]["congestion"] == {"periods": 1, "change_points": 3}
    assert report["errors"] == 0


def test_a_target_that_fails_does_not_stop_the_others():
    def fetch(target, start):
        if target == "bad":
            raise RuntimeError("boom")
        return (pd.DataFrame(columns=["epoch", "values"]),
                pd.DataFrame(columns=["epoch", "loss_pct"]))

    done = []
    report = main.run_once(
        now=NOW, fetch=fetch, list_targets=lambda s: [("bad", "c"), ("good", "c")],
        find_congestion=lambda p: {"periods": [], "change_points": 0},
        find_degradation=lambda lo: {"periods": [], "change_points": 0},
        replace=lambda m, t, *a, **kw: done.append(t))
    assert report["errors"] == 1
    assert report["targets"]["bad"] == {"error": True}
    assert done == ["good", "good"]


def test_health_needs_a_pass_within_two_intervals_and_a_half(tmp_path, monkeypatch):
    monkeypatch.setenv("INFERENCE_STATE_DIR", str(tmp_path))
    status.write({"at": NOW, "interval": 3600})
    assert status.healthy(status.read(), now=NOW + 2 * 3600)
    assert not status.healthy(status.read(), now=NOW + 3 * 3600)
    assert status.healthy({"idle": "InfluxDB only"}, now=NOW + 10 * DAY)
    assert not status.healthy({}, now=NOW)


def test_influx_down_at_the_start_of_a_pass_is_a_failed_pass_not_a_crash():
    def boom(start):
        raise ConnectionError("influxdb down")
    report = main.run_once(now=NOW, list_targets=boom)
    assert report["errors"] == 1
    assert report["failed"] == "listing targets"
    assert "last_ok" not in report


def test_a_clean_pass_records_last_ok():
    report = main.run_once(now=NOW, list_targets=lambda s: [])
    assert report["last_ok"] == NOW


def test_a_pass_labels_by_where_the_periods_loss_fell():
    # main hands the label the same per-cycle loss the detector read: one deaf
    # bin carrying all the loss of a 28-bin period makes it the monitor's.
    c_start, d_start = main.windows(NOW)
    b = ((d_start + 3600) // 900) * 900
    loss = pd.DataFrame({"epoch": [b + i * 900 + 60 for i in range(28)],
                         "loss_pct": [65.0] + [0.0] * 27})
    deaf_rows = [{"_time": float(t)} for t in range(b, b + 900, 10)]
    written = {}
    report = main.run_once(
        now=NOW, fetch=lambda t, s: (pd.DataFrame(columns=["epoch", "values"]), loss),
        list_targets=lambda s: [("Google", "topsites")],
        find_congestion=lambda p: {"periods": [], "change_points": 0},
        find_degradation=lambda lo: {"periods": [{"start": b, "end": b + 28 * 900,
                                                  "mean_loss_pct": 65 / 28,
                                                  "lossy_bins": 1, "burst": 1}],
                                     "change_points": 2},
        replace=lambda m, t, c, periods, start, now, edge: written.setdefault(m, periods),
        read_deaf=lambda start, now: deaf.deaf_bins(deaf_rows),
        record_pass=lambda r, now: None)
    (period,) = written[store.DEGRADATION]
    assert period["class"] == "monitor_deaf" and period["deaf_loss_share"] == 1.0
    assert abs(period["deaf_share"] - 1 / 28) < 1e-9
    # The pass counts its bursts beside its deaf periods.
    assert report["targets"]["Google"]["degradation"]["bursts"] == 1


def test_a_pass_labels_deaf_periods_and_records_itself():
    c_start, d_start = main.windows(NOW)
    b = ((d_start + 3600) // 900) * 900
    pings = pd.DataFrame({"epoch": [c_start + 10], "values": [7.0]})
    loss = pd.DataFrame({"epoch": [b + 60, b + 960], "loss_pct": [100.0, 100.0]})
    deaf_rows = [{"_time": float(t)} for t in range(b, b + 1800, 10)]
    written, passes = {}, []

    def degradation(lo):
        return {"periods": [{"start": b, "end": b + 1800, "mean_loss_pct": 100.0}],
                "change_points": 1}

    report = main.run_once(
        now=NOW, fetch=lambda t, s: (pings, loss),
        list_targets=lambda s: [("Google", "topsites")],
        find_congestion=lambda p: {"periods": [], "change_points": 0},
        find_degradation=degradation,
        replace=lambda m, t, c, periods, start, now, edge: written.setdefault(m, periods),
        read_deaf=lambda start, now: deaf.deaf_bins(deaf_rows),
        record_pass=lambda r, now: passes.append((r["seconds"], now)))
    (period,) = written[store.DEGRADATION]
    assert period["class"] == "monitor_deaf" and period["deaf_share"] == 1.0
    assert period["deaf_loss_share"] == 1.0
    assert period["coverage"] == 1.0
    assert report["targets"]["Google"]["degradation"]["monitor_deaf"] == 1
    assert report["targets"]["Google"]["degradation"]["bursts"] == 0
    assert report["deaf_bins"] == 2
    assert passes == [(report["seconds"], NOW)]


def test_a_history_write_that_fails_does_not_fail_the_pass():
    def boom(report, now):
        raise RuntimeError("influx away")

    report = main.run_once(
        now=NOW, fetch=lambda t, s: (pd.DataFrame({"epoch": [], "values": []}),
                                      pd.DataFrame({"epoch": [], "loss_pct": []})),
        list_targets=lambda s: [("Google", "topsites")],
        find_congestion=lambda p: {"periods": [], "change_points": 0},
        find_degradation=lambda lo: {"periods": [], "change_points": 0},
        replace=lambda *a, **kw: None, read_deaf=lambda start, now: set(), record_pass=boom)
    assert report["errors"] == 0 and "last_ok" in report


def _quiet(**kw):
    return main.run_once(
        now=NOW, fetch=lambda t, s: (pd.DataFrame({"epoch": [], "values": []}),
                                     pd.DataFrame({"epoch": [], "loss_pct": []})),
        list_targets=lambda s: [("Google", "topsites")],
        find_congestion=lambda p: {"periods": [], "change_points": 0},
        find_degradation=lambda lo: {"periods": [], "change_points": 0},
        replace=lambda *a, **kw: None, read_deaf=lambda s, n: set(), record_pass=lambda r, n: None, **kw)


def test_the_shared_detector_runs_once_on_the_loss_window():
    _, d_start = main.windows(NOW)
    seen, written = [], []
    periods = [{"start": d_start + 900, "end": d_start + 9000, "scope": "shared"}]
    report = _quiet(shared_pass=lambda start, deaf: seen.append(start) or
                    {"periods": periods, "voters": 40},
                    replace_shared=lambda p, start, now, edge: written.append((len(p), start)))
    assert seen == [d_start]
    assert written == [(1, d_start)]
    assert report["shared"]["periods"] == 1 and report["shared"]["shared"] == 1
    assert report["errors"] == 0


def test_too_few_voters_keep_the_earlier_shared_periods():
    written = []
    report = _quiet(shared_pass=lambda start, deaf: {"periods": [], "voters": 3},
                    replace_shared=lambda *a, **kw: written.append(a))
    assert written == []
    assert report["shared"]["voters"] == 3


def test_a_failing_shared_pass_is_an_error_not_a_crash():
    def boom(start, deaf):
        raise RuntimeError("influx down")
    report = _quiet(shared_pass=boom)
    assert report["shared"] == {"error": True}
    assert report["errors"] == 1


def test_without_a_shared_pass_nothing_shared_is_written():
    assert "shared" not in _quiet()


def _shared_ok(start, deaf):
    return {"periods": [], "voters": 40, "voter_targets": ["a"], "monitor": set()}


def test_jitterbug_on_the_aggregate_runs_after_the_shared_pass_with_its_voters():
    c_start, d_start = main.windows(NOW)
    seen, written = [], []

    def agg(s, c, d, now):
        seen.append((s["voter_targets"], c, d, now))
        return {"periods": [{"start": c + 900, "end": c + 9000}], "cycles": 2000, "added": 12,
                "since": c - 3600}

    report = _quiet(shared_pass=_shared_ok, replace_shared=lambda *a, **kw: None,
                    aggregate_pass=agg,
                    replace_shared_congestion=lambda p, s, e, edge: written.append((len(p), s, e)))
    assert seen == [(["a"], c_start, d_start, NOW)]
    assert written == [(1, c_start, NOW)]
    assert report["shared_congestion"]["periods"] == 1
    assert report["shared_congestion"]["added"] == 12 and report["errors"] == 0


def test_no_aggregate_pass_without_enough_voters_or_an_empty_stream():
    calls, written = [], []
    _quiet(shared_pass=lambda s, d: {"periods": [], "voters": 3},
           aggregate_pass=lambda *a, **kw: calls.append(a))
    assert calls == []
    _quiet(shared_pass=_shared_ok, replace_shared=lambda *a, **kw: None,
           aggregate_pass=lambda *a, **kw: {"periods": [], "cycles": 0, "added": 0},
           replace_shared_congestion=lambda *a, **kw: written.append(a))
    assert written == []  # an empty stream keeps what earlier passes wrote


def test_a_failing_aggregate_pass_leaves_the_shared_result_standing():
    def boom(*a):
        raise RuntimeError("no state volume")

    report = _quiet(shared_pass=_shared_ok, replace_shared=lambda *a, **kw: None, aggregate_pass=boom)
    assert report["shared_congestion"] == {"error": True}
    assert report["shared"]["voters"] == 40 and report["errors"] == 1


def test_a_stream_that_starts_late_replaces_periods_from_its_start_only():
    c_start, _ = main.windows(NOW)
    written = []
    _quiet(shared_pass=_shared_ok, replace_shared=lambda *a, **kw: None,
           aggregate_pass=lambda *a, **kw: {"periods": [], "cycles": 5, "added": 5,
                                      "since": c_start + 7 * 86400},
           replace_shared_congestion=lambda p, s, e, edge: written.append(s))
    assert written == [c_start + 7 * 86400]


def test_an_aggregate_pass_without_a_shared_pass_is_never_run():
    calls = []
    report = _quiet(aggregate_pass=lambda *a, **kw: calls.append(a))
    assert calls == [] and "shared_congestion" not in report


def test_the_segments_pass_reads_the_shared_result_and_replaces_the_window():
    _, d_start = main.windows(NOW)
    seen, written = [], []

    def seg(s):
        seen.append(s["voters"])
        return [{"start": d_start + 900, "end": d_start + 9000, "confidence": "strong"},
                {"start": d_start + 9900, "end": d_start + 12000, "confidence": "none"}]

    report = _quiet(shared_pass=_shared_ok, replace_shared=lambda *a, **kw: None,
                    segments_pass=seg, replace_segments=lambda p, s, e, edge: written.append((len(p), s, e)))
    assert seen == [40] and written == [(2, d_start, NOW)]
    assert report["shared_segment"] == {"periods": 2, "hypotheses": 1}
    assert report["errors"] == 0


def test_no_segments_pass_without_enough_voters_and_a_failure_is_isolated():
    calls = []
    _quiet(shared_pass=lambda s, d: {"periods": [], "voters": 3},
           segments_pass=lambda s: calls.append(s))
    assert calls == []

    def boom(s):
        raise RuntimeError("no path data")

    report = _quiet(shared_pass=_shared_ok, replace_shared=lambda *a, **kw: None, segments_pass=boom)
    assert report["shared_segment"] == {"error": True}
    assert report["shared"]["voters"] == 40 and report["errors"] == 1


def test_every_writer_settles_from_the_sliding_edge_not_the_start_date(monkeypatch):
    # INFERENCE_SINCE pins both windows' starts; the edge store.py settles
    # from is where each window would start without it.
    monkeypatch.setenv("INFERENCE_SINCE", "2026-09-25")
    c_start, d_start = main.windows(NOW)
    c_edge, d_edge = main.edges(NOW)
    assert (c_edge, d_edge) == (NOW - 14 * DAY, NOW - 7 * DAY)
    assert c_start > c_edge
    edges = {}

    def replace(m, t, c, periods, start, now, edge):
        edges[m] = (start, edge)

    main.run_once(
        now=NOW, fetch=lambda t, s: (pd.DataFrame({"epoch": [], "values": []}),
                                     pd.DataFrame({"epoch": [], "loss_pct": []})),
        list_targets=lambda s: [("Google", "topsites")],
        find_congestion=lambda p: {"periods": [], "change_points": 0},
        find_degradation=lambda lo: {"periods": [], "change_points": 0},
        replace=replace, read_deaf=lambda s, n: set(), record_pass=lambda r, n: None,
        shared_pass=_shared_ok,
        replace_shared=lambda p, s, e, edge: edges.__setitem__("shared", (s, edge)),
        segments_pass=lambda s: [],
        replace_segments=lambda p, s, e, edge: edges.__setitem__("segment", (s, edge)),
        aggregate_pass=lambda *a: {"periods": [], "cycles": 5, "added": 5},
        replace_shared_congestion=lambda p, s, e, edge: edges.__setitem__("agg", (s, edge)))
    assert edges == {store.CONGESTION: (c_start, c_edge), store.DEGRADATION: (d_start, d_edge),
                     "shared": (d_start, d_edge), "segment": (d_start, d_edge),
                     "agg": (c_start, c_edge)}


class FakeTriage:
    def __init__(self, fail=False):
        self.fail, self.added, self.enriched, self.prepared = fail, [], [], []

    def add_icmp(self, target, pings, loss, periods):
        self.added.append((target, len(periods)))

    def prepare(self):
        self.prepared.append(len(self.enriched))

    def enrich(self, kind, target, write):
        self.enriched.append((kind, target, len(write)))
        if self.fail:
            # A real Triage never raises; one that did must not stop the write.
            raise RuntimeError("triage bug")
        for p in write:
            p["triage"] = "x"

    def report(self):
        return {"periods": len(self.enriched), "errors": 0, "seconds": 0.5}


def _with_triage(tri, replace, targets=(("Facebook", "topsites"), ("Google", "topsites"))):
    made = {}

    def make(ts, start, now, deaf_bins, categories):
        made.update(targets=ts, start=start, categories=categories)
        return tri

    report = main.run_once(
        now=NOW, fetch=lambda t, s: (pd.DataFrame({"epoch": [NOW - 100], "values": [7.0]}),
                                     pd.DataFrame({"epoch": [NOW - 100], "loss_pct": [0.0]})),
        list_targets=lambda s: list(targets),
        find_congestion=lambda p: {"periods": [{"start": NOW - 7200, "end": NOW - 3600}],
                                   "change_points": 1},
        find_degradation=lambda lo: {"periods": [], "change_points": 0},
        replace=replace, read_deaf=lambda s, n: made.setdefault("deaf_from", s) and set(),
        record_pass=lambda r, n: None, make_triage=make)
    return report, made


def test_the_triage_sees_every_target_before_any_period_is_written():
    tri, order = FakeTriage(), []

    def replace(m, t, c, periods, start, now, edge, enrich):
        order.append((t, len(tri.added)))
        enrich(periods)

    report, made = _with_triage(tri, replace)
    assert made["targets"] == ["Facebook", "Google"]
    assert made["categories"] == {"Facebook": "topsites", "Google": "topsites"}
    # Both destinations were read before the first write: breadth sees both.
    assert all(n == 2 for _, n in order)
    assert ("congestion", "Facebook", 1) in tri.enriched
    # The layers were read once, before the first write (and its delete).
    assert tri.prepared == [0]
    assert report["triage"] == {"periods": 4, "errors": 0, "seconds": 0.5}


def test_the_triage_reads_from_the_earliest_writable_start_less_a_day():
    c_start, d_start = main.windows(NOW)
    c_edge, _ = main.edges(NOW)
    _, made = _with_triage(FakeTriage(), lambda *a, **kw: None)
    expected = c_edge + store.GUARD[store.CONGESTION] - DAY - 2 * 900
    assert made["start"] == expected
    # The deaf bins reach as far back: a congestion period older than the
    # loss window still has its radio read.
    assert made["deaf_from"] == min(d_start, expected)


def test_a_write_that_fails_in_the_triage_is_that_targets_error_only():
    tri, written = FakeTriage(fail=True), []

    def replace(m, t, c, periods, start, now, edge, enrich):
        enrich(periods)
        written.append((m, t))

    report, _ = _with_triage(tri, replace)
    # store.replace calls enrich before it writes; if enrich raised there the
    # target's write is counted failed, and the next target still goes out.
    assert report["targets"]["Google"].get("error") and report["targets"]["Facebook"].get("error")
    assert report["errors"] == 2


def test_without_the_triage_nothing_changes_in_the_writes():
    written = []
    report = main.run_once(
        now=NOW, fetch=lambda t, s: (pd.DataFrame({"epoch": [], "values": []}),
                                     pd.DataFrame({"epoch": [], "loss_pct": []})),
        list_targets=lambda s: [("Google", "topsites")],
        find_congestion=lambda p: {"periods": [], "change_points": 0},
        find_degradation=lambda lo: {"periods": [], "change_points": 0},
        replace=lambda m, t, c, periods, start, now, edge: written.append(m),
        read_deaf=lambda s, n: set(), record_pass=lambda r, n: None)
    assert written == [store.CONGESTION, store.DEGRADATION]
    assert "triage" not in report


def test_inference_since_bounds_the_triage_too(monkeypatch):
    monkeypatch.setenv("INFERENCE_SINCE", "2026-09-25")
    _, made = _with_triage(FakeTriage(), lambda *a, **kw: None, targets=(("Facebook", "t"),))
    assert made["start"] >= main.since()
