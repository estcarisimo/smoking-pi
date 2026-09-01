"""Tests for the daily digest.

The behaviour that matters most is negative: a digest must never report a
healthy network from an absence of data. Everything else — the wording, the
chart, the links — is worth less than that one property, because a cheerful
08:30 message is exactly how a broken monitor stays undetected.
"""

from __future__ import annotations

import pytest

import digest
import schedule


@pytest.fixture(autouse=True)
def digest_env(monkeypatch):
    for var in (
        "DIGEST_ENABLED", "DIGEST_AT", "DIGEST_TZ", "DIGEST_WINDOW_HOURS",
        "DIGEST_MAX_LATENESS", "DIGEST_SILENT", "DIGEST_HISTORY_MAX",
        "ALERT_CHARTS", "TZ", "HIGH_LOSS_PCT",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DIGEST_ENABLED", "true")
    monkeypatch.setenv("DIGEST_AT", "08:30")
    monkeypatch.setenv("DIGEST_TZ", "UTC")
    # Charts need matplotlib; these tests are about the decision logic.
    monkeypatch.setenv("ALERT_CHARTS", "false")


def _at(hour=8, minute=30, day=30):
    from datetime import datetime, timezone
    return datetime(2026, 8, day, hour, minute, tzinfo=timezone.utc).timestamp()


def _targets(*specs):
    return [
        {"target": name, "measurement": "latency", "avg_loss_pct": loss,
         "median_ms": 10.0, "p95_ms": 20.0}
        for name, loss in specs
    ]


@pytest.fixture
def collected(monkeypatch):
    """Patch aggregates.collect, the one InfluxDB call the digest makes."""
    box = {"value": {"window_hours": 24, "generated_at": "2026-08-30T08:30:00",
                     "target_total": 2, "targets": _targets(("A", 0.0), ("B", 0.0)),
                     "cpe": []}}

    def fake_collect(hours=24):
        value = box["value"]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(digest.aggregates, "collect", fake_collect)
    return box


@pytest.fixture
def sent(monkeypatch):
    """Capture notifier.notify calls; control success."""
    calls = []
    box = {"ok": True}

    def fake_notify(event, image=None):
        calls.append({"event": event, "image": image})
        return box["ok"]

    monkeypatch.setattr(digest.notifier, "notify", fake_notify)
    return calls, box


# ---------------------------------------------------------------------------
# The property that matters: never claim health you did not verify
# ---------------------------------------------------------------------------


def test_influx_failure_sends_nothing(collected, sent):
    calls, _ = sent
    collected["value"] = RuntimeError("InfluxDB unreachable")
    state = {}
    assert digest.check(state, now=_at()) is False
    assert calls == [], "a digest was sent despite having no data"


def test_zero_targets_sends_nothing(collected, sent):
    """An empty result is indistinguishable from a healthy one. Say nothing."""
    calls, _ = sent
    collected["value"] = {**collected["value"], "targets": [], "target_total": 0}
    assert digest.check({}, now=_at()) is False
    assert calls == []


def test_a_failed_build_still_retires_the_slot(collected, sent):
    """Otherwise every tick for four hours re-queries a dead InfluxDB."""
    calls, _ = sent
    collected["value"] = RuntimeError("down")
    state = {}
    digest.check(state, now=_at())
    assert state["digest"]["last_fired_slot"] == _at()
    assert state["digest"]["last_error"] == "no data"
    # And the next tick does not try again.
    digest.check(state, now=_at() + 60)
    assert calls == []


def test_build_returns_none_rather_than_raising(collected):
    collected["value"] = RuntimeError("boom")
    assert digest.build({}, now=_at()) is None


# ---------------------------------------------------------------------------
# Firing once
# ---------------------------------------------------------------------------


def test_fires_at_the_slot_and_records_it(collected, sent):
    calls, _ = sent
    state = {}
    assert digest.check(state, now=_at()) is True
    assert len(calls) == 1
    assert calls[0]["event"]["type"] == "digest"
    assert state["digest"]["last_fired_slot"] == _at()


def test_does_not_refire_in_the_same_minute(collected, sent):
    calls, _ = sent
    state = {}
    digest.check(state, now=_at())
    digest.check(state, now=_at() + 20)
    digest.check(state, now=_at() + 59)
    assert len(calls) == 1


def test_disabled_by_default(collected, sent, monkeypatch):
    calls, _ = sent
    monkeypatch.delenv("DIGEST_ENABLED", raising=False)
    assert digest.check({}, now=_at()) is False
    assert calls == []


def test_bad_digest_at_disables_without_crashing(collected, sent, monkeypatch):
    calls, _ = sent
    monkeypatch.setenv("DIGEST_AT", "25:00")
    assert digest.check({}, now=_at()) is False
    assert calls == []


# ---------------------------------------------------------------------------
# Delivery failure: retry, but bounded
# ---------------------------------------------------------------------------


def test_delivery_failure_retries_next_tick(collected, sent):
    calls, box = sent
    box["ok"] = False
    state = {}
    digest.check(state, now=_at())
    assert state["digest"]["attempts"] == 1
    assert "last_fired_slot" not in state["digest"], "slot retired despite failure"
    digest.check(state, now=_at() + 60)
    assert state["digest"]["attempts"] == 2
    assert len(calls) == 2


def test_attempts_are_capped_at_three(collected, sent):
    calls, box = sent
    box["ok"] = False
    state = {}
    for i in range(8):
        digest.check(state, now=_at() + i * 60)
    assert len(calls) == digest.MAX_ATTEMPTS, (
        f"expected at most {digest.MAX_ATTEMPTS} delivery attempts, "
        f"got {len(calls)}"
    )
    # After giving up the slot is retired, so it does not retry forever.
    assert state["digest"]["last_fired_slot"] == _at()


def test_success_clears_the_attempt_counter(collected, sent):
    calls, box = sent
    state = {}
    box["ok"] = False
    digest.check(state, now=_at())
    assert state["digest"]["attempts"] == 1
    box["ok"] = True
    digest.check(state, now=_at() + 60)
    assert state["digest"]["attempts"] == 0
    assert "last_error" not in state["digest"]


# ---------------------------------------------------------------------------
# History: what fired, once records have been popped
# ---------------------------------------------------------------------------


def test_history_survives_recovery_popping_the_record():
    """reconcile() deletes a recovered key, so history is the only witness."""
    state = {"incidents": {}}
    digest.record_history(state, {"key": "k1", "rule": "high_loss",
                                  "severity": "warning", "type": "alert"},
                          now=_at() - 3600)
    digest.record_history(state, {"key": "k1", "type": "recovery"},
                          now=_at() - 1800)
    assert len(state["history"]) == 2
    assert state["incidents"] == {}


def test_history_is_pruned_by_age_and_count(monkeypatch):
    monkeypatch.setenv("DIGEST_HISTORY_MAX", "5")
    state = {}
    for i in range(20):
        digest.record_history(state, {"key": f"k{i}", "type": "alert"}, now=_at())
    assert len(state["history"]) == 5

    state = {"history": [{"ts": _at() - 72 * 3600, "key": "ancient"}]}
    digest.prune_history(state, now=_at())
    assert state["history"] == []


def test_digest_counts_alerts_and_recoveries_from_history(collected, sent):
    calls, _ = sent
    state = {}
    digest.record_history(state, {"key": "a", "type": "alert"}, now=_at() - 3600)
    digest.record_history(state, {"key": "b", "type": "alert"}, now=_at() - 1800)
    digest.record_history(state, {"key": "a", "type": "recovery"}, now=_at() - 900)
    # Outside the 24h window: must not be counted.
    digest.record_history(state, {"key": "old", "type": "alert"},
                          now=_at() - 40 * 3600)
    digest.check(state, now=_at())
    event = calls[0]["event"]
    assert event["alerts_fired"] == 2
    assert event["recoveries"] == 1


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


def test_all_clear_when_nothing_is_lossy(collected, sent):
    calls, _ = sent
    digest.check({}, now=_at())
    text = calls[0]["event"]["message"]
    assert "🟢" in text
    assert "all clear" in text.lower()


def test_lossy_targets_are_listed_worst_first(collected, sent):
    calls, _ = sent
    collected["value"] = {
        **collected["value"],
        "targets": _targets(("Quiet", 0.0), ("Bad", 44.0), ("Meh", 3.0)),
    }
    digest.check({}, now=_at())
    text = calls[0]["event"]["message"]
    assert text.index("Bad") < text.index("Meh")
    assert "Quiet" not in text, "a clean target should not be listed individually"
    assert "🔴" in text


def test_open_incidents_drive_the_headline_red(collected, sent):
    calls, _ = sent
    state = {"incidents": {"k": {"rule": "target_down"}}}
    digest.check(state, now=_at())
    assert calls[0]["event"]["message"].startswith("🔴")


def test_digest_is_silent_by_default(collected, sent):
    calls, _ = sent
    digest.check({}, now=_at())
    assert calls[0]["event"]["silent"] is True


def test_digest_can_be_made_loud(collected, sent, monkeypatch):
    calls, _ = sent
    monkeypatch.setenv("DIGEST_SILENT", "false")
    digest.check({}, now=_at())
    assert calls[0]["event"]["silent"] is False


def test_target_names_are_escaped_in_the_message(collected, sent):
    """Target names are operator-editable and land in Telegram HTML."""
    calls, _ = sent
    collected["value"] = {
        **collected["value"], "targets": _targets(("a<b&c", 30.0)),
    }
    digest.check({}, now=_at())
    text = calls[0]["event"]["message"]
    assert "a&lt;b&amp;c" in text
    assert "a<b&c" not in text


def test_a_new_slot_gets_a_full_retry_budget(collected, sent):
    """Yesterday's failures must not shrink today's budget.

    Two failed attempts, then the Pi is off overnight. The next day's slot
    inherited `attempts=2` and got a single try instead of three — the budget
    silently shrinking the longer delivery had been unreliable.
    """
    calls, box = sent
    state = {}
    box["ok"] = False
    digest.check(state, now=_at())
    digest.check(state, now=_at() + 60)
    assert state["digest"]["attempts"] == 2
    assert len(calls) == 2

    # Next day, same wall-clock slot. Delivery still failing.
    calls.clear()
    tomorrow = _at(day=31)
    for i in range(6):
        digest.check(state, now=tomorrow + i * 60)
    assert len(calls) == digest.MAX_ATTEMPTS, (
        f"the new slot got {len(calls)} attempts, not {digest.MAX_ATTEMPTS} — "
        "the counter carried over from the previous slot"
    )


# ---------------------------------------------------------------------------
# Active mutes
#
# A mute is the one thing here that can cause a missed outage, so the daily
# message a user already reads has to say what is currently silenced.
# ---------------------------------------------------------------------------


def test_the_digest_names_what_is_muted(collected, mutes_file):
    mutes_file([{"target": "A", "until": 9_999_999_999.0,
                 "reason": "router reboot"}])
    payload = digest.build({}, now=_at())
    assert len(payload["active_mutes"]) == 1
    assert "Muted" in payload["message"]
    assert "A" in payload["message"]
    assert "router reboot" in payload["message"]


def test_a_clean_digest_has_no_muted_section(collected, mutes_file):
    """An empty section every morning trains the reader to skip it."""
    mutes_file([])
    payload = digest.build({}, now=_at())
    assert payload["active_mutes"] == []
    assert "Muted" not in payload["message"]


def test_an_expired_mute_is_not_reported_as_active(collected, mutes_file):
    mutes_file([{"target": "A", "until": 1.0}])
    payload = digest.build({}, now=_at())
    assert payload["active_mutes"] == []


def test_the_digest_reports_how_much_a_mute_swallowed(collected, mutes_file):
    """The number that tells a user the mute has gone on too long."""
    mutes_file([{"target": "A", "until": 9_999_999_999.0}])
    state = {"incidents": {"target_down:A": {"muted_suppressed_count": 7}}}
    payload = digest.build(state, now=_at())
    assert payload["muted_suppressed"] == 7
    assert "7 alerts suppressed" in payload["message"]
