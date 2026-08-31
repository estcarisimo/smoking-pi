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
    # These were missing, which made the tests that read them depend on the
    # developer's own shell. Anything the alerter reads belongs here.
    "OPENCLAW_GATEWAY_TOKEN",
    "OPENCLAW_HOOK_PATH",
    "ALERT_RESOLVE_AFTER",
    "ALERT_MAX_PER_HOUR",
    "STALE_WINDOW",
    "MICROCUT_LOSS_PCT",
    "ALERT_MARKUP",
    "VERDICT_BROAD_PCT",
    "VERDICT_MIN_TARGETS",
    "VERDICT_IMPAIRED_LOSS_PCT",
    "VERDICT_STALE_DOWN_HOURS",
    # Link building reads these; an exported value would change the rendered
    # message and, with TSDB_TYPE, whether links appear at all.
    "PUBLIC_BASE_HOST",
    "GRAFANA_PUBLIC_URL",
    "WEB_ADMIN_PUBLIC_URL",
    "TUNNEL_BASE_HOST",
    "GRAFANA_TUNNEL_URL",
    "WEB_ADMIN_TUNNEL_URL",
    "TSDB_TYPE",
    # Mutes are read on the delivery path; an exported value would point the
    # suite at a real mutes file and could silence the alerts it asserts on.
    "ALERT_MUTES_FILE",
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


@pytest.fixture
def mutes_file(monkeypatch, tmp_path):
    """Point the mute lookup at a temp file and hand back a writer.

    The alerter only ever reads this file, so the fixture plays the part the
    mcp-server plays in production.
    """
    import json

    path = tmp_path / "mutes.json"
    monkeypatch.setenv("ALERT_MUTES_FILE", str(path))

    def write(entries):
        path.write_text(json.dumps({"mutes": entries}), encoding="utf-8")

    write.path = path
    return write
