"""Collector shaping tests -- InfluxDB is mocked at query_influx().

The queries live in ``common.aggregates`` now (the alerter's digest needs
the same numbers); ``collector`` is a thin re-export. Patch the module that
OWNS the function, not the shim: ``collect()`` resolves ``query_influx``
in its own globals, so patching the re-exported name leaves the real one in
place and the test silently talks to a live InfluxDB instead of the stub.
"""

from datetime import datetime, timezone

import pytest

import collector
from common import aggregates


def _dispatch(monkeypatch, handler):
    monkeypatch.setattr(aggregates, "query_influx", handler)


def _target_rows(flux):
    """Fake Flux responses for the latency/dns_latency aggregate queries."""
    if "cpe_latency" in flux:
        return _cpe_rows(flux)
    if "median()" in flux and '"median"' in flux:
        return [
            {"target": "google_dns", "_measurement": "latency", "_value": 0.012},
            {"target": "isp_gw", "_measurement": "latency", "_value": 0.004},
        ]
    if "quantile" in flux:
        return [
            {"target": "google_dns", "_measurement": "latency", "_value": 0.030},
            {"target": "isp_gw", "_measurement": "latency", "_value": 0.009},
        ]
    if "mean()" in flux:
        # loss stored as 0-1 ratio
        return [
            {"target": "google_dns", "_measurement": "latency", "_value": 0.02},
            {"target": "isp_gw", "_measurement": "latency", "_value": 0.0},
        ]
    if "max()" in flux:
        return [
            {"target": "google_dns", "_measurement": "latency", "_value": 0.35},
        ]
    if "count()" in flux:
        return [
            {"target": "google_dns", "_measurement": "latency", "_value": 7},
        ]
    raise AssertionError(f"unexpected flux: {flux}")


def _cpe_rows(flux):
    if "count()" in flux:
        return [{"target": "cpe", "protocol": "icmp", "_value": 4}]
    if "max()" in flux:
        # cpe loss is already a 0-100 percentage
        return [{"target": "cpe", "protocol": "icmp", "_value": 40.0}]
    if "median()" in flux:
        return [{"target": "cpe", "protocol": "icmp", "_value": 1.25}]
    if "sort" in flux:
        return [
            {
                "target": "cpe",
                "protocol": "icmp",
                "_value": 40.0,
                "_time": datetime(2026, 7, 28, 3, 0, tzinfo=timezone.utc),
            }
        ]
    raise AssertionError(f"unexpected cpe flux: {flux}")


def test_collect_converts_both_loss_scales(monkeypatch):
    _dispatch(monkeypatch, _target_rows)
    data = collector.collect(hours=24)

    assert data["window_hours"] == 24
    by_target = {t["target"]: t for t in data["targets"]}

    # latency/dns_latency: seconds -> ms, ratio -> percent
    g = by_target["google_dns"]
    assert g["median_ms"] == 12.0
    assert g["p95_ms"] == 30.0
    assert g["avg_loss_pct"] == 2.0
    assert g["max_loss_pct"] == 35.0
    assert g["loss_events"] == 7

    # targets without loss rows get zero defaults
    isp = by_target["isp_gw"]
    assert isp["avg_loss_pct"] == 0.0
    assert isp["loss_events"] == 0

    # worst target (by loss) sorts first
    assert data["targets"][0]["target"] == "google_dns"

    # cpe_latency: loss stays as a percentage, jitter stays in ms
    cpe = data["cpe"]["stats"][0]
    assert cpe["max_loss_pct"] == 40.0
    assert cpe["median_jitter_ms"] == 1.25
    assert cpe["lossy_windows"] == 4
    assert data["cpe"]["worst_windows"][0]["loss_pct"] == 40.0
    assert data["cpe"]["worst_windows"][0]["time"].startswith("2026-07-28T03:00")


def test_collect_caps_targets_at_worst_30(monkeypatch):
    def handler(flux):
        if "cpe_latency" in flux:
            return []
        if "mean()" in flux:
            # 50 targets with increasing loss ratios
            return [
                {"target": f"t{i:02d}", "_measurement": "latency", "_value": i / 100.0}
                for i in range(50)
            ]
        return []

    _dispatch(monkeypatch, handler)
    data = collector.collect(hours=24)

    assert data["target_total"] == 50
    assert data["targets_truncated"] is True
    assert len(data["targets"]) == collector.MAX_TARGETS == 30
    # highest-loss target kept, lowest-loss dropped
    kept = {t["target"] for t in data["targets"]}
    assert "t49" in kept and "t00" not in kept


def test_flux_str_rejects_injection():
    assert collector.flux_str("smokeping") == '"smokeping"'
    with pytest.raises(ValueError):
        collector.flux_str('bucket") |> yield() //"')
    with pytest.raises(ValueError):
        collector.flux_str("a\\b")
    with pytest.raises(ValueError):
        collector.flux_str("a\nb")


def test_import_needs_no_env(monkeypatch):
    """Constructing queries must not require Influx env vars or a client."""
    monkeypatch.delenv("INFLUX_URL", raising=False)
    monkeypatch.delenv("INFLUX_TOKEN", raising=False)
    assert "from(bucket:" in aggregates._base_flux(["latency"], 6)
