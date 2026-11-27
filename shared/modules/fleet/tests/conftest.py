"""Pytest setup for Fleet. No test opens a socket or touches Docker."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SCRUB_ENV_VARS = [
    "FLEET_NODE_ID",
    "FLEET_NODE_NAME",
    "FLEET_SITE_NAME",
    "FLEET_LEAD_URLS",
    "FLEET_LEAD_MODE",
    "FLEET_STATE_DIR",
]


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch, tmp_path):
    for name in SCRUB_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    # Nothing a test runs may write to the real /var/lib/fleet.
    monkeypatch.setenv("FLEET_STATE_DIR", str(tmp_path / "state"))
