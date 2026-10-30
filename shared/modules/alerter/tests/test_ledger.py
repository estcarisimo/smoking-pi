"""The delivery ledger: one notification per incident and kind, ever."""

import json

import ledger
import main
import outbox
import quiet

T = 1_792_346_400.0  # 2026-10-18 18:00 UTC


def _alert(key="target_down:NYT", first_seen=T, count=1, type="alert",
           message="NYT down"):
    return {"type": type, "key": key, "rule": key.split(":")[0],
            "severity": "critical", "target": "NYT", "message": message,
            "state": {"first_seen": first_seen, "notified_count": count}}


def test_the_key_is_the_incident_and_the_kind():
    assert ledger.key_of(_alert()) == f"target_down:NYT@{int(T)}|alert"
    assert ledger.key_of(_alert(count=3)) == f"target_down:NYT@{int(T)}|update3"
    assert ledger.key_of(_alert(type="recovery")) == f"target_down:NYT@{int(T)}|recovery"
    # The same rule next week is another incident.
    assert ledger.key_of(_alert(first_seen=T + 86400)) != ledger.key_of(_alert())
    # A watchdog event has no lifecycle: its key is already one event.
    assert ledger.key_of({"type": "alert", "key": "watchdog:wifi:1.5"}) == \
        "watchdog:wifi:1.5|alert"
    # Summaries, digests and reports are built fresh: no key.
    for kind in ("undelivered", "held", "digest", "report"):
        assert ledger.key_of({"type": kind, "key": kind}) is None


def test_a_final_outcome_is_kept_and_the_record_is_bounded():
    state = {}
    idem = ledger.key_of(_alert())
    ledger.record(state, idem, ledger.FAILED, T)
    assert not ledger.done(state, idem)
    ledger.record(state, idem, ledger.QUEUED, T + 60)
    ledger.record(state, idem, ledger.TOLD, T + 120)
    ledger.record(state, idem, ledger.FAILED, T + 180)
    assert ledger.outcome(state, idem) == ledger.QUEUED
    ledger.record(state, "other|alert", ledger.SENT, T + ledger.KEEP_S + 61)
    assert idem not in state["delivered"]  # older than seven days
    assert json.loads(json.dumps(state)) == state  # lives in state.json


def test_a_notification_already_handed_over_is_not_sent_again(monkeypatch):
    calls = []
    monkeypatch.setattr(main.notifier, "send",
                        lambda payload, image=None: calls.append(payload) or "sent")
    current = {}
    main._deliver(current, _alert(), now=T)
    main._deliver(current, _alert(), now=T + 60)
    assert len(calls) == 1
    # A re-notification is a new message; its recovery too.
    main._deliver(current, _alert(count=2), now=T + 3600)
    main._deliver(current, _alert(type="recovery"), now=T + 4000)
    assert len(calls) == 3


def test_what_the_gateway_answered_shuts_the_outbox_and_is_never_resent(monkeypatch):
    outcomes = iter(["handed"])
    calls = []
    monkeypatch.setattr(main.notifier, "send", lambda payload, image=None: (
        calls.append(payload) or next(outcomes)))
    current = {}
    main._deliver(current, _alert(), now=T)
    assert current["outbox"]["items"][0]["handed"] is True
    assert ledger.outcome(current, ledger.key_of(_alert())) == ledger.QUEUED
    # What follows waits behind it, as before.
    main._deliver(current, _alert(key="high_loss:Apple"), now=T + 30)
    assert len(calls) == 1
    # Back: the summary is of what the gateway does not hold.
    sent = []
    assert outbox.flush(current, T + 120, send=lambda e: sent.append(e) or "sent",
                        reachable=lambda: True) is True
    assert [e["key"] for e in sent] == ["high_loss:Apple"]  # resent as it was


def test_an_outbox_holding_only_what_the_gateway_has_clears_silently():
    state = {}
    outbox.enqueue(state, _alert(), now=T, handed=True)
    sent = []
    assert outbox.flush(state, T + 60, send=sent.append, reachable=lambda: False) is False
    assert outbox.pending(state)  # still shut while delivery is down
    assert outbox.flush(state, T + 600, send=sent.append, reachable=lambda: True) is None
    assert sent == [] and not outbox.pending(state)


def test_a_delivered_summary_marks_what_it_told():
    state = {}
    outbox.enqueue(state, _alert(), now=T)
    outbox.enqueue(state, _alert(key="high_loss:Apple"), now=T + 700)
    assert outbox.flush(state, T + 1400, send=lambda e: True,
                        reachable=lambda: True) is True
    assert ledger.outcome(state, ledger.key_of(_alert())) == ledger.TOLD
    assert ledger.done(state, ledger.key_of(_alert(key="high_loss:Apple")))


def test_an_outbox_summary_tells_what_quiet_hours_still_hold(monkeypatch):
    """The network was down as quiet hours began: NYT's alert waits in the
    outbox, and its hourly re-notification is held. The outbox is not held:
    its summary goes as soon as delivery works, and tells NYT. The morning
    summary must not tell NYT's alert again; its recovery is another kind
    and still goes."""
    monkeypatch.setenv("QUIET_FROM", "23:00")
    monkeypatch.setenv("QUIET_UNTIL", "07:00")
    monkeypatch.setenv("DIGEST_TZ", "UTC")
    night = 1_792_368_000.0 - 1800   # 2026-10-18 23:30 UTC
    first = night - 1200
    current = {}
    outbox.enqueue(current, _alert(first_seen=first), now=first)
    outbox.enqueue(current, _alert(key="high_loss:Apple"), now=first + 60)
    quiet.hold(current, _alert(first_seen=first, count=2), night)
    quiet.hold(current, _alert(first_seen=first, type="recovery"), night + 600)
    quiet.hold(current, _alert(key="microcut_burst:CPE"), night + 900)
    assert outbox.flush(current, night + 1200, send=lambda e: "sent",
                        reachable=lambda: True) is True
    held = [(i["payload"]["key"], i["payload"]["type"])
            for i in current["held"]["items"]]
    assert held == [("target_down:NYT", "recovery"), ("microcut_burst:CPE", "alert")]
    assert ledger.done(current, ledger.key_of(_alert(first_seen=first, count=2)))


def test_a_quiet_summary_that_waited_in_the_outbox_tells_what_it_carries(monkeypatch):
    """Review of #433: quiet hours ended with the network down, so their
    summary waited in the outbox, and went as it was. It told NYT: the
    hourly re-notification is still a new message, but the held alert
    itself is never sent again."""
    current = {}
    held_item = {"queued_at": T, "payload": _alert()}
    summary = {"type": "held", "key": "held", "severity": "info",
               "message": "held overnight", "items": [held_item]}
    outbox.enqueue(current, summary, now=T)
    assert outbox.flush(current, T + 60, send=lambda e: "sent",
                        reachable=lambda: True) is True
    assert ledger.outcome(current, ledger.key_of(_alert())) == ledger.TOLD


def test_a_corrupt_ledger_costs_nothing():
    state = {"delivered": ["not", "a", "dict"]}
    assert not ledger.done(state, "k|alert")
    ledger.prune(state, T)
    assert "delivered" not in state
