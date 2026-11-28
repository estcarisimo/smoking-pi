"""The two entrypoints: what state they report, and the idle loop."""

import json
import logging
import os
import threading

import pytest

from fleet import agent, lead, runner, settings, status
from fleet.layout import lead_paths, node_paths

from fakelead import enrolled

NODE = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
FLEET = "0b1e2f3a-0000-4000-8000-000000000001"


def cfg(**env):
    return settings.load({"FLEET_NODE_ID": NODE, **env})


def test_agent_not_enrolled(tmp_path):
    report, line = agent.describe(cfg(), node_paths(tmp_path))
    assert report["state"] == "not_enrolled" and report["enrolled"] is False
    assert "not enrolled" in line and NODE in line


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_agent_enrolled(tmp_path):
    paths = node_paths(tmp_path)
    enrolled(paths)
    report, line = agent.describe(cfg(), paths)
    assert report["state"] == "enrolled" and report["fleet_id"] == FLEET
    assert FLEET in line and report["health"] == "ok" and report["cert_not_after"]


def test_an_enrollment_without_its_credentials_is_corrupt(tmp_path):
    """enrollment.json alone (a volume restored in part, a hand edit): the
    agent cannot speak for the node, and says so instead of trying."""
    paths = node_paths(tmp_path)
    write(paths.enrollment, json.dumps({"node_id": NODE, "fleet_id": FLEET}))
    report, line = agent.describe(cfg(), paths)
    assert report["state"] == "state_corrupt" and report["enrolled"] is False
    assert report["health"] == "failing" and "fleet leave --force" in line


@pytest.mark.parametrize("text", ["{broken", "[]", "null", "42", '"x"', "{}",
                                  json.dumps({"fleet_id": FLEET}),
                                  json.dumps({"node_id": "nope", "fleet_id": FLEET}),
                                  json.dumps({"node_id": NODE + "\\n"})])
def test_a_corrupt_enrollment_is_its_own_state_never_not_enrolled(tmp_path, text):
    """PR C must not enroll over a corrupt state, and nothing may crash."""
    paths = node_paths(tmp_path)
    write(paths.enrollment, text)
    report, line = agent.describe(cfg(), paths)
    assert report["state"] == "state_corrupt" and report["enrolled"] is False
    assert "corrupt" in line and report["fleet_id"] is None
    assert any(p.startswith("enrollment.json") for p in report["problems"])


def test_an_unreadable_enrollment_is_corrupt(tmp_path):
    paths = node_paths(tmp_path)
    write(paths.enrollment, json.dumps({"node_id": NODE}))
    paths.enrollment.chmod(0)
    try:
        if os.access(paths.enrollment, os.R_OK):
            pytest.skip("running as root: the file stays readable")
        assert agent.describe(cfg(), paths)[0]["state"] == "state_corrupt"
    finally:
        paths.enrollment.chmod(0o600)


@pytest.mark.parametrize("env", [{"FLEET_NODE_ID": "11111111-2222-4333-8444-555555555555"}, {}])
def test_an_enrollment_for_another_node_id_is_identity_mismatch(tmp_path, env):
    """An older env file restored beside the agent volume that holds the
    enrolled key: the agent must not speak as a node it is not."""
    paths = node_paths(tmp_path)
    write(paths.enrollment, json.dumps({"node_id": NODE, "fleet_id": FLEET}))
    report, line = agent.describe(settings.load(env), paths)
    assert report["state"] == "identity_mismatch" and report["enrolled"] is False
    assert NODE in line and "nothing is sent" in line


def test_agent_without_identity(tmp_path):
    report, line = agent.describe(settings.load({}), node_paths(tmp_path))
    assert report["state"] == "no_identity" and report["node_id"] is None
    assert "smoking-pi fleet status" in line


def test_lead_states(tmp_path):
    paths = lead_paths(tmp_path)
    report, line = lead.describe(cfg(FLEET_LEAD_MODE="dedicated"), paths)
    assert report["state"] == "no_fleet" and "no fleet initialized" in line
    assert report["lead_mode"] == "dedicated"
    write(paths.fleet, json.dumps({"fleet_id": FLEET}))
    report, line = lead.describe(cfg(), paths)
    assert report["state"] == "initialized" and FLEET in line


@pytest.mark.parametrize("text", ["{broken", "[1]", "{}", json.dumps({"fleet_id": "x"})])
def test_a_corrupt_fleet_file_is_its_own_state(tmp_path, text):
    paths = lead_paths(tmp_path)
    write(paths.fleet, text)
    report, line = lead.describe(cfg(), paths)
    assert report["state"] == "state_corrupt" and report["fleet_id"] is None
    assert "corrupt" in line


def test_a_status_file_that_cannot_be_written_does_not_stop_the_service(
        monkeypatch, caplog):
    calls = []

    def refuse(report, root=None):
        calls.append(report)
        raise PermissionError("volume owned by root")

    monkeypatch.setattr(status, "write", refuse)
    stop = threading.Event()
    ticks = iter([False, False, True])
    monkeypatch.setattr(stop, "wait", lambda _t: next(ticks))
    with caplog.at_level(logging.WARNING, logger="fleet"):
        runner.run(status.snapshot(role="agent"), "hello", stop=stop)
    assert len(calls) == 3
    assert caplog.text.count("cannot write the status file (PermissionError)") == 1
    assert "volume owned by root" not in caplog.text


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
    assert lead.main() == 0 and calls["report"]["role"] == "lead"
    stop = threading.Event()
    stop.set()  # one turn, then out
    assert agent.main(stop=stop) == 0
    assert status.read()["role"] == "agent" and status.read()["state"] == "not_enrolled"


def test_health():
    assert not status.healthy({})
    # Enrolled and failing is unhealthy; not in a fleet is not.
    assert status.healthy({"at": 1000.0, "health": "idle"}, now=1000.0)
    assert status.healthy({"at": 1000.0, "health": "ok"}, now=1000.0)
    assert not status.healthy({"at": 1000.0, "health": "failing"}, now=1000.0)
    assert status.healthy({"at": 1000.0}, now=1000.0 + 3 * status.TICK_SECONDS)
    assert not status.healthy({"at": 1000.0}, now=1001.0 + 3 * status.TICK_SECONDS)
    assert status.main(["--check"]) == 1
    status.write({"at": 1e12})
    assert status.main(["--check"]) == 0


def test_status_refuses_fields_it_does_not_define():
    with pytest.raises(KeyError):
        status.snapshot(write_token="x")
