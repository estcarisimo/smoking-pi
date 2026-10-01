"""Pytest setup for the traffic meter. No test runs nft, opens a socket or
calls config-manager: nft and the HTTP opener are faked."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SCRUB_ENV_VARS = ["NETMETER", "NETMETER_INTERFACES", "NETMETER_STATE_DIR", "CONFIG_API_URL",
                  "CONFIG_API_TOKEN", "TSDB_TYPE", "INFLUX_URL", "INFLUX_TOKEN", "INFLUX_ORG",
                  "INFLUX_BUCKET"]


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch):
    for name in SCRUB_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
