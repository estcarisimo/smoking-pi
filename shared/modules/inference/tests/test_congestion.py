"""Persistent congestion: Jitterbug's own verdicts, passed through unchanged."""

from types import SimpleNamespace

import pandas as pd

import congestion


class FakeAnalyzer:
    def __init__(self, periods, inferences=3, change_points=4):
        self.seen = None
        self.change_points = [object()] * change_points
        self._periods, self._n = periods, inferences

    def analyze_from_dataframe(self, df):
        self.seen = df
        return SimpleNamespace(get_congested_periods=lambda: self._periods,
                               inferences=[object()] * self._n)


def _period(start, end, conf=0.9, jump=2.5, p=0.001):
    return SimpleNamespace(start_epoch=float(start), end_epoch=float(end), confidence=conf,
                           latency_jump=SimpleNamespace(magnitude=jump),
                           jitter_analysis=SimpleNamespace(p_value=p))


def test_congested_periods_are_jitterbugs_own():
    fake = FakeAnalyzer([_period(1000, 4600)])
    pings = pd.DataFrame({"epoch": [1, 1, 2], "values": [7.0, 7.5, 8.0], "extra": [0, 0, 0]})
    out = congestion.detect(pings, make_analyzer=lambda: fake)
    assert out == {"periods": [{"start": 1000, "end": 4600, "confidence": 0.9,
                                "jump_ms": 2.5, "ks_p": 0.001}],
                   "change_points": 4, "inferences": 3}
    # Jitterbug's input format: epoch and values, nothing else.
    assert list(fake.seen.columns) == ["epoch", "values"]


def test_no_pings_never_builds_an_analyzer():
    def boom():
        raise AssertionError("analyzer built with no data")
    out = congestion.detect(pd.DataFrame(columns=["epoch", "values"]), make_analyzer=boom)
    assert out["periods"] == []


def test_the_analyzer_uses_the_papers_configuration(monkeypatch):
    """BCP + KS, as in the paper; everything else at Jitterbug's defaults."""
    captured = {}

    class Config:
        def __init__(self):
            self.change_point_detection = SimpleNamespace(algorithm="ruptures",
                                                          bcp_device="mps")
            self.jitter_analysis = SimpleNamespace(method="jitter_dispersion")

    class Analyzer:
        def __init__(self, config):
            captured["config"] = config

    import sys
    import types
    fake = types.ModuleType("jitterbug")
    fake.JitterbugConfig, fake.JitterbugAnalyzer = Config, Analyzer
    monkeypatch.setitem(sys.modules, "jitterbug", fake)
    congestion._analyzer()
    cfg = captured["config"]
    assert cfg.change_point_detection.algorithm == "bcp"
    assert cfg.change_point_detection.bcp_device == "cpu"
    assert cfg.jitter_analysis.method == "ks_test"
