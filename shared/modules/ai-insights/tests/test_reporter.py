"""Reporter guardrail tests -- anthropic and the collector are mocked."""

import json
from datetime import date

import pytest

import reporter


SAMPLE_DATA = {
    "window_hours": 24,
    "generated_at": "2026-07-28T04:00:00+00:00",
    "target_total": 1,
    "targets_truncated": False,
    "targets": [
        {
            "target": "google_dns",
            "measurement": "latency",
            "median_ms": 12.0,
            "p95_ms": 30.0,
            "avg_loss_pct": 2.0,
            "max_loss_pct": 35.0,
            "loss_events": 7,
        }
    ],
    "cpe": {"stats": [], "worst_windows": []},
}


@pytest.fixture()
def reports_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    return tmp_path


def test_render_summary_contains_numbers():
    text = reporter.render_summary(SAMPLE_DATA)
    assert "google_dns" in text
    assert "median=12.0ms" in text
    assert "avg_loss=2.0%" in text
    assert "loss_events=7" in text


def test_render_summary_truncates_with_note(monkeypatch):
    monkeypatch.setenv("AI_MAX_INPUT_CHARS", "200")
    big = dict(SAMPLE_DATA)
    big["targets"] = SAMPLE_DATA["targets"] * 200
    text = reporter.render_summary(big)
    assert len(text) <= 200
    assert text.endswith(reporter.TRUNCATION_NOTE)


def test_run_once_without_api_key_skips_cleanly(monkeypatch, reports_dir, caplog):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with caplog.at_level("INFO", logger="ai-insights"):
        result = reporter.run_once()
    assert result is None
    assert "ANTHROPIC_API_KEY" in caplog.text
    assert list(reports_dir.glob("report-*.md")) == []


def _fake_anthropic(monkeypatch, text="## All good\nNo issues."):
    """Stub anthropic.Anthropic().messages.create()."""
    import types

    calls = []

    class FakeBlock:
        type = "text"

        def __init__(self, t):
            self.text = t

    class FakeMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(content=[FakeBlock(text)])

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    return calls


def test_run_once_writes_report_and_latest(monkeypatch, reports_dir):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    calls = _fake_anthropic(monkeypatch)
    import collector

    monkeypatch.setattr(collector, "collect", lambda hours=24: SAMPLE_DATA)

    path = reporter.run_once()

    assert path is not None and path.exists()
    assert path.name.startswith("report-") and path.suffix == ".md"
    latest = (reports_dir / "latest.md").read_text()
    assert "All good" in latest
    # the model saw the rendered summary, under the analyst system prompt
    assert calls[0]["system"] == reporter.SYSTEM_PROMPT
    assert "google_dns" in calls[0]["messages"][0]["content"]
    # state file recorded one report today
    state = json.loads((reports_dir / reporter.STATE_FILENAME).read_text())
    assert state == {"date": date.today().isoformat(), "count": 1}


def test_reports_per_day_cap(monkeypatch, reports_dir, caplog):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AI_REPORTS_PER_DAY", "2")
    _fake_anthropic(monkeypatch)
    import collector

    monkeypatch.setattr(collector, "collect", lambda hours=24: SAMPLE_DATA)

    assert reporter.run_once() is not None
    assert reporter.run_once() is not None
    with caplog.at_level("WARNING", logger="ai-insights"):
        assert reporter.run_once() is None  # capped
    assert "cap reached" in caplog.text
    # only two report files were written (plus latest.md)
    assert len(list(reports_dir.glob("report-*.md"))) == 2


def test_cap_resets_on_new_day(monkeypatch, reports_dir):
    monkeypatch.setenv("AI_REPORTS_PER_DAY", "1")
    (reports_dir / reporter.STATE_FILENAME).write_text(
        json.dumps({"date": "2000-01-01", "count": 99})
    )
    assert reporter._daily_cap_reached() is False
