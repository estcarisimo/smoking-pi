"""What an error body may carry: a message we wrote and an id, never the
text of the exception. REINTRODUCTION TESTS for CodeQL
py/stack-trace-exposure -- put ``str(e)`` back in a route and its case
here fails."""

import pytest
from conftest import login

from app.routes import api as api_routes
from app.routes import sources as sources_routes
from app.routes import targets as targets_routes

SECRET = "http://config-manager:5000 with token=hunter2 exploded"


def _boom(*args, **kwargs):
    raise RuntimeError(SECRET)


@pytest.mark.parametrize("method,path,module,attr", [
    ("GET", "/api/status", api_routes, "config_api"),
    ("POST", "/api/apply", api_routes, "config_api"),
    ("POST", "/api/smokeping/restart", api_routes, "config_api"),
    ("POST", "/api/ocas/refresh", api_routes, "config_api"),
    ("GET", "/api/ocas/status", api_routes, "config_api"),
    ("GET", "/api/targets/dns", api_routes, "config_api"),
    ("GET", "/sources/api/fetch/tranco", sources_routes, "tranco_service"),
    ("POST", "/sources/api/update", sources_routes, "config_api"),
    ("POST", "/targets/1/toggle", targets_routes, "config_api"),
])
def test_exception_text_never_reaches_the_body(client, monkeypatch, method,
                                               path, module, attr):
    class Exploding:
        def __getattr__(self, name):
            return _boom

        def is_database_available(self):
            return True

    monkeypatch.setattr(module, attr, Exploding())
    login(client)
    kwargs = {"json": {"sites": ["a.com"]}} if method == "POST" else {}
    resp = client.open(path, method=method, **kwargs)
    text = resp.get_data(as_text=True)
    assert "hunter2" not in text, f"{path} leaked the exception"
    assert SECRET not in text
    body = resp.get_json()
    assert body is not None and body.get("error")
    assert len(body["error_id"]) == 8


def test_error_id_lands_in_the_log(client, monkeypatch, caplog):
    class Exploding:
        def __getattr__(self, name):
            return _boom

    monkeypatch.setattr(api_routes, "config_api", Exploding())
    login(client)
    with caplog.at_level("ERROR"):
        body = client.get("/api/status").get_json()
    assert body["error_id"] in caplog.text
    assert SECRET in caplog.text


def test_not_found_is_static(client, monkeypatch):
    class NotFound:
        def is_database_available(self):
            return True

        def toggle_target_in_db(self, target_id):
            raise ValueError(f"no row {target_id} in postgresql://x:hunter2@db")

    monkeypatch.setattr(targets_routes, "config_api", NotFound())
    login(client)
    resp = client.post("/targets/7/toggle")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "Target not found"
    assert "hunter2" not in resp.get_data(as_text=True)


def test_validation_errors_are_still_specific(client, monkeypatch):
    """Per-field validation text is ours, not an exception's, and the form
    needs it."""
    class DB:
        def is_database_available(self):
            return True

    monkeypatch.setattr(targets_routes, "config_api", DB())
    login(client)
    resp = client.put("/targets/1", json={"host": "not a host!!"})
    assert resp.status_code == 400
    assert resp.get_json()["errors"]["host"]
