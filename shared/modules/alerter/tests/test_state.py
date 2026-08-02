"""Incident state lifecycle: dedup, cooldown, recovery, atomic persistence."""

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


def test_new_incident_notifies(state_file):
    st = state.load_state()
    actions = state.reconcile(st, [_incident()], now=1000.0)
    assert len(actions["alerts"]) == 1
    assert actions["recoveries"] == []
    record = st["incidents"]["target_down:google"]
    assert record["first_seen"] == 1000.0
    assert record["last_notified"] == 1000.0
    assert record["notified_count"] == 1


def test_active_within_cooldown_is_silent(state_file):
    st = state.load_state()
    state.reconcile(st, [_incident()], now=1000.0)
    actions = state.reconcile(st, [_incident()], now=1000.0 + 100)
    assert actions["alerts"] == []
    record = st["incidents"]["target_down:google"]
    assert record["last_seen"] == 1100.0
    assert record["notified_count"] == 1


def test_active_after_cooldown_renotifies(state_file):
    st = state.load_state()
    state.reconcile(st, [_incident()], now=1000.0)
    actions = state.reconcile(st, [_incident()], now=1000.0 + 3600)
    assert len(actions["alerts"]) == 1
    record = st["incidents"]["target_down:google"]
    assert record["notified_count"] == 2
    assert record["last_notified"] == 4600.0


def test_cooldown_env_tunable(state_file, monkeypatch):
    monkeypatch.setenv("ALERT_COOLDOWN", "60")
    st = state.load_state()
    state.reconcile(st, [_incident()], now=1000.0)
    actions = state.reconcile(st, [_incident()], now=1061.0)
    assert len(actions["alerts"]) == 1


def test_cleared_incident_sends_recovery_once(state_file):
    st = state.load_state()
    state.reconcile(st, [_incident()], now=1000.0)
    actions = state.reconcile(st, [], now=1200.0)
    assert actions["alerts"] == []
    assert len(actions["recoveries"]) == 1
    recovery = actions["recoveries"][0]
    assert recovery["key"] == "target_down:google"
    assert recovery["state"]["cleared_at"] == 1200.0
    assert st["incidents"] == {}
    # Nothing left to recover on the next pass.
    assert state.reconcile(st, [], now=1300.0)["recoveries"] == []


def test_state_roundtrip_is_atomic_json(state_file):
    st = state.load_state()
    state.reconcile(st, [_incident()], now=1000.0)
    state.save_state(st)
    on_disk = json.loads(state_file.read_text())
    assert "target_down:google" in on_disk["incidents"]
    # No leftover temp files from the atomic write.
    leftovers = [p for p in state_file.parent.iterdir() if p != state_file]
    assert leftovers == []
    # Reload sees the same record.
    assert state.load_state()["incidents"]["target_down:google"]["notified_count"] == 1


def test_corrupt_state_file_starts_fresh(state_file):
    state_file.write_text("{not json")
    st = state.load_state()
    assert st == {"incidents": {}, "reports": {}}


def test_fallback_when_default_dir_unwritable(monkeypatch):
    monkeypatch.setattr(state, "_dir_writable", lambda path: False)
    assert state.state_file() == state.FALLBACK_STATE_FILE
