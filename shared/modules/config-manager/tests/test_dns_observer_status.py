"""dns_observer_status.read: what the dashboard card believes."""

import json

import dns_observer_status as dos

NOW = 1_790_525_720.0
FRESH = {
    "state": "observing", "reason": "Receiving queries.", "fix": None,
    "heartbeat": NOW - 10, "stale_after": NOW + 80, "observed_until": NOW - 12,
    "coverage": "Network DNS only.", "top_domains": [{"name": "netflix.com", "queries": 9}],
    "canary": {"enabled": True, "seen_24h": 1, "sent_24h": 2, "via": "192.168.86.1"},
}


def _write(tmp_path, status):
    (tmp_path / "status.json").write_text(json.dumps(status))
    return tmp_path


def test_no_state_directory_means_no_observer_in_this_edition(tmp_path):
    assert dos.read(tmp_path / "absent", now=NOW) == {"available": False}


def test_directory_without_status_means_never_enabled(tmp_path):
    assert dos.read(tmp_path, now=NOW) == {"available": True, "enabled": False}


def test_fresh_status_is_passed_through_without_the_domains(tmp_path):
    card = dos.read(_write(tmp_path, FRESH), now=NOW)
    assert card["state"] == "observing" and card["live"] is True
    assert card["canary"] == {"enabled": True, "seen_24h": 1, "sent_24h": 2}
    assert "top_domains" not in card and card["fix"] == ""


def test_a_stale_heartbeat_reads_as_down_with_a_fix(tmp_path):
    card = dos.read(_write(tmp_path, FRESH), now=NOW + 3600)
    assert card["state"] == "down" and card["live"] is False
    assert "not running" in card["reason"] and "smoking-pi up" in card["fix"]


def test_stopped_is_not_live_and_not_rewritten(tmp_path):
    card = dos.read(_write(tmp_path, {**FRESH, "state": "stopped"}), now=NOW + 3600)
    assert card["state"] == "stopped" and card["live"] is False


def test_partial_is_live_as_in_the_observer(tmp_path):
    assert dos.read(_write(tmp_path, {**FRESH, "state": "partial"}), now=NOW)["live"] is True


def test_unreadable_status_is_down(tmp_path):
    (tmp_path / "status.json").write_text("{not json")
    card = dos.read(tmp_path, now=NOW)
    assert card["state"] == "down" and card["reason"] == dos.UNREADABLE


def test_unreadable_status_keeps_the_exception_out_of_the_card(tmp_path, caplog):
    """The card is an API response: no path, errno or parse position in it."""
    (tmp_path / "status.json").mkdir()  # read_text() raises IsADirectoryError
    card = dos.read(tmp_path, now=NOW)
    assert card["reason"] == dos.UNREADABLE and card["fix"] == dos.UNREADABLE_FIX
    assert str(tmp_path) not in json.dumps(card)
    assert str(tmp_path) in caplog.text  # ...the operator still gets it
