"""Notifier tests: per-mode delivery, formatting, retry behaviour."""

import httpx
import pytest

import notifier


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        # /tools/invoke answers 200 with {"ok": false} when the tool refuses,
        # so the body matters as much as the status code.
        self._body = {"ok": True} if body is None else body

    def json(self):
        if self._body is _NOT_JSON:
            raise ValueError("not json")
        return self._body


_NOT_JSON = object()


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
    assert notifier.format_message(_event(severity="warning")).startswith("🟡")
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

@pytest.fixture
def openclaw(monkeypatch):
    monkeypatch.setenv("NOTIFY_MODE", "openclaw")
    monkeypatch.setenv("OPENCLAW_URL", "http://gateway.example:18789")
    monkeypatch.delenv("OPENCLAW_HOOK_PATH", raising=False)
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "test-token")
    monkeypatch.setenv("OPENCLAW_CHANNEL", "telegram")
    monkeypatch.setenv("OPENCLAW_TO", "telegram:12345")


def test_openclaw_invokes_the_message_tool(posts, openclaw):
    assert notifier.notify(_event()) is True
    call = posts["calls"][0]
    assert call["url"] == "http://gateway.example:18789/tools/invoke"
    assert call["headers"]["Authorization"] == "Bearer test-token"
    body = call["json"]
    assert body["name"] == "message"
    assert body["args"]["action"] == "send"
    assert body["args"]["channel"] == "telegram"
    assert body["args"]["to"] == "telegram:12345"
    assert body["args"]["message"].startswith("🔴")


def test_openclaw_channel_defaults_to_telegram(posts, openclaw, monkeypatch):
    monkeypatch.delenv("OPENCLAW_CHANNEL", raising=False)
    assert notifier.notify(_event()) is True
    assert posts["calls"][0]["json"]["args"]["channel"] == "telegram"


def test_openclaw_accepts_the_legacy_token_name(posts, openclaw, monkeypatch):
    monkeypatch.delenv("OPENCLAW_GATEWAY_TOKEN")
    monkeypatch.setenv("OPENCLAW_HOOK_TOKEN", "legacy-token")
    assert notifier.notify(_event()) is True
    assert posts["calls"][0]["headers"]["Authorization"] == "Bearer legacy-token"


def test_gateway_token_wins_over_the_legacy_name(posts, openclaw, monkeypatch):
    monkeypatch.setenv("OPENCLAW_HOOK_TOKEN", "legacy-token")
    assert notifier.notify(_event()) is True
    assert posts["calls"][0]["headers"]["Authorization"] == "Bearer test-token"


