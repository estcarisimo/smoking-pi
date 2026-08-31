"""Notification delivery for the alerting engine.

``NOTIFY_MODE`` selects the backend:

- ``off`` (default): log-only. Incidents are still evaluated and logged so
  the engine is useful without any delivery configured.
- ``openclaw``: invoke OpenClaw's ``message`` tool over the Gateway's HTTP
  endpoint, ``POST {OPENCLAW_URL}/tools/invoke``, authenticated with the
  Gateway token. Values come from env — see .env.template /
  docs/alerting.md.

  This previously targeted ``/hooks/agent``, which does not exist on any
  OpenClaw build; the resulting 404s were read as "OpenClaw has no HTTP
  ingress at all" and the mode was left unusable. It does have one:
  ``/tools/invoke`` is always enabled, multiplexed onto the same port as
  the WebSocket gateway, and gated by Gateway auth plus tool policy.
  Verified against 2026.7.1-2.

  Two failure modes are specific to this endpoint and both need catching:
  it answers **HTTP 200 with an ``{"ok": false}`` body** when the tool
  itself fails, and it answers ``Tool not available`` when the ``message``
  tool is filtered out by tool policy rather than missing. :func:`preflight`
  distinguishes bad token, blocked tool, and unreachable gateway at
  startup, instead of letting every incident quietly exhaust its retries.
- ``webhook``: POST ``ALERT_WEBHOOK_URL`` with a generic JSON payload
  ``{type, rule, severity, target, message, state, ts}`` and an optional
  ``Authorization: Bearer {ALERT_WEBHOOK_TOKEN}`` header.

Delivery uses httpx with a 10s timeout and 3 attempts (exponential
backoff). Never raises: returns True on success, False otherwise.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime

import httpx

import templates

log = logging.getLogger("alerter.notifier")

DEFAULT_OPENCLAW_URL = "http://127.0.0.1:18789"
# The Gateway's HTTP tool-invoke endpoint. Always enabled, same port as the
# WebSocket gateway. Overridable via OPENCLAW_HOOK_PATH only so a bridge or
# proxy can be slotted in; the default is what stock OpenClaw serves.
DEFAULT_OPENCLAW_HOOK_PATH = "/tools/invoke"
OPENCLAW_TOOL = "message"
DEFAULT_OPENCLAW_CHANNEL = "telegram"
TIMEOUT_S = 10.0
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 1.0

# Kept as an alias; the table itself now lives in templates.
_SEVERITY_EMOJI = templates.SEVERITY_EMOJI


def notify_mode() -> str:
    return (os.environ.get("NOTIFY_MODE") or "off").strip().lower()


def format_message(event: dict, limit: int = templates.TG_TEXT_LIMIT) -> str:
    """Render an event for delivery. See templates.py for the budget rules."""
    return templates.format_message(event, limit)


def notify(event: dict) -> bool:
    """Deliver one event via the configured backend. Never raises."""
    mode = notify_mode()
    text = format_message(event)

    if mode == "openclaw":
        return _notify_openclaw(text)
    if mode == "webhook":
        return _notify_webhook(event, text)
    if mode != "off":
        log.warning("Unknown NOTIFY_MODE %r; logging only", mode)
    log.info("NOTIFY (%s) %s", mode, text)
    return True


def openclaw_hook_url() -> str:
    base = (os.environ.get("OPENCLAW_URL") or DEFAULT_OPENCLAW_URL).rstrip("/")
    path = os.environ.get("OPENCLAW_HOOK_PATH") or DEFAULT_OPENCLAW_HOOK_PATH
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def openclaw_token() -> str:
    """Gateway token. OPENCLAW_GATEWAY_TOKEN is OpenClaw's own env name."""
    return (
        os.environ.get("OPENCLAW_GATEWAY_TOKEN")
        or os.environ.get("OPENCLAW_HOOK_TOKEN")
        or ""
    )


def openclaw_invoke_payload(text: str) -> dict:
    return {
        "name": OPENCLAW_TOOL,
        "args": {
            "action": "send",
            "channel": os.environ.get("OPENCLAW_CHANNEL")
            or DEFAULT_OPENCLAW_CHANNEL,
            "to": os.environ.get("OPENCLAW_TO", ""),
            "message": text,
        },
    }


def _notify_openclaw(text: str) -> bool:
    url = openclaw_hook_url()
    token = openclaw_token()
    if not token:
        log.warning(
            "NOTIFY_MODE=openclaw but no Gateway token "
            "(OPENCLAW_GATEWAY_TOKEN/OPENCLAW_HOOK_TOKEN); skipping"
        )
        return False
    if not os.environ.get("OPENCLAW_TO"):
        log.warning("NOTIFY_MODE=openclaw but OPENCLAW_TO is unset; skipping")
        return False
    headers = {"Authorization": f"Bearer {token}"}
    return _post_with_retries(
        url, openclaw_invoke_payload(text), headers, body_must_be_ok=True
    )


