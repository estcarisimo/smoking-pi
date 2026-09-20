"""Unit tests for the SmokePing MCP server tools.

Everything is mocked at the backends layer -- no network access:
- the config-manager REST API via a FakeConfigAPI dispatcher
- InfluxDB via a fake ``query_influx`` returning canned record dicts
"""

from datetime import datetime, timedelta, timezone

import pytest

import backends
import server


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

TARGETS = [
    {
        "id": 1,
        "name": "google_dns",
        "host": "8.8.8.8",
        "title": "Google DNS",
        "category": "dns",
        "probe": "FPing",
        "is_active": True,
    },
    {
        "id": 2,
        "name": "cloudflare_dns",
        "host": "1.1.1.1",
        "title": "Cloudflare DNS",
        "category": "dns",
        "probe": "FPing",
        "is_active": False,
    },
]

CATEGORIES = [
    {"id": 10, "name": "dns", "display_name": "DNS"},
    {"id": 11, "name": "custom", "display_name": "Custom"},
]

PROBES = [
    {"id": 20, "name": "FPing", "is_default": True},
    {"id": 21, "name": "DNS", "is_default": False},
]


class FakeConfigAPI:
    """Records requests and serves canned config-manager responses."""

    def __init__(self):
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        key = (method, path)
        if key == ("GET", "/targets"):
            return {"targets": TARGETS, "total": len(TARGETS)}
        if key == ("GET", "/categories"):
            return {"categories": CATEGORIES}
        if key == ("GET", "/probes"):
            return {"probes": PROBES}
        if key == ("GET", "/health"):
            return {"status": "healthy", "service": "config-manager"}
        if key == ("GET", "/status"):
            return {
                "status": "healthy",
                "database": {"available": True, "target_count": 2},
                "smokeping": {"running": True},
            }
        if key == ("POST", "/targets"):
            payload = kwargs.get("json", {})
            return {
                "success": True,
                "target": {"id": 99, "is_active": True, **payload},
                "message": "Target created successfully",
            }
        if key == ("DELETE", "/targets/1"):
            return {"success": True, "message": "Target deleted successfully"}
        if key == ("POST", "/targets/1/toggle"):
            return {
                "success": True,
                "target": {**TARGETS[0], "is_active": False},
                "message": "Target deactivated successfully",
            }
        if key == ("POST", "/generate"):
            return {"success": True, "message": "generated"}
        if key == ("POST", "/restart"):
            return {"success": True, "message": "restarted"}
        raise AssertionError(f"unexpected request: {method} {path}")


class ExplodingConfigAPI:
    """Fails the test if any HTTP call is attempted."""

    def request(self, method, path, **kwargs):  # pragma: no cover
        raise AssertionError(f"unexpected API call: {method} {path}")


@pytest.fixture()
def api(monkeypatch):
    fake = FakeConfigAPI()
    monkeypatch.setattr(backends, "get_config_api", lambda: fake)
    return fake


@pytest.fixture()
def no_api(monkeypatch):
    monkeypatch.setattr(backends, "get_config_api", lambda: ExplodingConfigAPI())


# ---------------------------------------------------------------------------
# Flux string sanitization
# ---------------------------------------------------------------------------


def test_flux_str_quotes_safe_values():
    assert backends.flux_str("google_dns") == '"google_dns"'


@pytest.mark.parametrize(
    "bad",
    ['a"b', "a\\b", 'x") |> yield() //', "line\nbreak", "tab\tvalue"],
)
def test_flux_str_rejects_injection(bad):
    with pytest.raises(ValueError):
        backends.flux_str(bad)


def test_latency_stats_rejects_flux_injection(monkeypatch, no_api):
    def boom(flux):  # pragma: no cover
        raise AssertionError("query must not run for malicious target names")

    monkeypatch.setattr(backends, "query_influx", boom)
    monkeypatch.setattr(server, "query_influx", boom)
    result = server.get_latency_stats(target='evil") |> drop() //')
    assert "error" in result


def test_hours_validation():
    assert "error" in server.get_loss_events(hours=0)
    assert "error" in server.get_loss_events(hours="yesterday")
    assert "error" in server.get_latency_stats(hours=-4)
    assert "error" in server.get_microcut_stats(hours=10**9)
    assert "error" in server.get_wifi_stats(hours=0)


