"""Mutes keep a signal out of every episode, or a whole episode unsaid.

Muting is the one feature that can *cause* a missed outage, so most of these
tests are about what a mute must NOT do: widen past what it names, outlive
the occurrence an acknowledgement covered, or stop alerts when the mute file
itself is broken. Since front 8 a mute applies to the signals
(episodes.signals) -- a muted signal neither opens nor extends an episode --
and, by ``episode:<id>``, to one episode (policy.decide).
"""

import episodes
import policy
import state


def _incident(key="target_down:google", **overrides):
    incident = {
        "rule": "target_down",
        "severity": "critical",
        "key": key,
        "target": "google",
        "message": "google down",
        "value": 100.0,
        "start": 900.0,
        "end": 1000.0,
    }
    incident.update(overrides)
    return incident


def _mute(**overrides):
    entry = {"target": "google", "rule": None, "until": 9_999_999_999.0}
    entry.update(overrides)
    return entry


def _kept(incidents, now=1000.0, records=None):
    return [s["key"] for s in episodes.signals(incidents, {}, now, records=records)]


# --- what a mute covers -----------------------------------------------------

def test_a_muted_signal_is_dropped(mutes_file):
    mutes_file([_mute()])
    assert _kept([_incident()]) == []


def test_an_unrelated_mute_does_not_suppress(mutes_file):
    mutes_file([_mute(target="amazon")])
    assert _kept([_incident()]) == ["target_down:google"]


def test_target_and_rule_must_both_match(mutes_file):
    """A target+rule mute silences one noisy rule, not the target entirely."""
    mutes_file([_mute(target="google", rule="high_loss")])
    assert _kept([_incident()]) == ["target_down:google"]


def test_wildcard_mutes_everything(mutes_file):
    mutes_file([_mute(target="*")])
    assert _kept([_incident()]) == []


def test_an_expired_mute_stops_suppressing(mutes_file):
    mutes_file([_mute(until=500.0)])
    assert _kept([_incident()]) == ["target_down:google"]


def test_an_entry_with_no_filters_matches_nothing(mutes_file):
    mutes_file([{"until": 9_999_999_999.0}])
    assert _kept([_incident()]) == ["target_down:google"]


def test_an_entry_with_no_until_is_not_eternal(mutes_file):
    mutes_file([{"target": "google"}])
    assert _kept([_incident()]) == ["target_down:google"]


def test_an_ack_matches_only_its_own_signal(mutes_file):
    mutes_file([{"key": "target_down:google", "until": 9_999_999_999.0,
                 "target": "*"}])
    assert _kept([_incident(), _incident(key="target_down:apple",
                                         target="apple")]) == ["target_down:apple"]


def test_an_ack_lasts_until_the_signal_ends(mutes_file):
    """ack_incident says "until it recovers": the ack covers the occurrence
    first seen before it was made; the next one under the same key is new."""
    st = {"incidents": {}}
    state.track(st, [_incident()], now=1000.0)
    mutes_file([{"key": "target_down:google", "clear_on_recovery": True,
                 "created_at": 1500.0, "until": 9_999_999_999.0}])
    assert _kept([_incident()], now=2000.0, records=st["incidents"]) == []
    later = {"target_down:google": {"first_seen": 9000.0}}
    assert _kept([_incident()], now=9000.0, records=later) == ["target_down:google"]


def test_an_ack_without_a_readable_creation_time_does_not_mute(mutes_file):
    """Review of #332: an ack that cannot say which occurrence it covers
    fails toward alerting -- noise, not silence."""
    records = {"target_down:google": {"first_seen": 1000.0}}
    for created in ({}, {"created_at": "yesterday"}):
        mutes_file([{"key": "target_down:google", "clear_on_recovery": True,
                     "until": 9_999_999_999.0, **created}])
        assert _kept([_incident()], records=records) == ["target_down:google"]


def test_an_episode_can_be_acknowledged_by_its_key(mutes_file):
    """``ack_incident("episode:7")``: recorded, never pushed, its end
    included."""
    mutes_file([{"key": "episode:7", "until": 9_999_999_999.0}])
    ep = {"key": "episode:7", "opened_at": 1000.0, "down": True,
          "judgment": {"class": "local_link", "cause": "your line"}}
    st = {"episodes": {"closed": []}}
    assert policy.decide(st, [{"event": "active", "episode": ep}], 2000.0) == []
    assert ep["policy"]["muted"] is True


# --- the mute file must never be able to stop alerts ------------------------

def test_a_corrupt_mutes_file_is_ignored(mutes_file):
    mutes_file.path.write_text("{not json at all", encoding="utf-8")
    assert _kept([_incident()]) == ["target_down:google"]


def test_a_missing_mutes_file_is_the_normal_case(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERT_MUTES_FILE", str(tmp_path / "nope.json"))
    assert _kept([_incident()]) == ["target_down:google"]


def test_a_mutes_file_that_is_not_a_list_is_ignored(mutes_file):
    mutes_file.path.write_text('{"mutes": "everything"}', encoding="utf-8")
    assert _kept([_incident()]) == ["target_down:google"]
