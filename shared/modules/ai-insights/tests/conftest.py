"""Pytest fixtures for the ai-insights test suite.

All network access (InfluxDB, Anthropic) is mocked; importing the modules
must never require environment variables.
"""

import sys
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch):
    """The collector's CPE summary reads the microcut threshold from the
    environment (common.microcuts), and its incidents the alerter's
    thresholds (common.definitions); an exported value would move the Flux
    the tests assert on."""
    for name in ("MICROCUT_LOSS_PCT", "WIFI_WEAK_DBM", "WIFI_WEAK_SAMPLES",
                 "WIDESPREAD_PCT", "WIDESPREAD_LOSS_PCT", "VERDICT_BROAD_PCT",
                 "HIGH_LOSS_PCT"):
        monkeypatch.delenv(name, raising=False)
