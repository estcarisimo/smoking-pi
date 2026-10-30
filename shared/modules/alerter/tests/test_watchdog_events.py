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
    assert asked == [] and state["watchdog"] == {"since": NOW, "told": {}}


def test_each_new_event_is_told_once_with_its_weight():
    state = {"watchdog": {"since": NOW - 3600}}
    rows = [
        _row(4000, "wifi: before the first look; rebooting"),
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
    # The same rows again: nothing new.
    assert watchdog_events.collect(state, lambda src: rows, now=NOW + 60) == []


def test_a_failed_query_loses_nothing():
    state = {"watchdog": {"since": NOW - 600}}

    def broken(src):
        raise ConnectionError("down")

    assert watchdog_events.collect(state, broken, now=NOW) == []
    rows = [_row(300, "wifi: x; rebooting")]
    assert len(watchdog_events.collect(state, lambda src: rows, now=NOW + 60)) == 1


def test_a_long_gap_reads_at_most_a_day_back():
    state = {"watchdog": {"since": NOW - 10 * 86400}}
    asked = []
    watchdog_events.collect(state, lambda src: asked.append(src) or [], now=NOW)
    assert "range(start: 2026-10-12T14:00:00Z)" in asked[0]


def test_a_muted_watchdog_says_nothing(mutes_file):
    mutes_file([{"rule": "watchdog", "until": 9_999_999_999.0}])
    state = {"watchdog": {"since": NOW - 3600}}
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
    monkeypatch.setattr(main.notifier, "send",
                        lambda payload, image=None: sent.append(payload) or "sent")
    monkeypatch.setattr(main.watchdog_events.time, "time", lambda: NOW)
    main.run_iteration()          # first look: marks the start
    assert sent == []
    main.state.save_state({**main.state.load_state(),
                           "watchdog": {"since": NOW - 600}})
    main.run_iteration()
    assert [p["rule"] for p in sent] == ["watchdog"]
    assert "rebooting" in sent[0]["message"]


# --- review of #335 ------------------------------------------------------------

# The watchdog's own texts (cli/lib/watchdog.sh), with sample values: a
# wording change there must not silently change what is sent.
REAL = {
    "wifi: nothing answers on wlan0, neither router 192.168.1.1 nor the Internet (associated to aa:bb:cc:dd:ee:ff); watching": None,
    "wifi: the uplink is now eth0, not Wi-Fi; nothing to watch": None,
    "wifi: wlan0 reaches the network again after 11 min": "info",
    "wifi: nothing answers on wlan0 for 10 min (associated, 0 packets received meanwhile); reloading the brcmfmac driver": "info",
    "wifi: nothing answers on wlan0 for 10 min; the driver is built in, nothing to reload": "warning",
    "wifi: reloading brcmfmac failed": "warning",
    "wifi: still nothing answers on wlan0 5 min after the driver reload; 15 min in all; rebooting": "info",
    "wifi: still nothing answers; not rebooting (installed with --no-reboot)": "warning",
    "wifi: still nothing answers; not rebooting: this watchdog rebooted 75 min ago, at most once every 6 h": "warning",
    "smokeping: cannot read svc-smokeping's state in container abc; not checked": "warning",
    "smokeping: svc-smokeping not running 5 min after the container started; restarting it": "info",
    "smokeping: svc-smokeping not running; not restarting, already restarted 3 times in 6 h (smoking-pi logs smokeping)": "warning",
    "smokeping: the restart failed": "warning",
    "smokeping: svc-smokeping keeps restarting 6 min after the container started: SmokePing exits at start, usually a configuration error, which a restart does not fix": "warning",
    "watchdog enabled": None,
    "watchdog disabled": None,
}


def test_the_watchdogs_real_messages_are_weighed_as_intended():
    assert {m: watchdog_events.severity(m) for m in REAL} == REAL


def test_every_message_the_watchdog_logs_is_one_of_those():
    """Each wd_log call in watchdog.sh, its fixed words, is covered above."""
    import re
    from pathlib import Path

    shell = (Path(__file__).resolve().parents[4] / "cli/lib/watchdog.sh").read_text()
    calls = re.findall(r'wd_log "([^"]+)"', shell)
    assert len(calls) >= 15
    stems = [re.split(r"\$", c)[0].rstrip() for c in calls]
    for stem in stems:
        assert any(stem in m or m.startswith(stem) for m in REAL), stem


def test_two_events_in_the_same_second_are_two_notifications():
    state = {"watchdog": {"since": NOW - 3600}}
    at = datetime.fromtimestamp(NOW - 600, tz=timezone.utc)
    rows = [
        {"_time": at, "_value": "wifi: x; reloading the brcmfmac driver", "area": "wifi"},
        {"_time": at.replace(microsecond=5), "_value": "wifi: reloading brcmfmac failed",
         "area": "wifi"},
    ]
    payloads = watchdog_events.collect(state, lambda src: rows, now=NOW)
    assert [p["severity"] for p in payloads] == ["info", "warning"]


def test_a_clock_that_went_back_loses_no_event():
    """After a reboot the clock restarts from the last saved time, earlier
    than the reboot event; the next event is stamped before it."""
    state = {"watchdog": {"since": NOW - 86_000}}
    reboot = [_row(600, "wifi: x; rebooting")]
    assert len(watchdog_events.collect(state, lambda src: reboot, now=NOW)) == 1
    after = reboot + [_row(3000, "smokeping: y; restarting it", "smokeping")]
    payloads = watchdog_events.collect(state, lambda src: after, now=NOW - 2900)
    assert [p["rule"] for p in payloads] == ["watchdog"]
    assert "restarting it" in payloads[0]["message"]


def test_a_corrupt_bookkeeping_never_costs_the_cycle():
    state = {"watchdog": {"since": "yesterday"}}
    assert watchdog_events.collect(state, lambda src: [], now=NOW) == []
