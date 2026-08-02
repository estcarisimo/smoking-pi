"""Unit tests for rrd2influx.py — pure unit, no rrdtool or InfluxDB needed."""

import json
import pathlib
import subprocess
import sys

import pytest

MODULE_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

import rrd2influx  # noqa: E402

GRAFANA_DASHBOARDS = (
    MODULE_DIR.parents[1] / "modules" / "grafana" / "provisioning"
)


# ───────────────────────── classification ─────────────────────────
class TestCategoryMapping:
    @pytest.mark.parametrize(
        ("subdir", "expected"),
        [
            ("websites", "topsites"),
            ("Netflix", "netflix"),
            ("DNS_Resolvers", "dns"),
            ("Custom", "custom"),
            # legacy directory names must keep working
            ("TopSites", "topsites"),
            ("resolvers", "dns"),
            # unmapped directory
            ("Weird", "unknown"),
        ],
    )
    def test_directory_to_category(self, subdir, expected):
        rrd = f"/var/lib/smokeping/{subdir}/Target.rrd"
        assert rrd2influx.category_for(rrd, "/var/lib/smokeping") == expected

    def test_rrd_at_root_is_unknown(self):
        rrd = "/var/lib/smokeping/Target.rrd"
        assert rrd2influx.category_for(rrd, "/var/lib/smokeping") == "unknown"

    def test_measurement_selection(self):
        base = "/var/lib/smokeping"
        assert rrd2influx.measurement_for(f"{base}/DNS_Resolvers/Google.rrd", base) == "dns_latency"
        assert rrd2influx.measurement_for(f"{base}/resolvers/Google.rrd", base) == "dns_latency"
        assert rrd2influx.measurement_for(f"{base}/websites/Google.rrd", base) == "latency"

    def test_probe_type(self):
        base = "/var/lib/smokeping"
        assert rrd2influx.probe_type_for(f"{base}/DNS_Resolvers/Google.rrd", base) == "dns"
        assert rrd2influx.probe_type_for(f"{base}/websites/Google6.rrd", base) == "fping6"
        assert rrd2influx.probe_type_for(f"{base}/websites/Google.rrd", base) == "fping"
        # '6' in the middle of a name must NOT trigger fping6 any more
        assert rrd2influx.probe_type_for(f"{base}/websites/S6Site.rrd", base) == "fping"


# ───────────────────────── loss conversion ─────────────────────────
class TestLossRatio:
    def test_zero_loss(self):
        assert rrd2influx.loss_to_ratio(0.0, 20) == 0.0

    def test_full_loss(self):
        assert rrd2influx.loss_to_ratio(20.0, 20) == 1.0

    def test_partial_loss(self):
        assert rrd2influx.loss_to_ratio(5.0, 20) == pytest.approx(0.25)

    def test_none_passthrough(self):
        assert rrd2influx.loss_to_ratio(None, 20) is None

    def test_bad_ping_count(self):
        assert rrd2influx.loss_to_ratio(1.0, 0) is None

    def test_clamped_to_unit_interval(self):
        assert rrd2influx.loss_to_ratio(25.0, 20) == 1.0
        assert rrd2influx.loss_to_ratio(-1.0, 20) == 0.0


# ───────────────────────── per-RRD ping count ─────────────────────────
class TestPingsFromDsNames:
    def test_counts_ping_sources(self):
        ds = ["uptime", "loss", "median"] + [f"ping{i}" for i in range(1, 11)]
        assert rrd2influx.pings_from_ds_names(ds, 20) == 10

    def test_dns_probe_count(self):
        ds = ["uptime", "loss", "median", "ping1", "ping2", "ping3", "ping4", "ping5"]
        assert rrd2influx.pings_from_ds_names(ds, 20) == 5

    def test_falls_back_without_ping_sources(self):
        assert rrd2influx.pings_from_ds_names(["uptime", "loss", "median"], 20) == 20
        assert rrd2influx.pings_from_ds_names([], 7) == 7

    def test_ignores_lookalike_sources(self):
        ds = ["ping", "pings", "ping1x", "1ping", "ping1", "ping2"]
        assert rrd2influx.pings_from_ds_names(ds, 20) == 2

    def test_a_fully_lost_cycle_reads_as_total_loss(self):
        # The bug this guards: a 10-ping probe losing all 10 must be 1.0,
        # not 10/20 = 0.5.
        ds = ["loss", "median"] + [f"ping{i}" for i in range(1, 11)]
        pings = rrd2influx.pings_from_ds_names(ds, 20)
        assert rrd2influx.loss_to_ratio(10.0, pings) == 1.0


