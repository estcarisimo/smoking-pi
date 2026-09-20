"""Suite-wide guards for the MCP server tests.

system_status() reads the wifi_link measurement, so any test that calls it
would open a real InfluxDB connection unless the query layer is stubbed.
AGENTS.md forbids that, and a stub scoped to one test file protects only
that file -- so it lives here, for every file in the suite. Tests that want
wifi data patch over it.
"""

import pytest

import backends
import server


@pytest.fixture(autouse=True)
def no_wifi_influx(monkeypatch):
    monkeypatch.setattr(backends, "query_influx", lambda flux: [])
    monkeypatch.setattr(server, "query_influx", lambda flux: [])
    monkeypatch.delenv("WIFI_WEAK_DBM", raising=False)
    # The microcut threshold too: a developer's exported value would move
    # every cut/floor assertion in the suite.
    monkeypatch.delenv("MICROCUT_LOSS_PCT", raising=False)
