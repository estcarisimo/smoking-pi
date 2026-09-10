"""Tests for ``get_chart``: the on-request PNG.

The renderer itself is tested next to it (alerter/tests/test_charts.py);
here the questions are the tool's: does it resolve the target the way the
rest of the server does, refuse what it should, return an image block, and
report delivery honestly.
"""

from __future__ import annotations

import pytest

import backends
import server
from common import charts, openclaw

from test_tools import FakeConfigAPI, TARGETS  # noqa: F401 - fixtures below

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
OPENCLAW_ENV = ("OPENCLAW_URL", "OPENCLAW_GATEWAY_TOKEN", "OPENCLAW_HOOK_TOKEN",
                "OPENCLAW_TO", "OPENCLAW_CHANNEL", "OPENCLAW_HOOK_PATH")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in OPENCLAW_ENV + ("PUBLIC_BASE_HOST", "TUNNEL_BASE_HOST"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def api(monkeypatch):
    fake = FakeConfigAPI()
    monkeypatch.setattr(backends, "get_config_api", lambda: fake)
    return fake


@pytest.fixture()
def renderer(monkeypatch):
    """Capture what the tool asks the renderer for, and hand back a PNG."""
    seen = {}

    def _render(target, measurement="latency", hours=24, peers=None):
        seen.update(target=target, measurement=measurement, hours=hours,
                    peers=list(peers or []))
        return PNG

    monkeypatch.setattr(charts, "render_target_chart", _render)
    return seen


@pytest.fixture()
def no_cpe(monkeypatch):
    monkeypatch.setattr(server, "query_influx", lambda flux: [])


def _blocks(result):
    assert isinstance(result, list) and len(result) == 2
    summary, image = result
    assert isinstance(summary, dict)
    assert isinstance(image, server.Image)
    return summary, image


# --- resolution ------------------------------------------------------------


def test_returns_a_summary_and_an_image_block(api, renderer):
    summary, image = _blocks(server.get_chart("google_dns"))
    assert image.data == PNG
    assert image._mime_type == "image/png"
    assert summary["target"] == "google_dns"
    assert summary["measurement"] == "latency"
    assert summary["hours"] == 24
    assert summary["png_bytes"] == len(PNG)
    assert "delivered" not in summary  # not asked to deliver


def test_measurement_follows_the_probe(api, renderer, monkeypatch):
    monkeypatch.setattr(server, "_fetch_targets", lambda api: [
        {**TARGETS[0], "name": "resolver", "probe": "DNS"},
    ])
    summary, _ = _blocks(server.get_chart("resolver"))
    assert summary["measurement"] == "dns_latency"
    assert renderer["measurement"] == "dns_latency"


def test_unknown_target_lists_the_alternatives(api, renderer, no_cpe):
    result = server.get_chart("nope")
    assert "error" in result
    assert result["available_targets"] == ["cloudflare_dns", "google_dns"]


def test_the_cpe_gateway_is_found_by_ip(api, renderer, monkeypatch):
    """The gateway is discovered, not configured: it is not in the DB, and
    its name is an IP the section-name rule would reject."""
    asked = []

    def _query(flux):
        asked.append(flux)
        return [{"_value": 6.7}]

    monkeypatch.setattr(server, "query_influx", _query)
    summary, _ = _blocks(server.get_chart("136.25.220.1", hours=6))
    assert summary["measurement"] == "cpe_latency"
    assert renderer["measurement"] == "cpe_latency"
    assert '"cpe_latency"' in asked[0] and '"136.25.220.1"' in asked[0]


def test_a_name_that_could_break_a_flux_literal_is_refused(api, renderer):
    result = server.get_chart('x" or true or "')
    assert "error" in result


def test_hours_are_bounded(api, renderer):
    assert "error" in server.get_chart("google_dns", hours=0)
    assert "error" in server.get_chart("google_dns", hours=24 * 31)
    assert "error" in server.get_chart("google_dns", hours="soon")


def test_no_data_is_an_error_not_an_empty_image(api, monkeypatch):
    monkeypatch.setattr(charts, "render_target_chart", lambda *a, **k: None)
    result = server.get_chart("google_dns")
    assert "error" in result and "no data" in result["error"]


def test_a_dead_config_api_is_reported(monkeypatch, renderer):
    class Dead:
        def request(self, *a, **k):
            raise backends.ConfigAPIError("config-manager unreachable")

    monkeypatch.setattr(backends, "get_config_api", lambda: Dead())
    assert "error" in server.get_chart("google_dns")


# --- peers -----------------------------------------------------------------


def test_peers_are_off_unless_asked(api, renderer):
    server.get_chart("google_dns")
    assert renderer["peers"] == []


def test_peers_share_category_and_measurement_and_are_active(api, renderer,
                                                              monkeypatch):
    monkeypatch.setattr(server, "_fetch_targets", lambda api: [
        {"name": "subject", "category": "dns", "probe": "FPing", "is_active": True},
        {"name": "same", "category": "dns", "probe": "FPing", "is_active": True},
        {"name": "dns_probe", "category": "dns", "probe": "DNS", "is_active": True},
        {"name": "paused", "category": "dns", "probe": "FPing", "is_active": False},
        {"name": "other_cat", "category": "web", "probe": "FPing", "is_active": True},
    ])
    summary, _ = _blocks(server.get_chart("subject", with_peers=True))
    assert renderer["peers"] == ["same"]
    assert summary["peers_drawn"] == ["same"]


# --- links -----------------------------------------------------------------


def test_links_ride_along_when_configured(api, renderer, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_HOST", "192.168.86.27")
    summary, _ = _blocks(server.get_chart("google_dns", hours=6))
    assert "graph" in summary["links"]
    assert "from=now-6h" in summary["links"]["graph"]


def test_no_links_when_unconfigured(api, renderer):
    summary, _ = _blocks(server.get_chart("google_dns"))
    assert "links" not in summary


# --- delivery --------------------------------------------------------------


def test_delivery_reports_a_missing_gateway_config(api, renderer):
    """Not configured must be a visible reason, not a silent skip."""
    summary, _ = _blocks(server.get_chart("google_dns", deliver=True))
    assert summary["delivered"] is False
    assert "OPENCLAW_GATEWAY_TOKEN" in summary["delivery_error"]


def test_delivery_posts_the_png_as_a_document(api, renderer, monkeypatch):
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "tok")
    monkeypatch.setenv("OPENCLAW_TO", "12345")
    sent = {}

    class Resp:
        status_code = 200
        text = "{}"

        def json(self):
            return {"ok": True}

    def _post(url, json=None, headers=None, timeout=None):
        sent.update(url=url, payload=json, headers=headers)
        return Resp()

    monkeypatch.setattr(openclaw.httpx, "post", _post)
    summary, _ = _blocks(server.get_chart("google_dns", hours=6, deliver=True))
    assert summary["delivered"] is True
    assert "delivery_error" not in summary
    assert sent["url"].endswith("/tools/invoke")
    assert sent["headers"]["Authorization"] == "Bearer tok"
    args = sent["payload"]["args"]
    assert sent["payload"]["name"] == "message"
    assert args["to"] == "12345"
    assert args["mimeType"] == "image/png"
    assert args["forceDocument"] is True
    assert args["filename"].startswith("smokeping-google_dns-6h-")
    assert "google_dns" in args["caption"] and "6h" in args["caption"]
    assert args["silent"] is True  # a chart someone asked for need not buzz


def test_delivery_failure_still_returns_the_image(api, renderer, monkeypatch):
    """The gateway refusing is not a reason to lose the chart: the caller
    still gets the picture, plus the reason it is not in the chat."""
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "tok")
    monkeypatch.setenv("OPENCLAW_TO", "12345")

    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"ok": False, "error": {"message": "Tool not available"}}

    monkeypatch.setattr(openclaw.httpx, "post", lambda *a, **k: Resp())
    summary, image = _blocks(server.get_chart("google_dns", deliver=True))
    assert image.data == PNG
    assert summary["delivered"] is False
    assert summary["delivery_error"] == "Tool not available"


