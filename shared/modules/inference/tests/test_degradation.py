"""Loss degradation: segments from change points, the 30 min / 2 % floor."""

import numpy as np
import pandas as pd

import degradation

T0 = 1_790_000_100  # not on a bin edge: bins are floored to 900 s
BIN = degradation.BIN_S


def _loss(per_bin: list[float], cycles: int = 3) -> pd.DataFrame:
    """Per-cycle loss with ``cycles`` cycles per 15-min bin at the given percents."""
    rows = []
    base = (T0 // BIN) * BIN
    for i, pct in enumerate(per_bin):
        for c in range(cycles):
            rows.append((base + i * BIN + c * 300, pct))
    return pd.DataFrame(rows, columns=["epoch", "loss_pct"])


def _probs_at(*indices):
    """A fake BCP: probability 1 at ``indices``, 0 elsewhere."""
    def probabilities(values):
        p = np.zeros(len(values))
        for i in indices:
            p[i] = 1.0
        return p
    return probabilities


def test_bins_average_the_cycles_of_each_quarter_hour():
    loss = pd.DataFrame({"epoch": [0, 300, 600, 900], "loss_pct": [0.0, 10.0, 20.0, 5.0]})
    b = degradation.bins(loss)
    assert list(b.index) == [0, 900]
    assert list(b.values) == [10.0, 5.0]


def test_an_episode_of_two_hours_at_five_percent_is_one_period():
    series = [0.0] * 20 + [5.0] * 8 + [0.0] * 20
    out = degradation.detect(_loss(series), _probs_at(20, 28))
    assert out["change_points"] == 2
    (p,) = out["periods"]
    base = (T0 // BIN) * BIN
    assert p["start"] == base + 20 * BIN
    assert p["end"] == base + 28 * BIN
    assert p["mean_loss_pct"] == 5.0
    assert p["truncated"] is False


def test_one_lost_ping_is_not_a_degradation():
    """10 pings x 3 cycles: one lost ping reads 3.3 % in its bin. BCP may cut
    a segment around it, but 15 minutes is under the 30-minute floor."""
    series = [0.0] * 20 + [3.3] + [0.0] * 20
    out = degradation.detect(_loss(series), _probs_at(20, 21))
    assert out["periods"] == []


def test_a_long_segment_under_two_percent_is_not_a_degradation():
    series = [0.0] * 20 + [1.5] * 10 + [0.0] * 20
    assert degradation.detect(_loss(series), _probs_at(20, 30))["periods"] == []


def test_adjacent_degraded_segments_are_reported_as_one():
    series = [0.0] * 10 + [4.0] * 4 + [12.0] * 4 + [0.0] * 10
    (p,) = degradation.detect(_loss(series), _probs_at(10, 14, 18))["periods"]
    assert p["bins"] == 8
    assert p["mean_loss_pct"] == 8.0


def test_a_degraded_first_segment_is_marked_truncated():
    """It starts where the window starts: its real start is unknown."""
    series = [6.0] * 6 + [0.0] * 20
    (p,) = degradation.detect(_loss(series), _probs_at(6))["periods"]
    assert p["truncated"] is True


def test_no_loss_at_all_never_runs_bcp():
    def boom(values):
        raise AssertionError("BCP ran on an all-zero series")
    out = degradation.detect(_loss([0.0] * 40), boom)
    assert out["periods"] == []


def test_too_few_bins_never_runs_bcp():
    def boom(values):
        raise AssertionError("BCP ran on a short series")
    assert degradation.detect(_loss([5.0] * 3), boom)["periods"] == []


def test_the_floors_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("INFERENCE_DEGRADATION_MIN_MINUTES", "15")
    monkeypatch.setenv("INFERENCE_DEGRADATION_LOSS_PCT", "3")
    series = [0.0] * 20 + [3.3] + [0.0] * 20
    (p,) = degradation.detect(_loss(series), _probs_at(20, 21))["periods"]
    assert round(p["mean_loss_pct"], 1) == 3.3


def test_a_gap_in_the_data_is_not_degraded_time(monkeypatch):
    """A segment of two bins with data on either side of a 4-hour gap spans
    over four hours but holds 30 minutes of measurements: under a 45-minute
    floor it is not a degradation."""
    monkeypatch.setenv("INFERENCE_DEGRADATION_MIN_MINUTES", "45")
    base = (T0 // BIN) * BIN
    rows = [(base + i * BIN, 0.0) for i in range(20)]
    rows.append((base + 20 * BIN, 6.0))                         # before the gap
    rows.append((base + 37 * BIN, 6.0))                         # after the gap
    rows += [(base + (38 + i) * BIN, 0.0) for i in range(20)]
    loss = pd.DataFrame(rows, columns=["epoch", "loss_pct"])
    assert degradation.detect(loss, _probs_at(20, 22))["periods"] == []


def test_one_bin_at_31_percent_in_14_is_a_burst():
    """The reference Pi, UBA, 2026-11-09: BCP cut a segment of 14 bins around
    one bin at 31 %; it reads as 3.5 h at 2.2 %. Detection is unchanged; the
    period says the loss was one bin."""
    series = [0.0] * 20 + [0.0] * 6 + [31.0] + [0.0] * 7 + [0.0] * 20
    (p,) = degradation.detect(_loss(series), _probs_at(20, 34))["periods"]
    base = (T0 // BIN) * BIN
    assert (p["start"], p["end"], p["bins"]) == (base + 20 * BIN, base + 34 * BIN, 14)
    assert round(p["mean_loss_pct"], 2) == 2.21
    assert p["lossy_bins"] == 1
    assert p["peak_pct"] == 31.0
    assert p["peak_start"] == base + 26 * BIN
    assert p["burst"] == 1


def test_a_bin_at_15_percent_next_to_a_clean_one_is_a_burst():
    series = [0.0] * 20 + [15.0, 0.0] + [0.0] * 20
    (p,) = degradation.detect(_loss(series), _probs_at(20, 22))["periods"]
    assert p["mean_loss_pct"] == 7.5 and p["bins"] == 2
    assert (p["lossy_bins"], p["peak_pct"], p["burst"]) == (1, 15.0, 1)


def test_loss_spread_over_the_period_is_not_a_burst():
    series = [0.0] * 20 + [3.0, 4.0, 6.0, 4.0, 3.0, 0.0, 4.0, 4.0] + [0.0] * 20
    (p,) = degradation.detect(_loss(series), _probs_at(20, 28))["periods"]
    base = (T0 // BIN) * BIN
    assert p["lossy_bins"] == 7
    assert p["peak_pct"] == 6.0 and p["peak_start"] == base + 22 * BIN
    assert p["burst"] == 0


def test_a_tie_keeps_the_earliest_peak():
    series = [0.0] * 20 + [5.0, 5.0, 5.0, 5.0] + [0.0] * 20
    (p,) = degradation.detect(_loss(series), _probs_at(20, 24))["periods"]
    assert p["peak_start"] == (T0 // BIN) * BIN + 20 * BIN


def test_merged_segments_pool_the_spread():
    """Two adjacent degraded segments become one period: the lossy bins add
    up, the peak is the higher of the two with its own bin's start."""
    series = [0.0] * 10 + [0.0, 9.0, 0.0, 0.0] + [3.0, 12.0, 0.0, 3.0] + [0.0] * 10
    (p,) = degradation.detect(_loss(series), _probs_at(10, 14, 18))["periods"]
    base = (T0 // BIN) * BIN
    assert p["bins"] == 8 and p["mean_loss_pct"] == 3.375
    assert p["lossy_bins"] == 4
    assert (p["peak_pct"], p["peak_start"]) == (12.0, base + 15 * BIN)
    assert p["burst"] == 0


def test_merging_keeps_the_earlier_peak_on_a_tie():
    series = [0.0] * 10 + [8.0, 0.0] + [8.0, 0.0] + [0.0] * 10
    (p,) = degradation.detect(_loss(series), _probs_at(10, 12, 14))["periods"]
    base = (T0 // BIN) * BIN
    assert p["bins"] == 4 and p["lossy_bins"] == 2 and p["burst"] == 0
    assert (p["peak_pct"], p["peak_start"]) == (8.0, base + 10 * BIN)
