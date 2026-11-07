"""OPENCLAW_PUSH=agent: the agent writes the alert, the relay catches what it
cannot deliver. What matters here is that no failure costs the alert, and that
nothing is said twice."""

from __future__ import annotations

import httpx
import pytest

import ledger
import main
import notifications
import notifier
import quiet
import templates

NOON = 1_790_000_000.0
PNG = b"\x89PNG fake"
COMPLETED = {"ok": True, "runId": "run-1",
             "completion": {"status": "ok", "replyDisposition": "visible",
                            "delivered": True, "deliveryAttempted": True}}


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = COMPLETED if body is None else body

    def json(self):
        if self._body is _NOT_JSON:
            raise ValueError("not json")
        return self._body


_NOT_JSON = object()


@pytest.fixture
def posts(monkeypatch):
    """httpx.post, answered per path: the hook from ``hook``, the relay OK."""
    calls = []
    answers = {"hook": [], "relay": []}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        which = answers["hook" if url.endswith("/hooks/agent") else "relay"]
        result = which.pop(0) if which else FakeResponse()
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(notifier.httpx, "post", fake_post)
    return {"calls": calls, **answers}


@pytest.fixture
def agent_mode(monkeypatch):
    monkeypatch.setenv("NOTIFY_MODE", "openclaw")
    monkeypatch.setenv("OPENCLAW_PUSH", "agent")
    monkeypatch.setenv("OPENCLAW_HOOKS_TOKEN", "hooks-tok")
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "gw-tok")
    monkeypatch.setenv("OPENCLAW_TO", "telegram:12345")
    monkeypatch.setenv("DIGEST_TZ", "America/Chicago")
    monkeypatch.setenv("ALERT_MARKUP", "plain")


def _episode(kind="degradation", **record) -> dict:
    base = {
        "id": "ep1", "key": "episode:1", "start": NOON - 900, "end": NOON,
        "duration_s": 900, "class": "upstream", "cause": "provider",
        "confidence": "high", "down": False, "sites": [], "evidence": [],
        "measures": {"affected": 16, "reporting": 23, "worst": []},
        "watchdog_actions": [], "step": 300,
    }
    base.update(record)
    final = kind in ("recovery", "monitor", "night")
    return {
        "type": "recovery" if final else "alert",
        "rule": "episode",
        "kind": kind,
        "key": "episode:1",
        "target": None,
        "severity": "info" if final else "warning",
        "silent": final,
        "episode": base,
        "render": {"now": NOON, "step": 300, "names": {}},
        "state": {"first_seen": NOON - 900, "notified_count": 1},
        "message": "the plain text",
    }


def _hooks(posts):
    return [c for c in posts["calls"] if c["url"].endswith("/hooks/agent")]


def _relays(posts):
    return [c for c in posts["calls"] if c["url"].endswith("/tools/invoke")]


# --- the hook's request -------------------------------------------------------


def test_default_is_the_relay_and_the_hook_is_never_called(agent_mode, posts, monkeypatch):
    # An existing install: not set at all.
    monkeypatch.delenv("OPENCLAW_PUSH")
    assert notifier.send(_episode()) == notifier.SENT
    assert not _hooks(posts)
    assert len(_relays(posts)) == 1


def test_the_turn_lands_in_the_conversation_and_is_delivered_to_the_chat(agent_mode, posts):
    assert notifier.send(_episode()) == notifier.SENT
    (call,) = _hooks(posts)
    assert call["url"] == "http://127.0.0.1:18789/hooks/agent"
    assert call["headers"]["Authorization"] == "Bearer hooks-tok"
    body = call["json"]
    assert body["sessionMode"] == "persistent"
    assert body["sessionKey"] == "agent:main:main"
    assert body["deliver"] is True
    assert body["channel"] == "telegram"
    assert body["to"] == "12345"  # the channel is its own field
    assert body["waitForCompletion"] is True
    # The turn ends before the request gives up on it.
    assert body["timeoutSeconds"] < call["timeout"].read
    assert not _relays(posts)