# ───────────────────────── state file ─────────────────────────
class TestStateFile:
    def test_round_trip(self, tmp_path):
        path = str(tmp_path / "state.json")
        state = {"/var/lib/smokeping/websites/Google.rrd": 1690000000}
        rrd2influx.save_state(path, state)
        assert rrd2influx.load_state(path) == state

    def test_atomic_write_leaves_no_temp_file(self, tmp_path):
        path = str(tmp_path / "state.json")
        rrd2influx.save_state(path, {"a": 1})
        assert not (tmp_path / "state.json.tmp").exists()
        assert (tmp_path / "state.json").exists()

    def test_missing_file_returns_empty(self, tmp_path):
        assert rrd2influx.load_state(str(tmp_path / "nope.json")) == {}

    def test_corrupt_file_returns_empty(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{not json")
        assert rrd2influx.load_state(str(path)) == {}

    def test_wrong_shape_returns_empty(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("[1, 2, 3]")
        assert rrd2influx.load_state(str(path)) == {}


# ───────────────────────── fetch parsing ─────────────────────────
SAMPLE_FETCH = """\
                          median             loss            ping1            ping2

1690000000: 1.2340000000e-02 0.0000000000e+00 1.1000000000e-02 1.3000000000e-02
1690000300: 2.0000000000e-02 2.0000000000e+00 nan 2.1000000000e-02
1690000600: nan nan nan nan
"""


class TestParseFetchOutput:
    def test_ds_names(self):
        ds_names, _ = rrd2influx.parse_fetch_output(SAMPLE_FETCH)
        assert ds_names == ["median", "loss", "ping1", "ping2"]

    def test_rows_and_values(self):
        _, rows = rrd2influx.parse_fetch_output(SAMPLE_FETCH)
        assert [ts for ts, _ in rows] == [1690000000, 1690000300, 1690000600]
        first = rows[0][1]
        assert first["median"] == pytest.approx(0.01234)
        assert first["loss"] == 0.0

    def test_nan_becomes_none(self):
        _, rows = rrd2influx.parse_fetch_output(SAMPLE_FETCH)
        second = rows[1][1]
        assert second["ping1"] is None
        assert second["loss"] == 2.0
        third = rows[2][1]
        assert all(v is None for v in third.values())

    def test_empty_output(self):
        ds_names, rows = rrd2influx.parse_fetch_output("")
        assert ds_names == []
        assert rows == []

    def test_fetch_rows_uses_subprocess(self, monkeypatch):
        calls = {}

        def fake_run(cmd, capture_output, text, check):
            calls["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout=SAMPLE_FETCH, stderr="")

        monkeypatch.setattr(rrd2influx.subprocess, "run", fake_run)
        ds_names, rows = rrd2influx.fetch_rows("/x/y.rrd", 1690000000, 1690000600)
        assert calls["cmd"] == [
            "rrdtool", "fetch", "/x/y.rrd", "AVERAGE",
            "--start", "1690000000", "--end", "1690000600",
        ]
        assert len(rows) == 3


# ───────────────────────── point building ─────────────────────────
class TestBuildPoints:
    def test_loss_converted_and_all_nan_rows_skipped(self):
        base = "/var/lib/smokeping"
        _, rows = rrd2influx.parse_fetch_output(SAMPLE_FETCH)
        points, last_ts = rrd2influx.build_points(
            f"{base}/websites/Google.rrd", rows, base, pings=20)
        # third row is all-NaN → skipped
        assert len(points) == 2
        assert last_ts == 1690000300
        line = points[1].to_line_protocol()
        assert "loss=0.1" in line          # 2 lost of 20 → 0.1 ratio
        assert 'target=Google' in line
        assert 'category=topsites' in line
        assert line.startswith("latency,")

    def test_timestamps_come_from_rows(self):
        base = "/var/lib/smokeping"
        _, rows = rrd2influx.parse_fetch_output(SAMPLE_FETCH)
        points, _ = rrd2influx.build_points(
            f"{base}/websites/Google.rrd", rows, base, pings=20)
        assert points[0].to_line_protocol().endswith("1690000000")


# ───────────────────────── dashboards ─────────────────────────
class TestDashboardsAreValidJson:
    def test_all_dashboard_json_files_parse(self):
        files = sorted(GRAFANA_DASHBOARDS.glob("dashboards/**/*.json"))
        assert len(files) >= 9, f"expected >= 9 dashboards, found {len(files)}"
        for path in files:
            with open(path) as fh:
                dashboard = json.load(fh)
            assert dashboard.get("uid"), f"{path.name} has no uid"

    def test_all_influx_dashboards_carry_smokeping_tag_and_links(self):
        files = sorted(GRAFANA_DASHBOARDS.glob("dashboards/**/*.json"))
        for path in files:
            with open(path) as fh:
                dashboard = json.load(fh)
            assert "smokeping" in dashboard.get("tags", []), path.name
            link_tags = [t for link in dashboard.get("links", [])
                         for t in link.get("tags", [])]
            assert "smokeping" in link_tags, f"{path.name} missing dashboards link"
