"""The outbox: undelivered notifications, delivered later as one message."""

import digest
import main
import outbox

T = 1_791_818_100.0  # 2026-10-12 15:15 UTC


def _alert(key="uplink_down", rule="uplink_down", severity="critical",
           target=None, message="10 of 10 targets at 100% loss", type="alert"):
    return {"type": type, "key": key, "rule": rule, "severity": severity,
            "target": target, "message": message}


def test_enqueue_starts_an_outbox_that_waits_a_cycle_before_retrying():
    state = {}
    assert not outbox.pending(state)
    outbox.enqueue(state, _alert(), now=T)
    assert outbox.pending(state)
    assert state["outbox"]["since"] == T
    assert not outbox.due(state, T)  # the failed send was this cycle's try
    assert outbox.due(state, T + outbox.RETRY_MIN_S)


def test_retries_back_off_to_ten_minutes():
    state = {}
    outbox.enqueue(state, _alert(), now=T)
    delays = []
    for _ in range(6):
        outbox.failed(state, T)
        delays.append(state["outbox"]["next_try"] - T)
    assert delays == [60, 120, 240, 480, 600, 600]


def test_the_outbox_is_bounded_and_counts_what_it_dropped():
    state = {}
    for i in range(outbox.MAX_ITEMS + 5):
        outbox.enqueue(state, _alert(key=f"k{i}"), now=T + i)
    assert len(state["outbox"]["items"]) == outbox.MAX_ITEMS
    assert state["outbox"]["dropped"] == 5
    assert state["outbox"]["items"][0]["payload"]["key"] == "k5"


def test_the_payload_is_kept_json_safe():
    state = {}
    outbox.enqueue(state, {**_alert(), "odd": {1, 2}}, now=T)
    assert isinstance(state["outbox"]["items"][0]["payload"]["odd"], str)


def test_one_recent_notification_is_resent_as_it_was():
    state = {}
    outbox.enqueue(state, _alert(), now=T)
    event = outbox.build(state, T + 120)
    assert event["key"] == "uplink_down" and event["type"] == "alert"


def test_one_old_notification_becomes_a_summary():
    state = {}
    outbox.enqueue(state, _alert(), now=T)
    event = outbox.build(state, T + outbox.RESEND_AS_IS_S + 1)
    assert event["type"] == "undelivered"


def test_the_summary_tells_each_incident_once_and_ends_on_now():
    """The 12/10 hang: hourly re-notifications of one critical, an outage,
    the recovery -- all undelivered. One message, each incident once."""
    state = {"incidents": {
        "microcut_burst:136.25.220.1/ipv4": {
            "rule": "microcut_burst", "severity": "warning",
            "target": "136.25.220.1", "first_seen": T + 19800},
    }}
    outbox.enqueue(state, _alert(key="outage:1", rule="outage",
                                 severity="warning", message="a brief cut"), now=T)
    for hour in range(6):
        outbox.enqueue(state, _alert(message=f"hour {hour}"), now=T + 600 + 3600 * hour)
    outbox.enqueue(state, _alert(type="recovery"), now=T + 19800)
    text = outbox.build(state, T + 20000)["message"]
    lines = text.split("\n")
    assert "Not delivered from 15:15 to 20:48 UTC (5h 33m)" in lines[0]
    assert len(lines) == 4
    assert lines[1].startswith("🟡 outage · 15:15")
    assert lines[2].startswith("🔴 uplink_down · 15:25 → ✅ 20:45")
    assert "hour 5" in lines[2]  # the newest numbers, not the first
    assert lines[3] == "Now open: 🟡 microcut_burst 136.25.220.1 (since 20:45)"


def test_the_summary_says_when_nothing_is_open():
    state = {}
    outbox.enqueue(state, _alert(), now=T)
    outbox.enqueue(state, _alert(type="recovery"), now=T + 900)
    assert outbox.build(state, T + 1000)["message"].endswith("Nothing open now.")


def test_an_incident_that_came_back_after_recovering_is_open_again():
    state = {}
    outbox.enqueue(state, _alert(), now=T)
    outbox.enqueue(state, _alert(type="recovery"), now=T + 900)
    outbox.enqueue(state, _alert(message="again"), now=T + 1800)
    line = outbox.build(state, T + 2000)["message"].split("\n")[1]
    assert "✅" not in line and "again" in line


def test_the_summary_escapes_names_and_counts_the_rest(monkeypatch):
    monkeypatch.setenv("ALERT_MARKUP", "html")
    state = {}
    for i in range(outbox.SUMMARY_LINES + 2):
        outbox.enqueue(state, _alert(key=f"target_down:<t{i}>", rule="target_down",
                                     target=f"<t{i}>"), now=T + i)
    text = outbox.build(state, T + 5000)["message"]
    assert "<t0>" not in text and "&lt;t0&gt;" in text
    assert text.split("\n")[-2] == "…and 2 more"


def test_flush_asks_before_it_sends_and_backs_off():
    state = {}
    outbox.enqueue(state, _alert(), now=T)
    sent = []
    now = T + outbox.RETRY_MIN_S
    assert outbox.flush(state, now, send=sent.append, reachable=lambda: False) is False
    assert sent == [] and state["outbox"]["next_try"] == now + 60
    assert outbox.flush(state, now + 1, send=sent.append, reachable=lambda: True) is None
    later = now + 60
    assert outbox.flush(state, later, send=lambda e: sent.append(e) or True,
                        reachable=lambda: True) is True
    assert len(sent) == 1 and not outbox.pending(state)


def test_a_failed_or_crashing_flush_keeps_everything():
    state = {}
    outbox.enqueue(state, _alert(), now=T)

    def boom(event):
        raise RuntimeError("boom")

    for send in (lambda e: False, boom):
        now = state["outbox"]["next_try"]
        assert outbox.flush(state, now, send=send, reachable=lambda: True) is False
        assert outbox.pending(state)


def test_deliver_queues_behind_a_waiting_outbox_and_records_how(monkeypatch):
    sent = []
    monkeypatch.setattr(main.notifier, "notify",
                        lambda payload, image=None: sent.append(payload) or ok[0])
    ok = [False]
    current = {}
    main._deliver(current, _alert(), now=T)               # fails: queued
    ok[0] = True
    main._deliver(current, _alert(key="outage:2"), now=T + 60)  # behind it
    assert len(sent) == 1
    assert [h["delivery"] for h in current["history"]] == ["queued", "queued"]
    assert len(current["outbox"]["items"]) == 2
    outbox.clear(current)
    main._deliver(current, _alert(key="outage:3"), now=T + 120)
    assert current["history"][-1]["delivery"] == "sent"


def test_history_records_the_delivery():
    state = {}
    digest.record_history(state, {**_alert(), "delivery": "queued"}, now=T)
    assert state["history"][0]["delivery"] == "queued"
