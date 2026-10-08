"""One pass: windows, the start date, and a failing target."""

import pandas as pd

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
        replace=lambda m, t, c, periods, start, now: written.append((m, t, start)))
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
        replace=lambda m, t, *a: done.append(t))
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
