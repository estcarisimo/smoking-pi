"""Unit tests for deep-link construction.

The behavior that matters most here is the negative one: with no base URL
configured the tools must emit no links at all, because a guessed
``http://localhost:3000`` fails silently for whoever is reading the answer on
their phone.
"""

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import logging

import pytest

import common.links
import links

LINK_VARS = (
    "PUBLIC_BASE_HOST",
    "GRAFANA_PUBLIC_URL",
    "WEB_ADMIN_PUBLIC_URL",
    "TUNNEL_BASE_HOST",
    "GRAFANA_TUNNEL_URL",
    "WEB_ADMIN_TUNNEL_URL",
)

# TSDB_TYPE is scrubbed too: it now gates link emission, so leaving the host's
# value in place would make these tests pass or fail depending on the machine
# they run on.
SCRUBBED = (*LINK_VARS, "TSDB_TYPE")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every test from an unconfigured deployment."""
    for var in SCRUBBED:
        monkeypatch.delenv(var, raising=False)
    # Refusals are logged once per process; each test starts unlogged.
    monkeypatch.setattr(common.links, "_REPORTED_BASES", set())


def _query(url):
    return parse_qs(urlparse(url).query)


# ---------------------------------------------------------------------------
# Unconfigured: silence, not guesses
# ---------------------------------------------------------------------------


def test_no_config_means_no_links():
    assert links.links_configured() is False
    assert links.grafana_base() is None
    assert links.web_admin_base() is None
    assert links.grafana_tunnel_base() is None
    assert links.has_tunnel_links() is False
    assert links.grafana_url("smokeping-lat-pct-v28", "target", "UBA") is None
    assert links.web_admin_target_url("UBA") is None
    assert links.target_links("UBA", "latency", "custom") == {}
    assert links.entry_point_links() == {}


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


def test_host_with_a_path_gets_the_port_before_the_path(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_HOST", "pi.lan/smokingpi")
    assert links.grafana_base() == "http://pi.lan:3000/smokingpi"


# An IPv6 literal carries colons, so "does it already have a port?" cannot be
# "is there a colon": that test gave http://2001:db8::5 -- no port, no
# brackets, a URL no browser opens.


@pytest.mark.parametrize(
    ("value", "grafana", "web_admin"),
    [
        ("2001:db8::5", "http://[2001:db8::5]:3000", "http://[2001:db8::5]:8080"),
        ("[2001:db8::5]", "http://[2001:db8::5]:3000", "http://[2001:db8::5]:8080"),
        ("[2001:db8::5]:9999", "http://[2001:db8::5]:9999", "http://[2001:db8::5]:9999"),
        ("fd12:3456::27/", "http://[fd12:3456::27]:3000", "http://[fd12:3456::27]:8080"),
        ("::ffff:192.0.2.1", "http://[::ffff:192.0.2.1]:3000", "http://[::ffff:192.0.2.1]:8080"),
    ],
)
def test_ipv6_literal_is_bracketed_and_gets_the_port(
    monkeypatch, value, grafana, web_admin
):
    monkeypatch.setenv("PUBLIC_BASE_HOST", value)
    assert links.grafana_base() == grafana
    assert links.web_admin_base() == web_admin


def test_ipv6_base_builds_a_parseable_link(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_HOST", "2001:db8::5")
    url = links.grafana_url("smokeping-lat-pct-v28", "target", "UBA")
    parsed = urlparse(url)
    assert parsed.hostname == "2001:db8::5"
    assert parsed.port == 3000


def test_ipv6_tunnel_host_is_bracketed_too(monkeypatch):
    monkeypatch.setenv("TUNNEL_BASE_HOST", "2001:db8::7")
    assert links.grafana_tunnel_base() == "http://[2001:db8::7]:3000"


def test_scheme_qualified_ipv6_base_is_left_alone(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_HOST", "https://[2001:db8::5]")
    assert links.grafana_base() == "https://[2001:db8::5]"


@pytest.mark.parametrize(
    "value", ["fe80::1", "[fe80::1]", "fe80::1%wlan0", "[fe80::1%25wlan0]:3000"]
)
def test_link_local_ipv6_makes_no_links_and_says_why(monkeypatch, caplog, value):
    """fe80:: needs a zone id to route, and browsers reject zone ids."""
    monkeypatch.setenv("PUBLIC_BASE_HOST", value)
    with caplog.at_level(logging.WARNING, logger="common.links"):
        assert links.grafana_base() is None
        assert links.links_configured() is False
    assert "link-local" in caplog.text


def test_link_local_primary_falls_back_to_the_tunnel(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_HOST", "fe80::1")
    monkeypatch.setenv("TUNNEL_BASE_HOST", "https://smokingpi.example.com")
    assert links.grafana_base() == "https://smokingpi.example.com"


@pytest.mark.parametrize(
    "value",
    ["[2001:db8::5", "[2001:db8::5]x", "[2001:db8::5]:", "[pi.lan]", "2001:zz::1", "::", "[]"],
)
def test_malformed_ipv6_makes_no_links(monkeypatch, caplog, value):
    monkeypatch.setenv("PUBLIC_BASE_HOST", value)
    with caplog.at_level(logging.WARNING, logger="common.links"):
        assert links.grafana_base() is None
    assert "Ignoring base host" in caplog.text


def test_a_refused_base_is_logged_once(monkeypatch, caplog):
    monkeypatch.setenv("PUBLIC_BASE_HOST", "fe80::1")
    with caplog.at_level(logging.WARNING, logger="common.links"):
        for _ in range(3):
            links.grafana_base()
            links.web_admin_base()
    assert caplog.text.count("Ignoring base host") == 1


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
# The tunnel tier: the same panel, reachable from outside the house
# ---------------------------------------------------------------------------


@pytest.fixture
def both_tiers(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_HOST", "192.168.86.27")
    monkeypatch.setenv("TUNNEL_BASE_HOST", "https://smokingpi.example.com")


def test_tunnel_alone_becomes_the_primary_link(monkeypatch):
    """A tunnel-only deployment gets links, not silence.

    Falling back matters: without it, a Pi reachable *only* through a tunnel
    would report itself unconfigured and answer with numbers and no links.
    """
    monkeypatch.setenv("TUNNEL_BASE_HOST", "https://smokingpi.example.com")
    assert links.links_configured() is True
    assert links.grafana_base() == "https://smokingpi.example.com"
    # No twin: the tunnel IS the primary, and the same URL under two labels
    # reads as two places to look.
    assert links.has_tunnel_links() is False
    result = links.target_links("UBA", "latency", "custom")
    assert result["graph"].startswith("https://smokingpi.example.com/d/")
    assert not [key for key in result if key.endswith("_tunnel")]


def test_both_tiers_emit_twinned_links(both_tiers):
    result = links.target_links("Amazon", "latency", "top_sites", hours=24)
    assert set(result) == {
        "graph",
        "per_ping_detail",
        "compare_with_peers",
        "edit",
        "graph_tunnel",
        "per_ping_detail_tunnel",
        "compare_with_peers_tunnel",
        "edit_tunnel",
    }
    # Same panel, same window -- only the host differs.
    assert result["graph"].startswith("http://192.168.86.27:3000/d/")
    assert result["graph_tunnel"].startswith("https://smokingpi.example.com/d/")
    assert _query(result["graph"]) == _query(result["graph_tunnel"])


def test_explicit_tunnel_urls_win_over_the_tunnel_host(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_HOST", "192.168.86.27")
    monkeypatch.setenv("TUNNEL_BASE_HOST", "https://smokingpi.example.com")
    monkeypatch.setenv("GRAFANA_TUNNEL_URL", "https://grafana.example.com")
    assert links.grafana_tunnel_base() == "https://grafana.example.com"
    assert links.web_admin_tunnel_base() == "https://smokingpi.example.com"


def test_a_tunnel_equal_to_the_primary_is_not_twinned(monkeypatch):
    """Configuring the same address twice must not double every link."""
    monkeypatch.setenv("PUBLIC_BASE_HOST", "https://smokingpi.example.com")
    monkeypatch.setenv("TUNNEL_BASE_HOST", "https://smokingpi.example.com/")
    assert links.has_tunnel_links() is False
    result = links.target_links("UBA", "latency", "custom")
    assert not [key for key in result if key.endswith("_tunnel")]


def test_entry_points_carry_both_tiers(both_tiers):
    result = links.entry_point_links(hours=24)
    assert set(result) == {
        "grafana_overview",
        "grafana_cpe_microcuts",
        "grafana_wifi_link",
        "web_admin_targets",
        "grafana_overview_tunnel",
        "grafana_cpe_microcuts_tunnel",
        "grafana_wifi_link_tunnel",
        "web_admin_targets_tunnel",
    }
    assert "/d/wifi-link-v1" in result["grafana_wifi_link"]
    assert result["web_admin_targets"] == "http://192.168.86.27:8080/targets/"
    assert result["web_admin_targets_tunnel"] == "https://smokingpi.example.com/targets/"


def test_wifi_links_are_the_graph_pair_and_nothing_else(both_tiers):
    """No edit link into a target search for wlan0, no peers: just the
    dashboard, keyed by interface, in both tiers."""
    result = links.wifi_links("wlan0", hours=6)
    assert set(result) == {"graph", "graph_tunnel"}
    assert "/d/wifi-link-v1" in result["graph"]
    assert "var-interface=wlan0" in result["graph"]
    assert "from=now-6h" in result["graph"]
    assert result["graph_tunnel"].startswith("https://smokingpi.example.com/")


def test_wifi_links_zoom_to_a_moment(both_tiers):
    from datetime import datetime, timezone

    moment = datetime(2026, 9, 19, 1, 30, tzinfo=timezone.utc)
    result = links.wifi_links("wlan0", at=moment)
    center = int(moment.timestamp() * 1000)
    assert f"from={center - 15 * 60 * 1000}" in result["graph"]
    assert f"to={center + 15 * 60 * 1000}" in result["graph"]


def test_wifi_links_empty_without_interface_or_config(both_tiers, monkeypatch):
    assert links.wifi_links(None) == {}
    for var in ("PUBLIC_BASE_HOST", "GRAFANA_PUBLIC_URL", "WEB_ADMIN_PUBLIC_URL",
                "TUNNEL_BASE_HOST", "GRAFANA_TUNNEL_URL", "WEB_ADMIN_TUNNEL_URL"):
        monkeypatch.delenv(var, raising=False)
    assert links.wifi_links("wlan0") == {}


def test_tunnel_links_stay_off_under_clickhouse(both_tiers, monkeypatch):
    """The backend gate covers both tiers -- a twinned 404 is still a 404."""
    monkeypatch.setenv("TSDB_TYPE", "clickhouse")
    assert links.links_configured() is False
    assert links.target_links("UBA", "latency", "custom") == {}
    assert links.entry_point_links() == {}


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
    center = int(moment.timestamp() * 1000)
    assert int(params["from"][0]) == center - 15 * 60 * 1000
    assert int(params["to"][0]) == center + 15 * 60 * 1000


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
        ("CurlHTTP1", "http_latency"),
        ("CurlHTTP2", "http_latency"),
        ("CurlHTTP3", "http_latency"),
        ("TCPPing", "tcp_latency"),
        (None, "latency"),
        ("SomethingNew", "latency"),
    ],
)
def test_probe_maps_to_measurement(probe, expected):
    assert links.measurement_for_probe(probe) == expected


def test_http_target_links_to_its_site_panel(configured):
    """Google_h2's graph is the site panel with all three versions on it."""
    out = links.target_links("Google_h2", measurement="http_latency")
    assert "/d/http-by-version-v1?" in out["graph"]
    assert "var-site=Google" in out["graph"]
    assert "var-site=Google_h2" not in out["graph"]