@pytest.mark.parametrize("make", [
    pytest.param(_episode, id="opening"),
    pytest.param(lambda: _episode("recovery"), id="note"),
])
def test_a_failed_turn_does_not_start_another_that_speaks(agent_mode, posts, monkeypatch, make):
    # "now" would run a turn on OpenClaw's "Hook ... (error)" event at once.
    monkeypatch.setattr(quiet, "active", lambda now: False)
    notifier.send(make())
    assert _hooks(posts)[0]["json"]["wakeMode"] == "next-heartbeat"


def test_the_session_key_can_be_another(agent_mode, posts, monkeypatch):
    monkeypatch.setenv("OPENCLAW_SESSION", "agent:main:telegram:direct:12345")
    notifier.send(_episode())
    assert _hooks(posts)[0]["json"]["sessionKey"] == "agent:main:telegram:direct:12345"


def test_the_idempotency_key_is_the_ledgers(agent_mode, posts):
    event = _episode()
    notifier.send(event)
    assert _hooks(posts)[0]["headers"]["Idempotency-Key"] == ledger.key_of(event)
    assert ledger.key_of(event).endswith("|alert")


def test_an_end_has_its_own_idempotency_key(agent_mode, posts, monkeypatch):
    monkeypatch.setattr(quiet, "active", lambda now: False)
    notifier.send(_episode("recovery"))
    assert _hooks(posts)[0]["headers"]["Idempotency-Key"].endswith("|recovery")


def test_the_same_event_is_the_same_body(agent_mode, posts):
    # OpenClaw replays a key only for an identical body: a resend from the
    # outbox must not become a second run.
    event = _episode()
    assert templates.agent_message(event) == templates.agent_message(event)


# --- the facts ----------------------------------------------------------------


def test_the_message_carries_the_facts_and_the_task(agent_mode):
    event = _episode(measures={"affected": 16, "reporting": 23,
                               "worst": [["Netflix_3", 30.0]]})
    text = templates.agent_message(event)
    assert "Kind: opened" in text
    assert "Since: " in text
    assert "Until: " not in text  # it is still going on
    assert "Probable cause: your provider, beyond the modem (high confidence)." in text
    assert "Scope: 16 of 23 destinations" in text
    assert "All times are in America/Chicago." in text
    assert "Episode id: ep1" in text
    assert "in English, in 2 to 5 sentences" in text
    assert '"all clear"' in text
    assert "<b>" not in text  # facts, not a rendered Telegram message


def test_a_note_is_the_same_facts_with_another_task(agent_mode):
    event = _episode("recovery")
    told, noted = templates.agent_message(event), templates.agent_message(event, noted=True)
    assert told.split("Task:")[0] == noted.split("Task:")[0]
    assert "2 to 5 sentences" not in noted


def test_a_closed_episode_has_its_end(agent_mode):
    text = templates.agent_message(_episode("recovery"))
    assert "Kind: closed" in text
    assert "Until: " in text
    # Said once: the end's own "Affected:", not a "Scope:" beside it.
    assert text.count("of 23 destinations") == 1


def test_a_blind_monitor_says_what_the_watchdog_did(agent_mode):
    text = templates.agent_message(_episode(
        "monitor", **{"class": "monitor", "detail": "radio",
                      "blind_range": [NOON - 3600, NOON],
                      "watchdog_actions": [[NOON - 60, "reloaded brcmfmac"]]}))
    assert "Kind: monitor blind" in text
    assert "The watchdog reloaded the Wi-Fi driver" in text


def test_the_watchdog_shows_in_an_episode_of_the_network_too(agent_mode):
    text = templates.agent_message(_episode(
        watchdog_actions=[[NOON - 60, "rebooted the Pi"]]))
    assert "The watchdog rebooted this Pi" in text


