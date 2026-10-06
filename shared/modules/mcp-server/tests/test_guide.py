"""One answering guide for every assistant (guide.py).

The server sends it as its instructions; the OpenClaw skill is generated
from it. These fail when the committed skill drifts from the guide, or when
the guide names a tool the server does not have.
"""

import importlib.util
import re
from pathlib import Path

import guide
import server

ROOT = Path(__file__).resolve().parents[4]
BUILD = ROOT / "shared" / "scripts" / "build-openclaw-skill.py"


def _builder():
    spec = importlib.util.spec_from_file_location("build_openclaw_skill", BUILD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_openclaw_skill_is_built_from_the_guide():
    skill = ROOT / "examples" / "openclaw" / "smokeping-monitoring" / "SKILL.md"
    assert skill.read_text() == _builder().build(), (
        "SKILL.md is stale: run python3 shared/scripts/build-openclaw-skill.py")


def test_the_skill_keeps_its_front_matter_first():
    assert _builder().build().startswith("---\nname: smokeping-monitoring\n")


def test_every_assistant_gets_the_guide_as_the_server_instructions():
    assert guide.GUIDE in server.SERVER_INSTRUCTIONS
    assert "clock is" in server.SERVER_INSTRUCTIONS


def test_the_timezone_line_names_the_pis_zone(monkeypatch):
    monkeypatch.setenv("TZ", "America/Chicago")
    assert "set to America/Chicago" in server._instructions()


def test_the_guide_names_only_tools_that_exist():
    registered = {name for name in dir(server) if not name.startswith("_")}
    named = set(re.findall(r"`((?:get|list|diagnose|system|add|remove|toggle|apply|mute|unmute|ack)_[a-z_]+)`", guide.GUIDE))
    assert named, "the guide should name the tools it relies on"
    assert named <= registered, named - registered


def test_the_guide_is_not_written_for_one_assistant_or_channel():
    for word in ("OpenClaw", "Telegram", "parse_mode", "<b>"):
        assert word not in guide.GUIDE


def test_the_doc_quotes_the_assistant_instructions_word_for_word():
    import guide
    doc = (ROOT / "docs" / "remote-connector.md").read_text()
    assert doc.count("```text\n" + guide.ASSISTANT_INSTRUCTIONS + "\n```") == 1