def test_wifi_interface_validation():
    assert "error" in server.get_wifi_stats(interface="wlan 0")
    assert "error" in server.get_wifi_stats(interface='x"|>drop()')
    assert "error" in server.get_wifi_stats(interface="a" * 16)
    for ok in ("wlan0", "wlp3s0", "wlan0.1", "wlan-ap", "wl_0"):
        assert "error" not in server.get_wifi_stats(interface=ok)


# ---------------------------------------------------------------------------
# Target name validation & add_target
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    ["1bad", "has space", "dash-name", "dot.name", "", "unicode✓", "_leading"],
)
def test_add_target_rejects_invalid_names(no_api, bad_name):
    result = server.add_target(name=bad_name, host="9.9.9.9")
    assert "error" in result


def test_add_target_resolves_category_and_probe(api):
    result = server.add_target(name="quad9", host="9.9.9.9", category="dns")
    assert result["success"] is True
    post = next(c for c in api.calls if c[:2] == ("POST", "/targets"))
    payload = post[2]["json"]
    assert payload == {
        "name": "quad9",
        "host": "9.9.9.9",
        "title": "quad9",
        "category_id": 10,
        "probe_id": 20,  # default probe (FPing)
    }
    assert "regenerated automatically" in result["note"]


def test_add_target_unknown_category_lists_valid_ones(api):
    result = server.add_target(name="quad9", host="9.9.9.9", category="nope")
    assert "error" in result
    assert result["valid_categories"] == ["custom", "dns"]
    assert not any(c[0] == "POST" for c in api.calls)


def test_add_target_explicit_probe(api):
    result = server.add_target(name="quad9", host="9.9.9.9", probe="DNS")
    assert result["success"] is True
    post = next(c for c in api.calls if c[:2] == ("POST", "/targets"))
    assert post[2]["json"]["probe_id"] == 21


# ---------------------------------------------------------------------------
# Name -> id resolution (remove / toggle)
# ---------------------------------------------------------------------------


def test_remove_target_resolves_name_to_id(api):
    result = server.remove_target("google_dns")
    assert result["success"] is True
    assert ("DELETE", "/targets/1", {}) in api.calls


def test_remove_target_unknown_name(api):
    result = server.remove_target("nonexistent")
    assert "No monitoring target named 'nonexistent'" in result["error"]
    assert result["available_targets"] == ["cloudflare_dns", "google_dns"]
    assert not any(c[0] == "DELETE" for c in api.calls)


def test_toggle_target_resolves_name_to_id(api):
    result = server.toggle_target("google_dns")
    assert result["success"] is True
    assert ("POST", "/targets/1/toggle", {}) in api.calls
    assert result["target"]["is_active"] is False


# ---------------------------------------------------------------------------
# list_targets / apply_config / system_status
# ---------------------------------------------------------------------------


def test_list_targets_shape(api):
    result = server.list_targets()
    assert result["total"] == 2
    assert result["targets"][0] == {
        "id": 1,
        "name": "google_dns",
        "host": "8.8.8.8",
        "title": "Google DNS",
        "category": "dns",
        "probe": "FPing",
        "is_active": True,
    }


def test_apply_config_combines_generate_and_restart(api):
    result = server.apply_config()
    assert result["success"] is True
    assert result["generate"]["message"] == "generated"
    assert result["restart"]["message"] == "restarted"
    assert [c[:2] for c in api.calls] == [
        ("POST", "/generate"),
        ("POST", "/restart"),
    ]


def test_system_status_summary(api):
    result = server.system_status()
    assert "overall status: healthy" in result["summary"]
    assert "smokeping container running: True" in result["summary"]
    assert "wifi" not in result            # wired host: nothing to say


def _wifi_last_rows(**over):
    """What group(columns:["_field"]) |> last() returns for an associated
    link: one row per field, tags riding along on each."""
    tags = {"interface": "wlan0", "ssid": "ExampleNet", "bssid": "00:00:5e:00:53:01",
            "_time": datetime(2026, 9, 19, 2, 0, tzinfo=timezone.utc)}
    fields = {"associated": 1, "uplink": 1, "signal_dbm": -52.0, "tx_bitrate_mbps": 433.3,
              "rx_bitrate_mbps": 433.3, "channel": 36, "band_ghz": 5.0, "width_mhz": 80,
              "connected_seconds": 1648, "rx_bytes": 10, "carrier_down_count": 2}
    fields.update(over)
    return [{**tags, "_field": k, "_value": v} for k, v in fields.items()]