def test_a_pattern_is_its_message_and_its_link(agent_mode):
    text = templates.agent_message({
        "type": "alert", "rule": "pattern", "kind": "pattern_new", "key": "pattern:x",
        "message": "🔁 For the 3rd Monday in a row, packet loss on most destinations",
        "links": {"last_window": "http://grafana/d/x"},
    })
    assert "Kind: pattern: a recurring window appeared" in text
    assert "3rd Monday" in text
    assert "http://grafana/d/x" in text


# --- what goes which way ------------------------------------------------------


@pytest.mark.parametrize("payload", [
    {"type": "weekly", "message": "the week"},
    {"type": "report", "message": "a report"},
    {"type": "held", "message": "the night"},
    {"type": "undelivered", "message": "while offline"},
])
def test_what_is_already_text_stays_on_the_relay(agent_mode, posts, payload):
    assert notifier.send(payload) == notifier.SENT
    assert not _hooks(posts)
    assert len(_relays(posts)) == 1


def _pattern() -> dict:
    return {"type": "alert", "rule": "pattern", "kind": "pattern_new",
            "key": "pattern:x", "silent": True, "message": "a recurring window",
            "state": {"first_seen": NOON, "notified_count": 1}}


NEVER_SOUND = [
    pytest.param(lambda: _episode("recovery"), id="end"),
    pytest.param(lambda: _episode("monitor"), id="monitor"),
    pytest.param(lambda: _episode("night"), id="night"),
    pytest.param(_pattern, id="pattern"),
]


@pytest.mark.parametrize("make", NEVER_SOUND)
def test_what_never_sounds_goes_silent_and_the_agent_only_gets_a_note(agent_mode, posts, monkeypatch, make):
    # The push policy of 2026-10-30: only an opening sounds. The hook has no
    # silent flag, so these must not be delivered by it, day or night.
    monkeypatch.setattr(quiet, "active", lambda now: False)
    assert notifier.send(make()) == notifier.SENT
    (relay,) = _relays(posts)
    assert relay["json"]["args"]["silent"] is True
    (note,) = _hooks(posts)
    assert note["json"]["deliver"] is False
    assert "channel" not in note["json"] and "to" not in note["json"]
    assert "waitForCompletion" not in note["json"]
    assert note["json"]["sessionKey"] == "agent:main:main"
    assert "Do not send anything" in note["json"]["message"]
    assert "NO_REPLY" in note["json"]["message"]
    # Told first: the note can fail, and nothing rides on it.
    assert posts["calls"].index(relay) < posts["calls"].index(note)
    assert notifier.take_receipt()["mode"] == "openclaw"


def test_an_update_keeps_the_relay_and_its_own_sound(agent_mode, posts, monkeypatch):
    monkeypatch.setattr(quiet, "active", lambda now: False)
    event = _episode("update")
    event["silent"] = False  # escalated: now the Internet is down
    assert notifier.send(event) == notifier.SENT
    assert "silent" not in _relays(posts)[0]["json"]["args"]
    assert _hooks(posts)[0]["json"]["deliver"] is False


@pytest.mark.parametrize("answer", [
    FakeResponse(404, {"error": "Not Found"}),
    FakeResponse(503, _NOT_JSON),
    httpx.ReadTimeout("slow"),
    httpx.ConnectError("refused"),
], ids=["404", "503", "timeout", "unreachable"])
def test_a_note_that_fails_costs_nothing_and_is_not_retried(agent_mode, posts, monkeypatch, answer, caplog):
    monkeypatch.setattr(quiet, "active", lambda now: False)
    posts["hook"].append(answer)
    assert notifier.send(_episode("recovery")) == notifier.SENT
    assert len(_relays(posts)) == 1  # not a fallback: it was already told
    assert len(_hooks(posts)) == 1
    assert "The agent was not told" in caplog.text


def test_no_note_for_what_nobody_was_told(agent_mode, posts, monkeypatch):
    # It waits in the outbox, and is noted when it goes.
    monkeypatch.setattr(quiet, "active", lambda now: False)
    posts["relay"].append(httpx.ConnectError("refused"))
    assert notifier.send(_episode("recovery")) == notifier.FAILED
    assert not _hooks(posts)


