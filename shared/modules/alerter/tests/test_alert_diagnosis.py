"""An alert carries the diagnosis of the incident it is about.

diagnose_loss (common/diagnosis.py) saw more than the verdict -- the first
hop's usual loss, a deaf radio, the app layer -- but only an assistant asked
it. These tests pin which incident an alert gets, how it is rendered, and
that a failed diagnosis never costs the alert.
"""

from __future__ import annotations

import time

import pytest

import alert_diagnosis
import main
import templates

NOW = 1_800_000_000.0
STEP = 300


def _inc(cls, targets, end_ago_s, conf="high", **over):
    inc = {"class": cls, "detail": None, "confidence": conf, "targets": targets,
           "start_epoch": NOW - end_ago_s - 600, "end_epoch": NOW - end_ago_s,
           "evidence": ["the first hop cut out with 14 of 18 destinations",
                        "this host's Wi-Fi was receiving the whole time",
                        "a third", "a fourth"],
           "against": [], "minutes": 10, "summary": f"summary of {cls}"}
    inc.update(over)
    return inc


def _result(*incidents):
    return {"incidents": list(incidents)}


def test_an_alert_gets_the_incident_naming_its_target_by_site():
    res = _result(_inc("upstream", ["amazon_icmp"], 0),
                  _inc("destination", ["google_h2", "google_icmp"], 60))
    found = alert_diagnosis.for_event({"rule": "high_loss", "target": "google_icmp"},
                                      res, STEP, now=NOW)
    assert found["class"] == "destination"
    assert len(found["evidence"]) == 3  # what a message has room for


def test_a_rule_about_everything_gets_the_newest_broad_incident():
    res = _result(_inc("probe_miss", ["x"], 0),
                  _inc("destination", ["amazon"], 0),
                  _inc("unclear", [], 30),
                  _inc("local_wifi", [], 120, detail="deaf"),
                  _inc("local_link", [], 900))
    found = alert_diagnosis.for_event({"rule": "uplink_down"}, res, STEP, now=NOW)
    assert found["class"] == "local_wifi" and found["detail"] == "deaf"


def test_an_alert_about_one_target_never_gets_another_targets_incident():
    """The review's case: high_loss on B with no incident of its own must
    not borrow 'those destinations, not you' from A -- nor an upstream one
    that does not name B."""
    res = _result(_inc("destination", ["amazon"], 0), _inc("upstream", ["netflix"], 0))
    assert alert_diagnosis.for_event({"rule": "high_loss", "target": "google"},
                                     res, STEP, now=NOW) is None
    res = _result(_inc("upstream", ["google_icmp", "amazon"], 0))
    assert alert_diagnosis.for_event({"rule": "high_loss", "target": "google"},
                                     res, STEP, now=NOW)["class"] == "upstream"


def test_a_microcut_burst_gets_a_cut_or_a_deaf_radio_not_a_destination():
    res = _result(_inc("destination", ["amazon"], 0),
                  _inc("local_link", [], 60, detail="microcut"))
    found = alert_diagnosis.for_event({"rule": "microcut_burst", "target": "CPE_IPv4"},
                                      res, STEP, now=NOW)
    assert found["class"] == "local_link" and found["detail"] == "microcut"
    res = _result(_inc("upstream", ["a", "b"], 0))
    assert alert_diagnosis.for_event({"rule": "microcut_burst", "target": "CPE_IPv4"},
                                     res, STEP, now=NOW) is None


def test_ipv6_down_gets_only_an_ipv6_incident():
    res = _result(_inc("local_link", [], 0))
    ev = {"rule": "ipv6_down", "target": "ipv6"}
    assert alert_diagnosis.for_event(ev, res, STEP, now=NOW) is None
    res = _result(_inc("destination", ["google6"], 0, detail="ipv6"))
    assert alert_diagnosis.for_event(ev, res, STEP, now=NOW)["detail"] == "ipv6"


def test_an_incident_counts_until_exactly_recent_steps_ago():
    edge = alert_diagnosis.RECENT_STEPS * STEP
    assert alert_diagnosis.for_event({"rule": "outage"}, _result(_inc("local_link", [], edge)),
                                     STEP, now=NOW) is not None
    assert alert_diagnosis.for_event({"rule": "outage"},
                                     _result(_inc("local_link", [], edge + 1)),
                                     STEP, now=NOW) is None


def test_only_loss_rules_are_diagnosed():
    res = _result(_inc("upstream", [], 0))
    assert alert_diagnosis.for_event({"rule": "exporter_stale"}, res, STEP, now=NOW) is None
    assert alert_diagnosis.for_event({"rule": "high_loss"}, None, STEP, now=NOW) is None


def test_a_probe_miss_explains_nothing():
    res = _result(_inc("probe_miss", ["google"], 0))
    assert alert_diagnosis.for_event({"rule": "high_loss", "target": "google"},
                                     res, STEP, now=NOW) is None
    assert alert_diagnosis.for_event({"rule": "outage"}, res, STEP, now=NOW) is None


def test_a_failed_diagnosis_is_none_and_logged(monkeypatch, caplog):
    def boom(flux):
        raise RuntimeError("influx down")
    monkeypatch.setattr(alert_diagnosis.flux, "query_influx", boom)
    assert alert_diagnosis.run({}) is None
    assert "sending the verdict alone" in caplog.text


