"""Unit tests for deep-link construction.

The behaviour that matters most here is the negative one: with no base URL
configured the tools must emit no links at all, because a guessed
``http://localhost:3000`` fails silently for whoever is reading the answer on
their phone.
"""

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

import links

LINK_VARS = ("PUBLIC_BASE_HOST", "GRAFANA_PUBLIC_URL", "WEB_ADMIN_PUBLIC_URL")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every test from an unconfigured deployment."""
    for var in LINK_VARS:
        monkeypatch.delenv(var, raising=False)


def _query(url):
    return parse_qs(urlparse(url).query)


# ---------------------------------------------------------------------------
# Unconfigured: silence, not guesses
# ---------------------------------------------------------------------------


def test_no_config_means_no_links():
    assert links.links_configured() is False
    assert links.grafana_base() is None
    assert links.web_admin_base() is None
    assert links.grafana_url("smokeping-lat-pct-v28", "target", "UBA") is None
    assert links.web_admin_target_url("UBA") is None
    assert links.target_links("UBA", "latency", "custom") == {}


def test_empty_string_counts_as_unset(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_HOST", "   ")
    assert links.links_configured() is False


# ---------------------------------------------------------------------------
# Base URL resolution
# ---------------------------------------------------------------------------


def test_bare_host_gets_the_default_ports(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_HOST", "192.168.86.27")
    assert links.grafana_base() == "http://192.168.86.27:3000"
    assert links.web_admin_base() == "http://192.168.86.27:8080"


def test_host_with_explicit_port_is_left_alone(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_HOST", "pi.local:9999")
    assert links.grafana_base() == "http://pi.local:9999"


def test_scheme_qualified_base_gets_no_port(monkeypatch):
    """A tunnel/proxy hostname is complete as given; appending :3000 breaks it."""
    monkeypatch.setenv("PUBLIC_BASE_HOST", "https://smokingpi.example.com")
    assert links.grafana_base() == "https://smokingpi.example.com"
    assert links.web_admin_base() == "https://smokingpi.example.com"


def test_explicit_urls_win_over_base_host(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_HOST", "192.168.86.27")
    monkeypatch.setenv("GRAFANA_PUBLIC_URL", "https://grafana.example.com/")
    assert links.grafana_base() == "https://grafana.example.com"
    # web-admin was not overridden, so it still derives from the host.
    assert links.web_admin_base() == "http://192.168.86.27:8080"


def test_one_service_configured_is_enough_for_that_service(monkeypatch):
    monkeypatch.setenv("GRAFANA_PUBLIC_URL", "https://grafana.example.com")
    assert links.links_configured() is True
    result = links.target_links("UBA", "latency", "custom")
    assert "graph" in result
    assert "edit" not in result


# ---------------------------------------------------------------------------
# URL shape
# ---------------------------------------------------------------------------


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_HOST", "192.168.86.27")


def test_grafana_url_carries_variable_and_relative_window(configured):
    url = links.grafana_url("smokeping-lat-pct-v28", "target", "UBA", hours=6)
    assert url.startswith("http://192.168.86.27:3000/d/smokeping-lat-pct-v28?")
    assert _query(url) == {
        "var-target": ["UBA"],
        "from": ["now-6h"],
        "to": ["now"],
    }


def test_cpe_dashboard_uses_its_own_variable_name(configured):
    """The microcut dashboard's variable is `cpe`, not `target`."""
    result = links.target_links("CPE_Gateway", "cpe_latency", hours=24)
    assert "var-cpe=CPE_Gateway" in result["graph"]
    assert "var-target" not in result["graph"]


def test_event_time_becomes_a_bracketing_absolute_window(configured):
    moment = datetime(2026, 8, 7, 3, 30, tzinfo=timezone.utc)
    url = links.grafana_url("cpe-microcut-v1", "cpe", "CPE", at=moment)
    params = _query(url)
    centre = int(moment.timestamp() * 1000)
    assert int(params["from"][0]) == centre - 15 * 60 * 1000
    assert int(params["to"][0]) == centre + 15 * 60 * 1000


