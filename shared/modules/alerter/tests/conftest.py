"""Pytest fixtures for the alerter test suite.

All network access (InfluxDB, OpenClaw, webhooks) is mocked; importing the
modules must never require environment variables. An autouse fixture scrubs
every alerter-related env var so tests are hermetic regardless of the host.
"""

import sys
from pathlib import Path

import pytest

MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

SCRUB_ENV_VARS = [
    "INFLUX_URL",
    "INFLUX_TOKEN",
    "INFLUX_ORG",
    "INFLUX_BUCKET",
    "NOTIFY_MODE",
    "OPENCLAW_URL",
    "OPENCLAW_HOOK_TOKEN",
    "OPENCLAW_CHANNEL",
    "OPENCLAW_TO",
    "ALERT_WEBHOOK_URL",
    "ALERT_WEBHOOK_TOKEN",
    "ALERT_INTERVAL",
    "ALERT_COOLDOWN",
    "ALERT_STATE_FILE",
    "DOWN_WINDOW",
    "HIGH_LOSS_PCT",
    "MICROCUT_BURST_N",
    "REPORTS_DIR",
    "REPORT_DELIVERY_INTERVAL",
    "REPORT_MAX_CHARS",
]


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch):
    for name in SCRUB_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def state_file(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    monkeypatch.setenv("ALERT_STATE_FILE", str(path))
    return path
