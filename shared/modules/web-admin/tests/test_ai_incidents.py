"""The web assistant answers from the shared guide and the shared diagnosis.

Before this, the in-UI chat had a prompt of its own (two sentences about
targets and statistics) and no diagnose_loss or get_incident_history: it
read raw loss events and attributed them itself, so it could call a hung
radio an outage after every MCP client had stopped (AGENTS.md: one
answering guide for every assistant).
"""

from datetime import datetime, timezone

from app.routes import ai
from app.services import ai_tools
from common import diagnosis_query, guide

T0 = datetime(2026, 10, 12, 15, 17, tzinfo=timezone.utc)


def test_the_system_prompt_is_the_shared_guide(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/London")
    prompt = ai._system_prompt()
    assert prompt.startswith(ai.CHAT_HEADER)
    assert prompt.endswith(guide.GUIDE)
    assert "Europe/London" in prompt
    # The guide names tools this chat lacks; the header says what to do then.
    assert "get_chart" in guide.GUIDE and "get_chart" in ai.CHAT_HEADER


def test_the_header_covers_what_this_chat_cannot_do():
    """Review of #339: the guide asks for a link on every warning bullet and
    for bold headings, but this chat's results carry no links and the page
    shows plain text (textContent, pre-wrap)."""
    assert "never write a URL" in ai.CHAT_HEADER
    assert "plain text, not Markdown" in ai.CHAT_HEADER


def test_the_chat_sends_it(client, monkeypatch):
    """The prompt the model receives is the one built above, not a copy."""
    from tests.test_ai import fake_anthropic, login, text_response

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls = fake_anthropic(monkeypatch, [text_response("ok")])
    login(client)
    client.post("/ai/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert calls[0]["system"] == ai._system_prompt()


def test_the_diagnosis_tools_are_offered():
    names = {t["name"] for t in ai_tools.TOOLS}
    assert {"diagnose_loss", "get_incident_history"} <= names
    assert not {"diagnose_loss", "get_incident_history"} & ai_tools.MUTATING_TOOLS


def test_diagnose_loss_answers_through_the_shared_code(monkeypatch):
    start = int(T0.timestamp())
    seen = {}

    def run(query, hours, cadences):
        seen["query"], seen["hours"] = query, hours
        return {
            "incidents": [{
                "class": "local_wifi", "detail": "deaf", "confidence": "high",
                "summary": "this host's Wi-Fi heard nothing for 5 h 19 min",
                "targets": [], "start_epoch": start, "end_epoch": start + 19140,
            }],
            "rows_truncated": False, "by_class": {"local_wifi": 1},
            "chronic": [], "coverage": {}, "targets_reporting": 18,
        }

    monkeypatch.setattr(diagnosis_query, "run", run)
    monkeypatch.setattr(ai_tools, "query_influx", lambda flux: [])
    out = ai_tools.execute_tool("diagnose_loss", {"hours": 48})
    assert seen["hours"] == 48 and seen["query"] is ai_tools.query_influx
    inc = out["incidents"][0]
    assert inc["class"] == "local_wifi" and inc["start"] == T0.isoformat()
    assert "start_epoch" not in inc
    assert out["by_class"] == {"local_wifi": 1} and out["truncated"] is False


def test_diagnose_loss_refuses_more_than_a_week():
    out = ai_tools.execute_tool("diagnose_loss", {"hours": 169})
    assert "at most 168" in out["error"]


def _history_query(rows, oldest):
    def query(flux):
        if "|> first()" in flux:
            return [{"_time": oldest}] if oldest else []
        assert 'r._measurement == "loss_incident"' in flux
        return rows
    return query


def test_incident_history_reads_the_stored_incidents(monkeypatch):
    rows = [
        {"_time": T0, "class": "local_wifi", "detail": "deaf",
         "confidence": "high", "summary": "deaf 5 h", "end_epoch": int(T0.timestamp()) + 19140,
         "minutes": 319, "targets": "", "evidence": "rx flat | associated",
         "against": "", "max_loss_pct": -1},
        {"_time": T0, "class": "probe_miss", "summary": "one point"},
    ]
    monkeypatch.setattr(ai_tools, "query_influx", _history_query(rows, T0))
    out = ai_tools.execute_tool("get_incident_history", {"days": 7})
    assert out["window_days"] == 7 and out["stored_from"] == T0.isoformat()
    assert [i["class"] for i in out["incidents"]] == ["local_wifi"]
    assert out["by_class"] == {"local_wifi": 1, "probe_miss": 1}
    inc = out["incidents"][0]
    assert inc["evidence"] == ["rx flat", "associated"] and "max_loss_pct" not in inc


def test_an_empty_history_is_not_a_quiet_one(monkeypatch):
    monkeypatch.setattr(ai_tools, "query_influx", _history_query([], None))
    out = ai_tools.execute_tool("get_incident_history", {})
    assert out["window_days"] == 30 and out["stored_from"] is None


def test_incident_history_validates_days():
    assert "between 1 and 365" in ai_tools.execute_tool(
        "get_incident_history", {"days": 0})["error"]
    assert "must be an integer" in ai_tools.execute_tool(
        "get_incident_history", {"days": "x"})["error"]
