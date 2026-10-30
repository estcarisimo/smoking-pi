"""The notification record (notifications.py): every decision, its text and what became of it."""

import json

import pytest

import digest
import notifications
import main
import outbox
import quiet
from common import notification_ledger

DAY = 1_791_763_200.0  # 2026-10-12 00:00 UTC
NOON = DAY + 12 * 3600


def _alert(key="high_loss:Google", rule="high_loss", severity="warning",
           target="Google", message="mean loss 30%", type="alert"):
    return {"type": type, "key": key, "rule": rule, "severity": severity,
            "target": target, "message": message}


def _entries():
    return notification_ledger.read(notifications.ledger_file())[0]


@pytest.fixture
def notified(monkeypatch):
    """notifier.send that succeeds (or not), leaving a receipt as the
    real one does."""
    box = {"ok": True, "sent": []}

    def fake(payload, image=None):
        box["sent"].append(payload)
        main.notifier.last_receipt.update(
            {"text": f"<b>{payload.get('message')}</b>", "mode": "telegram",
             "message_id": "4711" if box["ok"] else None})
        return "sent" if box["ok"] else "failed"

    monkeypatch.setattr(main.notifier, "send", fake)
    return box


# --- the file -------------------------------------------------------------------

def test_append_then_read_back():
    entry_id = notifications.record(_alert(), "sent", NOON,
                             {"text": "<b>Google</b> lost 30%", "mode": "telegram",
                              "message_id": "99"})
    [entry] = _entries()
    assert entry["id"] == entry_id
    assert entry["kind"] == "alert" and entry["type"] == "alert"
    assert entry["outcome"] == "sent" and entry["message_id"] == "99"
    assert entry["text"] == "<b>Google</b> lost 30%"
    assert entry["targets"] == ["Google"]
    assert entry["decided_at"] == entry["delivered_at"] == NOON


def test_an_update_by_id_changes_the_outcome():
    entry_id = notifications.record(_alert(), "failed", NOON)
    other = notifications.record(_alert(key="k2"), "sent", NOON + 1)
    notifications.update(entry_id, outcome="sent", delivered_at=NOON + 600)
    notifications.update("no-such-id", outcome="sent")
    by_id = {e["id"]: e for e in _entries()}
    assert by_id[entry_id]["outcome"] == "sent"
    assert by_id[entry_id]["delivered_at"] == NOON + 600
    assert by_id[other]["outcome"] == "sent"
    assert len(by_id) == 2


def test_a_corrupt_line_is_skipped_and_a_torn_one_does_not_swallow_the_next():
    notifications.record(_alert(key="before"), "sent", NOON)
    path = notifications.ledger_file()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("not json at all\n")
        fh.write('{"id": "torn", "kind": "al')  # a power cut mid-write
    notifications.record(_alert(key="after"), "sent", NOON + 60)
    entries, skipped = notification_ledger.read(path)
    assert [e["key"] for e in entries] == ["before", "after"]
    assert skipped == 2
    # Compaction drops what does not parse.
    assert notifications.compact(NOON + 120) == 2
    assert notification_ledger.read(path)[1] == 0


def test_pruned_to_thirty_days_with_updates_folded_in():
    old = notifications.record(_alert(key="old"), "sent", NOON - 31 * 86400)
    kept = notifications.record(_alert(key="kept"), "queued", NOON - 29 * 86400)
    notifications.update(kept, delivered_in="summary", delivered_at=NOON - 29 * 86400 + 60)
    assert notifications.compact(NOON) == 1
    with open(notifications.ledger_file(), encoding="utf-8") as fh:
        lines = [json.loads(line) for line in fh]
    assert [line["id"] for line in lines] == [kept]
    assert lines[0]["delivered_in"] == "summary"
    assert old not in {e["id"] for e in _entries()}


def test_compaction_leaves_it_readable_by_the_other_containers():
    import os
    import stat

    notifications.record(_alert(), "sent", NOON)
    notifications.compact(NOON)
    assert stat.S_IMODE(os.stat(notifications.ledger_file()).st_mode) == 0o644


