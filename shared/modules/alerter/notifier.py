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

import base64
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


def notify(event: dict, image: bytes | None = None) -> bool:
    """Deliver one event via the configured backend. Never raises.

    ``image`` is an optional PNG delivered as the message's attachment, with
    the text as its caption. Telegram allows a quarter as many characters in
    a caption as in a message, so the text is rendered to whichever budget
    applies -- see templates.assemble.
    """
    mode = notify_mode()
    limit = templates.TG_CAPTION_LIMIT if image else templates.TG_TEXT_LIMIT
    text = format_message(event, limit)

    if mode == "openclaw":
        if image is None:
            # `event` matters here, not just on the retry below: this is the
            # path a digest takes whenever there is no chart (ALERT_CHARTS
            # off, or every target clean so render_digest_chart returns None),
            # and without it the message loses its own `silent` flag and buzzes.
            return _notify_openclaw(text, event=event)
        if _notify_openclaw(text, image=image, event=event):
            return True
        # A chart failure must never cost the alert. Exactly ONE text-only
        # retry, as its own call: folding it into _post_with_retries would
        # multiply the 3-attempt budget into 6.
        log.warning("Image delivery failed; retrying text-only (chart_dropped=1)")
        # `event` still goes through: without it the retry loses the
        # message's own `silent` flag and a quiet digest rings on the retry.
        return _notify_openclaw(
            format_message(event, templates.TG_TEXT_LIMIT), event=event
        )
    if mode == "webhook":
        # A generic webhook receives JSON; an image has nowhere to go in it.
        return _notify_webhook(event, text)
    if mode != "off":
        log.warning("Unknown NOTIFY_MODE %r; logging only", mode)
    log.info("NOTIFY (%s)%s %s", mode, " [+chart]" if image else "", text)
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


def openclaw_invoke_payload(
    text: str,
    image: bytes | None = None,
    filename: str | None = None,
    silent: bool | None = None,
) -> dict:
    """Build the /tools/invoke body for one message.

    With an image the bytes ride as base64 in ``buffer`` and the text becomes
    ``caption``; there is no filesystem in this path, so nothing has to be
    shared between the container and the gateway host. ``forceDocument``
    defaults on because Telegram re-encodes photos as JPEG, and thin chart
    lines with small tick text are the worst case for JPEG.

    ``silent`` is per-message and wins over ``ALERT_SILENT``, because
    "notify quietly" is a property of *this* message rather than of the
    deployment: a daily digest should not buzz a phone, while the alert that
    wakes you at 3am should.
    """
    args = {
        "action": "send",
        "channel": os.environ.get("OPENCLAW_CHANNEL") or DEFAULT_OPENCLAW_CHANNEL,
        "to": os.environ.get("OPENCLAW_TO", ""),
    }
    quiet = _env_bool("ALERT_SILENT", False) if silent is None else bool(silent)

    if image is None:
        args["message"] = text
        # Previously only the image path could be silent, which meant a
        # text-only digest always rang.
        if quiet:
            args["silent"] = True
        return {"name": OPENCLAW_TOOL, "args": args}

    args["buffer"] = base64.b64encode(image).decode("ascii")
    args["filename"] = filename or "smokeping.png"
    args["mimeType"] = "image/png"
    args["caption"] = text
    if _env_bool("ALERT_IMAGE_AS_DOCUMENT", True):
        args["forceDocument"] = True
    if quiet:
        args["silent"] = True
    return {"name": OPENCLAW_TOOL, "args": args}


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _chart_filename(event: dict) -> str:
    slug = "".join(
        ch if ch.isalnum() or ch in "-_" else "-"
        for ch in str(event.get("target") or event.get("rule") or "alert")
    )
    return f"smokeping-{slug}-{int(time.time())}.png"


def _notify_openclaw(
    text: str, image: bytes | None = None, event: dict | None = None
) -> bool:
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
    payload = openclaw_invoke_payload(
        text,
        image,
        _chart_filename(event or {}) if image else None,
        silent=(event or {}).get("silent"),
    )
    return _post_with_retries(url, payload, headers, body_must_be_ok=True)


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
