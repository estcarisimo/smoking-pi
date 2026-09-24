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


def _uplink_rows(flux):
    from datetime import datetime, timezone
    if "exists r.previous" in flux:
        return [{"_time": datetime(2026, 9, 24, 13, 2, tzinfo=timezone.utc),
                 "previous": "wlan0", "interface": "eth0", "kind": "wired"}]
    if "host_uplink" in flux:
        return [{"_field": "interface", "_value": "eth0"},
                {"_field": "kind", "_value": "wired"},
                {"_field": "family", "_value": 4}]
    return []


def test_system_status_names_the_uplink_and_its_last_change(api, monkeypatch):
    _patch_influx(monkeypatch, _uplink_rows)
    uplink = server.system_status()["uplink"]
    assert uplink["interface"] == "eth0" and uplink["kind"] == "wired"
    assert uplink["family"] == 4 and uplink["changes_7d"] == 1
    assert uplink["last_change"]["previous"] == "wlan0"
    assert uplink["last_change"]["said"].startswith(
        "this host's uplink moved from wlan0 to eth0 (wired) at ")


def test_system_status_without_host_uplink_says_nothing(api, monkeypatch, caplog):
    def boom(flux):
        raise RuntimeError("Token hunter2 leaked")
    _patch_influx(monkeypatch, boom)
    result = server.system_status()
    assert "uplink" not in result
    assert "hunter2" not in caplog.text


def test_system_status_survives_a_malformed_uplink_row(api, monkeypatch):
    def fake(flux):
        if "host_uplink" in flux and "exists r.previous" not in flux:
            return [{"_field": "interface", "_value": "eth0"},
                    {"_field": "family", "_value": "four"}]
        return []
    _patch_influx(monkeypatch, fake)
    result = server.system_status()
    assert "uplink" not in result
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
    assert "r._value >= 0.1" in captured[1]
    assert "if r._value > 1.0 then 1.0" in captured[1]


def test_loss_events_carry_an_uplink_change_in_the_window(monkeypatch, no_api):
    ts = datetime(2026, 7, 28, 3, 15, tzinfo=timezone.utc)

    def fake(flux):
        if "host_uplink" in flux:
            assert "range(start: -8h)" in flux
            return [{"_time": ts, "previous": "wlan0", "interface": "", "kind": "none"}]
        return [{"_time": ts, "target": "google_dns", "_measurement": "latency",
                 "_value": 0.25}]

    _patch_influx(monkeypatch, fake)
    result = server.get_loss_events(hours=8, min_loss_pct=10)
    assert result["uplink_changes"][0]["said"].startswith(
        "this host lost its default route (it was on wlan0) at ")


def test_loss_events_without_a_change_have_no_uplink_key(monkeypatch, no_api):
    """Only when it changed: an empty list on every answer is noise."""
    ts = datetime(2026, 7, 28, 3, 15, tzinfo=timezone.utc)
    _patch_influx(monkeypatch, lambda flux: [] if "host_uplink" in flux else [
        {"_time": ts, "target": "google_dns", "_measurement": "latency", "_value": 0.25}])
    assert "uplink_changes" not in server.get_loss_events(hours=8, min_loss_pct=10)


def test_loss_events_threshold_validation(no_api):
    assert "error" in server.get_loss_events(min_loss_pct=250)
    assert "error" in server.get_loss_events(min_loss_pct="lots")


_CPE_T0 = datetime(2026, 9, 19, 0, 42, 33, tzinfo=timezone.utc)


def _cpe_fake(cut_windows, windows=2880, p50=10.0, p90=18.0, max_loss=None,
              target="136.25.220.1", protocol="ipv4"):
    """A day of cpe_latency: the floor as aggregates, plus the raw windows
    above the threshold as [(seconds after _CPE_T0, loss_pct), ...]."""
    rows = [{"_time": _CPE_T0 + timedelta(seconds=off), "target": target,
             "protocol": protocol, "_value": loss} for off, loss in cut_windows]
    if max_loss is None:
        max_loss = max([loss for _, loss in cut_windows] + [p90 + 4])
    tp = {"target": target, "protocol": protocol}

    def fake(flux):
        assert "cpe_latency" in flux
        if "r._value > 50.0" in flux:
            return rows
        if "count()" in flux:
            return [{**tp, "_value": windows}]
        if "quantile(q: 0.5)" in flux:
            return [{**tp, "_value": p50}]
        if "quantile(q: 0.9)" in flux:
            return [{**tp, "_value": p90}]
        if "max()" in flux:
            return [{**tp, "_value": max_loss}]
        if '_field == "jitter"' in flux:
            return [{**tp, "_value": 3.14159}]
        raise AssertionError(f"unexpected flux: {flux}")
    return fake


