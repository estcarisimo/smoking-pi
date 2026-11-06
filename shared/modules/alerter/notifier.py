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

  ``OPENCLAW_PUSH=agent`` hands an episode to the agent instead
  (``POST {OPENCLAW_URL}/hooks/agent``, its own ``OPENCLAW_HOOKS_TOKEN``):
  the agent writes the message in the Telegram conversation where the person
  will reply, so a follow-up has the context. On any failure the same
  delivery falls back to the relay above, once. ``/hooks/agent`` did not
  exist on 2026.7.1-2, as above; verified against 2026.11.1.
- ``telegram``: straight to the owner's own Telegram bot
  (``TELEGRAM_BOT_TOKEN``, ``TELEGRAM_CHAT_ID``), no OpenClaw: see
  telegram.py, which also keeps the token (part of every Bot API URL) out
  of the logs.
- ``webhook``: POST ``ALERT_WEBHOOK_URL`` with a generic JSON payload
  ``{type, rule, severity, target, message, state, ts}`` and an optional
  ``Authorization: Bearer {ALERT_WEBHOOK_TOKEN}`` header.

Delivery uses httpx with a 10s timeout. A webhook gets 3 attempts
(exponential backoff); the OpenClaw gateway gets ONE per call, because it
keeps every request it has received in its own durable retry queue and
replays them (2026-10-09: six copies of one alert in one second, from the
three attempts with a chart and three without). :func:`send` says which of
three things happened -- delivered, handed to the gateway (which delivers it
itself), or failed -- and never raises; :func:`notify` is True only for the
first.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from datetime import datetime
from urllib.parse import urlsplit

import httpx

import ledger
import telegram
import templates
from common import openclaw

log = logging.getLogger("alerter.notifier")

DEFAULT_OPENCLAW_URL = "http://127.0.0.1:18789"
# The Gateway's HTTP tool-invoke endpoint. Always enabled, same port as the
# WebSocket gateway. Overridable via OPENCLAW_HOOK_PATH only so a bridge or
# proxy can be slotted in; the default is what stock OpenClaw serves.
DEFAULT_OPENCLAW_HOOK_PATH = "/tools/invoke"
OPENCLAW_TOOL = "message"
DEFAULT_OPENCLAW_CHANNEL = "telegram"
TIMEOUT_S = 10.0
# OPENCLAW_PUSH=agent: the hook, and the conversation it lands in. Every direct
# message the agent gets shares this session under OpenClaw's default
# session.dmScope; OPENCLAW_SESSION says otherwise for any other scope.
PUSH_AGENT, PUSH_MESSAGE = "agent", "message"
HOOKS_AGENT_PATH = "/hooks/agent"
DEFAULT_AGENT_ID = "main"
DEFAULT_SESSION_KEY = "agent:main:main"
# waitForCompletion: HTTP 200 alone only says the turn was admitted, and a
# model that refuses every turn (the reference Pi, 40 times on 2026-11-06)
# would then lose every alert in silence. So the request stays open until the
# turn settles: OpenClaw ends the turn first, and the HTTP timeout is only for
# a gateway that never answers. Blocks the alerter's loop for that long, on
# the few messages a month the policy sends.
AGENT_TURN_S = 150
AGENT_HTTP_TIMEOUT = httpx.Timeout(AGENT_TURN_S + 30.0, connect=5.0)
TRANSPORT_AGENT = "openclaw-agent"
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 1.0

# Kept as an alias; the table itself now lives in templates.
_SEVERITY_EMOJI = templates.SEVERITY_EMOJI


def notify_mode() -> str:
    return (os.environ.get("NOTIFY_MODE") or "off").strip().lower()


def format_message(event: dict, limit: int = templates.TG_TEXT_LIMIT) -> str:
    """Render an event for delivery. See templates.py for the budget rules."""
    return templates.format_message(event, limit)