def test_no_note_without_a_token_and_the_message_still_goes(agent_mode, posts, monkeypatch, caplog):
    monkeypatch.setattr(quiet, "active", lambda now: False)
    monkeypatch.delenv("OPENCLAW_HOOKS_TOKEN")
    assert notifier.send(_episode("recovery")) == notifier.SENT
    assert not _hooks(posts)
    assert "will not know" in caplog.text


@pytest.mark.parametrize("make", [
    *NEVER_SOUND, pytest.param(_episode, id="opening"),
    pytest.param(lambda: _episode("down"), id="down"),
])
def test_quiet_hours_are_the_relay_alone(agent_mode, posts, monkeypatch, make):
    monkeypatch.setattr(quiet, "active", lambda now: True)
    assert notifier.send(make()) == notifier.SENT
    assert not _hooks(posts)
    assert len(_relays(posts)) == 1


def test_the_facts_that_cannot_be_written_do_not_cost_the_alert(agent_mode, posts, monkeypatch):
    def broken(*a, **k):
        raise KeyError("start")

    monkeypatch.setattr(templates, "agent_message", broken)
    posts["hook"].append(FakeResponse(400, {"ok": False, "error": "message required"}))
    assert notifier.send(_episode()) == notifier.SENT
    assert len(_relays(posts)) == 1


# --- fallback: no failure costs the alert ---------------------------------------


@pytest.mark.parametrize("answer", [
    FakeResponse(404, {"error": "Not Found"}),
    FakeResponse(401, _NOT_JSON),
    FakeResponse(400, {"ok": False, "error": "sessionKey must start with x"}),
    FakeResponse(429, _NOT_JSON),
    FakeResponse(503, {"ok": False, "error": "gateway_unavailable"}),
    FakeResponse(200, {"ok": False, "error": "no"}),
    httpx.ReadTimeout("slow"),
    httpx.ConnectError("refused"),
], ids=["404", "401", "400", "429", "503", "ok-false", "timeout", "unreachable"])
def test_a_hook_failure_falls_back_to_the_relay_once(agent_mode, posts, answer):
    posts["hook"].append(answer)
    assert notifier.send(_episode()) == notifier.SENT
    assert len(_hooks(posts)) == 1
    assert len(_relays(posts)) == 1
    assert notifier.take_receipt()["mode"] == "openclaw"


@pytest.mark.parametrize("completion", [
    {"status": "error"},
    {"status": "skipped"},
    {"status": "ok", "deliveryError": "delivery-failed"},
    {"status": "ok", "replyDisposition": "silent", "delivered": False},
    {"status": "ok", "replyDisposition": "empty"},
    {"status": "ok", "delivered": False, "deliverySuppressionReason": "heartbeat"},
], ids=["error", "skipped", "delivery-failed", "no-reply", "empty", "suppressed"])
def test_admitted_is_not_told(agent_mode, posts, completion):
    # 2026-11-06: a refused model on every turn, each one admitted with a 200.
    posts["hook"].append(FakeResponse(200, {"ok": True, "runId": "r", "completion": completion}))
    assert notifier.send(_episode()) == notifier.SENT
    assert len(_relays(posts)) == 1


@pytest.mark.parametrize("completion", [
    {"status": "ok", "replyDisposition": "visible", "delivered": True},
    # The agent used the message tool itself: no flag says it, none says not.
    {"status": "ok", "replyDisposition": "visible"},
    {"status": "ok", "replyDisposition": "silent", "delivered": True},
], ids=["delivered", "unknown", "message-tool"])
def test_a_settled_turn_that_told_them_is_not_relayed(agent_mode, posts, completion):
    posts["hook"].append(FakeResponse(200, {"ok": True, "runId": "r", "completion": completion}))
    assert notifier.send(_episode()) == notifier.SENT
    assert not _relays(posts)


