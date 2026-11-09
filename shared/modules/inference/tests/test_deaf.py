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


def _loss(b, per_bin):
    """Per-cycle loss, three cycles a bin, every cycle at its bin's value."""
    rows = [(b + i * 900 + k * 300, pct) for i, pct in enumerate(per_bin) for k in range(3)]
    return pd.DataFrame(rows, columns=["epoch", "loss_pct"])


def test_one_deaf_bin_stretched_over_clean_hours_is_still_the_monitors():
    # The reference Pi since the watchdog: a hang is one deaf bin; hours later
    # BCP merges it with the clean bins after it into a period of 28 bins.
    b = (T0 // 900) * 900
    periods = [{"start": b, "end": b + 28 * 900}]
    deaf.label(periods, {b}, _loss(b, [65.0] + [0.0] * 27))
    p = periods[0]
    assert p["class"] == "monitor_deaf" and p["deaf_loss_share"] == 1.0
    assert abs(p["deaf_share"] - 1 / 28) < 1e-9   # the bin share, ~0.036, still written


def test_real_loss_that_happens_to_cover_a_deaf_bin_stays_loss():
    b = (T0 // 900) * 900
    periods = [{"start": b, "end": b + 12 * 900}]
    deaf.label(periods, {b + 5 * 900}, _loss(b, [4.0] * 12))
    p = periods[0]
    assert p["class"] == "loss"
    assert abs(p["deaf_loss_share"] - 1 / 12) < 1e-9 and abs(p["deaf_share"] - 1 / 12) < 1e-9


def test_the_two_bin_period_right_after_a_hang_is_the_monitors():
    b = (T0 // 900) * 900
    periods = [{"start": b, "end": b + 2 * 900}]
    deaf.label(periods, {b + 900}, _loss(b, [3.3, 65.0]))
    p = periods[0]
    assert p["class"] == "monitor_deaf" and p["deaf_share"] == 0.5
    assert abs(p["deaf_loss_share"] - 65.0 / 68.3) < 1e-9


def test_a_long_hang_is_the_monitors():
    b = (T0 // 900) * 900
    periods = [{"start": b, "end": b + 44 * 900}]   # 11 h, the first read's hang
    deaf.label(periods, {b + i * 900 for i in range(1, 43)}, _loss(b, [5.0] + [100.0] * 42 + [5.0]))
    p = periods[0]
    assert p["class"] == "monitor_deaf"
    assert abs(p["deaf_share"] - 42 / 44) < 1e-9 and abs(p["deaf_loss_share"] - 4200 / 4210) < 1e-9


def test_most_loss_outside_the_deaf_bins_is_loss_even_if_most_bins_were_deaf():
    b = (T0 // 900) * 900
    periods = [{"start": b, "end": b + 4 * 900}]
    deaf.label(periods, {b, b + 900, b + 2 * 900}, _loss(b, [1.0, 1.0, 1.0, 30.0]))
    assert periods[0]["class"] == "loss" and periods[0]["deaf_share"] == 0.75


def test_a_period_without_loss_falls_back_to_the_bin_share():
    b = (T0 // 900) * 900
    periods = [{"start": b, "end": b + 4 * 900}]
    deaf.label(periods, {b, b + 900}, _loss(b, [0.0] * 4))
    assert periods[0]["class"] == "monitor_deaf" and periods[0]["deaf_share"] == 0.5
    assert "deaf_loss_share" not in periods[0]
    deaf.label(periods, {b}, pd.DataFrame(columns=["epoch", "loss_pct"]))
    assert periods[0]["class"] == "loss" and periods[0]["deaf_share"] == 0.25


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
    deaf.label(periods, {b + 900}, _loss(b, [2.0, 6.0]))
    assert periods[0]["deaf_share"] == 0.5
    assert periods[0]["deaf_loss_share"] == 0.75   # bins b and b+900
    assert periods[1]["deaf_share"] == 0.0   # one bin, b, not deaf
    assert periods[1]["deaf_loss_share"] == 0.0


def test_the_cadence_comes_from_the_exporters_setting(monkeypatch):
    b = (T0 // 900) * 900
    rows = _deaf_samples(b, b + 450, every=30)  # 15 samples
    assert deaf.deaf_bins(rows) == set()          # at 10 s they cover 150 s
    monkeypatch.setenv("WIFI_SAMPLE_INTERVAL", "30")
    assert deaf.deaf_bins(rows) == {b}            # at 30 s they cover 450 s: half