# What a send came to (:func:`send`).
SENT = "sent"
# The OpenClaw gateway answered with a failure, or received the request and
# did not answer in time: it may hold the message in its retry queue and
# deliver it later, so it must not be sent again.
HANDED = "handed"
# Nobody received it: safe to try again later.
FAILED = "failed"


# What the last send() handed to its transport: the text, the mode, and
# the message id when the transport gave one. The notification record
# (notifications.py) takes it; nothing here reads it.
last_receipt: dict = {}


def take_receipt() -> dict:
    """The last send()'s receipt, once: a later reader gets {}."""
    receipt = dict(last_receipt)
    last_receipt.clear()
    return receipt


def _handed(text: str, mode: str) -> None:
    last_receipt.update({"text": text, "mode": mode, "message_id": None})


def notify(event: dict, image: bytes | None = None) -> bool:
    """Deliver one event via the configured backend; True when delivered.
    Never raises. See :func:`send` for the three outcomes."""
    return send(event, image=image) == SENT


def send(event: dict, image: bytes | None = None) -> str:
    """Deliver one event via the configured backend: SENT, HANDED or FAILED.
    Never raises.

    ``image`` is an optional PNG delivered as the message's attachment, with
    the text as its caption. Telegram allows a quarter as many characters in
    a caption as in a message, so the text is rendered to whichever budget
    applies -- see templates.assemble.
    """
    last_receipt.clear()
    mode = notify_mode()
    limit = templates.TG_CAPTION_LIMIT if image else templates.TG_TEXT_LIMIT
    text = format_message(event, limit)

    if mode == "openclaw":
        if push_mode() == PUSH_AGENT and _through_agent(event):
            why = _notify_agent(templates.agent_message(event), ledger.key_of(event),
                                event.get("kind"))
            if why is None:
                _chart_after_agent(event, image)
                _handed(format_message(event, templates.TG_TEXT_LIMIT),
                        TRANSPORT_AGENT)
                return SENT
            return _fallback(why, lambda: _relay(event, text, image))
        return _relay(event, text, image)
    if mode == "telegram":
        silent = _silent(event)
        if image is None:
            return _outcome(_telegram(text, silent=silent))
        if _telegram(text, image=image, filename=_chart_filename(event),
                     silent=silent):
            return SENT
        # As for OpenClaw: a chart failure must never cost the alert.
        log.warning("Image delivery failed; retrying text-only (chart_dropped=1)")
        return _outcome(_telegram(
            format_message(event, templates.TG_TEXT_LIMIT), silent=silent))
    if mode == "webhook":
        # A generic webhook receives JSON; an image has nowhere to go in it.
        return _outcome(_notify_webhook(event, text))
    if mode != "off":
        log.warning("Unknown NOTIFY_MODE %r; logging only", mode)
    _handed(text, mode)
    log.info("NOTIFY (%s)%s %s", mode, " [+chart]" if image else "", text)
    return SENT


def _relay(event: dict, text: str, image: bytes | None) -> str:
    """The ``message`` tool: the fixed text, verbatim."""
    # `event` matters on both calls: it carries the message's own
    # `silent` flag, and without it a quiet digest buzzes.
    outcome, refused = _notify_openclaw(text, image=image, event=event)
    if image is None or not refused:
        return outcome
    # A chart failure must never cost the alert -- but only a gateway
    # that refused the request outright (HTTP 4xx) has not queued it.
    # One that answered anything else may replay it, and a text-only
    # copy beside it is the same alert twice.
    log.warning("Image delivery refused; retrying text-only (chart_dropped=1)")
    return _notify_openclaw(
        format_message(event, templates.TG_TEXT_LIMIT), event=event
    )[0]


def _fallback(why: str, relay) -> str:
    """The agent did not deliver: the relay, once, and which path it was."""
    log.warning("Agent push failed (%s); delivering through the message relay", why)
    outcome = relay()
    log.info("Delivery path: message relay (%s), after the agent push failed", outcome)
    return outcome


def _telegram(text: str, **kwargs) -> bool:
    _handed(text, "telegram")
    telegram.last_message_id = None
    sent = telegram.send(text, **kwargs)
    last_receipt["message_id"] = telegram.last_message_id if sent else None
    return sent


