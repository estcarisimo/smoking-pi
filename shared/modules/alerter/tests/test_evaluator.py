"""Evaluator rule tests against synthetic query results."""

from datetime import datetime, timedelta, timezone

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


STEP0 = datetime(2026, 9, 20, 1, 40, tzinfo=timezone.utc)


def _timed_points(per_target, start=STEP0):
    """Raw down-window rows: one point per target per 300 s step, oldest
    first, the way every target's RRD row shares the cycle's timestamp."""
    rows = []
    for target, values in per_target.items():
        for i, v in enumerate(values):
            rows.append({"target": target, "_value": v,
                         "_time": start + timedelta(seconds=300 * i)})
    return rows


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


def test_high_loss_needs_the_loss_to_persist_when_points_are_given():
    """2026-09-19 00:42Z: the link blinked for three minutes. Every target
    got ONE probe cycle at 60-80% loss, its 15 min mean cleared 20%, and
    eighteen "high loss" warnings went out for a blink. With the raw points
    in hand the rule sees one lossy cycle and stays quiet."""
    mean_rows = [{"target": "GoogleDNS", "category": "dns", "_value": 0.272}]
    one_blink = _timed_points({"GoogleDNS": [0.0, 0.8, 0.0]})
    assert evaluator.rule_high_loss(mean_rows, points=one_blink) == []

    sustained = _timed_points({"GoogleDNS": [0.2, 0.3, 0.3]})
    assert len(evaluator.rule_high_loss(mean_rows, points=sustained)) == 1


def test_high_loss_persistence_ignores_single_lost_pings():
    # One lost ping of ten is 10%: the Wi-Fi hop's background, ~100 such
    # points a day here. Two of those plus a bad cycle is still ONE bad cycle.
    mean_rows = [{"target": "Apple", "category": "top_sites", "_value": 0.25}]
    rows = _timed_points({"Apple": [0.1, 0.6, 0.1]})
    assert evaluator.rule_high_loss(mean_rows, points=rows) == []


def test_high_loss_persistence_counts_only_the_means_own_three_cycles():
    """Review of #74: the raw points span the 20 min down window, the mean
    15 min. A lossy cycle that has aged out of the mean must not corroborate
    a single new one -- oldest first: [0.8, 0.0, 0.8, 0.0] holds two lossy
    cycles in the window but only one in the mean's three."""
    mean_rows = [{"target": "Apple", "category": "top_sites", "_value": 0.27}]
    rows = _timed_points({"Apple": [0.8, 0.0, 0.8, 0.0]})
    assert evaluator.rule_high_loss(mean_rows, points=rows) == []
    rows = _timed_points({"Apple": [0.0, 0.8, 0.0, 0.8]})
    assert len(evaluator.rule_high_loss(mean_rows, points=rows)) == 1


def test_high_loss_persistence_counts_untimed_rows_as_given():
    mean_rows = [{"target": "x", "category": "ping", "_value": 0.3}]
    rows = _loss_points("x", [0.3, 0.3, 0.3])
    assert len(evaluator.rule_high_loss(mean_rows, points=rows)) == 1


def test_high_loss_min_points_env_tunable(monkeypatch):
    monkeypatch.setenv("HIGH_LOSS_MIN_POINTS", "1")
    mean_rows = [{"target": "GoogleDNS", "category": "dns", "_value": 0.272}]
    rows = _timed_points({"GoogleDNS": [0.0, 0.8, 0.0]})
    assert len(evaluator.rule_high_loss(mean_rows, points=rows)) == 1


def test_high_loss_without_points_is_the_bare_mean_comparison():
    mean_rows = [{"target": "x", "category": "ping", "_value": 0.27}]
    assert len(evaluator.rule_high_loss(mean_rows)) == 1


def test_high_loss_threshold_env_tunable(monkeypatch):
    monkeypatch.setenv("HIGH_LOSS_PCT", "50")
    mean_rows = [{"target": "flaky", "category": "ping", "_value": 0.35}]
    assert evaluator.rule_high_loss(mean_rows) == []


# ---------------------------------------------------------------------------
# microcut_burst
# ---------------------------------------------------------------------------

def test_microcut_burst_default_threshold():
    # Default is 2, rescaled when the probe went to a 1-in-3 duty cycle: the
    # rule counts OBSERVED windows, so a third of the sample rate sees a third
    # as many for the same real event rate.
    assert evaluator.DEFAULT_MICROCUT_BURST_N == 2
    rows = [
        {"target": "cpe1", "protocol": "ipv4", "_value": 2},
        {"target": "cpe1", "protocol": "ipv6", "_value": 1},
    ]
    incidents = evaluator.rule_microcut_burst(rows)
    assert len(incidents) == 1
    assert incidents[0]["key"] == "microcut_burst:cpe1/ipv4"
    assert incidents[0]["value"] == 2


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
# widespread: uplink_down / outage
# ---------------------------------------------------------------------------