def test_openclaw_without_token_skips(posts, monkeypatch):
    monkeypatch.setenv("NOTIFY_MODE", "openclaw")
    monkeypatch.delenv("OPENCLAW_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("OPENCLAW_HOOK_TOKEN", raising=False)
    assert notifier.notify(_event()) is False
    assert posts["calls"] == []


def test_openclaw_without_recipient_skips(posts, openclaw, monkeypatch):
    """A message with nowhere to go must not be reported as delivered."""
    monkeypatch.delenv("OPENCLAW_TO")
    assert notifier.notify(_event()) is False
    assert posts["calls"] == []


def test_http_200_with_ok_false_is_a_failure(posts, openclaw):
    """The trap: /tools/invoke reports tool failures inside a 200 response."""
    body = {"ok": False, "error": {"type": "tool_error", "message": "boom"}}
    posts["responses"].extend(FakeResponse(200, body) for _ in range(3))
    assert notifier.notify(_event()) is False
    assert len(posts["calls"]) == 3  # retried, not silently accepted


def test_ok_false_recovers_on_retry(posts, openclaw):
    posts["responses"].append(
        FakeResponse(200, {"ok": False, "error": {"message": "transient"}})
    )
    posts["responses"].append(FakeResponse(200, {"ok": True}))
    assert notifier.notify(_event()) is True
    assert len(posts["calls"]) == 2


def test_non_json_body_does_not_break_delivery(posts, openclaw):
    posts["responses"].append(FakeResponse(200, _NOT_JSON))
    assert notifier.notify(_event()) is True


def test_webhook_mode_ignores_the_body(posts, monkeypatch):
    """Only /tools/invoke has ok-in-body semantics; a generic hook does not."""
    monkeypatch.setenv("NOTIFY_MODE", "webhook")
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hook.example/x")
    posts["responses"].append(FakeResponse(200, {"ok": False}))
    assert notifier.notify(_event()) is True
    assert len(posts["calls"]) == 1


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


# ---------------------------------------------------------------------------
# endpoint construction + startup preflight
# ---------------------------------------------------------------------------

def test_hook_url_defaults(monkeypatch):
    monkeypatch.delenv("OPENCLAW_URL", raising=False)
    monkeypatch.delenv("OPENCLAW_HOOK_PATH", raising=False)
    assert notifier.openclaw_hook_url() == "http://127.0.0.1:18789/tools/invoke"


def test_hook_url_is_configurable(monkeypatch):
    # Overridable so a proxy or bridge can mount the endpoint elsewhere.
    monkeypatch.setenv("OPENCLAW_URL", "http://gw:9000/")
    monkeypatch.setenv("OPENCLAW_HOOK_PATH", "rpc/notify")
    assert notifier.openclaw_hook_url() == "http://gw:9000/rpc/notify"


def test_preflight_off_mode_is_noop(monkeypatch):
    monkeypatch.setenv("NOTIFY_MODE", "off")
    assert notifier.preflight() is True


def test_preflight_flags_missing_token(monkeypatch):
    monkeypatch.setenv("NOTIFY_MODE", "openclaw")
    monkeypatch.delenv("OPENCLAW_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("OPENCLAW_HOOK_TOKEN", raising=False)
    assert notifier.preflight() is False


def test_preflight_flags_missing_recipient(monkeypatch):
    monkeypatch.setenv("NOTIFY_MODE", "openclaw")
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "t")
    monkeypatch.delenv("OPENCLAW_TO", raising=False)
    assert notifier.preflight() is False


def test_preflight_reports_404_as_failure(monkeypatch, openclaw):
    monkeypatch.setattr(notifier.httpx, "post",
                        lambda *a, **k: FakeResponse(404))
    assert notifier.preflight() is False


def test_preflight_reports_a_rejected_token(monkeypatch, openclaw):
    """401 here means the Gateway token is wrong, not that the route is fine."""
    monkeypatch.setattr(notifier.httpx, "post",
                        lambda *a, **k: FakeResponse(401))
    assert notifier.preflight() is False


def test_preflight_detects_a_tool_blocked_by_policy(monkeypatch, openclaw, caplog):
    """A blocked tool answers 404 — the same status as a missing route.

    Reading the body is the only thing that separates "your tool policy blocks
    this" from "that endpoint does not exist", and reporting the wrong one is
    how the previous version of this integration concluded OpenClaw had no
    HTTP surface at all.
    """
    body = {"ok": False,
            "error": {"type": "not_found",
                      "message": "Tool not available: message"}}
    monkeypatch.setattr(notifier.httpx, "post",
                        lambda *a, **k: FakeResponse(404, body))
    with caplog.at_level("ERROR"):
        assert notifier.preflight() is False
    assert "tool-policy problem" in caplog.text
    assert "route itself is absent" not in caplog.text


def test_preflight_reports_a_genuinely_missing_route(monkeypatch, openclaw, caplog):
    """A 404 with no tool-policy explanation really is a bad path."""
    monkeypatch.setattr(notifier.httpx, "post",
                        lambda *a, **k: FakeResponse(404, {"ok": False,
                                                           "error": "nope"}))
    with caplog.at_level("ERROR"):
        assert notifier.preflight() is False
    assert "route itself is absent" in caplog.text


def test_preflight_reports_a_failing_gateway(monkeypatch, openclaw, caplog):
    """A 5xx says the gateway is broken, not that the tool is permitted.

    The success branch reads "any status we did not recognise means the tool
    answered with its own argument validation" — which is true for a 4xx from
    the tool, and false for a gateway that never reached it. Treating 502 as
    a pass reports delivery as healthy at exactly the moment it is not.
    """
    monkeypatch.setattr(notifier.httpx, "post",
                        lambda *a, **k: FakeResponse(502))
    with caplog.at_level("ERROR"):
        assert notifier.preflight() is False
    assert "tool permitted" not in caplog.text


def test_preflight_accepts_the_tools_own_validation_error(monkeypatch, openclaw):
    """`action required` proves the endpoint, token and tool policy all work."""
    body = {"ok": False,
            "error": {"type": "tool_error", "message": "action required"}}
    monkeypatch.setattr(notifier.httpx, "post",
                        lambda *a, **k: FakeResponse(200, body))
    assert notifier.preflight() is True


def test_preflight_probes_the_message_tool(monkeypatch, openclaw):
    seen = {}

    def capture(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers)
        return FakeResponse(200, {"ok": False, "error": {"message": "action required"}})

    monkeypatch.setattr(notifier.httpx, "post", capture)
    assert notifier.preflight() is True
    assert seen["url"].endswith("/tools/invoke")
    assert seen["json"] == {"name": "message", "args": {}}
    assert seen["headers"]["Authorization"] == "Bearer test-token"


def test_preflight_unreachable_is_failure(monkeypatch):
    monkeypatch.setenv("NOTIFY_MODE", "webhook")
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "http://nope.invalid/hook")

    def boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(notifier.httpx, "post", boom)
    assert notifier.preflight() is False


