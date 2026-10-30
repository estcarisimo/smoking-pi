"""The pieces of the pipeline, one at a time: the materiality gate, the
episode classifier, the monitor's blind spans, and the policy's updates.
The whole cycle is test_october.py and test_sequences.py."""

from datetime import datetime, timezone

import pytest

import episodes
import policy
from common import definitions, diagnosis

MATERIAL = dict(measurable=True, breadth_pct=70.0, categories=3, minutes=15,
                episodes_in_window=1, loss_pct=6.7)


# --- materiality (common.definitions) ---------------------------------------

def test_an_episode_meeting_all_five_conditions_is_material():
    assert definitions.material(**MATERIAL)
    assert definitions.materiality(**MATERIAL) == []


@pytest.mark.parametrize("change, unmet", [
    ({"measurable": False}, "measurable"),
    ({"breadth_pct": 29.9, "categories": 1}, "breadth"),
    ({"minutes": 10}, "persistence"),
    ({"loss_pct": 4.9}, "magnitude"),
    ({"loss_pct": None}, "magnitude"),
    ({"novel": False}, "novelty"),
])
def test_each_condition_is_needed(change, unmet):
    assert definitions.materiality(**{**MATERIAL, **change}) == [unmet]


def test_each_condition_has_its_alternative():
    # Two categories instead of 30% of the destinations.
    assert definitions.material(**{**MATERIAL, "breadth_pct": 10.0, "categories": 2})
    # Two episodes in the hour instead of three cycles in a row.
    assert definitions.material(**{**MATERIAL, "minutes": 5, "episodes_in_window": 2})
    # Latency twice last week's instead of 5% loss.
    assert definitions.material(**{**MATERIAL, "loss_pct": 0.0, "latency_ratio": 2.0})


def test_the_thresholds_are_the_decided_ones():
    assert definitions.MATERIAL_BREADTH_PCT == 30.0
    assert definitions.MATERIAL_MIN_CATEGORIES == 2
    assert definitions.MATERIAL_MIN_MINUTES == 15
    assert definitions.MATERIAL_MIN_EPISODES == 2
    assert definitions.MATERIAL_EPISODES_WINDOW_S == 3600
    assert definitions.MATERIAL_LOSS_PCT == 5.0
    assert definitions.MATERIAL_LATENCY_RATIO == 2.0
    assert definitions.MONITOR_BLIND_MIN_MINUTES == 15
    assert definitions.MICROCUT_MAX_S == 60


# --- classification (common.diagnosis.classify_episode) ---------------------

def _facts(**over):
    facts = {"blind": {}, "kinds": [], "monitor_kinds": [], "lossy_steps": 0,
             "total_steps": 0, "first_hop": None, "line_cut": False,
             "wifi": "receiving", "breadth_pct": 0.0, "categories": 0,
             "sites": [], "reporting": 23}
    facts.update(over)
    return facts


def test_all_blind_is_the_monitor_never_the_internet():
    """The audit: a deaf radio the watchdog reloaded read "not a clear
    pattern"; the Pi losing its route read "the line to the ISP"."""
    radio = diagnosis.classify_episode(_facts(blind={"radio": 900, "watchdog": 300}))
    assert (radio["class"], radio["cause"]) == ("monitor", "this Pi's radio (monitor blind)")
    route = diagnosis.classify_episode(_facts(blind={"route": 30}))
    assert route["cause"] == "this Pi (it lost its own default route)"
    database = diagnosis.classify_episode(_facts(monitor_kinds=["influx_unreadable"]))
    assert database["cause"] == "this Pi's monitor (its database could not be read)"


def test_total_loss_with_the_first_hop_lost_is_your_line():
    found = diagnosis.classify_episode(_facts(kinds=["uplink_down"], total_steps=3,
                                              lossy_steps=3, first_hop="lost"))
    assert (found["class"], found["confidence"]) == ("local_link", "high")


def test_total_loss_with_the_first_hop_answering_is_beyond_the_modem():
    found = diagnosis.classify_episode(_facts(kinds=["internet_down"], total_steps=3,
                                              first_hop="answers"))
    assert found["cause"] == "your provider, beyond the modem"


def test_without_the_first_hop_the_cause_is_undetermined_and_says_so():
    found = diagnosis.classify_episode(_facts(kinds=["uplink_down"], total_steps=3))
    assert (found["class"], found["cause"]) == ("undetermined", "undetermined")
    assert found["suspected"] == "local_link"


def test_a_cut_of_the_first_hop_with_12_destinations_is_your_line():
    found = diagnosis.classify_episode(_facts(kinds=["microcut_burst", "degradation"],
                                              line_cut=True, lossy_steps=1,
                                              breadth_pct=52.0, categories=2))
    assert found["class"] == "local_link" and found["confidence"] == "high"


def test_broad_loss_with_a_clean_first_hop_is_beyond_the_modem():
    found = diagnosis.classify_episode(_facts(kinds=["degradation"], lossy_steps=5,
                                              breadth_pct=70.0, categories=3,
                                              first_hop="answers"))
    assert (found["class"], found["confidence"]) == ("upstream", "high")


def test_one_destination_is_just_that_site():
    found = diagnosis.classify_episode(_facts(kinds=["high_loss"], sites=["UBA"]))
    assert (found["class"], found["cause"]) == ("destination", "just UBA")


