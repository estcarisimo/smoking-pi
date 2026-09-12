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
    response = client.put("/config/../etc/passwd", json={"a": 1})
    assert response.status_code in (400, 404)
    if response.status_code == 400:
        body = response.get_json()
        assert body["error"] == "Unknown configuration type"
        assert "passwd" not in response.get_data(as_text=True)
        assert body["known"] == ["probes", "sources", "targets"]


def test_bad_json_body_is_a_static_message(client):
    response = client.put("/config/targets", data="not json",
                          content_type="text/plain")
    assert response.status_code == 400
    assert response.get_json()["error"].startswith("Request body must be")


def test_status_dicts_carry_no_exception_text(monkeypatch):
    """/status serialises get_status() as-is, so its inner error fields
    must be static too."""
    monkeypatch.setattr(api_module.api, "_check_database_availability", _boom,
                        raising=False)
    status = api_module.api.get_status()
    text = str(status)
    assert "hunter2" not in text
