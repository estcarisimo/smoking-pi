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
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "ALERT_SILENT",
    "ALERT_IMAGE_AS_DOCUMENT",
    "ALERT_DIAGNOSIS_HOURS",
    "INCIDENT_HISTORY_HOURS",
    "OPENCLAW_URL",
    "OPENCLAW_HOOK_TOKEN",
    "OPENCLAW_CHANNEL",
    "OPENCLAW_TO",
    "ALERT_WEBHOOK_URL",
    "ALERT_WEBHOOK_TOKEN",
    "ALERT_INTERVAL",
    "ALERT_STATE_FILE",
    "DOWN_WINDOW",
    "HIGH_LOSS_PCT",
    "HIGH_LOSS_MIN_POINTS",
    "WIDESPREAD_PCT",
    "WIDESPREAD_LOSS_PCT",
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
    "WIFI_WEAK_DBM",
    "WIFI_WEAK_SAMPLES",
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
    # Quiet hours read these (and the digest's zone): an exported value
    # would hold the alerts a test expects to see sent.
    "QUIET_FROM",
    "QUIET_UNTIL",
    "DIGEST_TZ",
    # Read by clock.py when DIGEST_TZ is unset: a developer's own TZ must
    # not change what the tests print.
    "TZ",
    # A real URL here would make the suite check in with a real service.
    "ALERT_DEADMAN_URL",
    # The weekly summary, and the retired digest's settings it maps.
    "WEEKLY_ENABLED",
    "WEEKLY_DAY",
    "WEEKLY_AT",
    "WEEKLY_SILENT",
    "DIGEST_ENABLED",
    "DIGEST_AT",
]


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch, tmp_path):
    for name in SCRUB_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    # The incident history starts a background diagnosis from run_iteration.
    # It would outlive a test's mocks and query a real InfluxDB, so it is
    # off unless a test turns it on (test_incident_history.py).
    monkeypatch.setenv("INCIDENT_HISTORY_HOURS", "0")
    # A query through flux.query_influx (the evaluator, the watchdog events,
    # the incident history) that a test did not fake fails at once, as an
    # unreachable database would, instead of trying the network. The
    # watchdog events are read in every run_iteration, including under
    # tests that fake only the evaluator. Modules that call common.tsdb
    # directly are faked by their own tests.
    import flux

    def _no_influx(flux_src):
        raise ConnectionError("tests never reach InfluxDB")

    monkeypatch.setattr(flux, "query_influx", _no_influx)
    # The chart on an alert reads through common.tsdb: the same, so an
    # alert under test is sent without one rather than trying the network.
    from common import tsdb

    monkeypatch.setattr(tsdb, "query_influx", _no_influx)
    # Every delivery writes the notification ledger: into the test's own
    # directory, never /var/lib/alerter or /tmp.
    import notifications

    monkeypatch.setattr(notifications, "ledger_file",
                        lambda: str(tmp_path / "ledger" / "ledger.jsonl"))
    monkeypatch.setattr(notifications, "_last_compact", None)
    # The service labels are cached for an hour, per process: each test
    # reads its own.
    import names

    monkeypatch.setattr(names, "_cache", {"at": None, "labels": {}})


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
