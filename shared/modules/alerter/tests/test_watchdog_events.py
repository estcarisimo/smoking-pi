"""The host watchdog's actions, told once (watchdog_event in InfluxDB)."""

from datetime import datetime, timezone

import main
import watchdog_events

NOW = 1_791_900_000.0  # 2026-10-13 14:00 UTC


def _row(seconds_ago, message, area="wifi"):
    return {"_time": datetime.fromtimestamp(NOW - seconds_ago, tz=timezone.utc),
            "_value": message, "area": area}


def test_the_first_look_marks_where_to_start_and_sends_nothing():
    state = {}
    asked = []
    assert watchdog_events.collect(state, asked.append, now=NOW) == []
    assert asked == [] and state["watchdog"]["seen_until"] == NOW


def test_each_new_event_is_told_once_with_its_weight():
    state = {"watchdog": {"seen_until": NOW - 3600}}
    rows = [
        _row(4000, "wifi: before the last look; rebooting"),
        _row(1800, "wifi: nothing answers on wlan0, neither router nor the Internet; watching"),
        _row(1200, "wifi: no packet received for 10 min; reloading the brcmfmac driver"),
        _row(900, "wifi: no packet received for 15 min; rebooting"),
        _row(300, "smokeping: svc-smokeping keeps restarting 6 min after the container started", "smokeping"),
        _row(60, "watchdog enabled", "watchdog"),
    ]
    payloads = watchdog_events.collect(state, lambda src: rows, now=NOW)
    assert [(p["severity"], p["message"].split(": ", 1)[1][:22]) for p in payloads] == [
        ("info", "wifi: no packet receiv"),
        ("info", "wifi: no packet receiv"),
        ("warning", "smokeping: svc-smokepi"),
    ]
    assert payloads[1]["message"].startswith("the watchdog on this host, at 13:45 UTC:")
    assert len({p["key"] for p in payloads}) == 3
    assert state["watchdog"]["seen_until"] == NOW - 60
    # The same rows again: nothing new.
    assert watchdog_events.collect(state, lambda src: rows, now=NOW + 60) == []


def test_a_failed_query_loses_nothing():
    state = {"watchdog": {"seen_until": NOW - 600}}

    def broken(src):
        raise ConnectionError("down")

    assert watchdog_events.collect(state, broken, now=NOW) == []
    assert state["watchdog"]["seen_until"] == NOW - 600


def test_a_long_gap_reads_at_most_a_day_back():
    state = {"watchdog": {"seen_until": NOW - 10 * 86400}}
    asked = []
    watchdog_events.collect(state, lambda src: asked.append(src) or [], now=NOW)
    assert "range(start: 2026-10-12T14:00:00Z)" in asked[0]


def test_a_muted_watchdog_says_nothing(mutes_file):
    mutes_file([{"rule": "watchdog", "until": 9_999_999_999.0}])
    state = {"watchdog": {"seen_until": NOW - 3600}}
    rows = [_row(900, "wifi: no packet received for 15 min; rebooting")]
    assert watchdog_events.collect(state, lambda src: rows, now=NOW) == []


def test_the_loop_delivers_a_reboot_through_the_outbox(monkeypatch, state_file):
    sent = []
    monkeypatch.setattr(main.evaluator, "evaluate_with_context", lambda **_: (
        [], {"mean_rows": [], "micro_rows": [], "wifi_rows": {}, "uplink_changes": [],
             "windows": {"mean": 900, "step": 300, "stale": 1200}, "cadences": {},
             "judged": set()}))
    monkeypatch.setattr(main.evaluator, "_query", lambda src: [
        _row(120, "wifi: no packet received for 15 min; rebooting")])
    monkeypatch.setattr(main.notifier, "notify",
                        lambda payload, image=None: sent.append(payload) or True)
    monkeypatch.setattr(main.watchdog_events.time, "time", lambda: NOW)
    main.run_iteration()          # first look: marks the start
    assert sent == []
    main.state.save_state({**main.state.load_state(),
                           "watchdog": {"seen_until": NOW - 600}})
    main.run_iteration()
    assert [p["rule"] for p in sent] == ["watchdog"]
    assert "rebooting" in sent[0]["message"]