def _message_id(response) -> str | None:
    """The message id in a transport's answer, when it gives one."""
    try:
        body = response.json()
    except ValueError:
        return None
    for _ in range(3):
        if not isinstance(body, dict):
            return None
        for name in ("messageId", "message_id"):
            if body.get(name) is not None:
                return str(body[name])
        body = body.get("result") or body.get("details")
    return None


def _outcome(delivered: bool) -> str:
    return SENT if delivered else FAILED


TEST_MESSAGE = (
    "🧪 Smoking Pi test: alert delivery works. Sent on request by "
    "`smoking-pi alerts`; nothing is wrong with the network."
)


def send_test() -> bool:
    """Send one labeled test message through the configured path.

    The only proof that alerts arrive is one arriving: the preflight shows
    the gateway accepts the token and permits the tool, not that the
    recipient is right. With NOTIFY_MODE=off there is nothing to send.
    """
    mode = notify_mode()
    if mode == "openclaw":
        def relay() -> str:
            return _notify_openclaw(TEST_MESSAGE)[0]

        if push_mode() != PUSH_AGENT:
            return relay() == SENT
        # Its own key each time: a test is asked for again and must run again.
        why = _notify_agent(templates.agent_test_message(),
                            f"test@{int(time.time())}", "test")
        return why is None or _fallback(why, relay) == SENT
    if mode == "telegram":
        return telegram.send(TEST_MESSAGE)
    if mode == "webhook":
        return _notify_webhook({"type": "test", "message": TEST_MESSAGE}, TEST_MESSAGE)
    log.error("NOTIFY_MODE=%s: there is no delivery to test", mode)
    return False


# Where delivery leaves the house, per mode: what ``reachable`` connects to.
# OpenClaw relays to a chat service; Telegram's API is known, any other
# channel is judged by a public resolver's HTTPS port.
REACH_TIMEOUT_S = 5.0
_CHANNEL_HOSTS = {"telegram": "api.telegram.org"}
_FALLBACK_HOST = "1.1.1.1"


