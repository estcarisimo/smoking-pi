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
