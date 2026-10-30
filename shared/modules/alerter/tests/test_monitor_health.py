"""The monitor's own health: what the alerter says when it cannot see.

Before: InfluxDB unreadable meant every iteration failed into the log and
nothing was sent; exporter_stale watched ``latency`` alone, so the DNS, HTTP
and TCP probes, the gateway microcut probe and the Wi-Fi exporter could stop
unnoticed; and one failing Wi-Fi query or verdict silenced every rule.
"""

from datetime import datetime, timezone

import evaluator
import main
import state

NOW = 1_791_900_000.0  # 2026-10-13 14:00 UTC
W = 1200


def _at(seconds_ago):
    return datetime.fromtimestamp(NOW - seconds_ago, tz=timezone.utc)


def _count(*alive):
    return [{"_measurement": m, "_value": 10} for m in alive]


def _newest(name, seconds_ago):
    return {"_measurement": name, "kind": "newest", "_time": _at(seconds_ago)}


def _step(name, step):
    return {"_measurement": name, "kind": "step", "_value": step}


ALL = list(evaluator.LIVENESS_FIELDS)


# --- measurement_stale (pure) ------------------------------------------------

def test_every_writer_alive_is_quiet():
    assert evaluator.rule_measurement_stale(_count(*ALL), [], NOW, W) == []


def test_a_writer_that_stopped_is_a_warning_naming_it():
    alive = [m for m in ALL if m != "dns_latency"]
    [inc] = evaluator.rule_measurement_stale(
        _count(*alive), [_newest("dns_latency", 1800), _step("dns_latency", 300)],
        NOW, W)
    assert inc["key"] == "measurement_stale:dns_latency"
    assert inc["severity"] == "warning"
    assert "no dns_latency points written since 13:30 UTC" in inc["message"]
    assert "the DNS probe appears stalled" in inc["message"]


def test_a_short_gap_is_not_stale():
    alive = [m for m in ALL if m != "cpe_latency"]
    assert evaluator.rule_measurement_stale(
        _count(*alive), [_newest("cpe_latency", 600)], NOW, W) == []


def test_a_slow_probe_gets_four_of_its_own_steps():
    alive = [m for m in ALL if m != "http_latency"]
    rows = [_newest("http_latency", 1800), _step("http_latency", 900)]
    assert evaluator.rule_measurement_stale(_count(*alive), rows, NOW, W) == []
    rows = [_newest("http_latency", 3700), _step("http_latency", 900)]
    assert len(evaluator.rule_measurement_stale(_count(*alive), rows, NOW, W)) == 1


def test_a_writer_this_host_never_had_is_not_expected():
    """A wired host writes no wifi_link; no HTTP probes, no http_latency."""
    alive = [m for m in ALL if m not in ("wifi_link", "http_latency")]
    assert evaluator.rule_measurement_stale(_count(*alive), [], NOW, W) == []


def test_an_open_stall_stays_open_while_its_last_point_is_recent():
    alive = [m for m in ALL if m != "wifi_link"]
    [inc] = evaluator.rule_measurement_stale(
        _count(*alive), [_newest("wifi_link", 300)], NOW, W,
        open_keys={"measurement_stale:wifi_link"})
    assert inc["key"] == "measurement_stale:wifi_link"


def test_a_writer_silent_past_the_lookback_is_retired_without_a_recovery():
    """Review of #334: a writer removed on purpose (a wired uplink, a
    disabled probe) must not re-notify every hour forever."""
    alive = [m for m in ALL if m != "wifi_link"]
    [inc] = evaluator.rule_measurement_stale(
        _count(*alive), [], NOW, W, open_keys={"measurement_stale:wifi_link"})
    assert inc["quiet"] and inc["supersedes"] == ["measurement_stale:wifi_link"]
    current = {"incidents": {}}
    opened = evaluator.rule_measurement_stale(
        _count(*alive), [_newest("wifi_link", 3600)], NOW, W)
    state.reconcile(current, opened, now=NOW)
    actions = state.reconcile(current, [inc], now=NOW + 86_400)
    assert actions == {"alerts": [], "recoveries": []}
    assert current["incidents"] == {}


def test_the_newest_point_is_found_by_time_not_table_order():
    assert '|> sort(columns: ["_time"]) |> last()' in evaluator._last_written_flux(ALL)


# --- measurement_stale (wiring) ----------------------------------------------