def test_system_status_carries_the_wifi_hop_when_there_is_one(api, monkeypatch):
    def fake(flux):
        if '"uplink"' in flux and 'group(columns: ["interface"])' in flux:
            return [{"interface": "wlan0", "_value": 1}]
        return _wifi_last_rows() if "wifi_link" in flux else []
    _patch_influx(monkeypatch, fake)
    result = server.system_status()
    assert result["wifi"] == {
        "interface": "wlan0", "sampled_at": "2026-09-19T02:00:00+00:00",
        "associated": True, "uplink_is_wifi": True, "ssid": "ExampleNet",
        "bssid": "00:00:5e:00:53:01", "signal_dbm": -52.0, "tx_bitrate_mbps": 433.3,
        "rx_bitrate_mbps": 433.3, "channel": 36, "band_ghz": 5.0, "width_mhz": 80,
        "connected_seconds": 1648,
    }


def test_system_status_survives_a_wifi_lookup_failure(api, monkeypatch, caplog):
    """The stack's health page must not depend on one optional measurement,
    and the failure text must not reach the result."""
    def boom(flux):
        raise RuntimeError("Token hunter2 leaked")
    _patch_influx(monkeypatch, boom)
    result = server.system_status()
    assert "wifi" not in result
    assert "hunter2" not in str(result)
    assert "overall status: healthy" in result["summary"]


def test_system_status_survives_malformed_wifi_rows(api, monkeypatch):
    """Not just a failing query: a row shape the post-processing chokes on
    (a missing _time makes max() compare datetime with int) is swallowed too."""
    def fake(flux):
        if '"uplink"' in flux and 'group(columns: ["interface"])' in flux:
            return [{"interface": "wlan0", "_value": 1}]
        rows = _wifi_last_rows()
        del rows[0]["_time"]
        return rows
    _patch_influx(monkeypatch, fake)
    result = server.system_status()
    assert "wifi" not in result
    assert "overall status: healthy" in result["summary"]


# ---------------------------------------------------------------------------
# Influx-backed stats shaping
# ---------------------------------------------------------------------------


def _patch_influx(monkeypatch, fake):
    captured = []

    def wrapper(flux):
        captured.append(flux)
        return fake(flux)

    monkeypatch.setattr(backends, "query_influx", wrapper)
    monkeypatch.setattr(server, "query_influx", wrapper)
    return captured


def test_wifi_stats_shaping(monkeypatch, no_api):
    def fake(flux):
        assert "wifi_link" in flux
        if '"uplink"' in flux and 'group(columns: ["interface"])' in flux:
            return [{"interface": "wlan1", "_value": 0}, {"interface": "wlan0", "_value": 1}]
        assert 'r.interface == "wlan0"' in flux      # the uplink, never the spare radio
        if 'group(columns: ["_field"]) |> last()' in flux:
            return _wifi_last_rows()
        if "reduce(" in flux:
            assert "r._value < -75.0" in flux
            return [{"n": 8640, "weak": 43, "min": -81.0, "max": -49.0}]
        if "median()" in flux:
            return [{"_value": -52.0}]
        if "quantile(" in flux:
            return [{"_value": -58.4}]
        if "increase()" in flux:
            return [{"_field": "carrier_down_count", "_value": 1},
                    {"_field": "tx_failed", "_value": 12}]
        if "distinct(" in flux:
            return [{"_value": 2}]
        if "derivative(" in flux:
            return [{"_field": "rx_bytes", "_value": 7_807_500.0},
                    {"_field": "tx_bytes", "_value": 300_000.0}]
        if "limit(n: 5)" in flux:
            return [{"_time": datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc),
                     "_value": -81.0, "bssid": "00:00:5e:00:53:01", "interface": "wlan0"}]
        raise AssertionError(flux)

    _patch_influx(monkeypatch, fake)
    result = server.get_wifi_stats(hours=24)
    assert result["present"] is True
    assert result["interface"] == "wlan0" and result["uplink_is_wifi"] is True
    assert result["weak_below_dbm"] == -75.0
    assert result["now"]["ssid"] == "ExampleNet" and result["now"]["signal_dbm"] == -52.0
    assert "interface" not in result["now"]
    assert result["window"] == {
        "samples": 8640,
        "signal_dbm": {"min": -81.0, "max": -49.0, "median": -52.0, "p10": -58.4},
        "weak_share_pct": 0.5,
        "disconnects": 1,
        "tx_failed": 12,
        "roams": 1,
        "throughput_mbps": {"max_rx": 62.46, "max_tx": 2.4},
    }
    assert result["worst_windows"] == [
        {"time": "2026-09-19T01:00:00+00:00", "signal_dbm": -81.0, "bssid": "00:00:5e:00:53:01"}
    ]
    assert "links" not in result           # no base URL configured


