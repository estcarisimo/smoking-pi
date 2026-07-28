"""Tests for the optional shared-token API auth.

Importing api.py is side-effect free after the startup refactor (no
bootstrap, no DB connection at import time), so these tests exercise the
real Flask app with its test client. DATABASE_URL is unset in the test
environment, so database-backed routes respond 400 - which is all we need
to distinguish "authorized" from 401.
"""

import pytest

import api as api_module


@pytest.fixture()
def client():
    api_module.app.config["TESTING"] = True
    with api_module.app.test_client() as client:
        yield client


@pytest.fixture()
def fresh_db_probe():
    """Ensure the DB-mode cache does not leak between tests."""
    api_module.api.refresh_database_mode()
    yield
    api_module.api.refresh_database_mode()


def test_no_token_configured_allows_requests(client, monkeypatch, fresh_db_probe):
    monkeypatch.delenv("CONFIG_API_TOKEN", raising=False)
    assert client.get("/health").status_code == 200
    # No DB in tests -> /probes reports database unavailable, not 401
    assert client.get("/probes").status_code == 400


def test_health_is_always_open(client, monkeypatch):
    monkeypatch.setenv("CONFIG_API_TOKEN", "sekrit")
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "healthy"


def test_missing_token_rejected(client, monkeypatch, fresh_db_probe):
    monkeypatch.setenv("CONFIG_API_TOKEN", "sekrit")
    assert client.get("/probes").status_code == 401
    assert client.get("/status").status_code == 401
    assert client.post("/generate").status_code == 401


def test_wrong_token_rejected(client, monkeypatch, fresh_db_probe):
    monkeypatch.setenv("CONFIG_API_TOKEN", "sekrit")
    response = client.get(
        "/probes", headers={"Authorization": "Bearer wrong"}
    )
    assert response.status_code == 401
    response = client.get("/probes", headers={"X-API-Token": "wrong"})
    assert response.status_code == 401


def test_bearer_token_accepted(client, monkeypatch, fresh_db_probe):
    monkeypatch.setenv("CONFIG_API_TOKEN", "sekrit")
    response = client.get(
        "/probes", headers={"Authorization": "Bearer sekrit"}
    )
    # Authorized: passes auth, then fails on missing DB (400, not 401)
    assert response.status_code == 400


def test_x_api_token_accepted(client, monkeypatch, fresh_db_probe):
    monkeypatch.setenv("CONFIG_API_TOKEN", "sekrit")
    response = client.get("/probes", headers={"X-API-Token": "sekrit"})
    assert response.status_code == 400
