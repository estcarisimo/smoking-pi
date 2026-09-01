"""Mutes suppress the send without weakening anything else.

Muting is the one feature that can *cause* a missed outage, so most of these
tests are about what a mute must NOT do: consume notification budget, bypass
the rate limiter, reset an incident's lifecycle, produce a recovery for an
alert nobody saw, or stop alerts when the mute file itself is broken.
"""

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


def _mute(**overrides):
    entry = {"target": "google", "rule": None, "until": 9_999_999_999.0}
    entry.update(overrides)
    return entry


# --- suppression ------------------------------------------------------------

def test_a_muted_incident_does_not_alert(state_file, mutes_file):
    mutes_file([_mute()])
    st = state.load_state()
    actions = state.reconcile(st, [_incident()], now=1000.0)
    assert actions["alerts"] == []


def test_an_unrelated_mute_does_not_suppress(state_file, mutes_file):
    mutes_file([_mute(target="amazon")])
    st = state.load_state()
    actions = state.reconcile(st, [_incident()], now=1000.0)
    assert len(actions["alerts"]) == 1


def test_target_and_rule_must_both_match(state_file, mutes_file):
    """A target+rule mute silences one noisy rule, not the target entirely."""
    mutes_file([_mute(target="google", rule="high_loss")])
    st = state.load_state()
    actions = state.reconcile(st, [_incident()], now=1000.0)
    assert len(actions["alerts"]) == 1, "target_down should still alert"


def test_wildcard_mutes_everything(state_file, mutes_file):
    mutes_file([_mute(target="*")])
    st = state.load_state()
    actions = state.reconcile(st, [_incident()], now=1000.0)
    assert actions["alerts"] == []


def test_an_expired_mute_stops_suppressing(state_file, mutes_file):
    mutes_file([_mute(until=1500.0)])
    st = state.load_state()
    assert state.reconcile(st, [_incident()], now=1000.0)["alerts"] == []
    # Past `until`, and past the cooldown, so the only thing that changed is
    # the mute having lapsed.
    actions = state.reconcile(st, [_incident()], now=1000.0 + 4000)
    assert len(actions["alerts"]) == 1


def test_an_entry_with_no_filters_matches_nothing(state_file, mutes_file):
    """A malformed entry must fail toward alerting, never toward silence."""
    mutes_file([{"until": 9_999_999_999.0}])
    st = state.load_state()
    assert len(state.reconcile(st, [_incident()], now=1000.0)["alerts"]) == 1


def test_an_entry_with_no_until_is_not_eternal(state_file, mutes_file):
    mutes_file([{"target": "google"}])
    st = state.load_state()
    assert len(state.reconcile(st, [_incident()], now=1000.0)["alerts"]) == 1


# --- the mute must not weaken the rate limiter ------------------------------

def test_a_muted_alert_does_not_consume_budget(state_file, mutes_file,
                                               monkeypatch):
    """The reason the mute check sits AFTER _rate_limited(), not before.

    Budget counts what was actually sent. If a muted send consumed budget, a
    mute would drain the hour silently and the ceiling would be spent by the
    time anyone unmuted.
    """
    monkeypatch.setenv("ALERT_MAX_PER_HOUR", "2")
    mutes_file([_mute()])
    st = state.load_state()
    for i in range(5):
        state.reconcile(st, [_incident()], now=1000.0 + i * 4000)
    record = st["incidents"]["target_down:google"]
    assert record["notified_count"] == 0
    assert record["recent_notifications"] == []
    assert record["muted_suppressed_count"] == 5


def test_the_rate_limiter_is_consulted_before_the_mute(state_file, mutes_file,
                                                       monkeypatch):
    """Reintroduction guard for the ordering of the two checks.

    What actually depends on it is the *accounting*, not delivery. Either
    order suppresses the send, and the rolling-hour trim is idempotent for a
    given `now`, so an unmuted cycle re-trims correctly regardless. What does
    differ is `muted_suppressed_count`, which exists to answer "how much is
    this mute actually hiding?" in the digest and in list_alert_state. An
    alert the ceiling had already blocked was never the mute's to suppress,
    and counting it inflates the one number a user reads when deciding whether
    a mute has gone on too long.

    Swap the two checks in reconcile() and this fails.
    """
    monkeypatch.setenv("ALERT_MAX_PER_HOUR", "2")
    monkeypatch.setenv("ALERT_COOLDOWN", "0")
    st = state.load_state()
    # Spend the whole budget unmuted.
    state.reconcile(st, [_incident()], now=1000.0)
    state.reconcile(st, [_incident()], now=1010.0)
    record = st["incidents"]["target_down:google"]
    assert record["notified_count"] == 2

    # Now mute. This cycle is already over budget, so the ceiling stops it
    # first and the mute never gets the chance to claim the suppression.
    mutes_file([_mute()])
    state.reconcile(st, [_incident()], now=1020.0)
    assert record.get("muted_suppressed_count", 0) == 0, (
        "the ceiling blocked this one; the mute must not take credit for it"
    )
    assert len(record["recent_notifications"]) == 2


