"""The web admin's chat has the MCP server's get_notification_history,
through the same code (common/notification_ledger.py)."""

import json
import time

from app.services import ai_tools


def test_the_tool_is_listed_and_read_only():
    names = {tool["name"] for tool in ai_tools.TOOLS}
    assert "get_notification_history" in names
    assert "get_notification_history" not in ai_tools.MUTATING_TOOLS


def test_it_reads_the_ledger_beside_the_state_file(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERT_STATE_FILE", str(tmp_path / "state.json"))
    (tmp_path / "ledger.jsonl").write_text(json.dumps(
        {"id": "a", "kind": "alert", "outcome": "held",
         "decided_at": time.time() - 60, "text": "x"}) + "\n")
    out = ai_tools.execute_tool("get_notification_history", {"days": 2})
    assert [e["id"] for e in out["entries"]] == ["a"]
    assert out["entries"][0]["outcome"] == "held"


def test_it_validates_like_the_mcp_tool(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERT_STATE_FILE", str(tmp_path / "state.json"))
    assert "error" in ai_tools.execute_tool("get_notification_history", {"days": 31})
    assert "error" in ai_tools.execute_tool("get_notification_history", {"kind": "x"})
    assert ai_tools.execute_tool("get_notification_history", {})["recorded_from"] is None
