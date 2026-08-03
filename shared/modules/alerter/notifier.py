"""Notification delivery for the alerting engine.

``NOTIFY_MODE`` selects the backend:

- ``off`` (default): log-only. Incidents are still evaluated and logged so
  the engine is useful without any delivery configured.
- ``openclaw``: POST ``{OPENCLAW_URL}{OPENCLAW_HOOK_PATH}`` with a Bearer
  token (``OPENCLAW_HOOK_TOKEN``) and the OpenClaw hook JSON body. All
  values come from env — see .env.template / docs/alerting.md.

  IMPORTANT: this needs an OpenClaw that actually exposes an HTTP ingress.
  A stock gateway (verified on 2026.7.1-2) is WebSocket-only and answers
  404 on every HTTP path, so this mode requires an HTTP-RPC plugin or a
  bridge — see docs/openclaw-integration.md. :func:`preflight` probes the
  endpoint at startup so a missing ingress is visible immediately instead
  of failing silently on the first incident.
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
DEFAULT_OPENCLAW_HOOK_PATH = "/hooks/agent"  # OPENCLAW_HOOK_PATH
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


def openclaw_hook_url() -> str:
    base = (os.environ.get("OPENCLAW_URL") or DEFAULT_OPENCLAW_URL).rstrip("/")
    path = os.environ.get("OPENCLAW_HOOK_PATH") or DEFAULT_OPENCLAW_HOOK_PATH
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def _notify_openclaw(text: str) -> bool:
    url = openclaw_hook_url()
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


def preflight() -> bool:
    """Check the configured delivery endpoint once, at startup.

    Returns True when delivery looks usable. A 404 in openclaw mode almost
    always means the gateway has no HTTP ingress (stock OpenClaw is
    WebSocket-only), which would otherwise show up as every alert quietly
    exhausting its retries. Never raises — this is diagnostics, not a gate.
    """
    mode = notify_mode()
    if mode == "off":
        return True

    if mode == "openclaw":
        url = openclaw_hook_url()
        if not os.environ.get("OPENCLAW_HOOK_TOKEN", ""):
            log.error("NOTIFY_MODE=openclaw but OPENCLAW_HOOK_TOKEN is unset; "
                      "alerts will not be delivered")
            return False
    elif mode == "webhook":
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
            "Delivery preflight: %s returned 404. The endpoint does not exist "
            "— a stock OpenClaw gateway is WebSocket-only and serves no HTTP "
            "hook route. See docs/openclaw-integration.md", url)
        return False
    # 401/403 mean the route exists and rejected an unauthenticated probe,
    # which is exactly what a correctly-secured endpoint should do.
    log.info("Delivery preflight: %s reachable (HTTP %s)", url,
             response.status_code)
    return True


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
