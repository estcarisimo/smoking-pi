"""Mute-control tools: the write side of the single-writer contract.

These tools are the only writer of the mutes file. The tests here care about
the three things that would make muting dangerous: a mute that silently covers
nothing (typo'd target), a mute that outlives the outage it was hiding
(unbounded duration), and a half-written file left by a crash.
"""

import json

import pytest

import backends
import server
from common import mutes

TARGETS = [
    {"id": 1, "name": "google_dns", "host": "8.8.8.8", "title": "Google DNS",
     "category": "dns", "probe": "FPing", "is_active": True},
]


class FakeConfigAPI:
    def request(self, method, path, **kwargs):
        if (method, path) == ("GET", "/targets"):
            return {"targets": TARGETS, "total": len(TARGETS)}
        raise AssertionError(f"unexpected request: {method} {path}")


@pytest.fixture()
def api(monkeypatch):
    monkeypatch.setattr(backends, "get_config_api", lambda: FakeConfigAPI())


@pytest.fixture()
def files(monkeypatch, tmp_path):
    """Point both files at tmp_path and hand back helpers to read/seed them."""
    mutes_path = tmp_path / "mutes.json"
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("ALERT_MUTES_FILE", str(mutes_path))
    monkeypatch.setenv("ALERT_STATE_FILE", str(state_path))

    class Files:
        mutes = mutes_path
        state = state_path

        def read_mutes(self):
            return json.loads(mutes_path.read_text())["mutes"]

        def seed_state(self, incidents):
            state_path.write_text(json.dumps({"incidents": incidents}))

    return Files()


# --- mute_alerts ------------------------------------------------------------

def test_mute_requires_a_filter(files):
    """Omitting every argument must never mean "mute everything"."""
    result = server.mute_alerts()
    assert "error" in result
    assert not files.mutes.exists()


def test_mute_rejects_an_unknown_target(files, api):
    """A typo must fail loudly, not create a mute that matches nothing.

    Silently accepting `googl_dns` leaves the user believing they are covered
    while every alert still fires — or worse, believing the mute worked when
    it is the outage that stopped.
    """
    result = server.mute_alerts(target="googl_dns")
    assert "error" in result
    assert "available_targets" in result


def test_mute_writes_an_entry(files, api):
    result = server.mute_alerts(target="google_dns", hours=2, reason="rebooting")
    assert result["success"] is True
    entries = files.read_mutes()
    assert len(entries) == 1
    assert entries[0]["target"] == "google_dns"
    assert entries[0]["reason"] == "rebooting"


def test_mute_clamps_to_the_maximum(files, api):
    """An open-ended mute is how a real outage gets missed overnight."""
    result = server.mute_alerts(target="google_dns", hours=999)
    assert result["success"] is True
    assert "clamped" in result
    assert result["muted"]["remaining_minutes"] <= mutes.MAX_HOURS * 60


def test_mute_rejects_a_nonsense_duration(files, api):
    assert "error" in server.mute_alerts(target="google_dns", hours="soon")
    assert "error" in server.mute_alerts(target="google_dns", hours=0)
    assert "error" in server.mute_alerts(target="google_dns", hours=-3)


def test_muting_the_same_thing_twice_replaces_rather_than_stacks(files, api):
    server.mute_alerts(target="google_dns", hours=1)
    server.mute_alerts(target="google_dns", hours=4)
    assert len(files.read_mutes()) == 1


def test_wildcard_needs_no_target_lookup(files):
    """'*' is not a target name, so it must not be resolved as one."""
    result = server.mute_alerts(target="*", hours=1)
    assert result["success"] is True


# --- unmute_alerts ----------------------------------------------------------

def test_unmute_removes_the_matching_entry(files, api):
    server.mute_alerts(target="google_dns", hours=2)
    result = server.unmute_alerts(target="google_dns")
    assert result["removed"] == 1
    assert result["still_muted"] == []


def test_unmute_all_clears_everything(files, api):
    server.mute_alerts(target="google_dns", hours=2)
    server.mute_alerts(rule="high_loss", hours=2)
    result = server.unmute_alerts(all=True)
    assert result["removed"] == 2
    assert files.read_mutes() == []


def test_unmute_requires_a_filter_or_all(files):
    assert "error" in server.unmute_alerts()


# --- ack_incident -----------------------------------------------------------

def test_ack_requires_a_known_incident(files):
    files.seed_state({})
    result = server.ack_incident("target_down:google_dns")
    assert "error" in result
    assert result["active_keys"] == []


def test_ack_writes_a_key_scoped_entry(files):
    files.seed_state({"target_down:google_dns": {"rule": "target_down"}})
    result = server.ack_incident("target_down:google_dns")
    assert result["success"] is True
    entry = files.read_mutes()[0]
    assert entry["key"] == "target_down:google_dns"
    assert entry["clear_on_recovery"] is True
    # target/rule must be absent, or the ack could widen into a category mute.
    assert entry.get("target") is None
    assert entry.get("rule") is None


# --- list_alert_state -------------------------------------------------------

def test_list_alert_state_tolerates_a_missing_state_file(files):
    """"What's muted?" has to answer on a box where the alerter never ran."""
    result = server.list_alert_state()
    assert result["incidents"] == []
    assert result["active_mutes"] == []


def test_list_alert_state_reports_what_a_mute_is_hiding(files, api):
    files.seed_state({
        "target_down:google_dns": {
            "rule": "target_down", "severity": "critical",
            "target": "google_dns", "message": "down",
            "notified_count": 0, "muted_suppressed_count": 4,
        }
    })
    server.mute_alerts(target="google_dns", hours=2)
    result = server.list_alert_state()
    incident = result["incidents"][0]
    assert incident["muted"] is True
    assert incident["muted_suppressed_count"] == 4
    assert len(result["active_mutes"]) == 1


def test_list_alert_state_recomputes_muted_rather_than_trusting_the_cache(files):
    """The alerter's cached muted_until can be stale by the time we read it.

    A mute lifted a second ago leaves `muted_until` sitting in the state file,
    and reporting from that would tell the user something is still silenced
    when it is not.
    """
    files.seed_state({
        "target_down:google_dns": {
            "rule": "target_down", "target": "google_dns",
            "muted_until": 9_999_999_999.0,
        }
    })
    # No mutes file at all -- nothing is actually muted.
    assert server.list_alert_state()["incidents"][0]["muted"] is False


# --- durability -------------------------------------------------------------

def test_a_crash_mid_write_cannot_corrupt_the_file(files, api, monkeypatch):
    """os.replace is atomic, so a reader sees the old file or the new one.

    The alerter reads this file on its delivery path while this container
    writes it, with no lock between them — that safety rests entirely on the
    temp-file-plus-rename discipline, so it gets a test.
    """
    server.mute_alerts(target="google_dns", hours=2)
    good = files.mutes.read_text()

    real_replace = mutes.os.replace

    def exploding_replace(src, dst):
        raise OSError("simulated crash between write and rename")

    monkeypatch.setattr(mutes.os, "replace", exploding_replace)
    result = server.mute_alerts(rule="high_loss", hours=2)
    assert "error" in result

    monkeypatch.setattr(mutes.os, "replace", real_replace)
    assert files.mutes.read_text() == good, "the old file must survive intact"
    assert json.loads(good)["mutes"][0]["target"] == "google_dns"


def test_a_corrupt_mutes_file_reads_as_no_mutes(files):
    files.mutes.write_text("{ truncated")
    assert mutes.load() == []
    assert server.list_alert_state()["active_mutes"] == []
