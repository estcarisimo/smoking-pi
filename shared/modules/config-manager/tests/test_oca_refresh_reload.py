"""POST /oca/refresh: the refreshed Netflix targets are measured, not only saved.

The endpoint ran the fetcher, which writes the targets and the generated
files, and returned. SmokePing reads its Targets only when signaled, so it
kept measuring the previous appliances until something else reloaded it
(14 hours on the staging Pi with v2.30.0-rc.1).
"""

import subprocess

import pytest

import api as api_module


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("CONFIG_API_TOKEN", raising=False)
    api_module.app.config["TESTING"] = True
    with api_module.app.test_client() as c:
        yield c


def _fetcher(monkeypatch, returncode):
    def run(argv, **kw):
        assert argv[-1].endswith("oca_fetcher.py")
        return subprocess.CompletedProcess(argv, returncode, "ok", "boom")
    monkeypatch.setattr(api_module.subprocess, "run", run)


def test_a_successful_refresh_reloads_smokeping(client, monkeypatch):
    _fetcher(monkeypatch, 0)
    reloads = []
    monkeypatch.setattr(api_module.api, "_regenerate_smokeping_config",
                        lambda: reloads.append(True) or True)
    body = client.post("/oca/refresh").get_json()
    assert reloads == [True]
    assert body["success"] is True and body["reloaded"] is True


def test_an_unconfirmed_reload_is_said_not_hidden(client, monkeypatch):
    _fetcher(monkeypatch, 0)
    monkeypatch.setattr(api_module.api, "_regenerate_smokeping_config", lambda: False)
    body = client.post("/oca/refresh").get_json()
    assert body["success"] is True and body["reloaded"] is False
    assert api_module.RELOAD_UNCONFIRMED in body["message"]


def test_a_failed_reload_does_not_turn_a_saved_refresh_into_an_error(client, monkeypatch):
    _fetcher(monkeypatch, 0)

    def boom():
        raise RuntimeError("docker socket gone")
    monkeypatch.setattr(api_module.api, "_regenerate_smokeping_config", boom)
    r = client.post("/oca/refresh")
    assert r.status_code == 200 and r.get_json()["reloaded"] is False


def test_a_failed_fetch_reloads_nothing(client, monkeypatch):
    _fetcher(monkeypatch, 1)
    monkeypatch.setattr(api_module.api, "_regenerate_smokeping_config",
                        lambda: pytest.fail("reloaded after a failed fetch"))
    r = client.post("/oca/refresh")
    assert r.status_code == 500