def test_bounded_by_a_hard_cap(monkeypatch):
    monkeypatch.setattr(notifications, "MAX_ENTRIES", 3)
    for i in range(5):
        notifications.record(_alert(key=f"k{i}"), "sent", NOON + i)
    notifications.compact(NOON + 10)
    assert [e["key"] for e in _entries()] == ["k2", "k3", "k4"]


def test_compaction_runs_at_most_hourly_unless_the_file_is_too_big(monkeypatch):
    calls = []
    monkeypatch.setattr(notifications, "compact", lambda now=None: calls.append(now))
    notifications.record(_alert(), "sent", NOON)
    notifications.record(_alert(), "sent", NOON + 60)
    assert calls == [NOON]
    notifications.record(_alert(), "sent", NOON + 3601)
    assert calls == [NOON, NOON + 3601]
    monkeypatch.setattr(notifications, "MAX_BYTES", 10)
    notifications.record(_alert(), "sent", NOON + 3700)
    assert len(calls) == 3


def test_a_write_that_fails_costs_nothing(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setattr(notifications, "ledger_file", lambda: str(blocker / "notifications.jsonl"))
    assert notifications.record(_alert(), "sent", NOON)  # an id, and no exception
    notifications.update("x", outcome="sent")
    assert notifications.compact(NOON) == -1


def test_kinds():
    assert notifications.kind_of(_alert()) == "alert"
    assert notifications.kind_of(_alert(type="recovery")) == "recovery"
    assert notifications.kind_of(_alert(rule="watchdog")) == "watchdog"
    assert notifications.kind_of(_alert(rule="influx_unreadable")) == "monitor"
    assert notifications.kind_of({"type": "held"}) == "quiet_bundle"
    assert notifications.kind_of({"type": "undelivered"}) == "outbox_summary"
    assert notifications.kind_of({"type": "digest"}) == "digest"
    assert notifications.kind_of({"type": "report"}) == "report"
    assert set(notifications.kind_of({"type": t}) for t in ("alert", "held")) <= set(
        notification_ledger.KINDS)


def test_the_diagnosis_class_and_cause_are_kept():
    payload = {**_alert(), "diagnosis": {"class": "wifi", "summary": "the Pi's radio",
                                         "detail": None, "confidence": "high",
                                         "evidence": ["x"]}}
    notifications.record(payload, "sent", NOON)
    [entry] = _entries()
    assert entry["diagnosis"]["class"] == "wifi"
    assert entry["diagnosis"]["cause"] == "the Pi's radio"


# --- the hooks ------------------------------------------------------------------

def test_deliver_records_what_was_sent_with_its_text_and_message_id(notified):
    current = {"incidents": {}}
    payload = _alert()
    main._deliver(current, payload, now=NOON)
    [entry] = _entries()
    assert entry["id"] == payload["notification_id"]
    assert entry["outcome"] == "sent" and entry["message_id"] == "4711"
    assert entry["text"] == "<b>mean loss 30%</b>"
    assert entry["transport"] == "telegram"
    # What is sent and the old history are unchanged.
    assert payload["delivery"] == "sent"
    assert current["history"][-1]["delivery"] == "sent"


def test_a_failed_send_is_recorded_and_the_summary_that_carries_it_is_named(
        notified, monkeypatch):
    current = {"incidents": {}}
    notified["ok"] = False
    first, second = _alert(), _alert(key="outage:1", rule="outage", target=None)
    main._deliver(current, first, now=NOON)
    main._deliver(current, second, now=NOON + 60)  # joins the waiting outbox
    by_id = {e["id"]: e for e in _entries()}
    assert by_id[first["notification_id"]]["outcome"] == "failed"
    assert by_id[second["notification_id"]]["outcome"] == "queued"
    assert first["delivery"] == second["delivery"] == "queued"

    notified["ok"] = True
    later = NOON + outbox.RESEND_AS_IS_S + 600
    assert outbox.flush(current, later, send=main.notifier.send,
                        reachable=lambda: True) is True
    entries = _entries()
    [summary] = [e for e in entries if e["kind"] == "outbox_summary"]
    assert summary["outcome"] == "sent"
    assert summary["carries"] == [first["notification_id"], second["notification_id"]]
    for entry in entries:
        if entry["kind"] == "alert":
            assert entry["delivered_in"] == summary["id"]
            assert entry["delivered_at"] == later


def test_a_single_recent_resend_is_the_same_entry(notified):
    current = {"incidents": {}}
    notified["ok"] = False
    payload = _alert()
    main._deliver(current, payload, now=NOON)
    notified["ok"] = True
    outbox.flush(current, NOON + 120, send=main.notifier.send,
                 reachable=lambda: True)
    [entry] = _entries()
    assert entry["id"] == payload["notification_id"]
    assert entry["outcome"] == "sent" and entry["delivered_at"] == NOON + 120


def test_an_unreachable_retry_writes_nothing(notified):
    current = {"incidents": {}}
    notified["ok"] = False
    main._deliver(current, _alert(), now=NOON)
    outbox.flush(current, NOON + 120, send=main.notifier.send,
                 reachable=lambda: False)
    assert len(_entries()) == 1


def test_quiet_hours_held_then_the_bundle_names_what_it_carried(notified, monkeypatch):
    monkeypatch.setenv("QUIET_FROM", "23:00")
    monkeypatch.setenv("QUIET_UNTIL", "07:00")
    monkeypatch.setenv("DIGEST_TZ", "UTC")
    night, morning = DAY + 23.5 * 3600, DAY + 31 * 3600 + 300
    current = {"incidents": {}}
    held = [_alert(), {**_alert(type="recovery"), "duration_s": 600}]
    for i, payload in enumerate(held):
        main._deliver(current, payload, now=night + i)
    assert [e["outcome"] for e in _entries()] == ["held", "held"]
    assert notified["sent"] == []
    quiet.release(current, morning, lambda e: main._deliver(current, e, now=morning))
    entries = _entries()
    [bundle] = [e for e in entries if e["kind"] == "quiet_bundle"]
    assert bundle["outcome"] == "sent"
    assert bundle["carries"] == [p["notification_id"] for p in held]
    for entry in entries:
        if entry["kind"] != "quiet_bundle":
            assert entry["delivered_in"] == bundle["id"]
            assert entry["delivered_at"] == morning


def test_the_digest_is_recorded(monkeypatch, notified):
    monkeypatch.setenv("DIGEST_ENABLED", "true")
    monkeypatch.setenv("DIGEST_AT", "08:30")
    monkeypatch.setenv("ALERT_CHARTS", "false")
    monkeypatch.setattr(digest.aggregates, "collect", lambda hours=24: {
        "targets": [{"target": "A", "avg_loss_pct": 5.0, "median_ms": 1,
                     "p95_ms": 2}], "cpe": [], "incidents": None})
    at = DAY + 8.5 * 3600 + 60
    assert digest.check({"incidents": {}}, now=at) is True
    [entry] = _entries()
    assert entry["kind"] == "digest" and entry["outcome"] == "sent"
    assert entry["targets"] == ["A"]


def test_the_digest_counts_from_the_ledger_as_it_did_from_history(notified):
    """Same notifications, same window: the same numbers either way."""
    now = DAY + 8.5 * 3600
    from_ledger = {"incidents": {}}
    for at, payload in [(now - 3600, _alert(key="a")), (now - 1800, _alert(key="b")),
                        (now - 900, _alert(key="a", type="recovery")),
                        (now - 40 * 3600, _alert(key="old"))]:
        main._deliver(from_ledger, payload, now=at)
    legacy = {"history": from_ledger["history"]}
    fired_ledger = digest._history_since(from_ledger, now - 24 * 3600, now)
    # With no ledger, state["history"] alone.
    ledger_path = notifications.ledger_file()
    import os
    os.unlink(ledger_path)
    fired_state = digest._history_since(legacy, now - 24 * 3600, now)
    strip = [{k: e[k] for k in ("ts", "key", "type", "delivery")}
             for e in fired_ledger]
    assert strip == [{k: e[k] for k in ("ts", "key", "type", "delivery")}
                     for e in fired_state]
    assert [e["type"] for e in fired_ledger] == ["alert", "alert", "recovery"]


def test_before_the_ledger_begins_the_old_history_fills_in(notified):
    now = DAY + 8.5 * 3600
    state = {"incidents": {}, "history": [
        {"ts": now - 7200, "key": "pre", "type": "alert", "delivery": "sent"}]}
    main._deliver(state, _alert(key="post"), now=now - 600)
    fired = digest._history_since(state, now - 24 * 3600, now)
    assert [e["key"] for e in fired] == ["pre", "post"]


# --- show: what `smoking-pi alerts history` prints --------------------------------

def test_show_one_line_per_entry_in_the_digest_zone(monkeypatch, capsys):
    monkeypatch.setenv("DIGEST_TZ", "America/Chicago")
    notifications.record(_alert(), "sent", NOON, {"text": "<b>🔴 Google</b> lost 30%\nmore"})
    notifications.record({"type": "digest", "message": "Daily"}, "failed", NOON + 60,
                  {"text": "📊 Daily digest"})
    assert notifications.show(7, None, False, now=NOON + 120) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("2026-10-12 07:00 CDT  alert")
    assert "sent" in lines[0] and lines[0].endswith("🔴 Google lost 30%")
    assert "digest" in lines[1] and "failed" in lines[1]


def test_show_says_when_a_queued_one_went_out_later(capsys, notified):
    current = {"incidents": {}}
    notified["ok"] = False
    main._deliver(current, _alert(), now=NOON)
    notified["ok"] = True
    outbox.flush(current, NOON + 120, send=main.notifier.send,
                 reachable=lambda: True)
    main._deliver(current, _alert(key="k2"), now=NOON + 180)
    outbox.enqueue(current, {**_alert(key="k3"), "notification_id": "x"}, NOON + 200)
    notifications.record(_alert(key="k3"), "queued", NOON + 200, entry_id="x")
    notifications.update("x", delivered_in="summary1", delivered_at=NOON + 900)
    notifications.show(7, None, False, now=NOON + 1000)
    out = capsys.readouterr().out.splitlines()
    assert " sent " in out[0]
    assert "queued→sent" in out[-1]


def test_show_filters_by_kind_and_prints_json(capsys):
    notifications.record(_alert(), "sent", NOON)
    notifications.record({"type": "digest", "message": "d"}, "sent", NOON + 1)
    notifications.show(7, "digest", True, now=NOON + 2)
    rows = json.loads(capsys.readouterr().out)
    assert [r["kind"] for r in rows] == ["digest"]
    notifications.show(1, None, False, now=NOON + 3 * 86400)
    assert "No notifications in the last 1 day(s) (the record begins" in \
        capsys.readouterr().out


def test_show_cli_refuses_more_than_the_ledger_keeps(capsys):
    with pytest.raises(SystemExit):
        notifications.main(["show", "--days", "31"])
    with pytest.raises(SystemExit):
        notifications.main(["show", "--kind", "nonsense"])


# --- bounded under repeated failure (review of #434) ------------------------------

def test_a_summary_refused_every_retry_is_one_entry_with_attempts(notified):
    current = {"incidents": {}}
    notified["ok"] = False
    main._deliver(current, _alert(), now=NOON)
    main._deliver(current, _alert(key="k2"), now=NOON + 1)
    late = NOON + outbox.RESEND_AS_IS_S + 60
    for i in range(5):
        current["outbox"]["next_try"] = 0
        outbox.flush(current, late + i * 600, send=main.notifier.send,
                     reachable=lambda: True)
    summaries = [e for e in _entries() if e["kind"] == "outbox_summary"]
    assert len(summaries) == 1
    assert summaries[0]["outcome"] == "failed" and summaries[0]["attempts"] == 5
    notified["ok"] = True
    current["outbox"]["next_try"] = 0
    assert outbox.flush(current, late + 3600, send=main.notifier.send,
                        reachable=lambda: True) is True
    [summary] = [e for e in _entries() if e["kind"] == "outbox_summary"]
    assert summary["outcome"] == "sent" and summary["attempts"] == 6
    assert summary["delivered_at"] == late + 3600
    assert all(e.get("delivered_in") == summary["id"]
               for e in _entries() if e["kind"] == "alert")


def test_a_report_that_keeps_failing_is_one_entry(monkeypatch, tmp_path, notified):
    import reports_watcher

    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "report-20261012-120000.md").write_text("# Report\nfine")
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    notified["ok"] = False
    current = {"reports": {}}
    for i in range(4):
        reports_watcher.check(current, now=NOON + i * 300)
    [entry] = _entries()
    assert entry["kind"] == "report" and entry["outcome"] == "failed"
    assert entry["attempts"] == 4
    notified["ok"] = True
    assert reports_watcher.check(current, now=NOON + 1500) is True
    [entry] = _entries()
    assert entry["outcome"] == "sent" and entry["delivered_at"] == NOON + 1500