def test_a_slow_diagnosis_is_abandoned_within_its_budget(monkeypatch, caplog):
    """Each Influx query may take a minute to time out on a struggling Pi;
    the alerts must not wait for that."""
    import threading
    release = threading.Event()

    def slow(flux):
        release.wait(5)
        return []
    monkeypatch.setattr(alert_diagnosis.flux, "query_influx", slow)
    started = time.monotonic()
    assert alert_diagnosis.run({}, budget_s=0.2) is None
    assert time.monotonic() - started < 2
    assert "longer than" in caplog.text
    # Still running: the next iteration does not start a second one.
    assert alert_diagnosis.run({}, budget_s=0.2) is None
    assert "still running" in caplog.text
    release.set()
    alert_diagnosis._pending.result(timeout=10)


@pytest.mark.parametrize("raw, hours", [("", 3), ("0", 0), ("6", 6), ("x", 3),
                                        ("9999", 168), ("-2", 0)])
def test_the_window_is_configurable_and_zero_turns_it_off(monkeypatch, raw, hours):
    monkeypatch.setenv("ALERT_DIAGNOSIS_HOURS", raw)
    assert alert_diagnosis.hours() == hours
    if hours == 0:
        assert alert_diagnosis.run({}) is None


# --- rendering ---------------------------------------------------------------


def _alert(diag=None):
    """An episode's alert (policy.py), with the last hours' diagnosis."""
    event = {"type": "alert", "rule": "episode", "kind": "degradation",
             "severity": "warning", "key": "episode:1",
             "message": "🟡 Internet degraded\nCause: your provider, beyond "
                        "the modem (high confidence)\nRange: since 19:30",
             "episode": {"key": "episode:1"}}
    if diag:
        event["diagnosis"] = diag
    return event


def _diag(conf="high", cls="upstream", against=()):
    return {"class": cls, "detail": "spread", "confidence": conf,
            "summary": "Beyond your line.",
            "evidence": ["most destinations at once"],
            "against": list(against), "minutes": 5}


def test_a_confident_diagnosis_rides_under_the_episode():
    text = templates.format_message(_alert(_diag()))
    assert "Cause: your provider, beyond the modem" in text
    assert "Diagnosis of the last hours: Beyond your line. (high confidence)" in text


def test_a_low_confidence_diagnosis_is_left_out():
    text = templates.format_message(_alert(_diag(conf="low", against=["no Wi-Fi data"])))
    assert "Diagnosis" not in text


def test_diagnosis_text_is_escaped():
    d = _diag()
    d["summary"] = "a<b> & c"
    text = templates.format_message(_alert(d))
    assert "a&lt;b&gt; &amp; c" in text


def test_the_loop_diagnoses_only_an_alert_that_goes_out(monkeypatch):
    """The diagnosis costs about ten queries: only for an alert of the
    Internet down or degraded, never for an end or the monitor's message."""
    import main

    calls = []
    monkeypatch.setattr(alert_diagnosis, "run",
                        lambda cadences, budget_s=None: calls.append(1) or None)
    monkeypatch.setattr(main, "_deliver", lambda current, payload, **kw: None)
    for kind, expected in (("degradation", 1), ("recovery", 0), ("monitor", 0)):
        calls.clear()
        monkeypatch.setattr(main.policy, "decide",
                            lambda state, events, now, kind=kind: [{"kind": kind}])
        main._push({}, [], 0.0)
        assert len(calls) == expected


def test_diagnosis_query_runs_on_fake_rows_and_survives_a_failing_optional_query():
    """common.diagnosis_query is what the alerter calls; the MCP tests cover
    its output in depth, this pins that it runs here, degrades on an
    optional failure, and reports coverage honestly."""
    from common import diagnosis_query

    seen = []

    def query(flux):
        seen.append(flux)
        if "wifi_link" in flux:
            raise RuntimeError("wifi query failed")
        if 'r._measurement == "latency"' in flux and "r._value >=" in flux:
            return [{"target": "google", "category": "topsites", "_value": 1.0,
                     "_time": NOW - 600},
                    {"target": "amazon", "category": "topsites", "_value": 1.0,
                     "_time": NOW - 600}]
        if 'distinct(column: "target") |> count()' in flux:
            return [{"_time": NOW - 600, "_value": 10}]
        return []
    result = diagnosis_query.run(query, 3, {})
    assert result["coverage"]["wifi"] is False
    assert result["targets_reporting"] == 10
    assert result["incidents"] and all("class" in i for i in result["incidents"])
    assert any("range(start: -3h)" in f for f in seen)


def test_internet_down_is_diagnosed_like_any_outage():
    assert "internet_down" in alert_diagnosis.LOSS_RULES
    assert "internet_down" in alert_diagnosis.UNTARGETED_RULES



def test_monitor_dns_gets_only_the_same_reading():
    now = 1_791_900_000.0
    result = {"incidents": [
        {"class": "upstream", "detail": "outage", "end_epoch": now, "confidence": "high",
         "summary": "Beyond", "targets": ["a"]},
        {"class": "monitor", "detail": "names", "end_epoch": now - 60,
         "confidence": "high", "summary": "Own DNS", "targets": ["a"]},
    ]}
    found = alert_diagnosis.for_event({"rule": "monitor_dns", "target": None},
                                      result, 300, now=now)
    assert found["class"] == "monitor"
