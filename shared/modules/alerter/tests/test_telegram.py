"""Telegram delivery: the Bot API calls, the retries, and the token.

Every call is mocked at httpx.post. The bot token is part of every Bot API
URL, so several tests assert it never reaches a log line.
"""

import logging

import httpx
import pytest

import notifier
import telegram

TOKEN = "123456:AAAbbbCCCdddEEEfffGGGhhhIIIjjjKKKlll"


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = {"ok": True, "result": {}} if body is None else body

    def json(self):
        return self._body


@pytest.fixture()
def bot(monkeypatch):
    monkeypatch.setenv("NOTIFY_MODE", "telegram")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "4242")
    monkeypatch.setattr(telegram.time, "sleep", lambda s: None)
    calls = []

    def answer(method, **kw):
        return _Resp()

    def fake_post(url, json=None, data=None, files=None, timeout=None):
        method = url.rsplit("/", 1)[1]
        calls.append({"url": url, "method": method, "json": json, "data": data,
                      "files": files})
        return bot.answer(method, json=json, data=data)

    class Bot:
        pass
    bot = Bot()
    bot.calls = calls
    bot.answer = answer
    monkeypatch.setattr(telegram.httpx, "post", fake_post)
    return bot


def _no_token_in(caplog):
    assert TOKEN not in caplog.text
    assert TOKEN.split(":")[1] not in caplog.text


def test_an_alert_is_one_html_message_to_the_chat(bot):
    assert notifier.notify({"type": "alert", "rule": "target_down", "target": "a<b",
                            "severity": "critical", "message": "a<b is down"})
    [call] = bot.calls
    assert call["method"] == "sendMessage"
    assert call["url"] == f"{telegram.API}/bot{TOKEN}/sendMessage"
    body = call["json"]
    assert body["chat_id"] == "4242" and body["parse_mode"] == "HTML"
    assert "a&lt;b" in body["text"]  # escaped by templates, as for OpenClaw
    assert body["link_preview_options"] == {"is_disabled": True}
    assert "disable_notification" not in body


def test_plain_markup_sends_no_parse_mode(bot, monkeypatch):
    monkeypatch.setenv("ALERT_MARKUP", "plain")
    assert telegram.send("hello")
    assert "parse_mode" not in bot.calls[0]["json"]


def test_a_digest_is_silent(bot):
    assert notifier.notify({"type": "digest", "message": "all quiet", "silent": True})
    assert bot.calls[0]["json"]["disable_notification"] is True


def test_a_chart_is_a_photo_with_the_text_as_caption(bot):
    png = b"\x89PNG fake"
    assert notifier.notify({"type": "alert", "rule": "high_loss", "target": "x",
                            "message": "loss"}, image=png)
    [call] = bot.calls
    assert call["method"] == "sendPhoto"
    name, content, mime = call["files"]["photo"]
    assert content == png and mime == "image/png" and name.endswith(".png")
    assert call["data"]["chat_id"] == "4242" and call["data"]["caption"]
    assert all(isinstance(v, str) for v in call["data"].values())


def test_a_chart_can_go_as_a_document(bot, monkeypatch):
    monkeypatch.setenv("ALERT_IMAGE_AS_DOCUMENT", "true")
    assert telegram.send("cap", image=b"png")
    assert bot.calls[0]["method"] == "sendDocument" and "document" in bot.calls[0]["files"]


def test_a_failed_chart_still_delivers_the_alert_as_text(bot):
    bot.answer = lambda method, **kw: (
        _Resp(400, {"ok": False, "description": "Bad Request: wrong file"})
        if method == "sendPhoto" else _Resp())
    assert notifier.notify({"type": "alert", "rule": "high_loss", "target": "x",
                            "message": "loss"}, image=b"png")
    assert [c["method"] for c in bot.calls] == ["sendPhoto", "sendMessage"]


def test_a_wrong_chat_is_reported_once_not_retried(bot, caplog):
    bot.answer = lambda method, **kw: _Resp(400, {"ok": False,
                                                  "description": "Bad Request: chat not found"})
    with caplog.at_level(logging.WARNING):
        assert not telegram.send("x")
    assert len(bot.calls) == 1
    assert "chat not found" in caplog.text
    _no_token_in(caplog)


def test_a_server_error_is_retried(bot):
    answers = iter([_Resp(502, {}), _Resp(502, {}), _Resp()])
    bot.answer = lambda method, **kw: next(answers)
    assert telegram.send("x")
    assert len(bot.calls) == 3


