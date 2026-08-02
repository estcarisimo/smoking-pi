"""Notifier tests: per-mode delivery, formatting, retry behaviour."""

import httpx
import pytest

import notifier


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


@pytest.fixture
def posts(monkeypatch):
    """Capture httpx.post calls; each test configures the responses."""
    calls = []
    responses = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        result = responses.pop(0) if responses else FakeResponse(200)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(notifier.httpx, "post", fake_post)
    monkeypatch.setattr(notifier.time, "sleep", lambda s: None)
    return {"calls": calls, "responses": responses}


def _event(**overrides):
    event = {
        "type": "alert",
        "rule": "target_down",
        "severity": "critical",
        "target": "google",
        "message": "google down: 100% loss across all 5 probes in the window",
        "state": {"notified_count": 1},
    }
    event.update(overrides)
    return event


# --- formatting -------------------------------------------------------------

def test_format_alert_has_severity_emoji():
    text = notifier.format_message(_event())
    assert text.startswith("🔴")
    assert "target_down" in text


def test_format_warning_and_recovery():
    assert notifier.format_message(_event(severity="warning")).startswith("🟠")
    assert notifier.format_message(_event(type="recovery")).startswith("✅")


def test_format_report_passes_message_through():
    text = notifier.format_message({"type": "report", "message": "Daily report"})
    assert text == "Daily report"


# --- off mode ---------------------------------------------------------------

def test_off_mode_logs_only(posts):
    assert notifier.notify(_event()) is True
    assert posts["calls"] == []


def test_unknown_mode_falls_back_to_log_only(posts, monkeypatch):
    monkeypatch.setenv("NOTIFY_MODE", "carrier-pigeon")
    assert notifier.notify(_event()) is True
    assert posts["calls"] == []


# --- openclaw mode ----------------------------------------------------------

def test_openclaw_posts_hook_payload(posts, monkeypatch):
    monkeypatch.setenv("NOTIFY_MODE", "openclaw")
    monkeypatch.setenv("OPENCLAW_URL", "http://gateway.example:18789")
    monkeypatch.setenv("OPENCLAW_HOOK_TOKEN", "test-token")
    monkeypatch.setenv("OPENCLAW_CHANNEL", "telegram")
    monkeypatch.setenv("OPENCLAW_TO", "12345")

    assert notifier.notify(_event()) is True
    call = posts["calls"][0]
    assert call["url"] == "http://gateway.example:18789/hooks/agent"
    assert call["headers"]["Authorization"] == "Bearer test-token"
    body = call["json"]
    assert body["name"] == "SmokePing Alerts"
    assert body["wakeMode"] == "now"
    assert body["deliver"] is True
    assert body["channel"] == "telegram"
    assert body["to"] == "12345"
    assert body["message"].startswith("🔴")


def test_openclaw_without_token_skips(posts, monkeypatch):
    monkeypatch.setenv("NOTIFY_MODE", "openclaw")
    assert notifier.notify(_event()) is False
    assert posts["calls"] == []


# --- webhook mode -----------------------------------------------------------

def test_webhook_payload_and_bearer(posts, monkeypatch):
    monkeypatch.setenv("NOTIFY_MODE", "webhook")
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.example/alert")
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", "hook-token")

    assert notifier.notify(_event()) is True
    call = posts["calls"][0]
    assert call["url"] == "https://hooks.example/alert"
    assert call["headers"]["Authorization"] == "Bearer hook-token"
    body = call["json"]
    assert body["type"] == "alert"
    assert body["rule"] == "target_down"
    assert body["severity"] == "critical"
    assert body["target"] == "google"
    assert body["state"] == {"notified_count": 1}
    assert "ts" in body


def test_webhook_without_token_omits_auth_header(posts, monkeypatch):
    monkeypatch.setenv("NOTIFY_MODE", "webhook")
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.example/alert")
    assert notifier.notify(_event()) is True
    assert "Authorization" not in posts["calls"][0]["headers"]


# --- retries ----------------------------------------------------------------

def test_retry_then_success(posts, monkeypatch):
    monkeypatch.setenv("NOTIFY_MODE", "webhook")
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.example/alert")
    posts["responses"].extend(
        [httpx.ConnectError("refused"), FakeResponse(500), FakeResponse(200)]
    )
    assert notifier.notify(_event()) is True
    assert len(posts["calls"]) == 3


def test_retry_then_fail_returns_false(posts, monkeypatch):
    monkeypatch.setenv("NOTIFY_MODE", "webhook")
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.example/alert")
    posts["responses"].extend([httpx.ConnectError("refused")] * 3)
    assert notifier.notify(_event()) is False
    assert len(posts["calls"]) == 3
