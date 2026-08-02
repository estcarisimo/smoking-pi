"""Evaluator rule tests against synthetic query results."""

import evaluator
import flux


# ---------------------------------------------------------------------------
# Clamp handling
# ---------------------------------------------------------------------------

def test_clamp_ratio_passthrough():
    assert flux.clamp_loss_ratio(0.25) == 0.25
    assert flux.clamp_loss_ratio(0.0) == 0.0
    assert flux.clamp_loss_ratio(1.0) == 1.0


def test_clamp_legacy_count_clamps_to_full_loss():
    # Old exporters wrote packet counts (0..20) — clamp reads them as 100%.
    assert flux.clamp_loss_ratio(20) == 1.0
    assert flux.clamp_loss_ratio(3) == 1.0
    assert flux.clamp_loss_ratio(-1) == 0.0


def test_mean_loss_flux_uses_clamp_but_cpe_flux_does_not():
    # latency/dns_latency loss is a 0-1 ratio (legacy counts clamped);
    # cpe_latency loss is a 0-100 percent and must NOT be ratio-clamped.
    assert flux.CLAMP_LOSS_RATIO in evaluator._mean_loss_flux()
    assert flux.CLAMP_LOSS_RATIO not in evaluator._microcut_flux(50.0)


def test_microcut_flux_filters_above_the_loss_threshold():
    # The ICMP rate-limit floor on a CPE means "any loss at all" matches
    # every window; the query must filter on the configured percent.
    assert "r._value > 50.0" in evaluator._microcut_flux(50.0)
    assert "r._value > 80.0" in evaluator._microcut_flux(80.0)


# ---------------------------------------------------------------------------
# target_down
# ---------------------------------------------------------------------------

def _loss_points(target, values):
    return [{"target": target, "_value": v} for v in values]


def test_target_down_fires_when_all_points_lost():
    rows = _loss_points("google", [1.0, 1.0, 1.0, 1.0, 1.0])
    incidents = evaluator.rule_target_down(rows)
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc["rule"] == "target_down"
    assert inc["severity"] == "critical"
    assert inc["key"] == "target_down:google"
    assert inc["target"] == "google"


def test_target_down_legacy_counts_count_as_full_loss():
    rows = _loss_points("legacy", [20, 20, 20])
    assert len(evaluator.rule_target_down(rows)) == 1


def test_target_down_needs_min_points():
    rows = _loss_points("google", [1.0, 1.0])
    assert evaluator.rule_target_down(rows) == []


def test_target_down_any_response_clears():
    rows = _loss_points("google", [1.0, 1.0, 0.95, 1.0])
    assert evaluator.rule_target_down(rows) == []


# ---------------------------------------------------------------------------
# high_loss
# ---------------------------------------------------------------------------

def test_high_loss_fires_above_threshold_and_respects_exclude():
    mean_rows = [
        {"target": "flaky", "category": "ping", "_value": 0.35},
        {"target": "fine", "category": "ping", "_value": 0.01},
        {"target": "down", "category": "ping", "_value": 1.0},
    ]
    incidents = evaluator.rule_high_loss(mean_rows, exclude={"down"})
    assert [i["target"] for i in incidents] == ["flaky"]
    assert incidents[0]["severity"] == "warning"
    assert incidents[0]["value"] == 35.0


def test_high_loss_threshold_env_tunable(monkeypatch):
    monkeypatch.setenv("HIGH_LOSS_PCT", "50")
    mean_rows = [{"target": "flaky", "category": "ping", "_value": 0.35}]
    assert evaluator.rule_high_loss(mean_rows) == []


# ---------------------------------------------------------------------------
# microcut_burst
# ---------------------------------------------------------------------------

def test_microcut_burst_default_threshold():
    rows = [
        {"target": "cpe1", "protocol": "ipv4", "_value": 6},
        {"target": "cpe1", "protocol": "ipv6", "_value": 5},
    ]
    incidents = evaluator.rule_microcut_burst(rows)
    assert len(incidents) == 1
    assert incidents[0]["key"] == "microcut_burst:cpe1/ipv4"
    assert incidents[0]["value"] == 6


