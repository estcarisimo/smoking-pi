"""Signal bookkeeping (state.track) and atomic persistence.

Nothing here notifies any more (front 8): these are the records the rules
read back between cycles and the MCP server shows as open. What is said is
episodes.py's and policy.py's (test_october.py, test_episodes.py).
"""

import json

import state


def _incident(key="target_down:google", **overrides):
    incident = {
        "rule": "target_down",
        "severity": "critical",
        "key": key,
        "target": "google",
        "message": "google down",
        "value": 100.0,
    }
    incident.update(overrides)
    return incident


def test_a_new_signal_is_recorded(state_file):
    st = state.load_state()
    assert state.track(st, [_incident()], now=1000.0) == []
    record = st["incidents"]["target_down:google"]
    assert record["first_seen"] == 1000.0
    assert record["last_seen"] == 1000.0
    assert "notified_count" not in record


def test_an_active_signal_refreshes_its_record(state_file):
    st = state.load_state()
    state.track(st, [_incident()], now=1000.0)
    state.track(st, [_incident(message="google still down")], now=1100.0)
    record = st["incidents"]["target_down:google"]
    assert record["first_seen"] == 1000.0
    assert record["last_seen"] == 1100.0
    assert record["message"] == "google still down"


def test_a_signal_ends_once_after_the_grace_period(state_file):
    st = state.load_state()
    state.track(st, [_incident()], now=1000.0)
    assert state.track(st, [], now=2000.0) == []          # grace starts
    assert st["incidents"]["target_down:google"]["missing_since"] == 2000.0
    assert state.track(st, [], now=2500.0) == []          # still in grace
    assert state.track(st, [], now=2900.0) == ["target_down:google"]
    assert state.track(st, [], now=3000.0) == []
    assert st["incidents"] == {}


def test_reappearing_within_grace_continues_the_record(state_file):
    st = state.load_state()
    state.track(st, [_incident()], now=1000.0)
    state.track(st, [], now=1100.0)
    state.track(st, [_incident()], now=1200.0)
    record = st["incidents"]["target_down:google"]
    assert "missing_since" not in record
    assert record["first_seen"] == 1000.0


def test_grace_period_is_configurable(state_file, monkeypatch):
    monkeypatch.setenv("ALERT_RESOLVE_AFTER", "60")
    st = state.load_state()
    state.track(st, [_incident()], now=1000.0)
    state.track(st, [], now=1100.0)
    assert state.track(st, [], now=1160.0) == ["target_down:google"]


def test_a_slow_step_floors_the_grace_period(state_file, monkeypatch):
    monkeypatch.setenv("ALERT_RESOLVE_AFTER", "60")
    st = state.load_state()
    state.track(st, [_incident()], now=1000.0, min_resolve_after=1800)
    state.track(st, [], now=1100.0, min_resolve_after=1800)
    assert state.track(st, [], now=1200.0, min_resolve_after=1800) == []
    assert state.track(st, [], now=2900.0, min_resolve_after=1800) == [
        "target_down:google"]


def test_state_roundtrip_is_atomic_json(state_file):
    st = state.load_state()
    state.track(st, [_incident()], now=1000.0)
    state.save_state(st)
    assert json.loads(state_file.read_text())["incidents"]["target_down:google"]
    assert not list(state_file.parent.glob(".alerter-state.*"))
    assert state.load_state()["incidents"] == st["incidents"]


def test_corrupt_state_file_starts_fresh(state_file):
    state_file.write_text("{not json")
    st = state.load_state()
    assert st == {"incidents": {}, "reports": {}}


def test_fallback_when_default_dir_unwritable(monkeypatch):
    monkeypatch.setattr(state, "_dir_writable", lambda path: False)
    assert state.state_file() == state.FALLBACK_STATE_FILE


def test_the_transient_flag_follows_the_latest_report_of_a_key(state_file):
    st = state.load_state()
    state.track(st, [_incident(transient=True)], now=1000.0)
    assert st["incidents"]["target_down:google"]["transient"] is True
    state.track(st, [_incident()], now=1060.0)
    assert "transient" not in st["incidents"]["target_down:google"]


def test_an_outage_keeps_its_start(state_file):
    """One key, ``outage``, whatever its age: the start rides as ``since``."""
    st = state.load_state()
    state.track(st, [_incident(key="outage", rule="outage", target=None,
                               since=900)], now=1000.0)
    assert st["incidents"]["outage"]["since"] == 900


