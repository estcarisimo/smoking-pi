"""step_seconds and pings belong to the probe; a target payload with them
is refused, not silently stripped."""

import pytest

import api as api_module


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("CONFIG_API_TOKEN", raising=False)
    monkeypatch.setattr(api_module.api, "_check_database_available", lambda: True)
    api_module.api.refresh_database_mode()

    def no_db():
        raise AssertionError("a refused payload must not reach the database")

    monkeypatch.setattr(api_module, "get_db_session", no_db)
    api_module.app.config["TESTING"] = True
    with api_module.app.test_client() as c:
        yield c
    api_module.api.refresh_database_mode()


BASE = {"name": "Mine", "host": "example.org", "title": "Mine",
        "category_id": 1, "probe_id": 1}


@pytest.mark.parametrize("extra, fields", [
    ({"step_seconds": 60}, ["step_seconds"]),
    ({"pings": 20}, ["pings"]),
    ({"step_seconds": 60, "pings": 20}, ["step_seconds", "pings"]),
])
def test_create_refuses_probe_owned_fields(client, extra, fields):
    response = client.post("/targets", json={**BASE, **extra})
    assert response.status_code == 400
    body = response.get_json()
    assert body["fields"] == fields
    assert "probe" in body["error"]


def test_update_refuses_them_too(client):
    response = client.put("/targets/7", json={"pings": 20})
    assert response.status_code == 400
    assert response.get_json()["fields"] == ["pings"]
