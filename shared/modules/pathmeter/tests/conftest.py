"""Pytest setup for the path meter. No test runs scamper, resolves a name
or reaches InfluxDB: the runner, the resolver, the query and the writer are
all passed in as fakes."""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))  # shared/modules: `common` as a package

SCRUB_ENV_VARS = [
    "PATHMETER_INTERVAL", "PATHMETER_PPS", "PATHMETER_STATE_DIR", "TSDB_TYPE",
    "INFLUX_URL", "INFLUX_TOKEN", "INFLUX_ORG", "INFLUX_BUCKET",
]


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch, tmp_path):
    for name in SCRUB_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATHMETER_STATE_DIR", str(tmp_path / "state"))