# --- lifecycle is untouched -------------------------------------------------

def test_a_muted_incident_still_tracks_lifecycle(state_file, mutes_file):
    """The mute skips the SEND, not the rest of the loop body.

    last_seen, the severity/message refresh and the missing_since clearing all
    have to keep running. Skipping them would leave the incident looking brand
    new when the mute lifts, sending it down the first-seen path and alerting
    immediately -- exactly the flapping behaviour the grace period fixed.
    """
    mutes_file([_mute()])
    st = state.load_state()
    state.reconcile(st, [_incident()], now=1000.0)
    state.reconcile(
        st,
        [_incident(message="google down harder", severity="warning")],
        now=1600.0,
    )
    record = st["incidents"]["target_down:google"]
    assert record["first_seen"] == 1000.0, "must not look brand new"
    assert record["last_seen"] == 1600.0
    assert record["message"] == "google down harder"
    assert record["severity"] == "warning"


def test_unmuting_alerts_once_not_in_a_burst(state_file, mutes_file,
                                             monkeypatch):
    """No catch-up: suppressed alerts are dropped, not queued.

    Cooldown is zeroed so every cycle really would have sent something —
    otherwise the cooldown, not the mute, is what the test measures.
    """
    monkeypatch.setenv("ALERT_COOLDOWN", "0")
    mutes_file([_mute(until=5000.0)])
    st = state.load_state()
    for i in range(6):
        state.reconcile(st, [_incident()], now=1000.0 + i * 500)
    assert st["incidents"]["target_down:google"]["muted_suppressed_count"] == 6

    actions = state.reconcile(st, [_incident()], now=6000.0)
    assert len(actions["alerts"]) == 1, "exactly one alert, not six"


def test_an_incident_muted_from_first_sight_produces_no_recovery(
        state_file, mutes_file, monkeypatch):
    """Never announce the end of something nobody was told had started.

    This falls out of notified_count == 0 and the recovery branch already
    gating on it -- pinned here so a refactor cannot quietly lose it.
    """
    monkeypatch.setenv("ALERT_RESOLVE_AFTER", "100")
    mutes_file([_mute()])
    st = state.load_state()
    assert state.reconcile(st, [_incident()], now=1000.0)["alerts"] == []
    state.reconcile(st, [], now=1100.0)          # starts the grace period
    actions = state.reconcile(st, [], now=1300.0)  # grace elapsed
    assert actions["recoveries"] == []
    assert "target_down:google" not in st["incidents"]


def test_a_recovery_still_fires_for_an_alert_that_was_seen(
        state_file, mutes_file, monkeypatch):
    """Muting after the fact must not swallow the all-clear."""
    monkeypatch.setenv("ALERT_RESOLVE_AFTER", "100")
    st = state.load_state()
    assert len(state.reconcile(st, [_incident()], now=1000.0)["alerts"]) == 1
    mutes_file([_mute()])
    state.reconcile(st, [], now=1100.0)
    actions = state.reconcile(st, [], now=1300.0)
    assert len(actions["recoveries"]) == 1


# --- acks -------------------------------------------------------------------

def test_an_ack_matches_only_its_own_incident(state_file, mutes_file):
    """A key-scoped entry must never widen into a category-wide silence."""
    mutes_file([{"key": "target_down:google", "clear_on_recovery": True,
                 "until": 9_999_999_999.0}])
    st = state.load_state()
    actions = state.reconcile(
        st,
        [_incident(), _incident(key="high_loss:google", rule="high_loss")],
        now=1000.0,
    )
    keys = [a["key"] for a in actions["alerts"]]
    assert keys == ["high_loss:google"]


# --- the mute file must never be able to stop alerts ------------------------

def test_a_corrupt_mutes_file_is_ignored(state_file, mutes_file):
    """Delivery must not depend on this file being parseable."""
    mutes_file.path.write_text("{not json at all", encoding="utf-8")
    st = state.load_state()
    assert len(state.reconcile(st, [_incident()], now=1000.0)["alerts"]) == 1


def test_a_missing_mutes_file_is_the_normal_case(state_file, monkeypatch,
                                                 tmp_path):
    monkeypatch.setenv("ALERT_MUTES_FILE", str(tmp_path / "nope.json"))
    st = state.load_state()
    assert len(state.reconcile(st, [_incident()], now=1000.0)["alerts"]) == 1


def test_a_mutes_file_that_is_not_a_list_is_ignored(state_file, mutes_file):
    mutes_file.path.write_text('{"mutes": "everything"}', encoding="utf-8")
    st = state.load_state()
    assert len(state.reconcile(st, [_incident()], now=1000.0)["alerts"]) == 1