def test_compaction_also_bounds_the_size(monkeypatch):
    monkeypatch.setattr(notifications, "MAX_BYTES", 4000)
    for i in range(20):
        notifications.record({**_alert(key=f"k{i}"), "message": "x" * 400}, "sent", NOON + i,
                      {"text": "x" * 400})
    notifications.compact(NOON + 30)
    import os

    assert os.path.getsize(notifications.ledger_file()) <= 3000
    keys = [e["key"] for e in _entries()]
    assert keys and keys[-1] == "k19"


def test_the_digest_still_counts_an_alert_the_ledger_could_not_write(
        notified, monkeypatch):
    now = DAY + 8.5 * 3600
    state = {"incidents": {}}
    main._deliver(state, _alert(key="a"), now=now - 3600)
    monkeypatch.setattr(notifications, "_append", lambda line: False)  # a full card
    main._deliver(state, _alert(key="b"), now=now - 1800)
    fired = digest._history_since(state, now - 24 * 3600, now)
    assert [e["key"] for e in fired] == ["a", "b"]


def test_a_new_ledger_file_is_readable_by_the_web_admin():
    import os
    import stat

    old = os.umask(0o077)
    try:
        notifications.record(_alert(), "sent", NOON)
    finally:
        os.umask(old)
    assert stat.S_IMODE(os.stat(notifications.ledger_file()).st_mode) == 0o644