TARGETS = ["Google", "Apple", "Amazon", "NYT", "Facebook", "cloudflare",
           "GoogleDNS", "CloudflareDNS", "Quad9DNS", "CPE_IPv4"]


def _everyone(values, targets=TARGETS, start=STEP0):
    return _timed_points({t: list(values) for t in targets}, start=start)


def test_uplink_down_replaces_a_target_down_per_target():
    """2026-09-20 01:40Z-04:58Z: the Pi's Wi-Fi radio hung. Every target,
    the first hop included, went to 100% and stayed there, and the alerter
    sent 18 target_down criticals + 18 high_loss warnings, four times over.
    That is one incident."""
    rows = _everyone([1.0, 1.0, 1.0, 1.0])
    widespread = evaluator.rule_widespread(rows)
    assert [i["rule"] for i in widespread] == ["uplink_down"]
    inc = widespread[0]
    assert inc["severity"] == "critical"
    assert inc["key"] == "uplink_down"
    assert inc["target"] is None
    assert "10 of 10 targets at 100% loss" in inc["message"]
    assert "this host's uplink" in inc["message"]
    assert not inc.get("transient")

    per_target = evaluator.rule_target_down(rows)
    assert len(per_target) == len(TARGETS)
    assert evaluator.suppress_widespread(per_target, widespread) == []


def test_uplink_down_needs_the_latest_cycles_not_any_cycles():
    # Down for three cycles, then back: the latest cycle is clean, so this
    # is over -- an outage, not a live uplink_down.
    rows = _everyone([1.0, 1.0, 1.0, 0.0])
    widespread = evaluator.rule_widespread(rows)
    assert [i["rule"] for i in widespread] == ["outage"]
    assert "lost every packet" in widespread[0]["message"]


def test_uplink_down_tolerates_a_chronic_or_lucky_target():
    # A host that never answers ICMP is at 100% anyway; one that does not
    # matter. 9 of 10 at 100% is 90%, above WIDESPREAD_PCT.
    per_target = {t: [1.0, 1.0, 1.0, 1.0] for t in TARGETS}
    per_target["Google"] = [0.0, 0.0, 0.0, 0.0]
    widespread = evaluator.rule_widespread(_timed_points(per_target))
    assert [i["rule"] for i in widespread] == ["uplink_down"]
    assert "9 of 10 targets" in widespread[0]["message"]


def test_a_single_dead_target_is_not_widespread():
    per_target = {t: [0.0, 0.0, 0.0, 0.0] for t in TARGETS}
    per_target["NYT"] = [1.0, 1.0, 1.0, 1.0]
    assert evaluator.rule_widespread(_timed_points(per_target)) == []


def test_outage_is_one_transient_warning_for_a_blink_that_hit_everyone():
    """2026-09-19 00:42Z again, seen from the raw points: every target lost
    most of ONE cycle. One warning, keyed to the cycle, transient."""
    rows = _everyone([0.0, 0.8, 0.0, 0.0])
    widespread = evaluator.rule_widespread(rows)
    assert len(widespread) == 1
    inc = widespread[0]
    assert inc["rule"] == "outage"
    assert inc["severity"] == "warning"
    assert inc["transient"] is True
    step = int((STEP0 + timedelta(seconds=300)).timestamp())
    assert inc["key"] == f"outage:{step}"
    assert "10 of 10 targets lost packets in the same 5-minute span at 01:45 UTC" in inc["message"]


def test_outage_spans_consecutive_cycles_as_one_incident():
    rows = _everyone([0.0, 1.0, 1.0, 0.0])
    widespread = evaluator.rule_widespread(rows)
    assert len(widespread) == 1
    assert "10-minute span" in widespread[0]["message"]
    assert "lost every packet" in widespread[0]["message"]


def test_outage_with_partial_edge_cycles_still_says_every_packet():
    # The cut started and ended mid-cycle: 95% on the way in, 60% on the
    # way out, everything lost in between.
    rows = _everyone([0.0, 0.95, 1.0, 1.0, 1.0, 0.6, 0.0])
    widespread = evaluator.rule_widespread(rows)
    assert len(widespread) == 1
    assert "lost every packet" in widespread[0]["message"]
    assert "25-minute span" in widespread[0]["message"]


def test_a_two_cycle_outage_needs_both_cycles_total_to_say_every_packet():
    rows = _everyone([0.0, 1.0, 0.5, 0.0])
    inc = evaluator.rule_widespread(rows)[0]
    assert "10-minute span" in inc["message"] and "lost packets" in inc["message"]
    rows = _everyone([0.0, 1.0, 1.0, 0.0])
    assert "lost every packet" in evaluator.rule_widespread(rows)[0]["message"]