def test_a_rate_limit_waits_as_long_as_telegram_says(bot, monkeypatch):
    waits = []
    monkeypatch.setattr(telegram.time, "sleep", waits.append)
    answers = iter([_Resp(429, {"ok": False, "description": "Too Many Requests",
                                "parameters": {"retry_after": 7}}), _Resp()])
    bot.answer = lambda method, **kw: next(answers)
    assert telegram.send("x")
    assert waits == [7.0]


def test_a_long_rate_limit_gives_up_instead_of_stalling_the_loop(bot):
    bot.answer = lambda method, **kw: _Resp(429, {"ok": False, "parameters":
                                                  {"retry_after": 600}})
    assert not telegram.send("x")
    assert len(bot.calls) == 1


def test_no_answer_never_logs_the_url(bot, monkeypatch, caplog):
    def boom(url, **kw):
        raise httpx.ConnectError(f"cannot connect to {url}")
    monkeypatch.setattr(telegram.httpx, "post", boom)
    with caplog.at_level(logging.WARNING):
        assert not telegram.send("x")
        assert not telegram.preflight()
    assert "ConnectError" in caplog.text
    _no_token_in(caplog)


def test_unset_keys_skip_delivery(monkeypatch, caplog):
    monkeypatch.setenv("NOTIFY_MODE", "telegram")
    with caplog.at_level(logging.WARNING):
        assert not notifier.notify({"type": "alert", "message": "x"})
        assert not notifier.preflight()
    assert "TELEGRAM_BOT_TOKEN" in caplog.text


def test_preflight_names_the_bot_and_the_chat(bot, caplog):
    bot.answer = lambda method, **kw: _Resp(200, {"ok": True, "result":
                                                  {"username": "pi_alerts_bot"}})
    with caplog.at_level(logging.INFO):
        assert notifier.preflight()
    assert [c["method"] for c in bot.calls] == ["getMe", "getChat"]
    assert "Delivery preflight: Telegram bot @pi_alerts_bot can write to chat 4242" in caplog.text
    _no_token_in(caplog)


def test_preflight_a_bad_token(bot, caplog):
    bot.answer = lambda method, **kw: _Resp(401, {"ok": False, "description": "Unauthorized"})
    with caplog.at_level(logging.ERROR):
        assert not notifier.preflight()
    assert "rejected the bot token" in caplog.text
    _no_token_in(caplog)


def test_preflight_a_chat_the_bot_cannot_reach(bot, caplog):
    bot.answer = lambda method, **kw: (
        _Resp(200, {"ok": True, "result": {"username": "b"}}) if method == "getMe"
        else _Resp(400, {"ok": False, "description": "Bad Request: chat not found"}))
    with caplog.at_level(logging.ERROR):
        assert not notifier.preflight()
    assert "Send the bot a message first" in caplog.text


def test_the_test_message_goes_to_telegram(bot):
    assert notifier.send_test()
    assert bot.calls[0]["json"]["text"] == notifier.TEST_MESSAGE


def test_find_chat_reads_the_newest_message(bot):
    bot.answer = lambda method, **kw: _Resp(200, {"ok": True, "result": [
        {"message": {"chat": {"id": 1, "first_name": "Old"}}},
        {"message": {"chat": {"id": 4242, "first_name": "Ana", "last_name": "B"}}}]})
    assert telegram.find_chat(5) == {"id": "4242", "name": "Ana B"}
    assert bot.calls[0]["method"] == "getUpdates"


def test_find_chat_a_group_is_named_by_its_title(bot):
    bot.answer = lambda method, **kw: _Resp(200, {"ok": True, "result": [
        {"message": {"chat": {"id": -100123, "title": "House"}}}]})
    assert telegram.find_chat(5) == {"id": "-100123", "name": "House"}


def test_find_chat_a_bot_with_a_webhook_says_so(bot):
    bot.answer = lambda method, **kw: _Resp(409, {"ok": False})
    with pytest.raises(ValueError, match="webhook"):
        telegram.find_chat(5)


def test_find_chat_cli_prints_the_id_first(bot, capsys):
    bot.answer = lambda method, **kw: _Resp(200, {"ok": True, "result": [
        {"message": {"chat": {"id": 4242, "username": "ana"}}}]})
    assert telegram.main(["find-chat", "5"]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == "4242"
    assert "ana" in captured.err
    assert TOKEN not in captured.out + captured.err