# --- image delivery ---------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_image_rides_as_base64_with_the_text_as_caption(posts, openclaw):
    """No filesystem in this path: the bytes go in the request body.

    Verified against OpenClaw 2026.7.1-2 -- a `buffer` send really does
    deliver media on Telegram, which a reading of the bundle had suggested it
    would not.
    """
    import base64

    assert notifier.notify(_event(), image=PNG) is True
    args = posts["calls"][0]["json"]["args"]
    assert base64.b64decode(args["buffer"]) == PNG
    assert args["mimeType"] == "image/png"
    assert args["filename"].startswith("smokeping-google-")
    # Text belongs in the caption, never alongside it in `message`.
    assert "google" in args["caption"]
    assert "message" not in args


def test_the_chart_filename_is_bounded_and_never_empty(posts, openclaw):
    """Targets are operator-editable, so the slug needs its own limit.

    Two ways this bites: a very long target builds a filename a downstream
    store rejects, and a target made entirely of separators slugs down to
    nothing, leaving "smokeping--1739.png".
    """
    long_target = "a" * 400
    assert notifier.notify(_event(target=long_target), image=PNG) is True
    name = posts["calls"][0]["json"]["args"]["filename"]
    assert len(name) < 100, f"unbounded filename: {len(name)} chars"
    assert name.startswith("smokeping-aaa")

    posts["calls"].clear()
    assert notifier.notify(_event(target="..."), image=PNG) is True
    name = posts["calls"][0]["json"]["args"]["filename"]
    assert "--" not in name, f"slug collapsed to empty: {name}"
    assert name.startswith("smokeping-alert-")


def test_the_caption_respects_the_1024_budget(posts, openclaw):
    long_event = _event(message="x" * 6000)
    assert notifier.notify(long_event, image=PNG) is True
    args = posts["calls"][0]["json"]["args"]
    assert len(args["caption"]) <= 1024


def test_text_only_uses_the_4096_budget(posts, openclaw):
    assert notifier.notify(_event(message="x" * 6000)) is True
    args = posts["calls"][0]["json"]["args"]
    assert "buffer" not in args
    assert len(args["message"]) <= 4096


def test_force_document_is_on_by_default(posts, openclaw):
    """Telegram re-encodes photos as JPEG, which wrecks thin chart lines."""
    notifier.notify(_event(), image=PNG)
    assert posts["calls"][0]["json"]["args"]["forceDocument"] is True


def test_force_document_can_be_disabled(posts, openclaw, monkeypatch):
    monkeypatch.setenv("ALERT_IMAGE_AS_DOCUMENT", "false")
    notifier.notify(_event(), image=PNG)
    assert "forceDocument" not in posts["calls"][0]["json"]["args"]


def test_a_failed_image_send_retries_exactly_once_as_text(posts, openclaw):
    """A chart failure must never cost the alert -- but must not double the
    retry budget either. 3 attempts with the image, then ONE text-only call."""
    for _ in range(3):
        posts["responses"].append(FakeResponse(413))
    posts["responses"].append(FakeResponse(200))

    assert notifier.notify(_event(), image=PNG) is True
    assert len(posts["calls"]) == 4
    assert "buffer" in posts["calls"][0]["json"]["args"]
    assert "buffer" not in posts["calls"][3]["json"]["args"]
    assert "message" in posts["calls"][3]["json"]["args"]


def test_the_text_retry_is_bounded_when_everything_fails(posts, openclaw):
    for _ in range(10):
        posts["responses"].append(FakeResponse(500))
    assert notifier.notify(_event(), image=PNG) is False
    # 3 with the image + 3 text-only, never an unbounded cascade.
    assert len(posts["calls"]) == 6


def test_off_mode_notes_the_chart_without_sending(posts, caplog):
    with caplog.at_level("INFO", logger="alerter.notifier"):
        assert notifier.notify(_event(), image=PNG) is True
    assert "+chart" in caplog.text
    assert posts["calls"] == []