def test_microcut_stats_a_quiet_day_reports_the_floor_and_no_cuts(monkeypatch, no_api):
    """2026-08-30: 'worst window 20%' was reported as a microcut. It was the
    floor's tail; with nothing above 50% the answer is the floor itself."""
    _patch_influx(monkeypatch, _cpe_fake([], p50=14.0, p90=22.0, max_loss=44.0))
    result = server.get_microcut_stats(hours=24)
    assert result["cut_loss_pct"] == 50.0
    assert result["cuts"] == [] and result["worst_windows"] == []
    assert result["stats"] == [{
        "target": "136.25.220.1", "protocol": "ipv4", "windows": 2880,
        "p50_loss_pct": 14.0, "p90_loss_pct": 22.0, "max_loss_pct": 44.0,
        "median_jitter_ms": 3.142, "cut_windows": 0, "confirmed_cuts": 0,
        "possible_cuts": 0,
    }]
    assert "no microcuts" in result["note"]
    assert "p50 14% / p90 22%" in result["note"]
    assert "rate limiting, not a fault" in result["note"]


def test_microcut_stats_folds_the_real_cut_into_one_with_its_duration(monkeypatch, no_api):
    """2026-09-19 00:42:33-00:45:03Z: six consecutive windows at 100%, one
    every 30 s. One confirmed cut of 2 min 40 s, not six events."""
    six = [(30 * i, 100.0) for i in range(6)]
    _patch_influx(monkeypatch, _cpe_fake(six))
    result = server.get_microcut_stats(hours=24)
    assert len(result["cuts"]) == 1
    cut = result["cuts"][0]
    assert cut["start"] == "2026-09-19T00:42:33+00:00"
    assert cut["end"] == "2026-09-19T00:45:03+00:00"
    assert cut["seconds"] == 160 and cut["windows"] == 6
    assert cut["total"] is True and cut["confirmed"] is True
    assert "start_epoch" not in cut
    stats = result["stats"][0]
    assert stats["cut_windows"] == 6
    assert stats["confirmed_cuts"] == 1 and stats["possible_cuts"] == 0
    assert len(result["worst_windows"]) == 5          # the five worst CUT windows
    assert all(w["loss_pct"] == 100.0 for w in result["worst_windows"])
    assert "note" not in result


def test_microcut_stats_an_isolated_window_is_a_possible_cut(monkeypatch, no_api):
    """2026-09-07: seventeen isolated windows at 52-78% in fourteen days,
    reported as 'strong microcuts'. Each is a possible cut, and says so."""
    _patch_influx(monkeypatch, _cpe_fake([(0, 52.0), (23 * 60, 62.0)]))
    result = server.get_microcut_stats(hours=24)
    assert [c["confirmed"] for c in result["cuts"]] == [False, False]
    assert result["cuts"][0]["start"] > result["cuts"][1]["start"]   # newest first
    assert result["stats"][0]["possible_cuts"] == 2
    assert result["stats"][0]["confirmed_cuts"] == 0
    assert len(result["worst_windows"]) == 2


def test_microcut_stats_a_single_total_window_is_confirmed(monkeypatch, no_api):
    _patch_influx(monkeypatch, _cpe_fake([(0, 100.0)]))
    cut = server.get_microcut_stats(hours=24)["cuts"][0]
    assert cut["confirmed"] is True and cut["windows"] == 1 and cut["seconds"] == 10


def test_microcut_stats_tolerates_one_missing_window_inside_a_cut(monkeypatch, no_api):
    # 30 s cadence: windows at 0, 30, (missing 60), 90 are one cut; a gap of
    # two missing windows (0 -> 120) is two.
    _patch_influx(monkeypatch, _cpe_fake([(0, 100.0), (30, 100.0), (90, 100.0)]))
    assert len(server.get_microcut_stats(hours=24)["cuts"]) == 1
    _patch_influx(monkeypatch, _cpe_fake([(0, 100.0), (120, 100.0)]))
    assert len(server.get_microcut_stats(hours=24)["cuts"]) == 2


def test_microcut_stats_threshold_follows_the_env(monkeypatch, no_api):
    monkeypatch.setenv("MICROCUT_LOSS_PCT", "80")
    captured = _patch_influx(monkeypatch, lambda flux: [])
    result = server.get_microcut_stats(hours=24)
    assert result["cut_loss_pct"] == 80.0
    assert any("r._value > 80.0" in q for q in captured)


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


