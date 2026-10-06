"""Telegram delivery: alerts straight to a bot of the owner's, no OpenClaw.

Until this existed, a push reached a phone only through an OpenClaw gateway
(or a webhook someone built). A remote assistant (Claude, ChatGPT, Grok)
answers when asked and never pushes, so without OpenClaw an outage went
unreported however well it was detected.

The owner makes a bot with @BotFather and gives Smoking Pi its token
(``TELEGRAM_BOT_TOKEN``) and the chat to write to (``TELEGRAM_CHAT_ID``).
``python telegram.py find-chat`` reads the chat id from the first message
the owner sends the bot, so nobody has to look one up.

**The token is in every URL** (``/bot<TOKEN>/sendMessage``: that is the Bot
API). So nothing here logs a URL or an exception's text, both of which
would carry it: a log line names the method, the HTTP status and Telegram's
own ``description``, which never contains the token. httpx itself logs every
request URL at INFO (``HTTP Request: POST https://...``), and the alerter
logs at INFO: :class:`RedactBotToken` is installed on httpx's loggers on
import, whatever level they end up at.

Messages are the same Telegram HTML the OpenClaw path sends (templates.py);
``ALERT_MARKUP=plain`` sends them without a parse mode.
"""

from __future__ import annotations

import html
import logging
import os
import re
import sys
import time

import httpx

import templates

log = logging.getLogger("alerter.telegram")

API = "https://api.telegram.org"
TIMEOUT_S = 10.0
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 1.0
# Telegram says how long to wait on a 429; past this, give up this message.
MAX_RETRY_AFTER_S = 30

_BOT_PATH = re.compile(r"/bot[^/\s\"']+")


class RedactBotToken(logging.Filter):
    """Replace ``/bot<token>`` with ``/bot<redacted>`` in a record, args
    included (httpx passes the URL as an argument, not in the message)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.msg = record.getMessage()
            record.args = None
        if isinstance(record.msg, str):
            record.msg = _BOT_PATH.sub("/bot<redacted>", record.msg)
        return True


for _name in ("httpx", "httpcore"):
    _logger = logging.getLogger(_name)
    if not any(isinstance(f, RedactBotToken) for f in _logger.filters):
        _logger.addFilter(RedactBotToken())


def bot_token() -> str:
    return (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()


def chat_id() -> str:
    return (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()


def _url(method: str, token: str | None = None) -> str:
    return f"{API}/bot{token if token is not None else bot_token()}/{method}"


def _call(method: str, data: dict | None = None, files: dict | None = None,
          token: str | None = None, timeout: float = TIMEOUT_S) -> tuple[int, dict]:
    """One Bot API call: (HTTP status, JSON body). Status 0 = no answer.
    Raises nothing, and logs nothing that could carry the token."""
    try:
        if files:
            response = httpx.post(_url(method, token), data=data, files=files,
                                  timeout=timeout)
        else:
            response = httpx.post(_url(method, token), json=data or {}, timeout=timeout)
    except httpx.HTTPError as exc:
        # The exception's text can include the request URL, so only its type.
        return 0, {"ok": False, "description": f"no answer ({type(exc).__name__})"}
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    return response.status_code, body


def _describe(status: int, body: dict) -> str:
    text = str(body.get("description") or "no description")
    return f"HTTP {status}: {text}" if status else text


def _fields(silent: bool) -> dict:
    data: dict = {"chat_id": chat_id()}
    if templates.markup() == "html":
        data["parse_mode"] = "HTML"
    if silent:
        data["disable_notification"] = True
    return data


def _send(method: str, data: dict, files: dict | None = None) -> tuple[bool, str]:
    """Send with retries. A 4xx other than 429 is the request's fault (a
    wrong chat, a bot the owner never wrote to, bad HTML): retrying cannot
    fix it, so it is reported once."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        status, body = _call(method, data, files)
        if status == 200 and body.get("ok") is True:
            return True, ""
        log.warning("Telegram %s failed: %s (attempt %d/%d)", method,
                    _describe(status, body), attempt, MAX_ATTEMPTS)
        if 400 <= status < 500 and status != 429:
            break
        if attempt == MAX_ATTEMPTS:
            break
        wait = BACKOFF_BASE_S * (2 ** (attempt - 1))
        if status == 429:
            retry_after = (body.get("parameters") or {}).get("retry_after")
            if isinstance(retry_after, (int, float)):
                if retry_after > MAX_RETRY_AFTER_S:
                    break
                wait = float(retry_after)
        time.sleep(wait)
    log.error("Telegram delivery (%s) failed", method)
    return False, str(body.get("description") or "")


def _plain(text: str) -> str:
    """The message without its HTML: what is left to send when Telegram
    cannot parse the markup."""
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _send_text_or_plain(method: str, data: dict, key: str,
                        files: dict | None = None) -> bool:
    """Send; if Telegram cannot parse the HTML (a rendering bug, or a
    target name that slipped past escaping), send the same words without
    markup once. A formatting mistake must never cost the alert."""
    ok, why = _send(method, data, files)
    if ok or "parse_mode" not in data or "parse entities" not in why.lower():
        return ok
    log.warning("Telegram could not parse the message's HTML; sending it as plain text")
    plain = {k: v for k, v in data.items() if k != "parse_mode"}
    plain[key] = _plain(str(data[key]))
    return _send(method, plain, files)[0]