def test_no_hooks_token_goes_straight_to_the_relay(agent_mode, posts, monkeypatch):
    monkeypatch.delenv("OPENCLAW_HOOKS_TOKEN")
    assert notifier.send(_episode()) == notifier.SENT
    assert not _hooks(posts)
    assert len(_relays(posts)) == 1


def test_both_down_is_failed_and_waits_in_the_outbox(agent_mode, posts):
    posts["hook"].append(httpx.ConnectError("refused"))
    posts["relay"].append(httpx.ConnectError("refused"))
    assert notifier.send(_episode()) == notifier.FAILED


def test_the_log_says_which_path_delivered(agent_mode, posts, caplog):
    caplog.set_level("INFO", logger="alerter.notifier")
    notifier.send(_episode())
    assert "Delivery path: agent, degradation (run run-1)" in caplog.text
    caplog.clear()
    posts["hook"].append(FakeResponse(404, {}))
    notifier.send(_episode())
    assert "Agent push failed (HTTP 404)" in caplog.text
    assert "Delivery path: message relay (sent)" in caplog.text


def test_the_tokens_are_never_logged(agent_mode, posts, caplog):
    caplog.set_level("DEBUG")
    posts["hook"].append(FakeResponse(401, _NOT_JSON))
    notifier.send(_episode())
    assert "hooks-tok" not in caplog.text
    assert "gw-tok" not in caplog.text


# --- the record ---------------------------------------------------------------


def test_the_ledger_records_the_agent_transport(agent_mode, posts):
    state: dict = {}
    event = _episode()
    main._deliver(state, event, now=NOON)
    (entry,) = _ledger_entries()
    assert entry["transport"] == "openclaw-agent"
    assert entry["outcome"] == "sent"
    assert entry["text"].startswith("🟡")  # what was told, not the task
    assert ledger.done(state, ledger.key_of(event))


def test_the_ledger_records_the_relay_after_a_fallback(agent_mode, posts):
    posts["hook"].append(FakeResponse(404, {}))
    main._deliver({}, _episode(), now=NOON)
    (entry,) = _ledger_entries()
    assert entry["transport"] == "openclaw"
    assert entry["outcome"] == "sent"


def test_delivered_is_not_delivered_again(agent_mode, posts):
    state: dict = {}
    event = _episode()
    main._deliver(state, event, now=NOON)
    main._deliver(state, _episode(), now=NOON + 60)
    assert len(_hooks(posts)) == 1


def _ledger_entries() -> list[dict]:
    from common import notification_ledger

    return notification_ledger.read(notifications.ledger_file())[0]


# --- the chart ----------------------------------------------------------------


def test_an_opened_episode_sends_one_chart_after_the_agent(agent_mode, posts):
    assert notifier.send(_episode(), image=PNG) == notifier.SENT
    assert [c["url"].rsplit("/", 1)[1] for c in posts["calls"]] == ["agent", "invoke"]
    args = _relays(posts)[0]["json"]["args"]
    assert args["buffer"]
    # The chart, not the alert a second time.
    assert args["caption"] == "📈 The chart of this episode"
    assert args["silent"] is True  # one sound for one alert
    assert notifier.take_receipt()["mode"] == "openclaw-agent"


@pytest.mark.parametrize("kind", ["update", "recovery", "monitor"])
def test_any_other_chart_stays_with_the_relays_one_message(agent_mode, posts, kind, monkeypatch):
    monkeypatch.setattr(quiet, "active", lambda now: False)
    assert notifier.send(_episode(kind), image=PNG) == notifier.SENT
    (relay,) = _relays(posts)
    assert relay["json"]["args"]["caption"] != "📈 The chart of this episode"


def test_a_failed_chart_does_not_cost_the_alert(agent_mode, posts):
    posts["relay"].append(httpx.ConnectError("refused"))
    assert notifier.send(_episode(), image=PNG) == notifier.SENT
    assert notifier.take_receipt()["mode"] == "openclaw-agent"