def _fake(monkeypatch, liveness, last_written=(), fail=False, asked=None):
    def query(src):
        if asked is not None:
            asked.append(src)
        if 'union(tables' in src:
            return list(last_written)
        if '"http_latency"' in src:
            if fail:
                raise RuntimeError("flux error")
            return liveness
        if "count()" in src:
            return [{"_value": 30}]  # latency is being written
        return []

    monkeypatch.setattr(evaluator, "_query", query)
    monkeypatch.setattr(evaluator.time, "time", lambda: NOW)


def test_evaluate_reports_a_stopped_writer(monkeypatch):
    alive = [m for m in ALL if m != "tcp_latency"]
    _fake(monkeypatch, _count(*alive), [_newest("tcp_latency", 7200)])
    incidents, _ = evaluator.evaluate_with_context()
    assert [i["key"] for i in incidents] == ["measurement_stale:tcp_latency"]


def test_the_second_query_is_asked_only_on_suspicion(monkeypatch):
    asked = []
    _fake(monkeypatch, _count(*ALL), asked=asked)
    evaluator.evaluate_with_context()
    assert not any("union(tables" in q for q in asked)


def test_a_failed_liveness_query_costs_only_that_rule(monkeypatch):
    _fake(monkeypatch, [], fail=True)
    incidents, _ = evaluator.evaluate_with_context()
    assert incidents == []


def test_no_measurement_stale_while_exporter_stale_explains_it(monkeypatch):
    asked = []

    def query(src):
        asked.append(src)
        if "first()" in src:
            return [{"_value": 1}]  # it measured before: a stall
        return []

    monkeypatch.setattr(evaluator, "_query", query)
    incidents, _ = evaluator.evaluate_with_context()
    assert [i["key"] for i in incidents] == ["exporter_stale"]
    # The measurement_stale queries (by measurement), not the breadth's
    # (which names http_latency too).
    assert not any('group(columns: ["_measurement"])' in q for q in asked)


def test_a_failing_wifi_query_costs_only_the_wifi_evidence(monkeypatch):
    def query(src):
        if '"http_latency"' in src:
            return _count(*ALL)
        if 'r._measurement == "wifi_link"' in src and "difference(" not in src:
            raise RuntimeError("wifi flux error")
        if "cpe_latency" in src or "difference(" in src:
            return []
        if "count()" in src:
            return [{"_value": 30}]
        if "mean()" in src:
            return []
        return [{"target": "a", "_value": 1.0, "_time": _at(t)} for t in (60, 360, 660)]

    monkeypatch.setattr(evaluator, "_query", query)
    incidents, context = evaluator.evaluate_with_context()
    assert [i["key"] for i in incidents] == ["target_down:a"]
    assert context["wifi_rows"] == {"signal": [], "drops": [], "uplink": [], "rx": []}


# --- InfluxDB unreadable -----------------------------------------------------

def _loop(monkeypatch, state_file, readable):
    sent = []
    clock = {"now": NOW}

    def evaluate(**_):
        if not readable[0]:
            raise ConnectionError("influx down")
        return [], {"mean_rows": [], "micro_rows": [], "wifi_rows": {},
                    "uplink_changes": [], "windows": {"mean": 900, "step": 300},
                    "cadences": {}, "judged": {"google"}}

    monkeypatch.setattr(main.evaluator, "evaluate_with_context", evaluate)
    monkeypatch.setattr(main.notifier, "send",
                        lambda payload, image=None: sent.append(payload) or "sent")
    monkeypatch.setattr(main.notifier, "reachable", lambda: True)
    monkeypatch.setattr(main.time, "time", lambda: clock["now"])
    for module in (main.incident_history, main.digest, main.reports_watcher):
        for name in ("merge", "maybe_record", "check"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, lambda *a, **k: None)
    return sent, clock


def test_an_unreadable_influx_is_a_critical_and_holds_everything_else(
        monkeypatch, state_file):
    current = {"incidents": {}}
    down = {"rule": "target_down", "severity": "critical", "key": "target_down:google",
            "target": "google", "message": "google down"}
    state.reconcile(current, [down], now=NOW - 3600, judged={"google"})
    state.save_state(current)

    readable = [False]
    sent, clock = _loop(monkeypatch, state_file, readable)
    for minute in range(6):  # five minutes of failures: then it is news
        clock["now"] = NOW + 60 * minute
        main.run_iteration()
    alerts = [p for p in sent if p.get("type") == "alert"]
    assert [a["key"] for a in alerts] == ["influx_unreadable"]
    assert "cannot read InfluxDB" in alerts[0]["message"]
    assert "ConnectionError" in alerts[0]["message"]
    # Blind: the open target_down was neither recovered nor re-sent.
    for minute in range(6, 40):
        clock["now"] = NOW + 60 * minute
        main.run_iteration()
    assert [p.get("type") for p in sent] == ["alert"]
    assert "target_down:google" in state.load_state()["incidents"]

    # Readable again, and google answers: both recover, once each.
    readable[0] = True
    for minute in range(40, 80):
        clock["now"] = NOW + 60 * minute
        main.run_iteration()
    recovered = sorted(p["key"] for p in sent if p.get("type") == "recovery")
    assert recovered == ["influx_unreadable", "target_down:google"]


