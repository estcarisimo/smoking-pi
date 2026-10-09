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


def _alert(diag=None, scope="isp_upstream"):
    event = {"type": "alert", "rule": "high_loss", "severity": "warning",
             "target": "google", "message": "google: mean loss 22% over 15m",
             "verdict": {"scope": scope, "line": "Not you: upstream.",
                         "affected": 12, "total": 16, "cpe_cutting": []}}
    if diag:
        event["diagnosis"] = diag
    return event


def _diag(conf="high", cls="local_wifi", against=()):
    return {"class": cls, "detail": "deaf", "confidence": conf,
            "summary": "This host's own Wi-Fi, not the line.",
            "evidence": ["this host's Wi-Fi heard nothing for 300 s"],
            "against": list(against), "minutes": 5}


def test_a_confident_diagnosis_replaces_the_verdict_line_and_shows_why():
    text = templates.format_message(_alert(_diag()))
    assert "📶 This host's own Wi-Fi, not the line." in text
    assert "(high confidence)" in text
    assert "Why: this host's Wi-Fi heard nothing for 300 s." in text
    assert "Not you: upstream." not in text


def test_when_the_diagnosis_leads_the_context_line_does_not_contradict_it():
    event = {**_alert(_diag()), "rule": "outage", "target": None,
             "breadth": {"affected": 12, "total": 16, "targets": []}}
    event["verdict"]["cpe_cutting"] = ["CPE_IPv4"]
    text = templates.format_message(event)
    assert "outage · 12 of 16 affected" in text
    assert "local link" not in text
    # Without a leading diagnosis the verdict's reading stays.
    assert "local link cutting out" in templates.format_message(
        {**event, "diagnosis": None})


def test_an_unclear_diagnosis_never_replaces_the_verdict():
    text = templates.format_message(_alert(_diag(cls="unclear")))
    assert "Not you: upstream." in text and "Why:" not in text


def test_the_docs_example_is_what_the_template_renders(monkeypatch):
    """docs/alerting.md shows a rendered alert; it must be one."""
    import re
    from pathlib import Path

    from common import diagnosis
    monkeypatch.setenv("ALERT_MARKUP", "plain")
    doc = (Path(__file__).resolve().parents[4] / "docs" / "alerting.md").read_text()
    block = re.search(r"### The diagnosis.*?```text\n(.*?)```", doc, re.S).group(1)
    event = {"type": "alert", "rule": "target_down", "severity": "critical",
             "target": "google", "message": "google: 100% loss across all 4 probes",
             "verdict": {"scope": "local_link", "line": "Your line.", "affected": 18,
                         "total": 18, "cpe_cutting": ["CPE_IPv4"]},
             "diagnosis": {"class": "local_wifi", "detail": "deaf", "confidence": "high",
                           "summary": diagnosis.summary({"class": "local_wifi",
                                                         "detail": "deaf"}),
                           "evidence": ["this host's Wi-Fi heard nothing for 312 s (deaf)",
                                        "the first-hop probe lost everything for "
                                        "exactly that span"],
                           "against": []}}
    assert templates.format_message(event).strip() == block.strip()


def test_under_any_budget_the_why_goes_before_the_summary():
    """The summary is priority 0, the evidence 2: no budget keeps the Why
    and drops the claim it supports."""
    full = templates.format_message(_alert(_diag()))
    dropped_why = False
    for limit in range(60, len(full) + 1):
        text = templates.format_message(_alert(_diag()), limit)
        if "Why:" in text:
            assert "This host's own Wi-Fi" in text
        elif "This host's own Wi-Fi" in text:
            dropped_why = True
    assert dropped_why  # some budget keeps the summary and drops the Why


def test_a_low_confidence_diagnosis_leaves_the_verdict():
    text = templates.format_message(_alert(_diag(conf="low", against=["no Wi-Fi data"])))
    assert "Not you: upstream." in text
    assert "Why:" not in text and "But:" not in text


def test_no_measurements_arriving_outranks_the_diagnosis():
    text = templates.format_message(_alert(_diag(), scope="monitoring"))
    assert "Not you: upstream." in text and "Why:" not in text


def test_the_diagnosis_line_survives_a_caption_budget():
    caption = templates.format_message(_alert(_diag()), templates.TG_CAPTION_LIMIT)
    assert "This host's own Wi-Fi" in caption
    tight = templates.format_message(_alert(_diag()), 160)
    assert "This host's own Wi-Fi" in tight


def test_diagnosis_text_is_escaped():
    d = _diag()
    d["summary"] = "a<b> & c"
    d["evidence"] = ["<script>"]
    text = templates.format_message(_alert(d))
    assert "<script>" not in text and "a&lt;b&gt; &amp; c" in text


# --- the loop ----------------------------------------------------------------


def test_the_loop_diagnoses_once_and_only_when_a_loss_alert_goes_out(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERT_STATE_FILE", str(tmp_path / "state.json"))
    sent, runs = [], []
    incident = {"key": "high_loss:google", "rule": "high_loss", "severity": "warning",
                "target": "google", "message": "google: loss"}
    context = {"mean_rows": [], "micro_rows": [], "wifi_rows": None,
               "uplink_changes": [], "windows": {"mean": 900, "step": STEP},
               "cadences": {}}
    monkeypatch.setattr(main.evaluator, "evaluate_with_context",
                        lambda open_keys=(): ([incident], context))
    monkeypatch.setattr(main.alert_diagnosis, "run",
                        lambda cadences: runs.append(cadences) or _result(
                            _inc("destination", ["google"], 0)))
    monkeypatch.setattr(main.notifier, "notify",
                        lambda payload, image=None: sent.append(payload) or True)
    monkeypatch.setattr(main, "_chart_for", lambda event, peers: None)
    monkeypatch.setattr(main.alert_diagnosis.time, "time", lambda: NOW)
    main.run_iteration()
    assert len(runs) == 1
    [payload] = [p for p in sent if p.get("type") == "alert"]
    assert payload["diagnosis"]["class"] == "destination"
    # The same incident again: cooldown, no alert, no diagnosis.
    main.run_iteration()
    assert len(runs) == 1


# --- the shared query path, from the alerter's side ----------------------------


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