def test_wifi_stats_on_a_wired_host(monkeypatch, no_api):
    captured = _patch_influx(monkeypatch, lambda flux: [])
    result = server.get_wifi_stats()
    assert result["present"] is False and "wired" in result["note"]
    assert "now" not in result and "window" not in result
    assert len(captured) == 1                 # one probe, then stop


def test_wifi_stats_picks_the_uplink_when_two_radios_exist(monkeypatch, no_api):
    """A spare radio's numbers must never blend into the uplink's."""
    def fake(flux):
        if '"uplink"' in flux and 'group(columns: ["interface"])' in flux:
            return [{"interface": "wlan1", "_value": 0}, {"interface": "wlan0", "_value": 1}]
        assert 'r.interface == "wlan0"' in flux
        return []
    _patch_influx(monkeypatch, fake)
    result = server.get_wifi_stats()
    assert result["present"] is False and "wlan0" in result["note"]


def test_wifi_stats_without_an_uplink_flag_takes_the_first_radio(monkeypatch, no_api):
    def fake(flux):
        if '"uplink"' in flux and 'group(columns: ["interface"])' in flux:
            return [{"interface": "wlp3s0", "_value": 0}, {"interface": "wlan0", "_value": 0}]
        assert 'r.interface == "wlan0"' in flux
        return []
    _patch_influx(monkeypatch, fake)
    assert server.get_wifi_stats()["present"] is False


def test_wifi_stats_interface_filter_reaches_every_query(monkeypatch, no_api):
    captured = _patch_influx(monkeypatch, lambda flux: [])
    server.get_wifi_stats(interface="wlp3s0")
    assert captured and all('r.interface == "wlp3s0"' in q for q in captured)


def test_wifi_weak_threshold_is_configurable(monkeypatch, no_api):
    monkeypatch.setenv("WIFI_WEAK_DBM", "-70")
    captured = _patch_influx(monkeypatch, lambda flux: (
        [{"interface": "wlan0", "_value": 1}] if '"uplink"' in flux else []))
    result = server.get_wifi_stats()
    assert result["weak_below_dbm"] == -70.0
    assert any("r._value < -70.0" in q for q in captured)


def test_latency_stats_shaping(monkeypatch, no_api):
    def fake(flux):
        if "quantile" in flux:
            return [
                {"target": "google_dns", "_measurement": "latency", "_value": 0.030}
            ]
        if '_field == "median"' in flux:
            return [
                {"target": "google_dns", "_measurement": "latency", "_value": 0.0123}
            ]
        if '_field == "loss"' in flux:
            return [
                {"target": "google_dns", "_measurement": "latency", "_value": 0.05}
            ]
        raise AssertionError(f"unexpected flux: {flux}")

    captured = _patch_influx(monkeypatch, fake)
    result = server.get_latency_stats(target="google_dns", hours=12)
    assert result["window_hours"] == 12
    assert result["stats"] == [
        {
            "target": "google_dns",
            "measurement": "latency",
            "median_ms": 12.3,
            "p95_ms": 30.0,
            "avg_loss_pct": 5.0,
        }
    ]
    # target filter and clamping are present in the generated Flux
    assert any('r.target == "google_dns"' in q for q in captured)
    loss_query = next(q for q in captured if '_field == "loss"' in q)
    assert "if r._value > 1.0 then 1.0" in loss_query


def test_loss_events_shaping(monkeypatch, no_api):
    ts = datetime(2026, 7, 28, 3, 15, tzinfo=timezone.utc)

    def fake(flux):
        return [
            {
                "_time": ts,
                "target": "google_dns",
                "_measurement": "latency",
                "_value": 0.25,
            }
        ]

    captured = _patch_influx(monkeypatch, fake)
    result = server.get_loss_events(hours=8, min_loss_pct=10)
    assert result["event_count"] == 1
    assert result["events"][0] == {
        "time": "2026-07-28T03:15:00+00:00",
        "target": "google_dns",
        "measurement": "latency",
        "loss_pct": 25.0,
    }
    # threshold converted from percent to ratio; clamping applied
    assert "r._value >= 0.1" in captured[0]
    assert "if r._value > 1.0 then 1.0" in captured[0]


