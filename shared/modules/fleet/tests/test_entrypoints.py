"""The two entrypoints: what state they report, and the idle loop."""

import json
import logging
import threading

from fleet import agent, lead, runner, settings, status
from fleet.layout import lead_paths, node_paths

NODE = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
FLEET = "0b1e2f3a-0000-4000-8000-000000000001"


def cfg(**env):
    return settings.load({"FLEET_NODE_ID": NODE, **env})


def test_agent_not_enrolled(tmp_path):
    report, line = agent.describe(cfg(), node_paths(tmp_path))
    assert report["state"] == "not_enrolled" and report["enrolled"] is False
    assert "not enrolled" in line and NODE in line


def test_agent_enrolled(tmp_path):
    paths = node_paths(tmp_path)
    paths.enrollment.parent.mkdir(parents=True)
    paths.enrollment.write_text(json.dumps({"fleet_id": FLEET}))
    report, line = agent.describe(cfg(), paths)
    assert report["state"] == "enrolled" and report["fleet_id"] == FLEET
    assert FLEET in line


def test_agent_unreadable_enrollment_is_not_enrolled(tmp_path):
    paths = node_paths(tmp_path)
    paths.enrollment.parent.mkdir(parents=True)
    paths.enrollment.write_text("{broken")
    assert agent.describe(cfg(), paths)[0]["state"] == "not_enrolled"


def test_agent_without_identity(tmp_path):
    report, line = agent.describe(settings.load({}), node_paths(tmp_path))
    assert report["state"] == "no_identity" and report["node_id"] is None
    assert "smoking-pi fleet status" in line


def test_lead_states(tmp_path):
    paths = lead_paths(tmp_path)
    report, line = lead.describe(cfg(FLEET_LEAD_MODE="dedicated"), paths)
    assert report["state"] == "no_fleet" and "no fleet initialized" in line
    assert report["lead_mode"] == "dedicated"
    paths.fleet.parent.mkdir(parents=True)
    paths.fleet.write_text(json.dumps({"fleet_id": FLEET}))
    report, line = lead.describe(cfg(), paths)
    assert report["state"] == "initialized" and FLEET in line


def test_run_logs_one_line_writes_status_and_stops(tmp_path, caplog):
    stop = threading.Event()
    report = status.snapshot(role="agent", state="not_enrolled")
    stop.set()  # one tick, then out
    with caplog.at_level(logging.INFO, logger="fleet"):
        runner.run(report, "fleet agent: hello", problems=("FLEET_X: bad",), stop=stop)
    written = status.read()
    assert written["state"] == "not_enrolled" and status.healthy(written)
    assert "fleet agent: hello" in caplog.text and "FLEET_X: bad" in caplog.text


def test_main_runs_until_stopped(monkeypatch):
    calls = {}
    monkeypatch.setenv("FLEET_NODE_ID", NODE)
    monkeypatch.setattr(runner, "run", lambda report, line, problems=(): calls.update(
        report=report, line=line, problems=problems))
    assert agent.main() == 0 and calls["report"]["role"] == "agent"
    assert lead.main() == 0 and calls["report"]["role"] == "lead"


def test_health():
    assert not status.healthy({})
    assert status.healthy({"at": 1000.0}, now=1000.0 + 3 * status.TICK_SECONDS)
    assert not status.healthy({"at": 1000.0}, now=1001.0 + 3 * status.TICK_SECONDS)
    assert status.main(["--check"]) == 1
    status.write({"at": 1e12})
    assert status.main(["--check"]) == 0


def test_status_refuses_fields_it_does_not_define():
    import pytest
    with pytest.raises(KeyError):
        status.snapshot(write_token="x")