def test_two_separate_blinks_are_two_outages():
    rows = _everyone([0.9, 0.0, 0.9, 0.0])
    keys = [i["key"] for i in evaluator.rule_widespread(rows)]
    assert len(keys) == 2 and keys[0] != keys[1]


def test_uplink_down_needs_the_latest_cycles_to_be_consecutive_in_time():
    """Review of #74: adjacent in the list is not adjacent in time. Three
    total-loss cycles at 01:40, 01:45 and 02:30 (the cycles between them
    absent because too few targets reported) are not 'consecutive'."""
    early = _everyone([1.0, 1.0])
    late = _everyone([1.0], start=STEP0 + timedelta(seconds=3000))
    widespread = evaluator.rule_widespread(early + late)
    assert [i["rule"] for i in widespread] == ["outage", "outage"]


def test_outage_runs_do_not_bridge_a_gap_in_time():
    early = _everyone([0.9], start=STEP0)
    late = _everyone([0.9], start=STEP0 + timedelta(seconds=3000))
    widespread = evaluator.rule_widespread(early + late)
    assert len(widespread) == 2
    assert all("5-minute span" in i["message"] for i in widespread)


def test_outage_runs_tolerate_one_missing_cycle():
    rows = _everyone([0.9, 0.0, 0.9])
    # 0.0 rows are filtered by the Flux threshold only in the MCP tool; here
    # the middle cycle is present and clean, so it splits the run...
    assert len(evaluator.rule_widespread(rows)) == 2
    # ...whereas a cycle that is simply absent (nobody reported) does not.
    rows = _everyone([0.9], start=STEP0) + _everyone([0.9], start=STEP0 + timedelta(seconds=600))
    assert len(evaluator.rule_widespread(rows)) == 1


def test_widespread_needs_real_loss_not_the_single_ping_background():
    # Every target losing one ping of ten in the same cycle is the Wi-Fi hop
    # breathing, and 10% is under WIDESPREAD_LOSS_PCT.
    rows = _everyone([0.0, 0.1, 0.0, 0.0])
    assert evaluator.rule_widespread(rows) == []


def test_widespread_needs_enough_targets_to_mean_anything():
    rows = _everyone([1.0, 1.0, 1.0, 1.0], targets=["a", "b"])
    assert evaluator.rule_widespread(rows) == []


def test_widespread_thresholds_env_tunable(monkeypatch):
    monkeypatch.setenv("WIDESPREAD_LOSS_PCT", "10")
    rows = _everyone([0.0, 0.1, 0.0, 0.0])
    assert [i["rule"] for i in evaluator.rule_widespread(rows)] == ["outage"]
    monkeypatch.setenv("WIDESPREAD_PCT", "95")
    per_target = {t: [1.0, 1.0, 1.0, 1.0] for t in TARGETS}
    per_target["Google"] = [0.0, 0.0, 0.0, 0.0]
    assert evaluator.rule_widespread(_timed_points(per_target)) == []


def test_step_of_accepts_datetimes_iso_strings_and_epochs():
    ts = datetime(2026, 9, 20, 1, 42, 30, tzinfo=timezone.utc)
    step = int(ts.timestamp()) // 300 * 300
    assert evaluator._step_of(ts) == step
    assert evaluator._step_of("2026-09-20T01:42:30Z") == step
    assert evaluator._step_of(ts.timestamp()) == step
    assert evaluator._step_of("not a time") is None
    assert evaluator._step_of(None) is None


def test_suppress_drops_high_loss_and_target_down_under_any_widespread():
    incidents = [
        {"rule": "target_down", "key": "target_down:a"},
        {"rule": "high_loss", "key": "high_loss:b"},
        {"rule": "microcut_burst", "key": "microcut_burst:cpe/ipv4"},
        {"rule": "ipv6_down", "key": "ipv6_down"},
    ]
    outage = [{"rule": "outage", "key": "outage:1"}]
    kept = {i["rule"] for i in evaluator.suppress_widespread(incidents, outage)}
    # The first hop cutting is the outage's evidence; it stays.
    assert kept == {"microcut_burst", "ipv6_down"}
    uplink = [{"rule": "uplink_down", "key": "uplink_down"}]
    kept = {i["rule"] for i in evaluator.suppress_widespread(incidents, uplink)}
    assert kept == {"ipv6_down"}
    assert evaluator.suppress_widespread(incidents, []) == incidents