def test_loss_events_threshold_validation(no_api):
    assert "error" in server.get_loss_events(min_loss_pct=250)
    assert "error" in server.get_loss_events(min_loss_pct="lots")


def test_microcut_stats_shaping(monkeypatch, no_api):
    ts = datetime(2026, 7, 28, 2, 0, tzinfo=timezone.utc)

    def fake(flux):
        assert "cpe_latency" in flux
        if "count()" in flux:
            return [{"target": "cpe", "protocol": "icmp", "_value": 7}]
        if "max()" in flux:
            return [{"target": "cpe", "protocol": "icmp", "_value": 12.5}]
        if '_field == "jitter"' in flux:
            return [{"target": "cpe", "protocol": "icmp", "_value": 3.14159}]
        if "limit(n: 5)" in flux:
            return [
                {"_time": ts, "target": "cpe", "protocol": "icmp", "_value": 12.5}
            ]
        raise AssertionError(f"unexpected flux: {flux}")

    _patch_influx(monkeypatch, fake)
    result = server.get_microcut_stats(hours=24)
    assert result["stats"] == [
        {
            "target": "cpe",
            "protocol": "icmp",
            "lossy_windows": 7,
            "max_loss_pct": 12.5,
            "median_jitter_ms": 3.142,
        }
    ]
    assert result["worst_windows"] == [
        {
            "time": "2026-07-28T02:00:00+00:00",
            "target": "cpe",
            "protocol": "icmp",
            "loss_pct": 12.5,
        }
    ]


# ---------------------------------------------------------------------------
# Import safety
# ---------------------------------------------------------------------------


def test_import_does_not_require_env(monkeypatch):
    """server/backends import and tool registration need no env vars."""
    for var in (
        "CONFIG_API_URL",
        "CONFIG_API_TOKEN",
        "INFLUX_URL",
        "INFLUX_TOKEN",
        "INFLUX_ORG",
        "INFLUX_BUCKET",
    ):
        monkeypatch.delenv(var, raising=False)
    import importlib

    importlib.reload(backends)
    assert backends.influx_bucket() == "smokeping"
    # FastMCP instance exists and has our tools registered
    assert server.mcp.name == "smokeping"


# ---------------------------------------------------------------------------
# Deep links threaded through the tool responses
# ---------------------------------------------------------------------------


# Every variable link building reads. TSDB_TYPE gates link emission and the
# TUNNEL_* trio adds a second link beside each of the first, so an exported
# value on the developer's machine would otherwise decide whether these tests
# pass.
LINK_ENV = (
    "PUBLIC_BASE_HOST",
    "GRAFANA_PUBLIC_URL",
    "WEB_ADMIN_PUBLIC_URL",
    "TUNNEL_BASE_HOST",
    "GRAFANA_TUNNEL_URL",
    "WEB_ADMIN_TUNNEL_URL",
    "TSDB_TYPE",
)