def test_iso_string_times_work_too(configured):
    """InfluxDB rows reach the tools as datetimes, but _iso() stringifies them."""
    url = links.grafana_url("cpe-microcut-v1", "cpe", "CPE", at="2026-08-07T03:30:00Z")
    params = _query(url)
    assert int(params["to"][0]) - int(params["from"][0]) == 30 * 60 * 1000


def test_unparseable_time_falls_back_to_the_relative_window(configured):
    url = links.grafana_url("cpe-microcut-v1", "cpe", "CPE", hours=12, at="not a time")
    assert _query(url)["from"] == ["now-12h"]


def test_target_names_are_url_encoded(configured):
    url = links.grafana_url("individual-pings-v1", "target", "a b&c=d")
    assert "a b&c=d" not in url
    assert _query(url)["var-target"] == ["a b&c=d"]


def test_web_admin_link_prefilters_the_target_list(configured):
    assert (
        links.web_admin_target_url("UBA") == "http://192.168.86.27:8080/targets/?q=UBA"
    )
    assert links.web_admin_target_url() == "http://192.168.86.27:8080/targets/"


# ---------------------------------------------------------------------------
# Which dashboards a target gets
# ---------------------------------------------------------------------------


def test_ping_target_gets_graph_detail_peers_and_edit(configured):
    result = links.target_links("Amazon", "latency", "top_sites", hours=24)
    assert set(result) == {"graph", "per_ping_detail", "compare_with_peers", "edit"}
    assert "/d/smokeping-lat-pct-v28" in result["graph"]
    assert "/d/individual-pings-v1" in result["per_ping_detail"]
    assert "/d/top_sites-side-by-side-v1" in result["compare_with_peers"]


def test_dns_target_points_at_the_dns_dashboards(configured):
    result = links.target_links("Google_DNS", "dns_latency", "dns_resolvers")
    assert "/d/smokeping-dns-resolvers-v4" in result["graph"]
    assert "/d/dns-resolvers-v1" in result["per_ping_detail"]


def test_unknown_category_just_omits_the_comparison(configured):
    result = links.target_links("Whatever", "latency", "not_a_category")
    assert "compare_with_peers" not in result
    assert "graph" in result


def test_cpe_has_no_per_ping_or_peer_view(configured):
    result = links.target_links("CPE", "cpe_latency")
    assert set(result) == {"graph", "edit"}


def test_missing_target_name_yields_nothing(configured):
    assert links.target_links(None, "latency") == {}


@pytest.mark.parametrize(
    "probe,expected",
    [
        ("FPing", "latency"),
        ("FPing6", "latency"),
        ("DNS", "dns_latency"),
        (None, "latency"),
        ("SomethingNew", "latency"),
    ],
)
def test_probe_maps_to_measurement(probe, expected):
    assert links.measurement_for_probe(probe) == expected


def test_every_dashboard_uid_referenced_here_is_provisioned():
    """Guard against a dashboard being renamed out from under these links."""
    import json
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3]
    dashboards = root / "modules/grafana/provisioning/dashboards"
    if not dashboards.is_dir():  # pragma: no cover - layout changed
        pytest.skip(f"dashboard directory not found at {dashboards}")

    provisioned = {
        json.loads(path.read_text()).get("uid")
        for path in dashboards.rglob("*.json")
    }
    referenced = (
        {uid for uid, _ in links.DASHBOARD_BY_MEASUREMENT.values()}
        | {uid for uid, _ in links.DETAIL_BY_MEASUREMENT.values()}
        | set(links.COMPARE_BY_DB_CATEGORY.values())
    )
    assert referenced <= provisioned, (
        f"links.py points at dashboards that are not provisioned: "
        f"{sorted(referenced - provisioned)}"
    )
