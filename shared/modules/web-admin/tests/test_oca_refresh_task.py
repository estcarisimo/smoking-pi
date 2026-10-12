"""The nightly OCA refresh: a long enough timeout, and an unapplied refresh
is a warning, not "completed successfully".

config-manager now reloads SmokePing after the fetch (which may take 60 s),
and says whether SmokePing confirmed it. _make_request / the gateway are
replaced: no network.
"""

import logging

from app import tasks
from app.services import config_api
from app.services.config_api import ConfigManagerClient


class Answer:
    status_code = 200
    text = "{}"

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def test_the_refresh_request_gets_room_for_the_fetch_and_the_reload(monkeypatch):
    client = ConfigManagerClient("http://config-manager.invalid:5000")
    calls = []
    monkeypatch.setattr(client, "_make_request",
                        lambda *a, **k: calls.append((a, k)) or Answer({"success": True}))
    client.refresh_oca()
    assert calls[0][0] == ("POST", "/oca/refresh")
    assert calls[0][1]["timeout"] >= 90


def _gateway(monkeypatch, result):
    class Gateway:
        def refresh_oca_data(self):
            return result
    monkeypatch.setattr(config_api, "ConfigAPIGateway", Gateway)


def test_an_unconfirmed_reload_is_a_warning(monkeypatch, caplog):
    _gateway(monkeypatch, {"success": True, "reloaded": False,
                           "message": "OCA data refreshed; restart SmokePing"})
    with caplog.at_level(logging.INFO, logger=tasks.logger.name):
        tasks.refresh_ocas_task()
    assert any(r.levelno == logging.WARNING and "not applied" in r.message
               for r in caplog.records)
    assert "completed successfully" not in caplog.text


def test_a_confirmed_reload_is_success(monkeypatch, caplog):
    _gateway(monkeypatch, {"success": True, "reloaded": True, "message": "ok"})
    with caplog.at_level(logging.INFO, logger=tasks.logger.name):
        tasks.refresh_ocas_task()
    assert "completed successfully" in caplog.text
