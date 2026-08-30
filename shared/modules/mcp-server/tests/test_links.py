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
        "web_admin_targets",
        "grafana_overview_tunnel",
        "grafana_cpe_microcuts_tunnel",
        "web_admin_targets_tunnel",
    }
    assert result["web_admin_targets"] == "http://192.168.86.27:8080/targets/"
    assert result["web_admin_targets_tunnel"] == "https://smokingpi.example.com/targets/"


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
    """An unconfigured base URL is still the reason when both are wrong."""
    monkeypatch.setenv("TSDB_TYPE", "clickhouse")
    assert links.links_configured() is False
    assert links.dashboards_match_backend() is False