def test_microcut_cuts_and_worst_windows_link_to_their_own_moment(monkeypatch, no_api, linked):
    _patch_influx(monkeypatch, _cpe_fake([(0, 100.0), (30, 100.0)], target="CPE"))
    result = server.get_microcut_stats(hours=24)

    # The per-target summary spans the whole window...
    assert "from=now-24h" in result["stats"][0]["links"]["graph"]
    # ...while a cut and a worst window are zoomed to when they happened.
    center = int(_CPE_T0.timestamp() * 1000)
    assert f"from={center - 15 * 60 * 1000}" in result["cuts"][0]["graph"]
    assert "var-cpe=CPE" in result["cuts"][0]["graph"]
    graph = result["worst_windows"][0]["graph"]
    assert "var-cpe=CPE" in graph and "from=" in graph


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


def _loss_fake(per_target, background=0, targets=None, steps=None):
    events = _rows(per_target)
    total = len(targets if targets is not None else per_target)
    if steps is None:
        steps = max((len(v) for v in per_target.values()), default=0)

    def fake(flux):
        if "distinct(" in flux:
            # One row per probe step: how many targets reported in it.
            return [{"_time": _T0 + timedelta(seconds=300 * i), "_value": total}
                    for i in range(steps)]
        if "r._value > 0.0 and" in flux:
            return [{"_value": background}]
        return events
    return fake


def test_loss_events_default_threshold_skips_single_lost_pings(monkeypatch, no_api):
    captured = _patch_influx(monkeypatch, _loss_fake({}, background=143))
    result = server.get_loss_events(hours=24)
    # The pings-lost rule: no fixed percent, 1.5 pings' worth per target,
    # which is 15% on the default ten pings.
    assert result["min_loss_pct"] is None
    assert result["min_lost_pings"] == 1.5
    assert "r._value >= 0.15" in captured[1]
    # The denominator query must put its count in _value, where it is read
    # (distinct() does; count(column: "target") does not -- seen live), and
    # it must be PER STEP: over a day more names rotate through than report
    # in any one cycle, which put 80% out of reach -- also seen live.
    assert 'group(columns: ["_time"])' in captured[3]
    assert 'distinct(column: "target") |> count()' in captured[3]
    assert "count(column" not in captured[3]
    # The excluded background is counted, not hidden.
    assert "r._value > 0.0 and r._value < 0.15" in captured[2]
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
    assert "for 4 cycles" in run["cause"]
    assert "not the ISP" in run["cause"]
    # Still reported per target underneath, for anyone who wants the detail.
    assert len(result["episodes"]) == 10


def test_loss_events_one_total_loss_cycle_is_a_cut_not_the_uplink(monkeypatch, no_api):
    """Review of #74: the alerter needs three cycles of total loss before
    it calls the uplink down; the tool must not blame this host on one."""
    per_target = {t: [0.0, 1.0, 0.0, 0.0] for t in _TEN}
    _patch_influx(monkeypatch, _loss_fake(per_target))
    run = server.get_loss_events(hours=24)["widespread"][0]
    assert run["all_lost"] is True
    assert run["cause"].startswith("the link: a brief cut")
    assert "every packet lost" in run["cause"]
    assert "this host" not in run["cause"]


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
    assert f"limit(n: {server.MAX_ROLLUP_ROWS})" in captured[1]
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


def test_loss_events_denominator_is_the_cycles_own_not_the_windows(monkeypatch, no_api):
    """Seen live after #75: 23 distinct names over the day (OCA targets
    rotate), 18 reporting in any one cycle, 18 down together -- and 80% of
    23 is 19. The share must be of the targets that reported in that step."""
    per_target = {t: [1.0, 1.0, 1.0, 1.0] for t in _TEN}
    rotated = [f"oca_{i}" for i in range(4)]     # names seen elsewhere in the day
    _patch_influx(monkeypatch, _loss_fake(per_target, targets=_TEN))
    result = server.get_loss_events(hours=24)
    assert result["targets_reporting"] == 10
    assert len(result["widespread"]) == 1
    assert result["widespread"][0]["targets_total"] == 10
    # The same rows with a window-wide denominator of 14 would need 12 of 10.
    assert len(_TEN) < 0.8 * (len(_TEN) + len(rotated))