@pytest.fixture()
def unlinked(monkeypatch):
    for var in LINK_ENV:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def linked(unlinked, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_HOST", "192.168.86.27")


@pytest.fixture()
def linked_with_tunnel(linked, monkeypatch):
    monkeypatch.setenv("TUNNEL_BASE_HOST", "https://smokingpi.example.com")


def test_list_targets_carries_links(api, linked):
    result = server.list_targets()
    first = result["targets"][0]
    assert first["links"]["edit"].endswith("/targets/?q=google_dns")
    assert "/d/smokeping-lat-pct-v28?var-target=google_dns" in first["links"]["graph"]


def test_list_targets_omits_links_when_unconfigured(api, unlinked):
    result = server.list_targets()
    assert all("links" not in t for t in result["targets"])


def test_latency_stats_links_use_the_requested_window(monkeypatch, api, linked):
    def fake(flux):
        return [{"target": "google_dns", "_measurement": "latency", "_value": 0.01}]

    _patch_influx(monkeypatch, fake)
    result = server.get_latency_stats(target="google_dns", hours=6)
    graph = result["stats"][0]["links"]["graph"]
    assert "from=now-6h" in graph
    # google_dns is filed under the `dns` category in the fake DB, which has no
    # side-by-side dashboard, so no peer comparison is offered.
    assert "compare_with_peers" not in result["stats"][0]["links"]


def test_measurement_tools_do_not_call_the_api_when_links_are_off(
    monkeypatch, no_api, unlinked
):
    """The catalog lookup exists only to build links; without links, no call."""

    def fake(flux):
        return [{"target": "google_dns", "_measurement": "latency", "_value": 0.01}]

    _patch_influx(monkeypatch, fake)
    result = server.get_latency_stats(hours=6)
    assert result["stats"]  # no_api raises if the config API is touched


def test_latency_stats_survive_a_dead_config_api(monkeypatch, linked):
    """Losing the catalog costs the peer link, not the numbers or other links."""

    class DeadAPI:
        def request(self, method, path, **kwargs):
            raise backends.ConfigAPIError("config-manager unreachable")

    monkeypatch.setattr(backends, "get_config_api", lambda: DeadAPI())

    def fake(flux):
        return [{"target": "google_dns", "_measurement": "latency", "_value": 0.01}]

    _patch_influx(monkeypatch, fake)
    result = server.get_latency_stats(hours=6)
    assert result["stats"][0]["median_ms"] == 10.0
    # Only the category-dependent link is lost; the rest need no catalog.
    assert "graph" in result["stats"][0]["links"]
    assert "compare_with_peers" not in result["stats"][0]["links"]


def test_loss_events_roll_up_per_target(monkeypatch, api, linked):
    times = [
        datetime(2026, 7, 28, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 28, 4, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 28, 3, 0, tzinfo=timezone.utc),
    ]

    def fake(flux):
        return [
            {
                "_time": times[0],
                "target": "google_dns",
                "_measurement": "latency",
                "_value": 0.25,
            },
            {
                "_time": times[1],
                "target": "google_dns",
                "_measurement": "latency",
                "_value": 0.60,
            },
            {
                "_time": times[2],
                "target": "cloudflare_dns",
                "_measurement": "latency",
                "_value": 0.10,
            },
        ]

    _patch_influx(monkeypatch, fake)
    result = server.get_loss_events(hours=24, min_loss_pct=5)

    assert result["event_count"] == 3
    # Busiest target first.
    assert [r["target"] for r in result["by_target"]] == [
        "google_dns",
        "cloudflare_dns",
    ]
    busiest = result["by_target"][0]
    assert busiest["event_count"] == 2
    assert busiest["max_loss_pct"] == 60.0
    # Rows arrive newest-first, so first_time must be the OLDEST of the two.
    assert busiest["first_time"] == "2026-07-28T04:00:00+00:00"
    assert busiest["last_time"] == "2026-07-28T05:00:00+00:00"
    assert "/d/smokeping-lat-pct-v28" in busiest["links"]["graph"]


def test_microcut_worst_windows_link_to_their_own_moment(monkeypatch, no_api, linked):
    ts = datetime(2026, 7, 28, 2, 0, tzinfo=timezone.utc)

    def fake(flux):
        if "count()" in flux:
            return [{"target": "CPE", "protocol": "ipv4", "_value": 3}]
        if "max()" in flux:
            return [{"target": "CPE", "protocol": "ipv4", "_value": 40.0}]
        if "median()" in flux:
            return [{"target": "CPE", "protocol": "ipv4", "_value": 1.5}]
        return [{"_time": ts, "target": "CPE", "protocol": "ipv4", "_value": 40.0}]

    _patch_influx(monkeypatch, fake)
    result = server.get_microcut_stats(hours=24)

    # The per-target summary spans the whole window...
    assert "from=now-24h" in result["stats"][0]["links"]["graph"]
    # ...while an individual worst window is zoomed to when it happened.
    center = int(ts.timestamp() * 1000)
    graph = result["worst_windows"][0]["graph"]
    assert f"from={center - 15 * 60 * 1000}" in graph
    assert "var-cpe=CPE" in graph


def test_system_status_reports_unconfigured_links(api, unlinked):
    result = server.system_status()
    assert "links" not in result
    assert "PUBLIC_BASE_HOST" in result["deep_links"]


def test_system_status_distinguishes_wrong_backend_from_unconfigured(
    api, linked, monkeypatch
):
    """Two different reasons for no links, and two different fixes.

    `linked` sets PUBLIC_BASE_HOST, so reporting the "set PUBLIC_BASE_HOST"
    hint here would send the reader to check a setting that is already right.
    """
    monkeypatch.setenv("TSDB_TYPE", "clickhouse")
    result = server.system_status()
    assert "links" not in result
    assert "clickhouse" in result["deep_links"].lower()
    assert "PUBLIC_BASE_HOST" not in result["deep_links"]


def test_wifi_stats_links_zoom_each_worst_window(monkeypatch, no_api, linked):
    moment = datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc)

    def fake(flux):
        if '"uplink"' in flux and 'group(columns: ["interface"])' in flux:
            return [{"interface": "wlan0", "_value": 1}]
        if "limit(n: 5)" in flux:
            return [{"_time": moment, "_value": -81.0, "interface": "wlan0"}]
        if "reduce(" in flux:
            return [{"n": 10, "weak": 1, "min": -81.0, "max": -50.0}]
        return []

    _patch_influx(monkeypatch, fake)
    result = server.get_wifi_stats(hours=6)
    assert "/d/wifi-link-v1?var-interface=wlan0&from=now-6h" in result["links"]["graph"]
    center = int(moment.timestamp() * 1000)
    assert f"from={center - 15 * 60 * 1000}" in result["worst_windows"][0]["graph"]


