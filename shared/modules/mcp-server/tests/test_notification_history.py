"""get_notification_history: what the alerter told the user, from its ledger."""

import json
import time

import pytest

import server
from common import notification_ledger

NOW = time.time()


@pytest.fixture()
def ledger_at(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERT_STATE_FILE", str(tmp_path / "state.json"))
    path = tmp_path / notification_ledger.FILENAME

    def write(*lines):
        with open(path, "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write((line if isinstance(line, str) else json.dumps(line)) + "\n")

    return write


def _entry(entry_id, kind="alert", outcome="sent", ago_h=1.0, **extra):
    return {"id": entry_id, "kind": kind, "type": "alert", "outcome": outcome,
            "decided_at": NOW - ago_h * 3600, "text": f"<b>{entry_id}</b> text",
            "targets": ["Google"], "rule": "high_loss", **extra}


def test_newest_first_with_times_in_iso_and_counts(ledger_at):
    ledger_at(_entry("a", ago_h=5), _entry("b", kind="recovery", ago_h=2),
              _entry("c", kind="digest", outcome="failed", ago_h=1))
    out = server.get_notification_history(days=7)
    assert [e["id"] for e in out["entries"]] == ["c", "b", "a"]
    assert out["entries"][0]["decided"].endswith("+00:00")
    assert "decided_at" not in out["entries"][0]
    assert out["counts"] == {"alert/sent": 1, "recovery/sent": 1, "digest/failed": 1}
    assert out["total"] == 3 and out["truncated"] is False
    assert out["recorded_from"] is not None


def test_text_only_never_an_image(ledger_at):
    ledger_at(_entry("a"))
    out = server.get_notification_history()
    assert isinstance(out, dict)
    json.dumps(out)  # plain JSON all the way down


def test_kind_and_days_filter(ledger_at):
    ledger_at(_entry("old", ago_h=24 * 10), _entry("new"), _entry("d", kind="digest"))
    assert [e["id"] for e in server.get_notification_history(days=7)["entries"]] == \
        ["d", "new"]
    out = server.get_notification_history(days=30, kind="alert")
    assert [e["id"] for e in out["entries"]] == ["new", "old"]


def test_an_update_line_folds_into_its_entry(ledger_at):
    ledger_at(_entry("q", outcome="queued"),
              {"id": "q", "update": {"delivered_in": "s1", "delivered_at": NOW}})
    [entry] = server.get_notification_history()["entries"]
    assert entry["outcome"] == "queued" and entry["delivered_in"] == "s1"
    assert entry["delivered"] is not None


def test_a_corrupt_line_is_counted_not_fatal(ledger_at):
    ledger_at(_entry("a"), "{torn", _entry("b"))
    out = server.get_notification_history()
    assert {e["id"] for e in out["entries"]} == {"a", "b"}
    assert out["unreadable_lines"] == 1


def test_nothing_recorded_yet_is_not_nothing_sent(ledger_at):
    out = server.get_notification_history()
    assert out["entries"] == [] and out["recorded_from"] is None


@pytest.mark.parametrize("days", [0, 31, "x"])
def test_refuses_a_window_past_the_ledger(ledger_at, days):
    assert "error" in server.get_notification_history(days=days)


def test_refuses_an_unknown_kind(ledger_at):
    assert "kind must be one of" in server.get_notification_history(kind="sms")["error"]


def test_an_unreadable_ledger_is_an_error_with_an_id(monkeypatch, tmp_path):
    # A directory where the file should be: open() fails with an OSError.
    (tmp_path / notification_ledger.FILENAME).mkdir()
    monkeypatch.setenv("ALERT_STATE_FILE", str(tmp_path / "state.json"))
    out = server.get_notification_history()
    assert "error_id" in out and "ledger" in out["error"]
