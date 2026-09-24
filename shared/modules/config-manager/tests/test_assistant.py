"""GET /assistant: is a chat assistant calling the MCP server? Evidence only."""

from types import SimpleNamespace

import pytest

import api as api_module
import assistant

LOGS = """\
2026-09-24T00:37:28.150088216Z INFO:     Uvicorn running on http://127.0.0.1:8090
2026-09-24T09:12:03.000000000Z 2026-09-24 09:12:03,120 INFO mcp.tools: tool=get_latency_stats args=hours=6 -> 30 stats in 41ms
2026-09-24T09:12:05.000000000Z INFO:     127.0.0.1:50123 - "POST /mcp HTTP/1.1" 200 OK
2026-09-24T09:12:07.000000000Z 2026-09-24 09:12:07,901 INFO mcp.tools: tool=get_loss_events args=hours=24 -> 12 events in 88ms
"""


def test_no_container_is_absent_not_an_error():
    assert assistant.summarize(None, None, "") == {"mcp": "absent", "connected": False}


def test_a_stopped_server_says_so():
    got = assistant.summarize("exited", "2026-09-24T00:37:28Z", "")
    assert got == {"mcp": "stopped", "connected": False, "state": "exited"}


def test_connected_only_on_the_servers_own_tool_lines():
    got = assistant.summarize("running", "2026-09-24T00:36:51Z", LOGS)
    assert got["connected"] is True and got["calls"] == 2
    assert got["last_tool"] == "get_loss_events"
    assert got["last_call"] == "2026-09-24T09:12:07.000000000Z"
    assert got["since"] == "2026-09-24T00:36:51Z"


def test_http_traffic_without_a_tool_call_is_not_a_connection():
    """A probe or a health check hits /mcp; only a tool= line is a call."""
    quiet = "\n".join(line for line in LOGS.splitlines() if "tool=" not in line)
    got = assistant.summarize("running", "2026-09-24T00:36:51Z", quiet)
    assert got["connected"] is False and got["calls"] == 0
    assert "last_tool" not in got


# --- the endpoint -----------------------------------------------------------

@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("CONFIG_API_TOKEN", raising=False)
    api_module.app.config["TESTING"] = True
    with api_module.app.test_client() as c:
        yield c


def _docker(monkeypatch, container):
    client = SimpleNamespace(containers=SimpleNamespace(get=lambda name: container))
    monkeypatch.setattr(api_module.docker, "from_env", lambda: client)


def test_endpoint_reads_the_mcp_servers_log(client, monkeypatch):
    seen = {}

    def logs(timestamps, tail):
        seen.update(timestamps=timestamps, tail=tail)
        return LOGS.encode()

    container = SimpleNamespace(status="running", logs=logs,
                                attrs={"State": {"StartedAt": "2026-09-24T00:36:51Z"}})
    monkeypatch.setattr(api_module, "resolve_container_name", lambda svc: "pro-mcp-server-1")
    _docker(monkeypatch, container)
    body = client.get("/assistant").get_json()
    assert body["available"] is True and body["connected"] is True
    assert seen["timestamps"] is True and seen["tail"] > 0


def test_endpoint_without_the_container_is_absent(client, monkeypatch):
    def missing(svc):
        raise Exception("Container for service 'mcp-server' not found")

    monkeypatch.setattr(api_module, "resolve_container_name", missing)
    body = client.get("/assistant").get_json()
    assert body == {"available": True, "mcp": "absent", "connected": False}


def test_endpoint_when_docker_fails(client, monkeypatch):
    monkeypatch.setattr(api_module, "resolve_container_name", lambda svc: "x")

    def boom():
        raise RuntimeError("no docker socket")

    monkeypatch.setattr(api_module.docker, "from_env", boom)
    r = client.get("/assistant")
    assert r.status_code == 503 and r.get_json()["available"] is False
