"""What an error response may carry: a message we wrote and an id, nothing
an exception object could have brought with it.

REINTRODUCTION TESTS for CodeQL py/stack-trace-exposure: put ``str(e)``
back into any of these bodies and the first test fails on that route.
"""

import pytest

import api as api_module

SECRET_IN_EXCEPTION = "postgresql://user:hunter2@db/smokeping exploded"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("CONFIG_API_TOKEN", raising=False)
    api_module.app.config["TESTING"] = True
    with api_module.app.test_client() as client:
        yield client


def _boom(*args, **kwargs):
    raise RuntimeError(SECRET_IN_EXCEPTION)


@pytest.mark.parametrize("method,path,attr", [
    ("GET", "/status", "get_status"),
    ("GET", "/config/targets", "get_config"),
    ("POST", "/generate", "generate_smokeping_config"),
])
def test_exception_text_never_reaches_the_body(client, monkeypatch, method, path, attr):
    monkeypatch.setattr(api_module.api, attr, _boom)
    response = client.open(path, method=method, json={} if method == "POST" else None)
    assert response.status_code == 500
    body = response.get_json()
    assert SECRET_IN_EXCEPTION not in response.get_data(as_text=True)
    assert "hunter2" not in response.get_data(as_text=True)
    assert body["error"] and body["error_id"]
    assert len(body["error_id"]) == 8


def test_error_id_is_in_the_log(client, monkeypatch, caplog):
    monkeypatch.setattr(api_module.api, "get_status", _boom)
    with caplog.at_level("ERROR"):
        body = client.get("/status").get_json()
    assert body["error_id"] in caplog.text
    assert SECRET_IN_EXCEPTION in caplog.text  # the detail went to the log


def test_validation_reasons_come_back_verbatim(client, monkeypatch):
    """Static strings built by the validators are not exception text and
    the operator needs them."""
    monkeypatch.setattr(api_module.api, "update_config", _boom)  # must not be reached
    response = client.put("/config/targets", json={"active_targets": {
        "dns": [{"name": "x"}, "not-a-dict"],
    }})
    assert response.status_code == 400
    body = response.get_json()
    assert body["error"] == "Invalid configuration"
    assert "Invalid target format in dns" in body["problems"]
    assert "Target missing required fields (name, host) in dns" in body["problems"]


def test_unknown_config_type_is_named_without_echoing_it(client):
    # A single segment: Werkzeug would normalise "../" away before routing,
    # and a 404 would skip the branch under test.
    response = client.put("/config/not-a-real-type", json={"a": 1})
    assert response.status_code == 400
    body = response.get_json()
    assert body["error"] == "Unknown configuration type"
    assert "not-a-real-type" not in response.get_data(as_text=True)
    assert body["known"] == ["probes", "sources", "targets"]


def test_bad_json_body_is_a_static_message(client):
    response = client.put("/config/targets", data="not json",
                          content_type="text/plain")
    assert response.status_code == 400
    assert response.get_json()["error"].startswith("Request body must be")


def test_status_dicts_carry_no_exception_text(monkeypatch):
    """/status serialises get_status() as-is, so its inner error fields
    must be static too. Patches the checkers get_status() actually calls."""
    monkeypatch.setattr(api_module.api, "_check_database_status", _boom)
    status = api_module.api.get_status()
    assert status["status"] == "error"
    assert "hunter2" not in str(status)

    monkeypatch.setattr(api_module.api, "_check_database_status",
                        lambda: {"available": True})
    monkeypatch.setattr(api_module.api, "_check_smokeping_status", _boom)
    status = api_module.api.get_status()
    assert "hunter2" not in str(status)


def test_database_status_never_carries_the_dsn(monkeypatch):
    """The database check's exception is where a DSN with its password shows
    up. Patch the session factory it uses."""
    monkeypatch.setattr(api_module, "get_db_session", _boom, raising=True)
    result = api_module.api._check_database_status()
    assert result["available"] is False
    assert "hunter2" not in str(result)
