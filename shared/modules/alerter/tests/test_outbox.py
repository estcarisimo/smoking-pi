"""The outbox: undelivered notifications, delivered later as one message."""

import pytest

import main
import outbox

T = 1_791_818_100.0  # 2026-10-12 15:15 UTC


@pytest.fixture(autouse=True)
def utc_digest(monkeypatch):
    """Times print in DIGEST_TZ (clock.py); UTC keeps these readable."""
    monkeypatch.setenv("DIGEST_TZ", "UTC")


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
    assert "Not delivered from 15:15 to 20:48 (5h 33m)" in lines[0]
    assert len(lines) == 4
    # What is open now comes first: a message over budget is cut at the end.
    assert lines[1] == "Now open: 🟡 microcut_burst 136.25.220.1 (since 20:45)"
    assert lines[2].startswith("🟡 outage · 15:15 (over)")
    assert lines[3].startswith("🔴 uplink_down · 15:25 → ✅ 20:45")
    assert "hour 5" in lines[3]  # the newest numbers, not the first


def test_the_summary_says_when_nothing_is_open():
    state = {}
    outbox.enqueue(state, _alert(), now=T)
    outbox.enqueue(state, _alert(type="recovery"), now=T + 900)
    assert outbox.build(state, T + 1000)["message"].split("\n")[1].endswith(
        "Nothing open now.")


def test_an_incident_that_came_back_after_recovering_is_open_again():
    state = {}
    outbox.enqueue(state, _alert(), now=T)
    outbox.enqueue(state, _alert(type="recovery"), now=T + 900)
    outbox.enqueue(state, _alert(message="again"), now=T + 1800)
    line = outbox.build(state, T + 2000)["message"].split("\n")[2]
    assert "✅" not in line and "again" in line


def test_the_summary_escapes_names_and_counts_the_rest(monkeypatch):
    monkeypatch.setenv("ALERT_MARKUP", "html")
    state = {}
    for i in range(outbox.SUMMARY_LINES + 2):
        outbox.enqueue(state, _alert(key=f"target_down:<t{i}>", rule="target_down",
                                     target=f"<t{i}>"), now=T + i)
    text = outbox.build(state, T + 5000)["message"]
    assert "<t0>" not in text and "&lt;t0&gt;" in text
    assert text.split("\n")[-1] == "…and 2 more"


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
    monkeypatch.setattr(main.notifier, "send",
                        lambda payload, image=None: (
                            sent.append(payload) or ("sent" if ok[0] else "failed")))
    ok = [False]
    current = {}
    first, second, third = _alert(), _alert(key="outage:2"), _alert(key="outage:3")
    main._deliver(current, first, now=T)               # fails: queued
    ok[0] = True
    main._deliver(current, second, now=T + 60)  # behind it
    assert len(sent) == 1
    assert [first["delivery"], second["delivery"]] == ["queued", "queued"]
    assert len(current["outbox"]["items"]) == 2
    outbox.clear(current)
    main._deliver(current, third, now=T + 120)
    assert third["delivery"] == "sent"


# --- review of #333 -------------------------------------------------------------

def test_a_recovery_for_an_alert_already_sent_does_not_invent_a_start():
    """The alert reached the phone, then the link died; only the recovery
    is waiting. Its start is the duration back, or not stated at all."""
    state = {}
    outbox.enqueue(state, {**_alert(type="recovery"), "duration_s": 1800}, now=T)
    outbox.enqueue(state, _alert(key="outage:9", rule="outage",
                                 severity="warning"), now=T + 60)
    lines = outbox.build(state, T + 900)["message"].split("\n")
    assert lines[2].startswith("🔴 uplink_down · 14:45 → ✅ 15:15")
    state = {}
    outbox.enqueue(state, _alert(type="recovery"), now=T)
    outbox.enqueue(state, _alert(key="outage:9", rule="outage"), now=T + 60)
    line = outbox.build(state, T + 900)["message"].split("\n")[2]
    assert line.startswith("🔴 uplink_down · ✅ 15:15")


def test_a_refused_summary_becomes_a_plain_fallback_and_never_blocks():
    """A summary the receiver keeps refusing with the network up (a revoked
    token, or text it rejects) must not hold later alerts forever."""
    state = {"incidents": {"uplink_down": {"rule": "uplink_down", "first_seen": T}}}
    for i in range(3):
        outbox.enqueue(state, _alert(key=f"target_down:<b{i}>",
                                     target=f"<b{i}>"), now=T)
    sent = []

    def send(event):
        sent.append(event)
        return event["message"].startswith("Smoking Pi:")

    for _ in range(4):
        now = state["outbox"]["next_try"]
        result = outbox.flush(state, now, send=send, reachable=lambda: True)
    assert result is True and not outbox.pending(state)
    assert [e["message"].startswith("Smoking Pi:") for e in sent] == [
        False, False, False, True]
    assert "3 notification(s)" in sent[-1]["message"]
    assert "open now: uplink_down" in sent[-1]["message"]
    assert "<" not in sent[-1]["message"]


def test_a_long_summary_keeps_what_is_open_now(monkeypatch):
    import templates

    state = {"incidents": {"uplink_down": {"rule": "uplink_down",
                                           "severity": "critical",
                                           "first_seen": T}}}
    for i in range(30):
        outbox.enqueue(state, _alert(key=f"k{i}", message="x" * 400), now=T + i)
    event = outbox.build(state, T + 5000)
    text = templates.format_message(event)
    assert len(text) <= templates.TG_TEXT_LIMIT
    assert "Now open: 🔴 uplink_down" in text


def test_a_clock_that_went_back_neither_stalls_nor_counts_as_fresh():
    state = {}
    outbox.enqueue(state, _alert(), now=T)
    behind = T - 86_400  # a power cycle restored an old clock
    assert outbox.due(state, behind)
    assert outbox.build(state, behind)["type"] == "undelivered"


def test_a_persisted_outbox_is_due_after_a_restart():
    state = {}
    outbox.enqueue(state, _alert(), now=T)
    import json
    restored = json.loads(json.dumps(state))
    assert outbox.due(restored, T + 3600)
