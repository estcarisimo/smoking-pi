"""Reports watcher: single delivery per interval, truncation, missing dir."""

import os

import pytest

import notifier
import reports_watcher


@pytest.fixture
def delivered(monkeypatch):
    events = []

    def fake_notify(event):
        events.append(event)
        return True

    monkeypatch.setattr(notifier, "notify", fake_notify)
    return events


@pytest.fixture
def reports_dir(monkeypatch, tmp_path):
    directory = tmp_path / "reports"
    directory.mkdir()
    monkeypatch.setenv("REPORTS_DIR", str(directory))
    return directory


def _write_report(directory, name, content, mtime):
    path = directory / name
    path.write_text(content)
    os.utime(path, (mtime, mtime))
    return path


def test_missing_dir_skips_quietly(monkeypatch, tmp_path, delivered):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "nope"))
    assert reports_watcher.check({}, now=1000.0) is False
    assert delivered == []


def test_delivers_newest_report_once(reports_dir, delivered):
    _write_report(reports_dir, "report-old.md", "old", mtime=100)
    _write_report(reports_dir, "report-new.md", "new content", mtime=200)
    _write_report(reports_dir, "not-a-report.md", "ignored", mtime=300)

    state = {}
    assert reports_watcher.check(state, now=1000.0) is True
    assert len(delivered) == 1
    event = delivered[0]
    assert event["type"] == "report"
    assert event["message"] == "Daily network health report:\n\nnew content"
    assert state["reports"]["last_delivered_file"] == "report-new.md"

    # Same iteration state: nothing new, and interval not yet elapsed.
    assert reports_watcher.check(state, now=1001.0) is False
    assert len(delivered) == 1


def test_at_most_one_per_interval_even_with_new_files(reports_dir, delivered):
    _write_report(reports_dir, "report-a.md", "a", mtime=100)
    state = {}
    assert reports_watcher.check(state, now=1000.0) is True

    # A newer report appears, but the delivery interval has not elapsed.
    _write_report(reports_dir, "report-b.md", "b", mtime=2000)
    assert reports_watcher.check(state, now=1000.0 + 3600) is False

    # After the interval, the new report goes out.
    assert reports_watcher.check(state, now=1000.0 + 86400) is True
    assert delivered[-1]["message"].endswith("b")


def test_old_reports_not_redelivered_after_interval(reports_dir, delivered):
    _write_report(reports_dir, "report-a.md", "a", mtime=100)
    state = {}
    assert reports_watcher.check(state, now=1000.0) is True
    # Interval elapsed but no file newer than the delivered one.
    assert reports_watcher.check(state, now=1000.0 + 86400 * 2) is False
    assert len(delivered) == 1


def test_truncation(reports_dir, delivered, monkeypatch):
    monkeypatch.setenv("REPORT_MAX_CHARS", "10")
    _write_report(reports_dir, "report-big.md", "x" * 100, mtime=100)
    assert reports_watcher.check({}, now=1000.0) is True
    assert delivered[0]["message"] == reports_watcher.HEADER + "x" * 10


def test_failed_delivery_not_marked_delivered(reports_dir, monkeypatch):
    monkeypatch.setattr(notifier, "notify", lambda event: False)
    _write_report(reports_dir, "report-a.md", "a", mtime=100)
    state = {}
    assert reports_watcher.check(state, now=1000.0) is False
    assert state["reports"] == {}