def send(text: str, image: bytes | None = None, filename: str = "smokeping.png",
         silent: bool = False) -> bool:
    """One message, or one photo with ``text`` as its caption. Never raises."""
    if not bot_token() or not chat_id():
        log.warning("NOTIFY_MODE=telegram but TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID "
                    "is unset; skipping")
        return False
    data = _fields(silent)
    if image is None:
        data["text"] = text
        # Alert links point at this house's Grafana: a preview cannot load.
        data["link_preview_options"] = {"is_disabled": True}
        return _send_text_or_plain("sendMessage", data, "text")
    data["caption"] = text
    as_document = (os.environ.get("ALERT_IMAGE_AS_DOCUMENT") or "").strip().lower() \
        in ("1", "true", "yes", "on")
    field = "document" if as_document else "photo"
    # Multipart: every field is a string.
    form = {k: ("true" if v is True else str(v)) for k, v in data.items()}
    return _send_text_or_plain("sendDocument" if as_document else "sendPhoto", form,
                               "caption", files={field: (filename, image, "image/png")})


def preflight() -> bool:
    """At startup: the token is a bot's (getMe) and the bot can write to the
    chat (getChat). The second is the usual failure: a bot may write only to
    someone who wrote to it first. Never raises."""
    if not bot_token():
        log.error("NOTIFY_MODE=telegram but TELEGRAM_BOT_TOKEN is unset; alerts will "
                  "not be delivered")
        return False
    if not chat_id():
        log.error("NOTIFY_MODE=telegram but TELEGRAM_CHAT_ID is unset; alerts have "
                  "nowhere to go")
        return False
    status, body = _call("getMe")
    if status == 401:
        log.error("Delivery preflight: Telegram rejected the bot token (401). Copy it "
                  "again from @BotFather, then: sudo smoking-pi config set "
                  "TELEGRAM_BOT_TOKEN")
        return False
    if status != 200 or body.get("ok") is not True:
        log.error("Delivery preflight: Telegram getMe failed: %s", _describe(status, body))
        return False
    bot = (body.get("result") or {}).get("username") or "the bot"
    status, body = _call("getChat", {"chat_id": chat_id()})
    if status != 200 or body.get("ok") is not True:
        log.error("Delivery preflight: @%s cannot reach chat %s: %s. Send the bot a "
                  "message first, then check the chat id.", bot, chat_id(),
                  _describe(status, body))
        return False
    log.info("Delivery preflight: Telegram bot @%s can write to chat %s", bot, chat_id())
    return True


def _latest_update_id(token: str | None) -> int | None:
    """The id of the newest update Telegram still holds (it keeps them a
    day), without waiting. None when there is none."""
    status, body = _call("getUpdates", {"offset": -1, "timeout": 0}, token=token)
    if status == 401:
        raise ValueError("Telegram rejected the bot token")
    if status == 409:
        raise ValueError("this bot has a webhook set, so its messages cannot be "
                         "read here; remove it (deleteWebhook) or use another bot")
    ids = [u.get("update_id") for u in (body.get("result") or []) if body.get("ok")]
    ids = [i for i in ids if isinstance(i, int)]
    return max(ids) if ids else None


def find_chat(wait_s: int = 120, token: str | None = None) -> dict | None:
    """The chat of the first message sent to the bot *after* this starts,
    waiting up to ``wait_s``: ``{"id", "name"}``. An older message -- the
    owner's from yesterday, or a stranger's who found the bot -- is not an
    answer to "send the bot a message now". Long-polls getUpdates; the bot
    must not have a webhook (a fresh bot has none)."""
    deadline = time.monotonic() + wait_s
    last = _latest_update_id(token)
    offset = (last + 1) if last is not None else None
    while True:
        left = int(deadline - time.monotonic())
        if left <= 0:
            return None
        poll = max(1, min(25, left))
        req: dict = {"timeout": poll, "allowed_updates": ["message"]}
        if offset is not None:
            req["offset"] = offset
        status, body = _call("getUpdates", req, token=token, timeout=poll + 10)
        if status == 401:
            raise ValueError("Telegram rejected the bot token")
        if status == 409:
            raise ValueError("this bot has a webhook set, so its messages cannot be "
                             "read here; remove it (deleteWebhook) or use another bot")
        for update in (body.get("result") if body.get("ok") else None) or []:
            if isinstance(update.get("update_id"), int):
                offset = max(offset or 0, update["update_id"] + 1)
            chat = (update.get("message") or {}).get("chat") or {}
            if "id" in chat:
                name = chat.get("title") or " ".join(
                    p for p in (chat.get("first_name"), chat.get("last_name")) if p
                ) or chat.get("username") or str(chat["id"])
                return {"id": str(chat["id"]), "name": name}
        if status == 0:
            time.sleep(min(5, max(0, deadline - time.monotonic())))


def main(argv: list[str]) -> int:
    """``find-chat [SECONDS]``: print the chat id of the next message the
    bot receives (the token comes from the environment, never argv)."""
    if not argv or argv[0] != "find-chat":
        print("usage: telegram.py find-chat [SECONDS]", file=sys.stderr)
        return 2
    if not bot_token():
        print("TELEGRAM_BOT_TOKEN is not set.", file=sys.stderr)
        return 1
    wait_s = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 120
    try:
        found = find_chat(wait_s)
    except ValueError as exc:
        print(f"Cannot read the bot's messages: {exc}.", file=sys.stderr)
        return 1
    if not found:
        print(f"No message reached the bot in {wait_s} s.", file=sys.stderr)
        return 1
    # Machine-readable last line for the CLI; the name for the owner to check.
    print(f"The message came from: {found['name']} (chat {found['id']}).",
          file=sys.stderr)
    print(found["id"])
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(main(sys.argv[1:]))