def test_a_superseded_signal_is_dropped_at_once(state_file):
    st = state.load_state()
    warning = _incident(key="high_loss:google", rule="high_loss", severity="warning")
    state.track(st, [warning], now=1000.0)
    state.track(st, [_incident(supersedes=["high_loss:google"])], now=2000.0)
    assert "high_loss:google" not in st["incidents"]


def test_a_quiet_signal_is_never_recorded(state_file):
    st = state.load_state()
    tail = _incident(key="outage", rule="outage", target=None, quiet=True)
    assert state.track(st, [tail], now=1000.0) == []
    assert st["incidents"] == {}


def test_a_quiet_tail_delays_the_end_it_continues(state_file, monkeypatch):
    monkeypatch.setenv("ALERT_RESOLVE_AFTER", "100")
    st = state.load_state()
    up = _incident(key="uplink_down", rule="uplink_down", target=None)
    state.track(st, [up], now=1000.0)
    tail = _incident(key="outage", rule="outage", target=None, quiet=True,
                     continues="uplink_down")
    state.track(st, [tail], now=1100.0)
    assert state.track(st, [tail], now=1300.0) == []
    assert state.track(st, [], now=1400.0) == ["uplink_down"]


def test_an_absent_signal_with_no_data_is_held_not_ended(state_file, monkeypatch):
    monkeypatch.setenv("ALERT_RESOLVE_AFTER", "100")
    st = state.load_state()
    state.track(st, [_incident()], now=1000.0, judged={"google"})
    for now in (1100.0, 2000.0, 5000.0):
        assert state.track(st, [], now=now, judged={"apple"}) == []
    record = st["incidents"]["target_down:google"]
    assert record["held_since"] == 1100.0 and "missing_since" not in record
    state.track(st, [], now=6000.0, judged={"google"})
    assert state.track(st, [], now=6200.0, judged={"google"}) == ["target_down:google"]


def test_an_aggregate_signal_is_held_only_when_nothing_has_data(state_file,
                                                                monkeypatch):
    monkeypatch.setenv("ALERT_RESOLVE_AFTER", "100")
    st = state.load_state()
    up = _incident(key="uplink_down", rule="uplink_down", target=None)
    state.track(st, [up], now=1000.0, judged={"google"})
    state.track(st, [], now=1100.0, judged=set())
    assert state.track(st, [], now=1300.0, judged=set()) == []
    state.track(st, [], now=1400.0, judged={"google"})
    assert state.track(st, [], now=1600.0, judged={"google"}) == ["uplink_down"]


def test_a_hold_keeps_the_grace_period_data_had_already_started(state_file,
                                                                monkeypatch):
    monkeypatch.setenv("ALERT_RESOLVE_AFTER", "100")
    st = state.load_state()
    state.track(st, [_incident()], now=1000.0, judged={"google"})
    state.track(st, [], now=1050.0, judged={"google"})      # grace starts
    for now in (1100.0, 5000.0):                             # no data
        assert state.track(st, [], now=now, judged=set()) == []
    assert state.track(st, [], now=5060.0, judged={"google"}) == ["target_down:google"]


def test_a_blind_spell_restarts_the_grace_period(state_file, monkeypatch):
    monkeypatch.setenv("ALERT_RESOLVE_AFTER", "100")
    st = state.load_state()
    state.track(st, [_incident()], now=1000.0, judged={"google"})
    state.track(st, [], now=1050.0, judged={"google"})       # grace starts
    state.track(st, [], now=1100.0, blind=True)              # blind
    assert state.track(st, [], now=5000.0, judged={"google"}) == []
    assert state.track(st, [], now=5200.0, judged={"google"}) == ["target_down:google"]


def test_a_signal_with_no_data_for_a_day_is_dropped(state_file):
    st = state.load_state()
    state.track(st, [_incident()], now=0.0, judged={"google"})
    state.track(st, [], now=60.0, judged=set())
    state.track(st, [], now=60.0 + state.HOLD_MAX_S, judged=set())
    assert st["incidents"] == {}


def test_every_total_loss_is_held_when_nothing_has_data(state_file, monkeypatch):
    monkeypatch.setenv("ALERT_RESOLVE_AFTER", "100")
    for rule in ("internet_down", "monitor_dns"):
        st = state.load_state()
        state.track(st, [_incident(key=rule, rule=rule, target=None)],
                    now=1000.0, judged={"google"})
        for now in (1100.0, 1300.0, 5000.0):
            assert state.track(st, [], now=now, judged=set()) == []
        assert rule in st["incidents"]