def test_system_status_offers_entry_points_when_configured(api, linked):
    result = server.system_status()
    assert "deep_links" not in result
    assert result["links"]["web_admin_targets"].endswith("/targets/")
    assert "/d/cpe-microcut-v1" in result["links"]["grafana_cpe_microcuts"]
    assert "/d/wifi-link-v1" in result["links"]["grafana_wifi_link"]
    # No tunnel configured: no twins to try.
    assert not [key for key in result["links"] if key.endswith("_tunnel")]


def test_entry_points_and_target_links_both_twin(api, linked_with_tunnel):
    """The front doors get the from-anywhere twin too.

    These are the links an agent reaches for first when asked an open
    question, so twinning target links but not these would leave the most
    common answer LAN-only.
    """
    status = server.system_status()
    assert status["links"]["grafana_overview_tunnel"].startswith(
        "https://smokingpi.example.com/d/"
    )
    assert status["links"]["web_admin_targets_tunnel"].startswith(
        "https://smokingpi.example.com/"
    )

    first = server.list_targets()["targets"][0]
    assert first["links"]["graph"].startswith("http://192.168.86.27:3000/")
    assert first["links"]["graph_tunnel"].startswith("https://smokingpi.example.com/")


# ---------------------------------------------------------------------------
# get_loss_events: the shape of the loss, not just the points
#
# Two real nights drove this. 2026-09-20: the Pi's Wi-Fi radio hung for 3 h
# 20 min, every target went to 100%, and the answer "18 targets had loss
# events" was true and useless. Every ordinary day: 60-300 single-lost-ping
# points spread across every target, reported as "3 loss events ~9%" -- the
# Wi-Fi hop breathing, not events.
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 9, 20, 1, 40, tzinfo=timezone.utc)
_TEN = ["Google", "Apple", "Amazon", "NYT", "Facebook", "cloudflare",
        "GoogleDNS", "CloudflareDNS", "Quad9DNS", "CPE_IPv4"]


def _rows(per_target, start=_T0):
    """Newest-first rows for {target: [ratio per 300 s step]}; zeros are
    omitted the way the >= threshold filter would omit them."""
    rows = []
    for target, values in per_target.items():
        for i, v in enumerate(values):
            if v:
                rows.append({"_time": start + timedelta(seconds=300 * i), "target": target,
                             "_measurement": "latency", "_value": v})
    rows.sort(key=lambda r: r["_time"], reverse=True)
    return rows


def _loss_fake(per_target, background=0, targets=None):
    events = _rows(per_target)
    total = len(targets if targets is not None else per_target)

    def fake(flux):
        if "count(column" in flux:
            return [{"_value": total}]
        if "r._value > 0.0 and" in flux:
            return [{"_value": background}]
        return events
    return fake


def test_loss_events_default_threshold_skips_single_lost_pings(monkeypatch, no_api):
    captured = _patch_influx(monkeypatch, _loss_fake({}, background=143))
    result = server.get_loss_events(hours=24)
    assert result["min_loss_pct"] == 15.0
    assert "r._value >= 0.15" in captured[0]
    # The excluded background is counted, not hidden.
    assert "r._value > 0.0 and r._value < 0.15" in captured[1]
    assert result["background_points"] == 143
    assert result["events"] == [] and result["episodes"] == [] and result["widespread"] == []