def test_loss_events_a_cut_with_partial_edge_cycles_is_still_total_loss(monkeypatch, no_api):
    """Seen live: the 3 h 20 min hang began with a 95% cycle (it started
    mid-cycle), and `all(...)` over the run called it 'a brief cut'."""
    per_target = {t: [0.95, 1.0, 1.0, 1.0, 1.0, 0.6] for t in _TEN}
    _patch_influx(monkeypatch, _loss_fake(per_target))
    run = server.get_loss_events(hours=24)["widespread"][0]
    assert run["all_lost"] is True
    assert "this host's uplink" in run["cause"]
    assert "for 4 cycles" in run["cause"]


def test_loss_events_a_two_cycle_run_has_no_interior_to_excuse(monkeypatch, no_api):
    # Review of #76: with two steps both are edges; one total-loss cycle
    # out of two is not "every packet lost".
    per_target = {t: [0.0, 1.0, 0.5, 0.0] for t in _TEN}
    _patch_influx(monkeypatch, _loss_fake(per_target))
    run = server.get_loss_events(hours=24)["widespread"][0]
    assert run["minutes"] == 10 and run["all_lost"] is False
    per_target = {t: [0.0, 1.0, 1.0, 0.0] for t in _TEN}
    _patch_influx(monkeypatch, _loss_fake(per_target))
    assert server.get_loss_events(hours=24)["widespread"][0]["all_lost"] is True


def test_loss_events_reporting_takes_the_larger_count_when_a_cycle_splits(monkeypatch, no_api):
    per_target = {t: [1.0, 1.0, 1.0, 1.0] for t in _TEN}
    events = _rows(per_target)

    def fake(flux):
        if "distinct(" in flux:
            rows = []
            for i in range(4):
                t = _T0 + timedelta(seconds=300 * i)
                rows.append({"_time": t, "_value": 10})
                rows.append({"_time": t + timedelta(seconds=7), "_value": 1})  # jittered straggler
            return rows
        if "r._value > 0.0 and" in flux:
            return [{"_value": 0}]
        return events

    _patch_influx(monkeypatch, fake)
    result = server.get_loss_events(hours=24)
    assert result["targets_reporting"] == 10
    assert len(result["widespread"]) == 1


# ---------------------------------------------------------------------------
# get_loss_events on a probe that is not on the default 300 s / 10 pings
# ---------------------------------------------------------------------------

def _with_cadence(fake, **steps):
    """Answer the cadence query with ``name=(step, pings)``, else ``fake``."""
    def wrapped(flux):
        if '"step"' in flux and "last()" in flux:
            return [row for target, (step, pings) in steps.items()
                    for row in ({"target": target, "_field": "step", "_value": step},
                                {"target": target, "_field": "pings", "_value": pings})]
        return fake(flux)
    return wrapped


def _spaced_rows(target, values, step_s):
    rows = [{"_time": _T0 + timedelta(seconds=step_s * i), "target": target,
             "_measurement": "latency", "_value": v}
            for i, v in enumerate(values) if v]
    rows.sort(key=lambda r: r["_time"], reverse=True)
    return rows


def test_loss_events_folds_a_600_s_probes_run_with_its_own_step(monkeypatch, no_api):
    """Consecutive points of a 600 s probe are 600 s apart: on the fixed
    300 s gap they split into one-point episodes, each "5 minutes" long."""
    events = _spaced_rows("Slow", [1.0, 1.0, 1.0], 600)
    _patch_influx(monkeypatch, _with_cadence(lambda flux: events, Slow=(600, 20)))
    result = server.get_loss_events(hours=24)
    assert len(result["episodes"]) == 1
    assert result["episodes"][0]["points"] == 3
    assert result["episodes"][0]["minutes"] == 30
    entry = result["by_target"][0]
    assert (entry["step_s"], entry["pings"]) == (600, 20)


def test_loss_events_say_what_a_default_target_is_measured_with(monkeypatch, no_api):
    _patch_influx(monkeypatch, _loss_fake({"NYT": [0.2]}, targets=_TEN))
    entry = server.get_loss_events(hours=24)["by_target"][0]
    assert (entry["step_s"], entry["pings"]) == (300, 10)


def test_loss_events_survive_a_failed_cadence_query(monkeypatch, no_api):
    base = _loss_fake({"NYT": [1.0, 1.0]}, targets=_TEN)

    def fake(flux):
        if '"step"' in flux and "last()" in flux:
            raise RuntimeError("influx hiccup")
        return base(flux)

    _patch_influx(monkeypatch, fake)
    result = server.get_loss_events(hours=24)
    assert "error" not in result
    assert result["episodes"][0]["minutes"] == 10


