"""Unit tests for the SmokePing MCP server tools.

Everything is mocked at the backends layer -- no network access:
- the config-manager REST API via a FakeConfigAPI dispatcher
- InfluxDB via a fake ``query_influx`` returning canned record dicts
"""

from datetime import datetime, timezone

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


@pytest.fixture()
def linked(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_HOST", "192.168.86.27")
    monkeypatch.delenv("GRAFANA_PUBLIC_URL", raising=False)
    # TSDB_TYPE gates link emission, so an exported value on the developer's
    # machine would otherwise decide whether these tests pass.
    monkeypatch.delenv("TSDB_TYPE", raising=False)
    monkeypatch.delenv("WEB_ADMIN_PUBLIC_URL", raising=False)


@pytest.fixture()
def unlinked(monkeypatch):
    for var in (
        "PUBLIC_BASE_HOST",
        "GRAFANA_PUBLIC_URL",
        "WEB_ADMIN_PUBLIC_URL",
        "TSDB_TYPE",
    ):
        monkeypatch.delenv(var, raising=False)


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
    centre = int(ts.timestamp() * 1000)
    graph = result["worst_windows"][0]["graph"]
    assert f"from={centre - 15 * 60 * 1000}" in graph
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


def test_system_status_offers_entry_points_when_configured(api, linked):
    result = server.system_status()
    assert "deep_links" not in result
    assert result["links"]["web_admin_targets"].endswith("/targets/")
    assert "/d/cpe-microcut-v1" in result["links"]["grafana_cpe_microcuts"]
