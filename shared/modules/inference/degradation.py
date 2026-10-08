"""Degradation: a sustained rise in packet loss, found by BCP on the loss.

A separate signal from persistent congestion (congestion.py): loss can come
from the radio, a faulty line, an overloaded router, not only from a queue
that stays full, so it gets its own detector and its own shading rather
than feeding Jitterbug's verdict.

Each probe cycle's loss (percent) is averaged into 15-minute bins, the bin
Jitterbug uses for its minimum RTT. Bayesian change-point detection runs on
that series exactly as Jitterbug calls it (constant prior p = 1/(n+1),
Student-t likelihood, a change point where the probability exceeds 0.25).
The bins between two change points are one segment. A segment is degraded
when it lasts at least ``INFERENCE_DEGRADATION_MIN_MINUTES`` (30) and its
mean loss is at least ``INFERENCE_DEGRADATION_LOSS_PCT`` (2 %). With 10 pings
a cycle and 3 cycles a bin, a single lost ping reads 3.3 % in its bin: the
duration floor is what keeps one lost ping from being a degradation.
Adjacent degraded segments are reported as one.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

BIN_S = 900
CP_THRESHOLD = 0.25          # Jitterbug's default for BCP
DEFAULT_MIN_MINUTES = 30
DEFAULT_LOSS_PCT = 2.0
MIN_BINS = 8                 # fewer than two hours of bins: nothing to segment


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def min_minutes() -> float:
    return _env_float("INFERENCE_DEGRADATION_MIN_MINUTES", DEFAULT_MIN_MINUTES)


def loss_pct() -> float:
    return _env_float("INFERENCE_DEGRADATION_LOSS_PCT", DEFAULT_LOSS_PCT)


def bcp_probabilities(values: np.ndarray) -> np.ndarray:
    """Change-point probability per index, as Jitterbug computes it."""
    import bayesian_changepoint_detection.bayesian_models as bm
    import bayesian_changepoint_detection.offline_likelihoods as ol
    import bayesian_changepoint_detection.priors as pr

    n = len(values)
    _q, _p, pcp = bm.offline_changepoint_detection(
        values.astype(float),
        lambda k: float(pr.const_prior(k, p=1 / (n + 1))),
        ol.StudentT(device="cpu"),
        device="cpu",
    )
    if hasattr(pcp, "detach"):
        pcp = pcp.detach().cpu().numpy()
    return np.exp(pcp).sum(0)


def bins(loss: pd.DataFrame) -> pd.Series:
    """Mean loss percent per 15-minute bin, indexed by the bin's start."""
    if loss.empty:
        return pd.Series(dtype=float)
    keyed = loss.assign(bin=(loss["epoch"] // BIN_S) * BIN_S)
    return keyed.groupby("bin")["loss_pct"].mean().sort_index()


def detect(loss: pd.DataFrame, probabilities=bcp_probabilities) -> dict:
    """Degraded periods of one target from its per-cycle loss.

    Returns ``{"periods": [...], "change_points": n, "segments": n}``; each
    period has ``start``/``end`` (epoch s), ``mean_loss_pct`` and ``bins``.
    The first segment starts where the window starts, so its true start is
    unknown: it is reported with ``truncated`` set."""
    series = bins(loss)
    if len(series) < MIN_BINS or float(series.max()) <= 0.0:
        return {"periods": [], "change_points": 0, "segments": 1 if len(series) else 0}
    values = series.to_numpy(dtype=float)
    prob = probabilities(values)
    cuts = [int(i) for i in np.where(np.asarray(prob)[: len(values)] > CP_THRESHOLD)[0] if i > 0]
    edges = [0, *sorted(set(cuts)), len(values)]
    floor_s, floor_pct = min_minutes() * 60, loss_pct()
    segments, periods = [], []
    for a, b in zip(edges, edges[1:]):
        if b <= a:
            continue
        start = int(series.index[a])
        end = int(series.index[b - 1]) + BIN_S
        mean = float(values[a:b].mean())
        segments.append((start, end, mean, b - a))
    for i, (start, end, mean, n) in enumerate(segments):
        if end - start < floor_s or mean < floor_pct:
            continue
        if periods and periods[-1]["end"] == start:
            prev = periods[-1]
            total = prev["bins"] + n
            prev["mean_loss_pct"] = (prev["mean_loss_pct"] * prev["bins"] + mean * n) / total
            prev["bins"], prev["end"] = total, end
            continue
        periods.append({"start": start, "end": end, "mean_loss_pct": mean, "bins": n,
                        "truncated": i == 0})
    return {"periods": periods, "change_points": len(segments) - 1, "segments": len(segments)}
