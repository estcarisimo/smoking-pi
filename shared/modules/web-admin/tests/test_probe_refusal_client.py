"""ConfigManagerClient.update_probe: what a refusal carries to the page.

The page must never show config-manager's text, so the client keeps the
reason code and the numbers only. _make_request is replaced: no network.
"""

import pytest
import requests

from app.services.config_api import ConfigManagerClient, ProbeChangeRefused


class Answer:
    def __init__(self, status, text, body=None):
        self.status_code = status
        self.text = text
        self._body = body

    def json(self):
        if self._body is None:
            raise requests.exceptions.JSONDecodeError("Expecting value", self.text, 0)
        return self._body


def _client(monkeypatch, answer):
    client = ConfigManagerClient("http://config-manager.invalid:5000")
    monkeypatch.setattr(client, "_make_request", lambda *a, **k: answer)
    return client


def test_a_refusal_carries_the_reason_and_numbers_not_the_text(monkeypatch):
    body = {"error": "10 pings can take up to 100 s", "error_id": "abcd1234",
            "reason": "cycle_outruns_step", "pings": 10, "worst_seconds": 100.0,
            "step_seconds": 60, "extra": "/etc/passwd"}
    client = _client(monkeypatch, Answer(400, "{...}", body))
    with pytest.raises(ProbeChangeRefused) as refused:
        client.update_probe("CurlHTTP2", {"pings": 10})
    assert refused.value.reason == "cycle_outruns_step"
    assert refused.value.numbers == {"pings": 10, "worst_seconds": 100.0,
                                     "step_seconds": 60}


def test_numbers_that_are_not_numbers_are_dropped(monkeypatch):
    body = {"error": "x", "reason": "cycle_outruns_step",
            "pings": "<script>", "worst_seconds": True, "step_seconds": 60}
    client = _client(monkeypatch, Answer(400, "{...}", body))
    with pytest.raises(ProbeChangeRefused) as refused:
        client.update_probe("FPing", {"pings": 10})
    assert refused.value.numbers == {"step_seconds": 60}


def test_a_404_without_a_reason_is_a_refusal_with_none(monkeypatch):
    client = _client(monkeypatch, Answer(404, "{...}", {"error": "Probe not found"}))
    with pytest.raises(ProbeChangeRefused) as refused:
        client.update_probe("Nope", {"pings": 10})
    assert refused.value.reason == ""


@pytest.mark.parametrize("status", [200, 400, 404, 502])
def test_an_answer_that_is_not_json_is_a_runtime_error(monkeypatch, status):
    # requests' JSONDecodeError is a ValueError; it must not reach the page
    # through the refusal path.
    client = _client(monkeypatch, Answer(status, "<html>Bad Gateway</html>"))
    with pytest.raises(RuntimeError):
        client.update_probe("FPing", {"pings": 10})


def test_a_success_returns_the_body(monkeypatch):
    client = _client(monkeypatch, Answer(200, "{...}", {"changed": False}))
    assert client.update_probe("FPing", {"pings": 10}) == {"changed": False}