def test_widespread_all_lost_needs_every_row_of_a_target_in_its_step():
    """Two rows of one target in one 300 s step (a late duplicate): it is
    lost for that step only when both were."""
    def ev(target, pct, offset=0):
        return {"target": target, "loss_pct": pct,
                "_epoch": _T0.timestamp() + offset}

    reporting = {int(_T0.timestamp()) // 300 * 300: 3}
    events = [ev("a", 100.0), ev("b", 100.0), ev("c", 100.0), ev("c", 40.0, 60)]
    run = server._widespread_runs(events, reporting)[0]
    assert run["targets_affected"] == 3
    assert run["all_lost"] is False
    events[-1]["loss_pct"] = 100.0
    assert server._widespread_runs(events, reporting)[0]["all_lost"] is True


def test_loss_events_count_pings_lost_per_target(monkeypatch, no_api):
    """20 pings: two lost is 7.5%+ ; 5 DNS queries: one lost (20%) is not an
    event. The per-target bar goes into the query as a Flux dict."""
    base = _loss_fake({"NYT": [0.2]}, targets=_TEN)
    captured = _patch_influx(
        monkeypatch, _with_cadence(base, Big=(300, 20), GoogleDNS=(300, 5)))
    server.get_loss_events(hours=24)
    events_flux, background_flux = captured[1], captured[2]
    for flux in (events_flux, background_flux):
        assert flux.startswith('import "dict"\n')
        assert '{key: "Big", value: 0.075}' in flux
        assert '{key: "GoogleDNS", value: 0.3}' in flux
    assert ("r._value >= dict.get(dict: event_ratio, key: r.target, default: 0.15)"
            in events_flux)


def test_loss_events_explicit_percent_applies_to_everyone(monkeypatch, no_api):
    base = _loss_fake({"NYT": [0.2]}, targets=_TEN)
    captured = _patch_influx(monkeypatch, _with_cadence(base, Big=(300, 20)))
    result = server.get_loss_events(hours=24, min_loss_pct=12)
    assert "dict" not in captured[1]
    assert "r._value >= 0.12" in captured[1]
    assert result["min_loss_pct"] == 12.0 and result["min_lost_pings"] is None


def test_loss_events_tiny_percent_is_valid_flux(monkeypatch, no_api):
    captured = _patch_influx(monkeypatch, _loss_fake({}, targets=_TEN))
    server.get_loss_events(hours=24, min_loss_pct=0.001)
    assert "r._value >= 0.00001)" in captured[1]


# ---------------------------------------------------------------------------
# get_latency_stats: HTTP/TCP coverage and unknown target names
# ---------------------------------------------------------------------------


def test_latency_stats_reads_http_and_tcp(monkeypatch, no_api):
    captured = _patch_influx(monkeypatch, lambda flux: [])
    server.get_latency_stats(hours=6)
    for m in ("latency", "dns_latency", "http_latency", "tcp_latency"):
        assert all(f'r._measurement == "{m}"' in q for q in captured)


def test_latency_stats_unknown_target_names_the_real_ones(monkeypatch, api):
    _patch_influx(monkeypatch, lambda flux: [])
    result = server.get_latency_stats(target="Google_DNS", hours=6)
    assert result["error"] == "No monitoring target named 'Google_DNS' was found."
    assert result["available_targets"] == ["cloudflare_dns", "google_dns"]
    assert result["did_you_mean"] == ["google_dns"]
    assert "get_microcut_stats" in result["hint"]


def test_latency_stats_unknown_target_without_a_close_match(monkeypatch, api):
    _patch_influx(monkeypatch, lambda flux: [])
    result = server.get_latency_stats(target="CPE_Gateway", hours=6)
    assert "error" in result and "did_you_mean" not in result


def test_latency_stats_known_target_without_data_keeps_the_note(
    monkeypatch, api
):
    _patch_influx(monkeypatch, lambda flux: [])
    result = server.get_latency_stats(target="google_dns", hours=6)
    assert result["stats"] == [] and "No data points" in result["note"]


def test_latency_stats_dead_config_api_keeps_the_note(monkeypatch):
    class DeadAPI:
        def request(self, method, path, **kwargs):
            raise backends.ConfigAPIError("config-manager unreachable")

    monkeypatch.setattr(backends, "get_config_api", lambda: DeadAPI())
    _patch_influx(monkeypatch, lambda flux: [])
    result = server.get_latency_stats(target="Anything", hours=6)
    assert "error" not in result and "No data points" in result["note"]
