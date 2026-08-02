"""Notification delivery for the alerting engine.

``NOTIFY_MODE`` selects the backend:

- ``off`` (default): log-only. Incidents are still evaluated and logged so
  the engine is useful without any delivery configured.
- ``openclaw``: POST ``{OPENCLAW_URL}/hooks/agent`` with a Bearer token
  (``OPENCLAW_HOOK_TOKEN``) and the OpenClaw hook JSON body. All values
  come from env — see .env.template / docs/alerting.md for placeholders.
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

log = logging.getLogger("alerter.notifier")

DEFAULT_OPENCLAW_URL = "http://127.0.0.1:18789"
TIMEOUT_S = 10.0
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 1.0

_SEVERITY_EMOJI = {"critical": "🔴", "warning": "🟠"}


def notify_mode() -> str:
    return (os.environ.get("NOTIFY_MODE") or "off").strip().lower()


def format_message(event: dict) -> str:
    """Terse one-liner with an emoji severity prefix."""
    etype = event.get("type", "alert")
    if etype == "report":
        return str(event.get("message", ""))
    if etype == "recovery":
        return f"✅ recovered [{event.get('rule')}]: {event.get('message')}"
    emoji = _SEVERITY_EMOJI.get(event.get("severity", ""), "🟠")
    return f"{emoji} {event.get('severity')} [{event.get('rule')}]: {event.get('message')}"


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


def _notify_openclaw(text: str) -> bool:
    url = (
        os.environ.get("OPENCLAW_URL") or DEFAULT_OPENCLAW_URL
    ).rstrip("/") + "/hooks/agent"
    token = os.environ.get("OPENCLAW_HOOK_TOKEN", "")
    if not token:
        log.warning("NOTIFY_MODE=openclaw but OPENCLAW_HOOK_TOKEN is unset; skipping")
        return False
    payload = {
        "message": text,
        "name": "SmokePing Alerts",
        "wakeMode": "now",
        "deliver": True,
        "channel": os.environ.get("OPENCLAW_CHANNEL", ""),
        "to": os.environ.get("OPENCLAW_TO", ""),
    }
    headers = {"Authorization": f"Bearer {token}"}
    return _post_with_retries(url, payload, headers)


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


def _post_with_retries(url: str, payload: dict, headers: dict) -> bool:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = httpx.post(
                url, json=payload, headers=headers, timeout=TIMEOUT_S
            )
            if 200 <= response.status_code < 300:
                return True
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
