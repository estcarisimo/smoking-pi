"""Persistent congestion: Jitterbug, as published, with no logic of ours.

Jitterbug (``jitterbug-inference``, Carisimo et al., PAM 2022) bins the RTTs
into 15-minute minimums, finds change points in that minimum with Bayesian
change-point detection (BCP), and calls a period congested when the
minimum jumped AND the RTT distribution changed (a KS test). That is the
paper's configuration (``--algorithm bcp --method ks_test``); every other
option stays at the package's default. It detects queues that stay built
up: a floor that rises and stays. Loss is a different signal (degradation.py)
and does not feed this one.

The KS test compares RTT distributions and does not depend on the order of
samples, which matters here: SmokePing stores each cycle's pings sorted.
"""

from __future__ import annotations

import pandas as pd


def _analyzer():
    from jitterbug import JitterbugAnalyzer, JitterbugConfig

    config = JitterbugConfig()
    config.change_point_detection.algorithm = "bcp"
    config.change_point_detection.bcp_device = "cpu"
    config.jitter_analysis.method = "ks_test"
    return JitterbugAnalyzer(config)


def detect(pings: pd.DataFrame, make_analyzer=_analyzer) -> dict:
    """Run Jitterbug on one target's pings (``epoch``, ``values`` in ms).

    Returns ``{"periods": [...], "change_points": n, "inferences": n}``
    where each period is a congested one, as Jitterbug reported it."""
    if pings.empty:
        return {"periods": [], "change_points": 0, "inferences": 0}
    analyzer = make_analyzer()
    results = analyzer.analyze_from_dataframe(pings[["epoch", "values"]].reset_index(drop=True))
    periods = []
    for p in results.get_congested_periods():
        jump = getattr(p, "latency_jump", None)
        jitter = getattr(p, "jitter_analysis", None)
        periods.append({
            "start": int(p.start_epoch),
            "end": int(p.end_epoch),
            "confidence": float(p.confidence),
            "jump_ms": float(jump.magnitude) if jump is not None else None,
            "ks_p": float(jitter.p_value) if jitter is not None and jitter.p_value is not None
            else None,
        })
    return {
        "periods": periods,
        "change_points": len(getattr(analyzer, "change_points", None) or []),
        "inferences": len(results.inferences),
    }