def reach_target() -> tuple[str, int] | None:
    """The host:port a delivery depends on, or None when there is none."""
    mode = notify_mode()
    if mode == "telegram":
        return (_CHANNEL_HOSTS["telegram"], 443)
    if mode == "openclaw":
        channel = (os.environ.get("OPENCLAW_CHANNEL")
                   or DEFAULT_OPENCLAW_CHANNEL).strip().lower()
        return (_CHANNEL_HOSTS.get(channel, _FALLBACK_HOST), 443)
    if mode == "webhook":
        parts = urlsplit(os.environ.get("ALERT_WEBHOOK_URL", ""))
        if not parts.hostname:
            return None
        return (parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    return None


def reachable() -> bool:
    """Whether delivery could leave the house right now. Never raises.

    A TCP connect, not a send: the outbox asks this before every retry while
    the network is down, and a failed OpenClaw send leaves a copy in the
    gateway's durable queue that it replays later -- after the 12/10 hang,
    the same stale alert arrived twice once the Pi was back.
    """
    try:
        target = reach_target()
    except ValueError:  # an unparseable webhook URL: let the send decide
        return True
    if target is None:
        return True
    # The timeout is per address, and name resolution has none of its own:
    # with a dead resolver or a blackholed IPv6 address this can take longer
    # than REACH_TIMEOUT_S. It runs at most once per outbox retry interval.
    try:
        with socket.create_connection(target, timeout=REACH_TIMEOUT_S):
            return True
    except OSError:
        return False


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

    The shape lives in :mod:`common.openclaw` now, shared with the MCP
    server's on-request chart delivery; this name is kept for the call
    sites and tests here. OPENCLAW_TOOL/DEFAULT_OPENCLAW_CHANNEL above are
    the same values it uses.
    """
    return openclaw.invoke_payload(text, image, filename, silent)


def push_mode() -> str:
    """OPENCLAW_PUSH: anything but ``agent`` is the relay."""
    return (os.environ.get("OPENCLAW_PUSH") or PUSH_MESSAGE).strip().lower()


def hooks_token() -> str:
    """Not the Gateway token: OpenClaw wants a dedicated one for the hooks."""
    return (os.environ.get("OPENCLAW_HOOKS_TOKEN") or "").strip()


def hooks_url() -> str:
    base = (os.environ.get("OPENCLAW_URL") or DEFAULT_OPENCLAW_URL).rstrip("/")
    return base + HOOKS_AGENT_PATH


def _through_agent(event: dict) -> bool:
    """An episode or a recurring window: what has facts to narrate. Summaries,
    the weekly and the reports are text already and stay on the relay.

    The hook has no ``silent``, so what must wake nobody in quiet hours stays
    on the relay too (policy.py: a silent message goes as it is, at any hour).
    """
    if event.get("rule") not in ("episode", "pattern"):
        return False
    import quiet  # late: quiet -> notifications -> notifier

    return not (event.get("silent") and quiet.active(time.time()))


def agent_payload(message: str, kind: str | None = None) -> dict:
    """The /hooks/agent body: one turn in the conversation the person replies
    in, announced to their chat when it ends."""
    channel = (os.environ.get("OPENCLAW_CHANNEL")
               or DEFAULT_OPENCLAW_CHANNEL).strip()
    to = (os.environ.get("OPENCLAW_TO")
          or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    # OpenClaw's address form is for the message tool; the hook has the channel.
    to = to.removeprefix(f"{channel}:")
    return {
        "message": message,
        "name": f"Smoking Pi {kind}" if kind else "Smoking Pi",
        "agentId": DEFAULT_AGENT_ID,
        "sessionMode": "persistent",
        "sessionKey": (os.environ.get("OPENCLAW_SESSION")
                       or DEFAULT_SESSION_KEY).strip(),
        "deliver": True,
        "channel": channel,
        "to": to,
        "waitForCompletion": True,
        "timeoutSeconds": AGENT_TURN_S,
    }


def _completion_failure(completion: object) -> str | None:
    """Why a settled turn did not tell the person, or None when it did.

    ``delivered: false`` alone proves nothing: a turn that sent through the
    message tool satisfies delivery without it. A failed run, a failed
    delivery and a turn with nothing to say are what is known to be nothing
    in the chat -- and "the agent chose silence" is not its call to make:
    the policy has already decided this is worth saying.
    """
    if not isinstance(completion, dict):
        return None  # admitted, and this gateway says no more
    status = completion.get("status")
    if status != "ok":
        return f"the turn ended {status}"
    if completion.get("deliveryError"):
        return f"delivery failed ({completion['deliveryError']})"
    if completion.get("delivered") is not True and (
            completion.get("replyDisposition") in ("silent", "empty")
            or completion.get("deliverySuppressionReason")):
        return "the agent said nothing"
    return None


def _notify_agent(message: str, idem: str | None, kind: str | None) -> str | None:
    """One request to the hook. None when the person was told; otherwise why
    not, for the log, and the caller falls back to the relay."""
    token = hooks_token()
    if not token:
        return "no OPENCLAW_HOOKS_TOKEN; 'smoking-pi openclaw' sets it"
    payload = agent_payload(message, kind)
    if not payload["to"]:
        return "no recipient (OPENCLAW_TO is unset)"
    headers = {"Authorization": f"Bearer {token}"}
    if idem:
        headers["Idempotency-Key"] = idem[:256]
    try:
        response = httpx.post(hooks_url(), json=payload, headers=headers,
                              timeout=AGENT_HTTP_TIMEOUT)
    except httpx.TimeoutException as exc:
        return f"timeout, the agent may still answer ({type(exc).__name__})"
    except httpx.HTTPError as exc:
        return f"gateway not reached ({type(exc).__name__})"
    if not 200 <= response.status_code < 300:
        return f"HTTP {response.status_code}{_hook_error(response)}"
    try:
        body = response.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    if body.get("ok") is False:
        return f"refused{_hook_error(response)}"
    why = _completion_failure(body.get("completion"))
    if why is None:
        log.info("Delivery path: agent, %s (run %s)", kind or "message",
                 body.get("runId") or "?")
    return why


def _hook_error(response) -> str:
    """The hook's own ``error``, when it gave one: its 400s are worded (a
    session policy, a recipient) and say what to fix."""
    try:
        body = response.json()
    except ValueError:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    return f": {error}" if isinstance(error, str) and error else ""


def _chart_after_agent(event: dict, image: bytes | None) -> None:
    """The hook takes no attachment: the chart of a newly opened episode goes
    through the relay, under what the agent just wrote. Its caption says
    what it is and nothing of the message. Never costs the alert."""
    if image is None or event.get("kind") not in ("down", "degradation"):
        return
    token = openclaw_token()
    if not token or not os.environ.get("OPENCLAW_TO"):
        log.warning("No Gateway token or recipient for the chart (chart_dropped=1)")
        return
    payload = openclaw_invoke_payload(
        "📈 The chart of this episode", image, _chart_filename(event),
        silent=event.get("silent"))
    outcome, _ = _post_openclaw(openclaw_hook_url(), payload,
                                {"Authorization": f"Bearer {token}"})
    if outcome == FAILED:
        log.warning("The chart was not delivered (chart_dropped=1)")


def _silent(event: dict) -> bool:
    """The message's own ``silent`` wins over ALERT_SILENT (a digest should
    not buzz; the 3 a.m. outage should), as in common.openclaw."""
    if event.get("silent") is not None:
        return bool(event["silent"])
    return _env_bool("ALERT_SILENT", False)


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


# Targets and rule names are operator-editable and have no length limit of
# their own, so the slug needs one here: a 300-character target would build a
# filename some downstream store rejects outright. 48 is far longer than any
# real hostname and still leaves the prefix and timestamp legible.
_SLUG_MAX = 48


def _chart_filename(event: dict) -> str:
    slug = "".join(
        ch if ch.isalnum() or ch in "-_" else "-"
        for ch in str(event.get("target") or event.get("rule") or "alert")
    )
    # Strip separators only at the ends; a target of "..." would otherwise
    # slug to "---" and then to "", leaving "smokeping--1234.png".
    slug = slug[:_SLUG_MAX].strip("-_") or "alert"
    return f"smokeping-{slug}-{int(time.time())}.png"


def _notify_openclaw(
    text: str, image: bytes | None = None, event: dict | None = None
) -> tuple[str, bool]:
    """(outcome, refused): ``refused`` when the gateway turned the request
    down outright (HTTP 4xx), so nothing of it is queued anywhere."""
    url = openclaw_hook_url()
    token = openclaw_token()
    if not token:
        log.warning(
            "NOTIFY_MODE=openclaw but no Gateway token "
            "(OPENCLAW_GATEWAY_TOKEN/OPENCLAW_HOOK_TOKEN); skipping"
        )
        return FAILED, False
    if not os.environ.get("OPENCLAW_TO"):
        log.warning("NOTIFY_MODE=openclaw but OPENCLAW_TO is unset; skipping")
        return FAILED, False
    headers = {"Authorization": f"Bearer {token}"}
    _handed(text, "openclaw")
    payload = openclaw_invoke_payload(
        text,
        image,
        _chart_filename(event or {}) if image else None,
        silent=(event or {}).get("silent"),
    )
    return _post_openclaw(url, payload, headers)


# The request never reached the gateway, or not whole: nothing of it is
# queued there.
_NOT_REACHED = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout,
                httpx.WriteError, httpx.WriteTimeout, httpx.ProxyError)
# A proxy or tunnel in front of a remote gateway (docs/remote-openclaw.md)
# answering for a gateway it could not reach.
_PROXY_STATUSES = frozenset({502, 503, 504})


def _post_openclaw(url: str, payload: dict, headers: dict) -> tuple[str, bool]:
    """ONE request to the gateway, never retried here.

    Once the gateway has received a request it holds it: a failure it
    answers goes into its durable delivery queue, and so does a request it
    is still working on when the 10 s timeout fires. Each retry was one more
    copy for it to replay -- six of one alert on 2026-10-09. A request that
    never reached it waits in the outbox and is tried again next cycle.
    """
    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=TIMEOUT_S)
    except _NOT_REACHED as exc:
        log.warning("Notification to %s not delivered: the gateway was not "
                    "reached (%s)", url, exc)
        return FAILED, False
    except httpx.HTTPError as exc:
        log.warning("Notification sent to %s with no answer (%s): the gateway "
                    "may hold it, so it is not sent again", url, exc)
        return HANDED, False
    if 200 <= response.status_code < 300:
        error = _body_error(response)
        if error is None:
            last_receipt["message_id"] = _message_id(response)
            return SENT, False
        log.warning("Notification to %s answered HTTP %s with a failure: %s; "
                    "the gateway holds it in its queue, so it is not sent "
                    "again", url, response.status_code, error)
        return HANDED, False
    if response.status_code in _PROXY_STATUSES:
        log.warning("Notification to %s answered HTTP %s: the gateway was not "
                    "reached", url, response.status_code)
        return FAILED, False
    if response.status_code >= 500:
        log.warning("Notification to %s answered HTTP %s; the gateway may hold "
                    "it, so it is not sent again", url, response.status_code)
        return HANDED, False
    log.error("Notification to %s refused with HTTP %s", url, response.status_code)
    return FAILED, True


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
    # An episode's message (policy.py): which one, and its record -- cause,
    # confidence, range, scope -- for a receiver that wants fields, not text.
    if event.get("episode") is not None:
        payload["kind"] = event.get("kind")
        payload["episode"] = event["episode"]
    headers = {}
    _handed(text, "webhook")
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


def _preflight_hooks() -> str | None:
    """Whether the hook is there and takes the token, without running a turn;
    None when it is, else why not.

    An empty body: OpenClaw checks the token first and the body after, so an
    accepted token answers 400 ``message required`` and nothing runs. 401 is
    the token, 404 is hooks not enabled -- disabled hooks fall through to the
    rest of the gateway's routes.
    """
    if not hooks_token():
        return "no OPENCLAW_HOOKS_TOKEN"
    try:
        response = httpx.post(hooks_url(), json={},
                              headers={"Authorization": f"Bearer {hooks_token()}"},
                              timeout=TIMEOUT_S)
    except httpx.HTTPError as exc:
        return f"the gateway did not answer ({type(exc).__name__})"
    if response.status_code == 400:
        return None
    if response.status_code == 401:
        return "the hooks token was rejected (401); it must match hooks.token"
    if response.status_code == 404:
        return "hooks are not enabled on the gateway (404)"
    return f"unexpected HTTP {response.status_code}"


def _preflight_agent() -> bool:
    """The hook, then the relay it falls back to. Delivery works if either
    does. The hook's verdict comes last when it passed and first when it did
    not: `smoking-pi alerts` shows the last line, which is the path alerts
    will take."""
    url, why = hooks_url(), _preflight_hooks()
    if why is not None:
        log.warning("Agent push unavailable at %s: %s; alerts go through the "
                    "message relay. 'smoking-pi openclaw --push agent' sets it "
                    "up.", url, why)
        return _preflight_openclaw()
    relay = _preflight_openclaw()
    log.info("Delivery preflight: %s accepts the hooks token; alerts go through "
             "the agent (message relay %s)", url,
             "ready as the fallback" if relay else "NOT ready, no fallback")
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
        return _preflight_agent() if push_mode() == PUSH_AGENT else _preflight_openclaw()

    if mode == "telegram":
        return telegram.preflight()

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
    url: str, payload: dict, headers: dict, body_must_be_ok: bool = False,
) -> bool:
    """POST with up to MAX_ATTEMPTS tries (the webhook; never the gateway)."""
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