def _notify_webhook(event: dict, text: str) -> bool:
    url = os.environ.get("ALERT_WEBHOOK_URL", "")
    if not url:
        log.warning("NOTIFY_MODE=webhook but ALERT_WEBHOOK_URL is unset; skipping")
        return False
    payload = {
        "type": event.get("type", "alert"),
        "rule": event.get("rule"),
        "severity": event.get("severity"),
        "target": event.get("target"),
        "message": text,
        "state": event.get("state"),
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    headers = {}
    token = os.environ.get("ALERT_WEBHOOK_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return _post_with_retries(url, payload, headers)


def _preflight_openclaw() -> bool:
    """Probe /tools/invoke so a misconfiguration is loud at startup.

    Invokes the `message` tool with no arguments. A reachable, permitted tool
    rejects that with its own validation error, which is exactly the signal we
    want: the endpoint answered, the token was accepted, and the tool is not
    filtered out by policy. Never raises — this is diagnostics, not a gate.
    """
    url = openclaw_hook_url()
    if not openclaw_token():
        log.error("NOTIFY_MODE=openclaw but no Gateway token is set "
                  "(OPENCLAW_GATEWAY_TOKEN); alerts will not be delivered")
        return False
    if not os.environ.get("OPENCLAW_TO"):
        log.error("NOTIFY_MODE=openclaw but OPENCLAW_TO is unset; alerts have "
                  "nowhere to go")
        return False

    headers = {"Authorization": f"Bearer {openclaw_token()}"}
    try:
        response = httpx.post(
            url, json={"name": OPENCLAW_TOOL, "args": {}},
            headers=headers, timeout=TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        log.error("Delivery preflight to %s failed: %s — is the OpenClaw "
                  "gateway running? Alerts will not be delivered.", url, exc)
        return False

    if response.status_code == 401:
        log.error("Delivery preflight: %s rejected the Gateway token (401). "
                  "It must match gateway.auth.token in openclaw.json.", url)
        return False

    # A 404 here is ambiguous and the distinction matters: the endpoint
    # answers 404 both when the ROUTE is absent and when the TOOL is filtered
    # out by policy. Reading the body is what separates them. Conflating the
    # two is precisely the mistake that produced "OpenClaw has no HTTP
    # ingress" from a single missing path.
    error = _body_error(response) or ""
    if "not available" in error.lower() or "unknown tool" in error.lower():
        log.error(
            "Delivery preflight: %s is reachable, but the %r tool is not "
            "available to it (%s) — allow it via tools.allow in "
            "openclaw.json. This is a tool-policy problem, not a missing "
            "endpoint.", url, OPENCLAW_TOOL, error)
        return False
    if response.status_code == 404:
        log.error("Delivery preflight: %s returned 404 with no tool-policy "
                  "explanation — the route itself is absent. Check "
                  "OPENCLAW_HOOK_PATH. See docs/openclaw-integration.md", url)
        return False

    if response.status_code >= 500:
        log.error(
            "Delivery preflight: %s answered HTTP %s — the gateway itself is "
            "failing, so this says nothing about whether the %r tool is "
            "permitted. Alerts are unlikely to be delivered until the gateway "
            "is healthy.", url, response.status_code, OPENCLAW_TOOL)
        return False

    # Any other error here is the tool's own argument validation, which means
    # it is reachable and permitted — the good case.
    log.info("Delivery preflight: %s reachable, %r tool permitted (HTTP %s)",
             url, OPENCLAW_TOOL, response.status_code)
    return True


def preflight() -> bool:
    """Check the configured delivery endpoint once, at startup.

    Returns True when delivery looks usable, so a misconfiguration is visible
    immediately instead of showing up as every alert quietly exhausting its
    retries. Never raises — this is diagnostics, not a gate.
    """
    mode = notify_mode()
    if mode == "off":
        return True

    if mode == "openclaw":
        return _preflight_openclaw()

    if mode == "webhook":
        url = os.environ.get("ALERT_WEBHOOK_URL", "")
        if not url:
            log.error("NOTIFY_MODE=webhook but ALERT_WEBHOOK_URL is unset; "
                      "alerts will not be delivered")
            return False
    else:
        return True

    try:
        response = httpx.post(url, json={"preflight": True}, timeout=TIMEOUT_S)
    except httpx.HTTPError as exc:
        log.error("Delivery preflight to %s failed: %s — alerts will not be "
                  "delivered until this is reachable", url, exc)
        return False

    if response.status_code == 404:
        log.error(
            "Delivery preflight: %s returned 404 — the webhook endpoint does "
            "not exist. Check ALERT_WEBHOOK_URL.", url)
        return False
    # 401/403 mean the route exists and rejected an unauthenticated probe,
    # which is exactly what a correctly-secured endpoint should do.
    log.info("Delivery preflight: %s reachable (HTTP %s)", url,
             response.status_code)
    return True


def _body_error(response: httpx.Response) -> str | None:
    """Return an error string when a 2xx body actually reports failure.

    /tools/invoke answers HTTP 200 with {"ok": false, "error": {...}} when the
    tool refuses — a blocked tool, a bad channel, an unknown recipient. Trusting
    the status code alone would count every one of those as a delivered alert,
    which is the worst possible failure for a notifier.
    """
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict) or body.get("ok") is not False:
        return None
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error or "ok=false")


def _post_with_retries(
    url: str, payload: dict, headers: dict, body_must_be_ok: bool = False
) -> bool:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = httpx.post(
                url, json=payload, headers=headers, timeout=TIMEOUT_S
            )
            if 200 <= response.status_code < 300:
                error = _body_error(response) if body_must_be_ok else None
                if error is None:
                    return True
                log.warning(
                    "Notification to %s returned HTTP %s but the body reports "
                    "failure: %s (attempt %d/%d)",
                    url,
                    response.status_code,
                    error,
                    attempt,
                    MAX_ATTEMPTS,
                )
            else:
                log.warning(
                    "Notification POST to %s returned HTTP %s (attempt %d/%d)",
                    url,
                    response.status_code,
                    attempt,
                    MAX_ATTEMPTS,
                )
        except httpx.HTTPError as exc:
            log.warning(
                "Notification POST to %s failed: %s (attempt %d/%d)",
                url,
                exc,
                attempt,
                MAX_ATTEMPTS,
            )
        if attempt < MAX_ATTEMPTS:
            time.sleep(BACKOFF_BASE_S * (2 ** (attempt - 1)))
    log.error("Notification delivery to %s failed after %d attempts", url, MAX_ATTEMPTS)
    return False
