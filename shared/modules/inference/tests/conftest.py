"""Pytest setup for the inference module. No test reaches InfluxDB, imports
Jitterbug or runs BCP: the query, the client, the analyzer and the change-point
probabilities are all passed in as fakes."""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))  # shared/modules: `common` as a package

SCRUB_ENV_VARS = [
    "INFERENCE_INTERVAL", "INFERENCE_CONGESTION_DAYS", "INFERENCE_DEGRADATION_DAYS",
    "INFERENCE_SINCE", "INFERENCE_CATEGORIES", "INFERENCE_TARGETS",
    "INFERENCE_DEGRADATION_MIN_MINUTES", "INFERENCE_DEGRADATION_LOSS_PCT",
    "INFERENCE_STATE_DIR", "TSDB_TYPE",
    "INFLUX_URL", "INFLUX_TOKEN", "INFLUX_ORG", "INFLUX_BUCKET",
]


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch):
    for name in SCRUB_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
