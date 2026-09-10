"""Send one message (optionally with a PNG) into the OpenClaw chat.

Two containers talk to OpenClaw's Gateway: the alerter pushes incidents, and
the MCP server hands over a chart someone asked for (``get_chart`` with
``deliver=True``). Both use the same endpoint, ``POST {OPENCLAW_URL}/tools/invoke``,
invoking the Gateway's ``message`` tool -- so the payload shape lives here
once. The alerter keeps its own retry loop (nobody is waiting on an alert,
so it can back off); :func:`send` here is single-shot because a person is
waiting on the tool result and would rather see the error than wait 10 s.

Endpoint traps, all verified against OpenClaw 2026.7.x:

- It answers **HTTP 200 with** ``{"ok": false}`` when the tool itself refuses
  (blocked by policy, bad channel, unknown recipient). Status alone is not
  delivery.
- A tool blocked by policy answers **404** with ``Tool not available`` in the
  body -- the same status as a missing route, so the body must be read.
"""

from __future__ import annotations

import base64
import logging
import os

import httpx

log = logging.getLogger("openclaw")

DEFAULT_URL = "http://127.0.0.1:18789"
# Always enabled on stock OpenClaw, multiplexed onto the gateway port.
# OPENCLAW_HOOK_PATH exists only so a bridge or proxy can be slotted in.
DEFAULT_HOOK_PATH = "/tools/invoke"
TOOL = "message"
DEFAULT_CHANNEL = "telegram"
TIMEOUT_S = 15.0


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def hook_url() -> str:
    base = (os.environ.get("OPENCLAW_URL") or DEFAULT_URL).rstrip("/")
    path = os.environ.get("OPENCLAW_HOOK_PATH") or DEFAULT_HOOK_PATH
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def token() -> str:
    """Gateway token. OPENCLAW_GATEWAY_TOKEN is OpenClaw's own env name;
    OPENCLAW_HOOK_TOKEN is accepted as a legacy alias."""
    return (
        os.environ.get("OPENCLAW_GATEWAY_TOKEN")
        or os.environ.get("OPENCLAW_HOOK_TOKEN")
        or ""
    )


def configured() -> str | None:
    """None when delivery can work; otherwise the one-line reason it cannot.

    Checked before building a payload so a tool can say *why* the chart is
    not in the chat rather than failing with a transport error.
    """
    if not token():
        return "no Gateway token (OPENCLAW_GATEWAY_TOKEN is unset)"
    if not os.environ.get("OPENCLAW_TO"):
        return "no recipient (OPENCLAW_TO is unset)"
    return None


def invoke_payload(
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
        "channel": os.environ.get("OPENCLAW_CHANNEL") or DEFAULT_CHANNEL,
        "to": os.environ.get("OPENCLAW_TO", ""),
    }
    quiet = _env_bool("ALERT_SILENT", False) if silent is None else bool(silent)

    if image is None:
        args["message"] = text
        if quiet:
            args["silent"] = True
        return {"name": TOOL, "args": args}

    args["buffer"] = base64.b64encode(image).decode("ascii")
    args["filename"] = filename or "smokeping.png"
    args["mimeType"] = "image/png"
    args["caption"] = text
    if _env_bool("ALERT_IMAGE_AS_DOCUMENT", True):
        args["forceDocument"] = True
    if quiet:
        args["silent"] = True
    return {"name": TOOL, "args": args}


def body_error(response: httpx.Response) -> str | None:
    """Return an error string when a 2xx body actually reports failure."""
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


def send(
    text: str,
    image: bytes | None = None,
    filename: str | None = None,
    silent: bool | None = None,
) -> str | None:
    """Deliver one message. Returns None on success, else the reason.

    Single attempt, no retry: this is called with a person waiting on the
    other end of a tool call. The reason string is meant to be shown to them
    verbatim ("blocked by policy", "OPENCLAW_TO is unset"), which is how a
    misconfiguration gets noticed and fixed instead of logged and ignored.
    """
    problem = configured()
    if problem:
        return problem
    payload = invoke_payload(text, image, filename, silent)
    try:
        response = httpx.post(
            hook_url(), json=payload,
            headers={"Authorization": f"Bearer {token()}"}, timeout=TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        return f"gateway unreachable at {hook_url()}: {exc}"
    if response.status_code == 404:
        # Blocked-by-policy and missing-route share this status.
        return (f"gateway answered 404 -- the '{TOOL}' tool is blocked by "
                f"policy, or {hook_url()} is not a /tools/invoke endpoint: "
                f"{response.text[:200]}")
    if not 200 <= response.status_code < 300:
        return f"gateway answered HTTP {response.status_code}: {response.text[:200]}"
    return body_error(response)