def test_a_404_names_both_causes(api, renderer, monkeypatch):
    """Policy-blocked and route-missing share a status; say so."""
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "tok")
    monkeypatch.setenv("OPENCLAW_TO", "12345")

    class Resp:
        status_code = 404
        text = "Tool not available: message"

        def json(self):
            return {}

    monkeypatch.setattr(openclaw.httpx, "post", lambda *a, **k: Resp())
    summary, _ = _blocks(server.get_chart("google_dns", deliver=True))
    assert "blocked by policy" in summary["delivery_error"]
    assert "not a /tools/invoke endpoint" in summary["delivery_error"]


def test_an_unreachable_gateway_is_a_reason_not_an_exception(api, renderer,
                                                              monkeypatch):
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "tok")
    monkeypatch.setenv("OPENCLAW_TO", "12345")

    def _boom(*a, **k):
        raise openclaw.httpx.ConnectError("refused")

    monkeypatch.setattr(openclaw.httpx, "post", _boom)
    summary, _ = _blocks(server.get_chart("google_dns", deliver=True))
    assert summary["delivered"] is False
    assert "unreachable" in summary["delivery_error"]


# --- filename --------------------------------------------------------------


def test_chart_filename_is_safe_and_bounded():
    name = charts.chart_filename("a b/c" + "x" * 100, 24)
    assert name.startswith("smokeping-a-b-c")
    assert name.endswith(".png") and "-24h-" in name
    assert len(name) < 90
    assert charts.chart_filename("...", 6).startswith("smokeping-chart-6h-")
