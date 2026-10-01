"""Pytest setup for the mDNS responder. No test opens a socket."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SCRUB_ENV_VARS = ["MDNS_NAME", "MDNS_INTERFACES", "MDNS_STATE_DIR"]


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch):
    for name in SCRUB_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