def test_microcut_burst_env_tunable(monkeypatch):
    monkeypatch.setenv("MICROCUT_BURST_N", "3")
    rows = [{"target": "cpe1", "protocol": "ipv6", "_value": 3}]
    assert len(evaluator.rule_microcut_burst(rows)) == 1


def test_microcut_message_states_the_loss_threshold(monkeypatch):
    monkeypatch.setenv("MICROCUT_LOSS_PCT", "70")
    rows = [{"target": "cpe1", "protocol": "ipv4", "_value": 9}]
    incidents = evaluator.rule_microcut_burst(rows)
    assert "9 windows over 70% loss" in incidents[0]["message"]


# ---------------------------------------------------------------------------
# exporter_stale
# ---------------------------------------------------------------------------

def test_exporter_stale_fires_on_no_rows():
    incidents = evaluator.rule_exporter_stale([])
    assert len(incidents) == 1
    assert incidents[0]["key"] == "exporter_stale"
    assert incidents[0]["severity"] == "critical"


def test_exporter_stale_quiet_when_points_exist():
    assert evaluator.rule_exporter_stale([{"_value": 42}]) == []


# ---------------------------------------------------------------------------
# ipv6_down
# ---------------------------------------------------------------------------

def test_ipv6_down_aggregate_incident():
    mean_rows = [
        {"target": "google6", "category": "fping6", "_value": 1.0},
        {"target": "cloudflare6", "category": "fping6", "_value": 1.0},
        {"target": "google", "category": "fping", "_value": 0.0},
    ]
    incidents = evaluator.rule_ipv6_down(mean_rows)
    assert len(incidents) == 1
    assert incidents[0]["key"] == "ipv6_down"
    assert incidents[0]["value"] == 2
    assert "IPv6 connectivity appears down" in incidents[0]["message"]


def test_ipv6_down_quiet_when_some_v6_target_healthy():
    mean_rows = [
        {"target": "google6", "category": "fping6", "_value": 1.0},
        {"target": "cloudflare6", "category": "fping6", "_value": 0.0},
        {"target": "google", "category": "fping", "_value": 0.0},
    ]
    assert evaluator.rule_ipv6_down(mean_rows) == []


def test_ipv6_down_quiet_when_ipv4_also_down():
    # Everything at 100% loss looks like a total outage, not an IPv6 issue
    # (target_down / exporter rules cover that).
    mean_rows = [
        {"target": "google6", "category": "fping6", "_value": 1.0},
        {"target": "google", "category": "fping", "_value": 1.0},
    ]
    assert evaluator.rule_ipv6_down(mean_rows) == []


# ---------------------------------------------------------------------------
# evaluate() wiring (mocked query layer)
# ---------------------------------------------------------------------------

def test_evaluate_dispatches_queries_and_excludes_down_from_high_loss(monkeypatch):
    def fake_query(flux_src):
        if "cpe_latency" in flux_src:
            return [{"target": "cpe1", "protocol": "ipv4", "_value": 7}]
        if "-10m" in flux_src:  # exporter staleness probe
            return [{"_value": 30}]
        if "mean()" in flux_src:
            return [
                {"target": "deadhost", "category": "ping", "_value": 1.0},
                {"target": "flaky", "category": "ping", "_value": 0.30},
            ]
        # raw down-window points
        return _loss_points("deadhost", [1.0, 1.0, 1.0, 1.0])

    monkeypatch.setattr(evaluator, "_query", fake_query)
    incidents = evaluator.evaluate()
    keys = {i["key"] for i in incidents}
    assert keys == {
        "target_down:deadhost",
        "high_loss:flaky",  # deadhost excluded: already down
        "microcut_burst:cpe1/ipv4",
    }
