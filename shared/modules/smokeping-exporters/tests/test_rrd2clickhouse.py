"""ClickHouse exporter: RRD classification and loss conversion.

These mirror the InfluxDB exporter's guarantees so both backends describe the
same RRD identically.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("clickhouse_connect", reason="ClickHouse exporter deps not installed")

import rrd2clickhouse as ch  # noqa: E402

BASE = Path("/var/lib/smokeping")


class TestMeasurementType:
    def test_dns_directories(self):
        assert ch.measurement_type_for(BASE / "DNS_Resolvers/Google.rrd", BASE) == "dns_latency"
        assert ch.measurement_type_for(BASE / "resolvers/Quad9.rrd", BASE) == "dns_latency"

    def test_everything_else_is_latency(self):
        assert ch.measurement_type_for(BASE / "websites/Google.rrd", BASE) == "latency"
        assert ch.measurement_type_for(BASE / "Netflix/oca1.rrd", BASE) == "latency"


class TestCategory:
    def test_current_directory_names(self):
        assert ch.category_for(BASE / "websites/Google.rrd", BASE) == "topsites"
        assert ch.category_for(BASE / "Netflix/oca1.rrd", BASE) == "netflix"
        assert ch.category_for(BASE / "DNS_Resolvers/Google.rrd", BASE) == "dns"
        assert ch.category_for(BASE / "Custom/Thing.rrd", BASE) == "custom"

    def test_legacy_directory_names(self):
        assert ch.category_for(BASE / "TopSites/Google.rrd", BASE) == "topsites"
        assert ch.category_for(BASE / "resolvers/Google.rrd", BASE) == "dns"

    def test_unmapped_directory(self):
        assert ch.category_for(BASE / "whatever/Google.rrd", BASE) == "unknown"

    def test_uses_top_level_not_immediate_parent(self):
        # The old code took path_parts[-2], which for a nested tree returned
        # the leaf directory instead of the section.
        assert ch.category_for(BASE / "websites/nested/Google.rrd", BASE) == "topsites"

    def test_matches_influx_exporter(self):
        import rrd2influx
        for path in ("websites/Google.rrd", "Netflix/oca.rrd",
                     "DNS_Resolvers/G.rrd", "Custom/C.rrd", "odd/X.rrd"):
            assert ch.category_for(BASE / path, BASE) == \
                rrd2influx.category_for(str(BASE / path), str(BASE))


class TestPingCount:
    def test_counts_ping_sources(self):
        ds = ["loss", "median"] + [f"ping{i}" for i in range(1, 11)]
        assert ch.pings_from_ds_names(ds) == 10

    def test_dns_probe(self):
        ds = ["loss", "median"] + [f"ping{i}" for i in range(1, 6)]
        assert ch.pings_from_ds_names(ds) == 5

    def test_fallback_without_ping_sources(self):
        assert ch.pings_from_ds_names(["loss", "median"]) == 20


class TestLossToPercent:
    def test_no_loss(self):
        assert ch.loss_to_percent(0.0, 10) == 0.0

    def test_total_loss_is_100_not_1000(self):
        # The bug this guards: the old code did `value * 100`, turning a
        # fully-lost 10-ping cycle into 1000%.
        assert ch.loss_to_percent(10.0, 10) == 100.0

    def test_half_loss(self):
        assert ch.loss_to_percent(5.0, 10) == 50.0

    def test_dns_probe_scale(self):
        assert ch.loss_to_percent(5.0, 5) == 100.0

    def test_none_passthrough(self):
        assert ch.loss_to_percent(None, 10) is None

    def test_bad_ping_count(self):
        assert ch.loss_to_percent(1.0, 0) is None

    def test_clamped(self):
        assert ch.loss_to_percent(25.0, 10) == 100.0
        assert ch.loss_to_percent(-1.0, 10) == 0.0
