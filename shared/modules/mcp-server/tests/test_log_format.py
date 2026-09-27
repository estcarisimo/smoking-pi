"""The web tour reads the server's own ``mcp.tools: tool=`` lines as proof
the assistant was used (config-manager/assistant.py), so main() must win
over the handler the SDK installs on import."""

import importlib.util
import logging
import os

import main

HERE = os.path.dirname(os.path.abspath(__file__))
ASSISTANT = os.path.join(HERE, "..", "..", "config-manager", "assistant.py")


def test_main_replaces_the_bare_handler_the_sdk_installed(monkeypatch):
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    bare = logging.StreamHandler()
    bare.setFormatter(logging.Formatter("%(message)s"))
    root.handlers = [bare]
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    monkeypatch.setattr(main.mcp, "run", lambda **kw: None)
    try:
        main.main()
        assert [h.formatter._fmt for h in root.handlers] == [main.LOG_FORMAT]
    finally:
        root.handlers, _ = saved
        root.setLevel(saved[1])


def test_a_formatted_tool_line_is_what_the_tour_counts():
    record = logging.LogRecord(
        "mcp.tools", logging.INFO, __file__, 0,
        "tool=%s args=%s -> %s in %.0fms", ("system_status", "-", "ok", 12.0), None,
    )
    line = "2026-09-27T15:36:55.105233159Z " + logging.Formatter(main.LOG_FORMAT).format(record)
    spec = importlib.util.spec_from_file_location("assistant", ASSISTANT)
    assistant = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(assistant)
    match = assistant._CALL.match(line)
    assert match and match.group(2) == "system_status"