def test_the_monitors_own_dns():
    found = diagnosis.classify_episode(_facts(kinds=["monitor_dns"], total_steps=3))
    assert found["cause"] == "this Pi's own DNS"


# --- blind spans ------------------------------------------------------------

def _t(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def test_deaf_samples_fold_into_spans():
    rows = [{"_time": _t(t)} for t in (100, 110, 120, 200, 210)]
    assert episodes.deaf_spans(rows) == [[90, 120, "radio"], [190, 210, "radio"]]


def test_a_lost_route_runs_until_it_is_back_or_now():
    changes = [{"time": "2026-10-09T21:21:00+00:00", "interface": "", "previous": "wlan0"},
               {"time": "2026-10-09T21:21:30+00:00", "interface": "wlan0",
                "previous": "none"}]
    [[lo, hi, reason]] = episodes.route_spans(changes, now=0)
    assert (hi - lo, reason) == (30, "route")
    [[lo, hi, _]] = episodes.route_spans(changes[:1], now=lo + 100)
    assert hi - lo == 100


def test_an_upgrade_window_from_its_markers():
    rows = [{"_time": _t(1000), "phase": "start"}, {"_time": _t(1400), "phase": "end"},
            {"_time": _t(5000), "phase": "start"}]
    assert episodes.upgrade_spans(rows, now=5100) == [
        [1000, 1400, "upgrade"], [5000, 5100, "upgrade"]]


def test_only_the_watchdogs_actions_blind_the_monitor():
    signals = [{"rule": "watchdog", "at": 100.0, "severity": "info",
                "message": "reloading the brcmfmac driver"},
               {"rule": "watchdog", "at": 200.0, "severity": "info",
                "message": "wlan0 reaches the network again after 11 min"},
               {"rule": "watchdog", "at": 300.0, "severity": "warning",
                "message": "reloading brcmfmac failed"}]
    assert episodes.watchdog_spans(signals, 300) == [[100.0, 400.0, "watchdog"]]


# --- policy: one update per change ------------------------------------------

def _ep(**over):
    ep = {"key": "episode:1", "id": 1, "opened_at": 0.0, "start": 0.0, "end": 900.0,
          "material": True, "down": False, "microcut": False, "blind": [],
          "watchdog": [], "signals": {}, "loss_start": 0.0, "loss_end": 900.0,
          "judgment": {"class": "upstream", "cause": "your provider, beyond the modem",
                       "confidence": "high", "evidence": []},
          "facts": {"breadth_pct": 40.0, "categories": 2},
          "materiality": {"unmet": [], "loss_pct": 6.0}}
    ep.update(over)
    return ep


def _decide(state, ep, now):
    return [p["kind"] for p in policy.decide(state, [{"event": "active", "episode": ep}], now)]


def test_a_class_change_is_one_update_and_escalation_sounds():
    state, ep = {}, _ep()
    assert _decide(state, ep, 1000.0) == ["degradation"]
    assert _decide(state, ep, 1060.0) == []
    ep["down"] = True
    ep["judgment"] = {**ep["judgment"], "class": "local_link", "cause": "your line"}
    payloads = policy.decide(state, [{"event": "active", "episode": ep}], 1120.0)
    assert [(p["kind"], p["silent"]) for p in payloads] == [("update", False)]
    assert "now the Internet is down" in payloads[0]["message"]
    assert _decide(state, ep, 1180.0) == []


def test_a_breadth_jump_is_one_silent_update_and_updates_are_capped():
    state, ep = {}, _ep()
    _decide(state, ep, 1000.0)
    ep["facts"] = {"breadth_pct": 75.0, "categories": 3}
    payloads = policy.decide(state, [{"event": "active", "episode": ep}], 1060.0)
    assert [(p["kind"], p["silent"]) for p in payloads] == [("update", True)]
    ep["judgment"] = {**ep["judgment"], "class": "local_link", "cause": "your line"}
    assert _decide(state, ep, 1120.0) == ["update"]
    ep["judgment"] = {**ep["judgment"], "class": "undetermined", "cause": "undetermined"}
    assert _decide(state, ep, 1180.0) == []  # MAX_UPDATES


def test_the_end_corrects_the_first_cause():
    state, ep = {}, _ep()
    _decide(state, ep, 1000.0)
    ep["judgment"] = {**ep["judgment"], "class": "local_link",
                      "cause": "your line (the modem or the line to the ISP)"}
    ep["policy"]["updates"] = policy.MAX_UPDATES  # no update on the way
    [payload] = policy.decide(state, [{"event": "closed", "episode": ep}], 2000.0)
    assert payload["kind"] == "recovery" and payload["silent"]
    assert "corrects the first message (your provider, beyond the modem)" in payload["message"]


def test_the_ceiling_caps_alerts_across_episodes(monkeypatch):
    monkeypatch.setenv("ALERT_MAX_PER_HOUR", "2")
    state = {}
    sent = [_decide(state, _ep(key=f"episode:{i}"), 1000.0 + i) for i in range(4)]
    assert sent == [["degradation"], ["degradation"], [], []]


def test_a_monitor_episode_says_nothing_while_open():
    ep = _ep(judgment={"class": "monitor", "cause": "this Pi's radio (monitor blind)"},
             material=False, down=False)
    assert _decide({}, ep, 1000.0) == []
