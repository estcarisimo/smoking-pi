"""What a tool's error result may say: a message chosen in code and an id.

A tool result goes to the model and from there into a chat, so it gets the
same discipline as an HTTP error body. REINTRODUCTION TESTS: put ``{exc}``
back into a result and the case for that path fails.
"""

import httpx
import pytest

import backends
import server
from test_tools import FakeConfigAPI


class _InfluxLikeError(Exception):
    """Shaped like influxdb_client's ApiException: the str() carries the
    response headers and body, and the Flux query is in the message."""

    def __str__(self):
        return ("(401) Reason: Unauthorized HTTP response headers: "
                "HTTPHeaderDict({'Authorization': 'Token hunter2'}) "
                'query: from(bucket:"smokeping") |> range(start:-24h)')


@pytest.mark.parametrize("tool,kwargs", [
    (server.get_latency_stats, {}),
    (server.get_loss_events, {}),
    (server.get_microcut_stats, {}),
    (server.get_wifi_stats, {}),
])
def test_influx_exception_text_never_reaches_the_result(monkeypatch, tool, kwargs):
    def boom(flux):
        raise _InfluxLikeError()

    monkeypatch.setattr(server, "query_influx", boom)
    monkeypatch.setattr(backends, "query_influx", boom)
    result = tool(**kwargs)
    text = str(result)
    assert "hunter2" not in text
    assert "HTTPHeaderDict" not in text
    assert "from(bucket" not in text
    assert result["error"].startswith("InfluxDB query failed")
    assert len(result["error_id"]) == 8


def test_influx_failure_detail_is_in_the_log(monkeypatch, caplog):
    def boom(flux):
        raise _InfluxLikeError()

    monkeypatch.setattr(server, "query_influx", boom)
    with caplog.at_level("ERROR"):
        result = server.get_latency_stats()
    assert result["error_id"] in caplog.text
    assert "hunter2" in caplog.text


def test_config_api_unreachable_message_has_no_url(monkeypatch, caplog):
    """The base URL may carry credentials in a deployment that puts the API
    behind a proxy with userinfo; and even without them it is an internal
    address that has no business in a chat."""
    api = backends.ConfigAPI(base_url="http://user:hunter2@config-manager:5000",
                             token="t0k3n")

    def boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(api.client, "request", boom)
    with caplog.at_level("WARNING"):
        with pytest.raises(backends.ConfigAPIError) as info:
            api.request("GET", "/targets")
    message = str(info.value)
    assert "hunter2" not in message and "config-manager:5000" not in message
    assert "unreachable" in message and "ConnectError" in message
    assert "config-manager:5000" in caplog.text  # the operator still gets it


def test_config_api_http_error_carries_status_and_static_error_only(monkeypatch):
    api = backends.ConfigAPI(base_url="http://config-manager:5000", token="t")

    class Resp:
        status_code = 500
        text = 'Traceback (most recent call last): postgresql://x:hunter2@db'

        def json(self):
            return {"error": "Status check failed", "error_id": "abcd1234"}

    monkeypatch.setattr(api.client, "request", lambda *a, **k: Resp())
    with pytest.raises(backends.ConfigAPIError) as info:
        api.request("GET", "/status")
    message = str(info.value)
    assert message == "GET /status failed with HTTP 500: Status check failed"
    assert "hunter2" not in message


def test_config_api_non_json_error_body_is_not_echoed(monkeypatch):
    api = backends.ConfigAPI(base_url="http://config-manager:5000", token="t")

    class Resp:
        status_code = 502
        text = "<html>nginx upstream at 10.0.0.7 timed out; token=hunter2</html>"

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(api.client, "request", lambda *a, **k: Resp())
    with pytest.raises(backends.ConfigAPIError) as info:
        api.request("POST", "/generate")
    assert str(info.value) == "POST /generate failed with HTTP 502"


def test_mutes_file_write_failure_is_static(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise OSError("[Errno 13] Permission denied: '/var/lib/alerter-mutes/hunter2'")

    monkeypatch.setenv("ALERT_MUTES_FILE", str(tmp_path / "mutes.json"))
    monkeypatch.setattr(backends, "get_config_api", lambda: FakeConfigAPI())
    monkeypatch.setattr(server.mutes, "save", boom)
    result = server.mute_alerts(target="google_dns", hours=1, reason="test")
    assert "hunter2" not in str(result)
    assert result["error"].startswith("Could not write the mutes file")
    assert len(result["error_id"]) == 8


def test_a_200_with_a_non_json_body_is_refused(monkeypatch):
    """A captive portal or a proxy's login page answers 200 with HTML. That
    must become a static error, not {"raw": "<html>...token..."} handed to
    the model by system_status/apply_config."""
    api = backends.ConfigAPI(base_url="http://config-manager:5000", token="t")

    class Resp:
        status_code = 200
        text = "<html>Sign in — session token hunter2</html>"

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(api.client, "request", lambda *a, **k: Resp())
    with pytest.raises(backends.ConfigAPIError) as info:
        api.request("GET", "/status")
    assert "hunter2" not in str(info.value)
    assert "non-JSON" in str(info.value)


def test_an_error_field_from_a_proxy_is_not_relayed(monkeypatch):
    """Only config-manager's own static messages ride into the tool result;
    an arbitrary {"error": ...} from whatever answered is left in the log."""
    api = backends.ConfigAPI(base_url="http://config-manager:5000", token="t")

    class Resp:
        status_code = 502
        text = '{"error": "upstream auth failed for token hunter2"}'

        def json(self):
            return {"error": "upstream auth failed for token hunter2"}

    monkeypatch.setattr(api.client, "request", lambda *a, **k: Resp())
    with pytest.raises(backends.ConfigAPIError) as info:
        api.request("GET", "/targets")
    assert str(info.value) == "GET /targets failed with HTTP 502"


def test_get_chart_cpe_lookup_failure_is_static(monkeypatch):
    """The CPE-by-IP branch of get_chart queries Influx outside the
    measurement tools' try; it must not let an ApiException escape."""
    monkeypatch.setattr(backends, "get_config_api", lambda: FakeConfigAPI())

    def boom(flux):
        raise _InfluxLikeError()

    monkeypatch.setattr(server, "query_influx", boom)
    result = server.get_chart("136.25.220.1", hours=6)
    assert isinstance(result, dict)
    assert "hunter2" not in str(result) and "HTTPHeaderDict" not in str(result)
    assert result["error"].startswith("InfluxDB query failed")
