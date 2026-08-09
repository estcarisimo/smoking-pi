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

    # Absent, but inside the grace period: nothing yet, record retained.
    actions = state.reconcile(st, [], now=1200.0)
    assert actions == {"alerts": [], "recoveries": []}
    assert st["incidents"]["target_down:google"]["missing_since"] == 1200.0

    # Grace elapsed (default ALERT_RESOLVE_AFTER=900).
    actions = state.reconcile(st, [], now=1200.0 + 900)
    assert actions["alerts"] == []
    assert len(actions["recoveries"]) == 1
    recovery = actions["recoveries"][0]
    assert recovery["key"] == "target_down:google"
    assert recovery["state"]["cleared_at"] == 2100.0
    assert st["incidents"] == {}
    # Nothing left to recover on the next pass.
    assert state.reconcile(st, [], now=2200.0)["recoveries"] == []


# ---------------------------------------------------------------------------
# Flap damping
#
# A target_down incident oscillated on a five-minute cycle because its window
# held exactly the minimum number of points. Recovery deleted the record, so
# the next appearance took the first-seen path and alerted immediately, which
# meant ALERT_COOLDOWN never applied. It sent ~50 messages an hour to a real
# phone for four hours.
# ---------------------------------------------------------------------------


def test_flapping_incident_notifies_once_not_per_cycle(state_file):
    """The regression: alert/recovery/alert/recovery, forever."""
    st = state.load_state()
    sent = 0
    now = 1000.0
    # Five minutes present, one minute absent — the observed live pattern —
    # for two hours.
    for cycle in range(24):
        for _ in range(4):
            actions = state.reconcile(st, [_incident()], now=now)
            sent += len(actions["alerts"]) + len(actions["recoveries"])
            now += 60.0
        actions = state.reconcile(st, [], now=now)
        sent += len(actions["alerts"]) + len(actions["recoveries"])
        now += 60.0

    # Two hours at a 3600s cooldown: the initial alert plus at most one
    # re-notify per elapsed hour. The old code sent 4 per five-minute cycle.
    assert sent <= 3, f"flapping incident produced {sent} notifications"


def test_reappearing_within_grace_does_not_realert(state_file):
    st = state.load_state()
    state.reconcile(st, [_incident()], now=1000.0)
    state.reconcile(st, [], now=1060.0)  # drops out
    actions = state.reconcile(st, [_incident()], now=1120.0)  # comes back
    assert actions["alerts"] == []
    assert actions["recoveries"] == []
    assert "missing_since" not in st["incidents"]["target_down:google"]


def test_grace_period_is_configurable(state_file, monkeypatch):
    monkeypatch.setenv("ALERT_RESOLVE_AFTER", "60")
    st = state.load_state()
    state.reconcile(st, [_incident()], now=1000.0)
    assert state.reconcile(st, [], now=1030.0)["recoveries"] == []
    assert len(state.reconcile(st, [], now=1100.0)["recoveries"]) == 1


def test_recovery_is_immediate_when_grace_is_zero(state_file, monkeypatch):
    monkeypatch.setenv("ALERT_RESOLVE_AFTER", "0")
    st = state.load_state()
    state.reconcile(st, [_incident()], now=1000.0)
    # Still one pass to mark it missing, then it clears on the next.
    state.reconcile(st, [], now=1060.0)
    assert len(state.reconcile(st, [], now=1060.0)["recoveries"]) == 1


# ---------------------------------------------------------------------------
# Rate ceiling — independent backstop
# ---------------------------------------------------------------------------


def test_hourly_ceiling_caps_notifications(state_file, monkeypatch):
    """A ceiling that holds even if the lifecycle logic is wrong again."""
    monkeypatch.setenv("ALERT_COOLDOWN", "0")  # pathological: alert every pass
    monkeypatch.setenv("ALERT_MAX_PER_HOUR", "3")
    st = state.load_state()
    sent = 0
    for i in range(60):
        sent += len(state.reconcile(st, [_incident()], now=1000.0 + i * 60)["alerts"])
    assert sent == 3


def test_ceiling_window_rolls_forward(state_file, monkeypatch):
    monkeypatch.setenv("ALERT_COOLDOWN", "0")
    monkeypatch.setenv("ALERT_MAX_PER_HOUR", "2")
    st = state.load_state()
    for i in range(5):
        state.reconcile(st, [_incident()], now=1000.0 + i)
    # An hour later the budget is available again.
    assert len(state.reconcile(st, [_incident()], now=1000.0 + 3700)["alerts"]) == 1


def test_ceiling_can_be_disabled(state_file, monkeypatch):
    monkeypatch.setenv("ALERT_COOLDOWN", "0")
    monkeypatch.setenv("ALERT_MAX_PER_HOUR", "0")
    st = state.load_state()
    sent = sum(
        len(state.reconcile(st, [_incident()], now=1000.0 + i)["alerts"])
        for i in range(10)
    )
    assert sent == 10


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
