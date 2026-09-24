"""GET/POST /first-run: whether the web admin's welcome tour shows itself."""

import pytest

import api as api_module


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.delenv("CONFIG_API_TOKEN", raising=False)
    monkeypatch.setattr(api_module, "OUTPUT_DIR", tmp_path)
    api_module.app.config["TESTING"] = True
    with api_module.app.test_client() as c:
        yield c


def test_a_new_install_has_not_seen_the_tour(client):
    assert client.get("/first-run").get_json() == {
        "completed": False, "outcome": None, "at": None}


@pytest.mark.parametrize("outcome", ["done", "skipped"])
def test_finishing_or_skipping_is_remembered(client, outcome, tmp_path):
    assert client.post("/first-run", json={"outcome": outcome}).status_code == 200
    body = client.get("/first-run").get_json()
    assert body["completed"] is True and body["outcome"] == outcome and body["at"]
    assert (tmp_path / ".first-run.json").exists()


def test_reset_shows_it_again(client):
    client.post("/first-run", json={"outcome": "done"})
    assert client.post("/first-run", json={"outcome": "reset"}).get_json()["completed"] is False
    assert client.get("/first-run").get_json()["completed"] is False
    # Resetting twice is not an error.
    assert client.post("/first-run", json={"outcome": "reset"}).status_code == 200


@pytest.mark.parametrize("body", [{}, {"outcome": "maybe"}, None])
def test_anything_else_is_refused(client, body):
    response = client.post("/first-run", json=body) if body is not None \
        else client.post("/first-run", data="not json")
    assert response.status_code == 400
    assert client.get("/first-run").get_json()["completed"] is False


def test_a_corrupt_marker_does_not_trap_every_login_in_the_tour(client, tmp_path):
    (tmp_path / ".first-run.json").write_text("{not json")
    assert client.get("/first-run").get_json()["completed"] is True


def test_the_endpoints_require_the_token(client, monkeypatch):
    monkeypatch.setenv("CONFIG_API_TOKEN", "sekrit")
    assert client.get("/first-run").status_code == 401
    assert client.post("/first-run", json={"outcome": "done"}).status_code == 401