def test_loss_events_folds_a_run_into_one_episode(monkeypatch, no_api):
    # One target, 25 minutes of total loss, then one more point 20 minutes
    # later: one episode of five points, and a second, separate one.
    per_target = {"NYT": [1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.6]}
    _patch_influx(monkeypatch, _loss_fake(per_target, targets=_TEN))
    result = server.get_loss_events(hours=24)
    assert result["event_count"] == 6
    assert len(result["episodes"]) == 2
    later, cut = result["episodes"]          # newest first
    assert cut == {
        "target": "NYT", "measurement": "latency",
        "start": "2026-09-20T01:40:00+00:00", "end": "2026-09-20T02:00:00+00:00",
        "minutes": 25, "points": 5, "max_loss_pct": 100.0, "all_lost": True,
    }
    assert later["points"] == 1 and later["all_lost"] is False
    # One target down is not widespread.
    assert result["widespread"] == []


def test_loss_events_names_the_hung_radio_night_as_this_hosts_uplink(monkeypatch, no_api):
    per_target = {t: [1.0, 1.0, 1.0, 1.0] for t in _TEN}
    _patch_influx(monkeypatch, _loss_fake(per_target))
    result = server.get_loss_events(hours=24)
    assert result["targets_reporting"] == 10
    assert len(result["widespread"]) == 1
    run = result["widespread"][0]
    assert run["targets_affected"] == 10 and run["targets_total"] == 10
    assert run["all_lost"] is True
    assert run["minutes"] == 20
    assert run["start"] == "2026-09-20T01:40:00+00:00"
    assert "this host's uplink" in run["cause"]
    assert "not the ISP" in run["cause"]
    # Still reported per target underneath, for anyone who wants the detail.
    assert len(result["episodes"]) == 10


def test_loss_events_calls_a_blink_across_everyone_a_cut_of_the_link(monkeypatch, no_api):
    per_target = {t: [0.0, 0.8, 0.0, 0.0] for t in _TEN}
    _patch_influx(monkeypatch, _loss_fake(per_target))
    run = server.get_loss_events(hours=24)["widespread"][0]
    assert run["all_lost"] is False
    assert run["minutes"] == 5
    assert run["cause"].startswith("the link: a brief cut")


def test_loss_events_widespread_tolerates_a_lucky_target(monkeypatch, no_api):
    per_target = {t: [1.0, 1.0, 1.0] for t in _TEN}
    per_target["Google"] = [0.0, 0.0, 0.0]
    _patch_influx(monkeypatch, _loss_fake(per_target))
    result = server.get_loss_events(hours=24)
    assert result["widespread"][0]["targets_affected"] == 9
    assert result["widespread"][0]["all_lost"] is True


def test_loss_events_two_separate_cuts_are_two_widespread_runs(monkeypatch, no_api):
    per_target = {t: [1.0, 0.0, 0.0, 0.0, 1.0] for t in _TEN}
    _patch_influx(monkeypatch, _loss_fake(per_target))
    runs = server.get_loss_events(hours=24)["widespread"]
    assert len(runs) == 2
    assert runs[0]["start"] > runs[1]["start"]   # newest first


def test_loss_events_widespread_needs_a_denominator(monkeypatch, no_api):
    # Two targets reporting: breadth means nothing, say nothing.
    per_target = {"a": [1.0, 1.0], "b": [1.0, 1.0]}
    _patch_influx(monkeypatch, _loss_fake(per_target))
    assert server.get_loss_events(hours=24)["widespread"] == []


def test_loss_events_rollups_see_past_the_events_cap(monkeypatch, no_api):
    """720 points (18 targets, 40 steps) exceed the 500-row events cap; the
    episodes and the widespread run must still cover the whole cut."""
    targets = [f"t{i}" for i in range(18)]
    per_target = {t: [1.0] * 40 for t in targets}
    captured = _patch_influx(monkeypatch, _loss_fake(per_target))
    result = server.get_loss_events(hours=24)
    assert f"limit(n: {server.MAX_ROLLUP_ROWS})" in captured[0]
    assert result["event_count"] == 720
    assert len(result["events"]) == 500 and result["truncated"] is True
    assert result["widespread"][0]["minutes"] == 200
    assert all(e["points"] == 40 for e in result["episodes"])


def test_loss_events_episode_carries_a_graph_link(monkeypatch, api, linked):
    per_target = {"NYT": [1.0, 1.0, 1.0]}
    _patch_influx(monkeypatch, _loss_fake(per_target, targets=_TEN))
    episode = server.get_loss_events(hours=6)["episodes"][0]
    assert "/d/smokeping-lat-pct-v28" in episode["graph"]
    assert "var-target=NYT" in episode["graph"]
