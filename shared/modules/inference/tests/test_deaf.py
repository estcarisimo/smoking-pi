"""The labels beside a period: the monitor's deafness and the data coverage.
The detectors are not involved; periods and samples are built by hand."""
import pandas as pd

import deaf

T0 = 1_790_000_100  # not on a bin boundary on purpose


def _deaf_samples(start, end, every=10):
    return [{"_time": float(t)} for t in range(start, end, every)]


def test_a_bin_is_deaf_when_the_radio_was_deaf_for_half_of_it():
    b = (T0 // 900) * 900
    rows = _deaf_samples(b, b + 450)          # 45 samples = exactly half
    assert deaf.deaf_bins(rows) == {b}
    assert deaf.deaf_bins(rows[:-1]) == set()  # 44: under half


def test_a_period_is_the_monitors_when_half_its_bins_were_deaf():
    b = (T0 // 900) * 900
    periods = [{"start": b, "end": b + 4 * 900}]
    deaf.label(periods, {b, b + 900})
    assert periods[0]["class"] == "monitor_deaf" and periods[0]["deaf_share"] == 0.5
    deaf.label(periods, {b})
    assert periods[0]["class"] == "loss" and periods[0]["deaf_share"] == 0.25


def test_with_no_deaf_samples_every_period_is_loss():
    periods = [{"start": T0, "end": T0 + 3600}]
    assert deaf.label(periods, set())[0]["class"] == "loss"


def test_coverage_is_the_share_of_bins_with_a_sample():
    b = (T0 // 900) * 900
    periods = [{"start": b, "end": b + 4 * 900}]
    epochs = pd.Series([b + 10, b + 20, b + 3 * 900 + 5])  # bins 0 and 3 of four
    assert deaf.coverage(periods, epochs)[0]["coverage"] == 0.5


def test_a_sample_time_may_be_a_timestamp_string():
    b = (T0 // 900) * 900
    rows = [{"_time": pd.Timestamp(t, unit="s", tz="UTC").isoformat()} for t in range(b, b + 900, 10)]
    assert deaf.deaf_bins(rows) == {b}


def test_a_period_ending_off_a_boundary_counts_its_last_bin_and_a_short_one_counts_one():
    b = (T0 // 900) * 900
    periods = [{"start": b + 100, "end": b + 1000}, {"start": b + 10, "end": b + 20}]
    deaf.label(periods, {b + 900})
    assert periods[0]["deaf_share"] == 0.5   # bins b and b+900
    assert periods[1]["deaf_share"] == 0.0   # one bin, b, not deaf


def test_the_cadence_comes_from_the_exporters_setting(monkeypatch):
    b = (T0 // 900) * 900
    rows = _deaf_samples(b, b + 450, every=30)  # 15 samples
    assert deaf.deaf_bins(rows) == set()          # at 10 s they cover 150 s
    monkeypatch.setenv("WIFI_SAMPLE_INTERVAL", "30")
    assert deaf.deaf_bins(rows) == {b}            # at 30 s they cover 450 s: half