def test_a_brief_influx_hiccup_says_nothing(monkeypatch, state_file):
    readable = [False]
    sent, clock = _loop(monkeypatch, state_file, readable)
    for minute in range(4):
        clock["now"] = NOW + 60 * minute
        main.run_iteration()
    readable[0] = True
    for minute in range(4, 8):
        clock["now"] = NOW + 60 * minute
        main.run_iteration()
    assert sent == []
    assert "unreadable_since" not in state.load_state().get("monitor", {})


def test_a_flapping_influx_still_alerts(monkeypatch, state_file):
    """Review of #334: one good read among timeouts must not reset the
    clock, or a database failing every other cycle never alerts."""
    readable = [False]
    sent, clock = _loop(monkeypatch, state_file, readable)
    for minute in range(12):
        readable[0] = minute % 2 == 1
        clock["now"] = NOW + 60 * minute
        main.run_iteration()
    assert [p["key"] for p in sent if p.get("type") == "alert"] == ["influx_unreadable"]


def test_a_bug_is_not_reported_as_the_database(monkeypatch, state_file):
    readable = [True]
    sent, clock = _loop(monkeypatch, state_file, readable)

    def broken(**_):
        raise KeyError("windows")

    monkeypatch.setattr(main.evaluator, "evaluate_with_context", broken)
    for minute in range(6):
        clock["now"] = NOW + 60 * minute
        main.run_iteration()
    [alert] = [p for p in sent if p.get("type") == "alert"]
    assert alert["key"] == "evaluation_failed"
    assert "InfluxDB" not in alert["message"]


def test_no_false_stall_right_after_the_database_comes_back(monkeypatch, state_file):
    """Review of #334: the writers could not write while InfluxDB was down;
    the empty window that leaves is the outage already reported."""
    readable = [False]
    sent, clock = _loop(monkeypatch, state_file, readable)
    stall = {"rule": "exporter_stale", "severity": "critical",
             "key": "exporter_stale", "target": None, "message": "stalled"}
    good = main.evaluator.evaluate_with_context

    def evaluate(**kw):
        incidents, context = good(**kw)
        return incidents + [stall], {**context, "windows": {**context["windows"],
                                                            "stale": 1200}}

    for minute in range(30):
        clock["now"] = NOW + 60 * minute
        main.run_iteration()
    readable[0] = True
    monkeypatch.setattr(main.evaluator, "evaluate_with_context", evaluate)
    for minute in range(30, 50):  # inside one stale window after it clears
        clock["now"] = NOW + 60 * minute
        main.run_iteration()
    assert "exporter_stale" not in [p["key"] for p in sent]
    for minute in range(55, 60):  # past it: a stall is a stall again
        clock["now"] = NOW + 60 * minute
        main.run_iteration()
    assert "exporter_stale" in [p["key"] for p in sent]


def test_a_failing_verdict_costs_its_line_not_the_alert(monkeypatch, state_file):
    incident = {"key": "target_down:google", "rule": "target_down",
                "severity": "critical", "target": "google", "message": "down"}
    context = {"mean_rows": [], "micro_rows": [], "wifi_rows": {},
               "uplink_changes": [], "windows": {"mean": 900, "step": 300},
               "cadences": {}, "judged": {"google"}}
    sent = []
    monkeypatch.setattr(main.evaluator, "evaluate_with_context",
                        lambda **_: ([incident], context))

    def broken(*a, **k):
        raise KeyError("boom")

    monkeypatch.setattr(main.verdict, "classify", broken)
    monkeypatch.setattr(main.notifier, "send",
                        lambda payload, image=None: sent.append(payload) or "sent")
    monkeypatch.setattr(main, "_chart_for", lambda event, peers: None)
    main.run_iteration()
    assert [p["key"] for p in sent if p.get("type") == "alert"] == ["target_down:google"]
