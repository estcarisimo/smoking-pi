"""Reading the targets and their pings and loss from InfluxDB."""

from datetime import datetime, timezone

import pytest

import series


def _t(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def test_pings_in_milliseconds_and_loss_in_percent():
    rows = [
        {"_time": _t(600), "_field": "ping1", "_value": 0.0071},
        {"_time": _t(600), "_field": "ping2", "_value": 0.0075},
        {"_time": _t(600), "_field": "loss", "_value": 0.2},
        {"_time": _t(300), "_field": "loss", "_value": 7.0},   # legacy count: clamped
        {"_time": _t(300), "_field": "median", "_value": 0.007},  # not asked for, ignored
        {"_time": _t(300), "_field": "ping3", "_value": None},
    ]
    seen = []
    pings, loss = series.fetch("Google", 0, query=lambda q: seen.append(q) or rows)
    assert list(pings["epoch"]) == [600, 600]
    assert list(pings["values"].round(6)) == [7.1, 7.5]
    assert loss.to_dict("records") == [{"epoch": 300, "loss_pct": 100.0},
                                       {"epoch": 600, "loss_pct": 20.0}]
    assert 'r.target == "Google"' in seen[0]


def test_a_quote_in_a_target_name_never_reaches_flux():
    with pytest.raises(ValueError):
        series.fetch('x" or true', 0, query=lambda q: [])


def test_targets_by_category_by_default():
    rows = [{"target": "Google", "category": "topsites"},
            {"target": "W_x_icmp", "category": "dns_wizard"},
            {"target": "CPE_IPv4", "category": "cpe"},
            {"target": "OCA_1", "category": "netflix"}]
    assert series.targets(0, query=lambda q: rows) == [("CPE_IPv4", "cpe"),
                                                        ("Google", "topsites")]


def test_an_explicit_target_list_wins(monkeypatch):
    monkeypatch.setenv("INFERENCE_TARGETS", "OCA_1, Google")
    rows = [{"target": "Google", "category": "topsites"},
            {"target": "NYT", "category": "topsites"},
            {"target": "OCA_1", "category": "netflix"}]
    assert series.targets(0, query=lambda q: rows) == [("Google", "topsites"),
                                                        ("OCA_1", "netflix")]