# --- said once (ledger.py, #433) ------------------------------------------------

def _incident(**extra):
    return {**_alert(), "state": {"first_seen": NOON - 600, "notified_count": 1},
            **extra}


def test_a_duplicate_is_recorded_as_skipped_and_not_counted_by_the_digest(notified):
    current = {"incidents": {}}
    main._deliver(current, _incident(), now=NOON)
    main._deliver(current, _incident(), now=NOON + 60)  # the same alert again
    assert len(notified["sent"]) == 1
    first, second = _entries()
    assert first["outcome"] == "sent"
    assert second["outcome"] == "skipped-duplicate"
    assert second["key"] == first["key"]
    fired = digest._history_since(current, NOON - 3600, NOON + 120)
    assert [e["delivery"] for e in fired] == ["sent"]


def test_handed_to_the_gateway_is_its_own_outcome(monkeypatch):
    def handed(payload, image=None):
        main.notifier.last_receipt.update({"text": "x", "mode": "openclaw",
                                           "message_id": None})
        return main.notifier.HANDED

    monkeypatch.setattr(main.notifier, "send", handed)
    current = {"incidents": {}}
    payload = _incident()
    main._deliver(current, payload, now=NOON)
    [entry] = _entries()
    assert entry["outcome"] == "handed" and entry["transport"] == "openclaw"
    assert entry["delivered_at"] == NOON
    assert payload["delivery"] == "handed"