def test_tcp_target_links_to_the_dashboard_without_a_variable(configured):
    out = links.target_links("Google_tcp443", measurement="tcp_latency")
    assert "/d/http-by-version-v1" in out["graph"]
    assert "var-" not in out["graph"]


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


# ---------------------------------------------------------------------------
# Backend gating
# ---------------------------------------------------------------------------


def test_clickhouse_backend_emits_no_links(monkeypatch, configured):
    """Every pinned uid belongs to the InfluxDB provisioning tree.

    The ClickHouse tree is a parallel set with different uids and no CPE
    dashboard at all, so under TSDB_TYPE=clickhouse each of these links would
    resolve to a Grafana 404 -- while looking perfectly valid in the answer.
    Same doctrine as an unset base URL: no link beats a broken one.
    """
    monkeypatch.setenv("TSDB_TYPE", "clickhouse")
    assert links.links_configured() is False
    assert links.target_links(name="Cloudflare", measurement="latency") == {}


def test_influxdb_and_unset_both_emit_links(monkeypatch, configured):
    for value in ("influxdb", "InfluxDB", ""):
        monkeypatch.setenv("TSDB_TYPE", value)
        assert links.links_configured() is True
    monkeypatch.delenv("TSDB_TYPE", raising=False)
    assert links.links_configured() is True


def test_backend_gate_is_independent_of_base_url(monkeypatch):
    """The backend mismatch is detectable on its own, with no base URL set.

    Note this fixture-less test does NOT configure a base URL, so
    links_configured() would be False regardless of backend — it says nothing
    about which gate closed. The assertion that carries weight is the second:
    dashboards_match_backend() reports the mismatch without needing a base URL
    to be configured first, so the two gates can't mask each other.
    """
    monkeypatch.setenv("TSDB_TYPE", "clickhouse")
    assert links.links_configured() is False
    assert links.dashboards_match_backend() is False

