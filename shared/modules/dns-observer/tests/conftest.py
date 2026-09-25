"""Pytest setup for the DNS observer. No test touches the network or Docker."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SCRUB_ENV_VARS = [
    "DNS_UPSTREAMS", "DNS_FALLBACK", "DNS_BOOTSTRAP", "DNS_ALLOW_PRIVATE_UPSTREAM",
    "DNS_ALLOW_CLIENTS", "DNS_BIND", "DNS_PORT", "DNS_ANONYMIZE_CLIENTS",
    "DNS_RETENTION_DAYS", "DNS_UPSTREAM_TIMEOUT_MS", "DNS_ADMIN_ADDRESS",
    "DNS_ADMIN_USER", "DNS_ADMIN_PASSWORD", "DNS_CANARY_VIA", "DNS_CANARY_PORT",
    "DNS_CANARY_DOMAIN", "DNS_CANARY_INTERVAL", "DNS_CANARY_MISSES",
    "DNS_QUIET_AFTER", "DNS_STATE_DIR", "ADGUARD_BINARY", "ADGUARD_CONF",
    "ADGUARD_WORK",
]


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch):
    for name in SCRUB_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def env(tmp_path):
    return {
        "DNS_ADMIN_PASSWORD": "pw",
        "DNS_STATE_DIR": str(tmp_path / "state"),
        "ADGUARD_CONF": str(tmp_path / "conf" / "AdGuardHome.yaml"),
        "ADGUARD_WORK": str(tmp_path / "work"),
    }