def test_a_fallback_keeps_the_chart_in_the_relays_one_message(agent_mode, posts):
    posts["hook"].append(FakeResponse(404, {}))
    assert notifier.send(_episode(), image=PNG) == notifier.SENT
    (relay,) = _relays(posts)
    assert relay["json"]["args"]["buffer"]
    assert relay["json"]["args"]["caption"] != "📈 The chart of this episode"


# --- the test send ------------------------------------------------------------


def test_the_test_send_goes_through_the_agent(agent_mode, posts):
    assert notifier.send_test() is True
    (call,) = _hooks(posts)
    assert call["headers"]["Idempotency-Key"].startswith("test@")
    assert "Nothing is wrong with the network" in call["json"]["message"]
    assert not _relays(posts)


def test_the_test_send_falls_back(agent_mode, posts):
    posts["hook"].append(FakeResponse(404, {}))
    assert notifier.send_test() is True
    assert len(_relays(posts)) == 1


def test_the_test_send_says_when_neither_delivered(agent_mode, posts):
    posts["hook"].append(FakeResponse(404, {}))
    posts["relay"].append(httpx.ConnectError("refused"))
    assert notifier.send_test() is False


# --- preflight ----------------------------------------------------------------


def test_preflight_a_400_is_the_token_accepted_and_no_turn_ran(agent_mode, posts, caplog):
    caplog.set_level("INFO", logger="alerter.notifier")
    posts["hook"].append(FakeResponse(400, {"ok": False, "error": "message required"}))
    assert notifier.preflight() is True
    (call,) = _hooks(posts)
    assert call["json"] == {}
    assert call["headers"]["Authorization"] == "Bearer hooks-tok"
    # The last line is what `smoking-pi alerts` shows: the path taken.
    last = [r.getMessage() for r in caplog.records][-1]
    assert "accepts the hooks token; openings go through the agent" in last
    assert "ready as the fallback" in last


@pytest.mark.parametrize("answer,reason", [
    (FakeResponse(401, _NOT_JSON), "rejected (401)"),
    (FakeResponse(404, _NOT_JSON), "not enabled"),
    (FakeResponse(200, {}), "unexpected HTTP 200"),
    (httpx.ConnectError("refused"), "did not answer"),
])
def test_preflight_a_failure_says_why_and_checks_the_relay(agent_mode, posts, caplog, answer, reason):
    caplog.set_level("INFO", logger="alerter.notifier")
    posts["hook"].append(answer)
    assert notifier.preflight() is True  # the relay answers
    assert reason in caplog.text
    assert "alerts go through the message relay" in caplog.text
    assert _relays(posts)
    last = [r.getMessage() for r in caplog.records][-1]
    assert "Delivery preflight" in last and "tools/invoke" in last


def test_preflight_no_hooks_token_does_not_probe(agent_mode, posts, monkeypatch, caplog):
    monkeypatch.delenv("OPENCLAW_HOOKS_TOKEN")
    assert notifier.preflight() is True
    assert not _hooks(posts)
    assert "no OPENCLAW_HOOKS_TOKEN" in caplog.text


def test_preflight_the_hook_alone_is_enough_and_says_there_is_no_fallback(agent_mode, posts, monkeypatch, caplog):
    caplog.set_level("INFO", logger="alerter.notifier")
    monkeypatch.delenv("OPENCLAW_GATEWAY_TOKEN")
    posts["hook"].append(FakeResponse(400, {}))
    assert notifier.preflight() is True
    assert "NOT ready, no fallback" in caplog.text


def test_preflight_neither_is_false(agent_mode, posts, monkeypatch):
    monkeypatch.delenv("OPENCLAW_GATEWAY_TOKEN")
    posts["hook"].append(FakeResponse(404, _NOT_JSON))
    assert notifier.preflight() is False


def test_preflight_message_mode_does_not_touch_the_hook(agent_mode, posts, monkeypatch):
    monkeypatch.setenv("OPENCLAW_PUSH", "message")
    assert notifier.preflight() is True
    assert not _hooks(posts)

