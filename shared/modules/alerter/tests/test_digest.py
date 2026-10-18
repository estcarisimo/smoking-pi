"""Tests for the daily digest.

The behavior that matters most is negative: a digest must never report a
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
    """``(name, loss)`` or ``(name, loss, loss outside the monitor's spans)``."""
    return [
        {"target": name, "measurement": "latency", "avg_loss_pct": spec[0],
         "median_ms": 10.0, "p95_ms": 20.0,
         **({"avg_loss_pct_measurable": spec[1]} if len(spec) > 1 else {})}
        for name, *spec in specs
    ]


_NO_INCIDENTS = {"by_class": {}, "monitor_minutes": 0, "truncated": False,
                 "incidents": []}


@pytest.fixture
def collected(monkeypatch):
    """Patch aggregates.collect, the one InfluxDB call the digest makes."""
    box = {"value": {"window_hours": 24, "generated_at": "2026-08-30T08:30:00",
                     "target_total": 2, "targets": _targets(("A", 0.0), ("B", 0.0)),
                     "cpe": [], "incidents": _NO_INCIDENTS}}

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


# ---------------------------------------------------------------------------
# The Wi-Fi hop, when the host has one
# ---------------------------------------------------------------------------

def test_wifi_line_appears_only_when_collected(collected):
    payload = digest.build({"incidents": {}, "history": []}, hours=24, now=1_700_000_000)
    assert "Local link" not in payload["message"]
    assert payload["wifi"] == {}

    collected["value"]["wifi"] = {
        "interface": "wlan0", "uplink_is_wifi": True, "ssid": "ExampleNet",
        "channel": 36, "band_ghz": 5.0, "tx_bitrate_mbps": 433.3, "samples": 8640,
        "min_dbm": -71.0, "max_dbm": -49.0, "median_dbm": -52.0,
        "disconnects": 1, "roams": 0,
    }
    payload = digest.build({"incidents": {}, "history": []}, hours=24, now=1_700_000_000)
    text = payload["message"]
    assert "<b>Local link</b>" in text
    assert "🟡 Wi-Fi ExampleNet ch36 — -52 dBm median, -71 dBm min, 433 Mb/s, 1 disconnect." in text
    assert payload["wifi"]["ssid"] == "ExampleNet"


def test_uplink_changes_are_listed_under_local_link(collected):
    """A wired host has no Wi-Fi line, so the changes bring the heading."""
    collected["value"]["uplink"] = {
        "current": {"interface": "eth0", "kind": "wired", "family": 4},
        "changes": [{"time": "2026-09-24T13:02:00+00:00", "previous": "wlan0",
                     "interface": "eth0", "kind": "wired"}],
    }
    payload = digest.build({"incidents": {}, "history": []}, hours=24, now=1_700_000_000)
    text = payload["message"]
    assert "<b>Local link</b>" in text
    assert "🟡 This host's uplink moved from wlan0 to eth0 (wired) at " in text
    assert payload["uplink"]["current"]["interface"] == "eth0"


def test_a_quiet_uplink_adds_nothing(collected):
    collected["value"]["uplink"] = {"current": {"interface": "wlan0", "kind": "wireless",
                                                "family": 4}, "changes": []}
    text = digest.build({"incidents": {}, "history": []}, hours=24, now=1_700_000_000)["message"]
    assert "Local link" not in text


def test_many_uplink_changes_keep_the_newest_three(collected):
    changes = [{"time": f"2026-09-24T1{h}:00:00+00:00", "previous": "wlan0",
                "interface": "eth0", "kind": "wired"} for h in range(5)]
    collected["value"]["uplink"] = {"current": None, "changes": changes}
    text = digest.build({"incidents": {}, "history": []}, hours=24, now=1_700_000_000)["message"]
    assert text.count("uplink moved") == 3
    assert "…and 2 earlier changes." in text
    # Newest first: 14:00 is listed before 13:00 and 12:00.
    moved = [line for line in text.splitlines() if "uplink moved" in line]
    assert moved == sorted(moved, reverse=True)


def test_a_resolver_change_is_listed_under_local_link(collected):
    collected["value"]["resolver"] = {
        "current": {"router": {"owner": "AS15169 GOOGLE - Google LLC, US", "ecs": ""}},
        "changes": [{"time": "2026-09-24T13:02:00+00:00", "path": "router",
                     "previous": "AS19281 QUAD9-AS-1, US",
                     "owner": "AS15169 GOOGLE - Google LLC, US"}],
    }
    payload = digest.build({"incidents": {}, "history": []}, hours=24, now=1_700_000_000)
    text = payload["message"]
    assert "<b>Local link</b>" in text
    assert ("🟡 The resolver answering through the router changed from "
            "AS19281 QUAD9-AS-1, US to AS15169 GOOGLE - Google LLC, US at ") in text
    assert payload["resolver"]["current"]["router"]["owner"].startswith("AS15169")


def test_a_steady_resolver_adds_nothing(collected):
    collected["value"]["resolver"] = {
        "current": {"router": {"owner": "AS15169 GOOGLE - Google LLC, US", "ecs": ""}},
        "changes": []}
    text = digest.build({"incidents": {}, "history": []}, hours=24, now=1_700_000_000)["message"]
    assert "Local link" not in text and "resolver" not in text


def test_wifi_line_lights(collected):
    base = {"interface": "wlan0", "uplink_is_wifi": True, "ssid": "N", "channel": 1,
            "tx_bitrate_mbps": 100.0, "samples": 10, "max_dbm": -50.0, "median_dbm": -55.0}
    for extra, light in (
        ({"min_dbm": -60.0, "disconnects": 0}, "🟢"),
        ({"min_dbm": -80.0, "weak_samples": 6, "disconnects": 0}, "🟡"),
        ({"min_dbm": -60.0, "disconnects": 3}, "🔴"),
    ):
        collected["value"]["wifi"] = {**base, **extra}
        text = digest.build({"incidents": {}, "history": []}, hours=24, now=1_700_000_000)["message"]
        assert f"{light} Wi-Fi N ch1" in text


def test_wifi_line_weak_light_follows_the_configured_samples(collected, monkeypatch):
    """Weak as the verdict and the diagnosis read it: WIFI_WEAK_SAMPLES
    readings under WIFI_WEAK_DBM. One dip of the minimum (-80 dBm once in a
    day of -55) lit it before."""
    collected["value"]["wifi"] = {"interface": "wlan0", "ssid": "N", "channel": 1,
                                  "min_dbm": -80.0, "weak_samples": 1,
                                  "disconnects": 0, "samples": 1440}
    text = digest.build({"incidents": {}, "history": []}, hours=24, now=1_700_000_000)["message"]
    assert "🟢 Wi-Fi N ch1" in text
    monkeypatch.setenv("WIFI_WEAK_SAMPLES", "1")
    text = digest.build({"incidents": {}, "history": []}, hours=24, now=1_700_000_000)["message"]
    assert "🟡 Wi-Fi N ch1" in text


def test_a_malformed_uplink_row_does_not_cost_the_digest(monkeypatch):
    """collect() must still return: the digest sends nothing when it raises."""
    from common import aggregates

    def fake(flux):
        if "exists r.previous" in flux:
            return []
        if "host_uplink" in flux:
            return [{"_field": "interface", "_value": "eth0"},
                    {"_field": "family", "_value": "four"}]
        return []

    monkeypatch.setattr(aggregates, "query_influx", fake)
    assert aggregates._collect_uplink(24) == {}


# The window's incidents (common.aggregates, from the shared diagnosis).
def _incident(cls, minutes, summary):
    return {"class": cls, "confidence": "high", "start": "2026-10-12T10:10:00-05:00",
            "end": "2026-10-12T15:50:00-05:00", "minutes": minutes, "targets": 24,
            "summary": summary}


def test_a_deaf_radio_day_is_the_monitors_not_two_dozen_destinations(collected):
    """2026-10-13 on the reference Pi: the radio heard nothing for 5 h 40 min
    and the steady targets' mean loss was ~24% (340 of 1440 minutes). The
    digest said "problems worth a look" with five destinations in red; the
    alert and the assistants said this host's Wi-Fi."""
    collected["value"]["targets"] = _targets(("Google", 24.1, 0.1),
                                             ("GoogleDNS", 23.8, 0.0))
    collected["value"]["incidents"] = {
        "by_class": {"local_wifi": 1}, "monitor_minutes": 340, "truncated": False,
        "incidents": [_incident("local_wifi", 340, "This host's own Wi-Fi, not the line.")]}
    text = digest.build({"incidents": {}, "history": []}, hours=24, now=1_700_000_000)["message"]
    assert text.splitlines()[0] == (
        "🟡 Last 24h: the monitor itself could not measure for 5 h 40 min.")
    assert "5 h 40 min: This host's own Wi-Fi, not the line." in text
    assert "🔴" not in text
    assert "🟡 Google — 24.1% loss (0.1% while it could)" in text
    assert "not the targets'" in text


def test_a_target_measured_mostly_while_the_monitor_was_deaf_is_not_red(collected):
    """The replay of 2026-10-13 after #387: five Ocean_Reef_* targets,
    renamed the day before (#310), had most of their points inside the deaf
    span and a 70.4% mean. A share of the window (340 of 1440 minutes)
    explains 23.6 points of it, so they stayed red; their loss outside the
    span is what decides."""
    collected["value"]["targets"] = _targets(("Ocean_Reef_1", 70.4, 0.3),
                                             ("Ocean_Reef_2", 100.0, None),
                                             ("Google", 24.1, 0.1))
    collected["value"]["incidents"] = {
        "by_class": {"local_wifi": 1}, "monitor_minutes": 340, "truncated": False,
        "incidents": [_incident("local_wifi", 340, "This host's own Wi-Fi, not the line.")]}
    text = digest.build({"incidents": {}, "history": []}, hours=24, now=1_700_000_000)["message"]
    assert text.startswith("🟡 Last 24h: the monitor itself could not measure")
    assert "🔴" not in text
    assert "🟡 Ocean_Reef_1 — 70.4% loss (0.3% while it could)" in text
    assert ("🟡 Ocean_Reef_2 — 100.0% loss (all while the monitor could not "
            "measure)") in text


def test_a_red_destination_is_listed_before_the_deaf_spells_targets(collected):
    """Review of #391: five targets at 70% inside the deaf span filled the
    five lines, and the one red destination fell under "…and 1 more"."""
    collected["value"]["targets"] = _targets(
        *[(f"Reef_{i}", 70.4, 0.0) for i in range(5)], ("Broken", 40.0, 35.0))
    collected["value"]["incidents"] = {
        "by_class": {"local_wifi": 1}, "monitor_minutes": 340, "truncated": False,
        "incidents": [_incident("local_wifi", 340, "This host's own Wi-Fi, not the line.")]}
    text = digest.build({"incidents": {}, "history": []}, hours=24, now=1_700_000_000)["message"]
    assert "🔴 Broken — 40.0% loss (35.0% while it could)" in text
    assert "…and 1 more with loss." in text


def test_a_destination_lossy_outside_the_deaf_spell_stays_red(collected):
    collected["value"]["targets"] = _targets(("Broken", 60.0, 45.0), ("B", 24.0, 0.0))
    collected["value"]["incidents"] = {
        "by_class": {"local_wifi": 1}, "monitor_minutes": 340, "truncated": False,
        "incidents": [_incident("local_wifi", 340, "This host's own Wi-Fi, not the line.")]}
    text = digest.build({"incidents": {}, "history": []}, hours=24, now=1_700_000_000)["message"]
    assert text.startswith("🔴 Last 24h: problems worth a look.")
    assert "🔴 Broken — 60.0% loss (45.0% while it could)" in text
    assert "🟡 B — 24.0% loss" in text


def test_a_cut_of_the_line_is_named_when_the_means_are_clean(collected):
    """2026-10-16: a ten-minute local_link incident the day before, and every
    mean under 0.2%. The digest said "all clear"."""
    collected["value"]["incidents"] = {
        "by_class": {"local_link": 1}, "monitor_minutes": 0, "truncated": False,
        "incidents": [_incident("local_link", 10, "The line between the house and the ISP.")]}
    text = digest.build({"incidents": {}, "history": []}, hours=24, now=1_700_000_000)["message"]
    assert text.startswith("🟡 Last 24h: mostly fine")
    assert "10 min: The line between the house and the ISP." in text


def test_unread_incidents_are_said_not_taken_for_none(collected):
    collected["value"]["incidents"] = None
    text = digest.build({"incidents": {}, "history": []}, hours=24, now=1_700_000_000)["message"]
    assert "Incidents could not be read for this window." in text


def test_the_red_light_is_the_alerts_high_loss_pct(collected, monkeypatch):
    collected["value"]["targets"] = _targets(("A", 30.0), ("B", 0.0))
    text = digest.build({"incidents": {}, "history": []}, hours=24, now=1_700_000_000)["message"]
    assert text.startswith("🔴")
    monkeypatch.setenv("HIGH_LOSS_PCT", "40")
    text = digest.build({"incidents": {}, "history": []}, hours=24, now=1_700_000_000)["message"]
    assert text.startswith("🟡") and "🟡 A — 30.0% loss" in text


def test_a_short_deaf_spell_does_not_hide_a_dead_destination(collected):
    """Review of #387: five deaf minutes explain 0.3% of a day's loss, not a
    destination lost all day."""
    collected["value"]["targets"] = _targets(("Dead", 100.0, 100.0), ("B", 0.4, 0.4))
    collected["value"]["incidents"] = {
        "by_class": {"local_wifi": 1}, "monitor_minutes": 5, "truncated": False,
        "incidents": [_incident("local_wifi", 5, "This host's own Wi-Fi, not the line.")]}
    text = digest.build({"incidents": {}, "history": []}, hours=24, now=1_700_000_000)["message"]
    assert text.startswith("🔴 Last 24h: problems worth a look.")
    assert "🔴 Dead — 100.0% loss" in text