def test_evaluate_collapses_the_hung_radio_night_into_one_incident(monkeypatch):
    """End to end through evaluate(): the 2026-09-20 rows produce exactly
    one incident, and it leads the list."""
    def fake_query(flux_src):
        if "cpe_latency" in flux_src:
            return [{"target": "136.25.220.1", "protocol": "ipv4", "_value": 99}]
        if "wifi_link" in flux_src:
            return []
        if "-10m" in flux_src or ("count()" in flux_src and "group()" in flux_src):
            return [{"_value": 40}]
        if "mean()" in flux_src:
            return [{"target": t, "category": "top_sites", "_value": 1.0} for t in TARGETS]
        return _everyone([1.0, 1.0, 1.0, 1.0])

    monkeypatch.setattr(evaluator, "_query", fake_query)
    incidents = evaluator.evaluate()
    assert [i["key"] for i in incidents] == ["uplink_down"]


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


def test_exporter_stale_message_reports_the_window_it_queried():
    """The message must not carry a hardcoded duration.

    It used to say "10m" literally. STALE_WINDOW then made the real window
    configurable (and defaulted it to 1200 s), leaving every alert claiming a
    window the rule had not looked at.
    """
    assert "20m" in evaluator.rule_exporter_stale([], window_s=1200)[0]["message"]
    assert "5m" in evaluator.rule_exporter_stale([], window_s=300)[0]["message"]


def test_exporter_stale_does_not_round_a_sub_minute_window_away():
    """A window that isn't whole minutes must not be reported as minutes.

    Integer-dividing by 60 turned 90 s into "1m", which is the same class of
    bug as the hardcoded duration above: the operator is told a window that
    was never queried, and goes looking for the discrepancy in the wrong file.
    """
    assert "90s" in evaluator.rule_exporter_stale([], window_s=90)[0]["message"]
    assert "45s" in evaluator.rule_exporter_stale([], window_s=45)[0]["message"]


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
        # raw down-window points: two targets, four probe cycles each
        return _timed_points({"deadhost": [1.0, 1.0, 1.0, 1.0],
                              "flaky": [0.3, 0.0, 0.3, 0.3]})

    monkeypatch.setattr(evaluator, "_query", fake_query)
    incidents = evaluator.evaluate()
    keys = {i["key"] for i in incidents}
    assert keys == {
        "target_down:deadhost",
        "high_loss:flaky",  # deadhost excluded: already down
        "microcut_burst:cpe1/ipv4",
    }


def test_evaluate_passes_the_raw_points_to_high_loss_for_persistence(monkeypatch):
    """A 15 min mean over the threshold from ONE bad cycle is a blink, and
    evaluate() must hand rule_high_loss the raw rows that show it."""
    def fake_query(flux_src):
        if "cpe_latency" in flux_src or "wifi_link" in flux_src:
            return []
        if "-10m" in flux_src or "count()" in flux_src:
            return [{"_value": 30}]
        if "mean()" in flux_src:
            return [{"target": "blink", "category": "ping", "_value": 0.27}]
        return _timed_points({"blink": [0.0, 0.8, 0.0, 0.0]})

    monkeypatch.setattr(evaluator, "_query", fake_query)
    assert evaluator.evaluate() == []


def test_context_carries_the_four_wifi_aggregates(monkeypatch):
    """Each wifi_link query is recognizable by its verb, and none of them
    can be mistaken for the down-window probe (the fallthrough branch)."""
    seen = []

    def fake_query(flux_src):
        if "wifi_link" in flux_src:
            seen.append(flux_src)
            if "reduce(" in flux_src:
                assert "r._value < -75.0" in flux_src
                return [{"interface": "wlan0", "n": 360, "weak": 0, "min": -52.0}]
            if '"rx_packets"' in flux_src:
                # Over the down window, not the hour: it answers "did the
                # radio receive anything while everything was down".
                assert "-1200s" in flux_src
                return [{"interface": "wlan0", "_value": 4053}]
            if "increase()" in flux_src:
                return [{"interface": "wlan0", "_value": 1}]
            if '"uplink"' in flux_src:
                return [{"interface": "wlan0", "_value": 1}]
            raise AssertionError(flux_src)
        if "-10m" in flux_src:
            return [{"_value": 30}]
        if "mean()" in flux_src or "cpe_latency" in flux_src:
            return []
        return []

    monkeypatch.setattr(evaluator, "_query", fake_query)
    monkeypatch.delenv("WIFI_WEAK_DBM", raising=False)
    _, context = evaluator.evaluate_with_context()
    assert len(seen) == 4
    assert context["wifi_rows"] == {
        "signal": [{"interface": "wlan0", "n": 360, "weak": 0, "min": -52.0}],
        "drops": [{"interface": "wlan0", "_value": 1}],
        "uplink": [{"interface": "wlan0", "_value": 1}],
        "rx": [{"interface": "wlan0", "_value": 4053}],
    }


def test_wifi_weak_threshold_reaches_the_query(monkeypatch):
    monkeypatch.setenv("WIFI_WEAK_DBM", "-70")
    assert "r._value < -70.0" in evaluator._wifi_signal_flux(
        evaluator._env_float("WIFI_WEAK_DBM", evaluator.DEFAULT_WIFI_WEAK_DBM))
