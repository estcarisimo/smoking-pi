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

    def test_http_and_tcp_directories(self):
        assert ch.measurement_type_for(BASE / "HTTP/Google_h3.rrd", BASE) == "http_latency"
        assert ch.measurement_type_for(BASE / "TCP/Google_tcp443.rrd", BASE) == "tcp_latency"

    def test_everything_else_is_latency(self):
        assert ch.measurement_type_for(BASE / "websites/Google.rrd", BASE) == "latency"
        assert ch.measurement_type_for(BASE / "Netflix/oca1.rrd", BASE) == "latency"


class TestCategory:
    def test_current_directory_names(self):
        assert ch.category_for(BASE / "websites/Google.rrd", BASE) == "topsites"
        assert ch.category_for(BASE / "Netflix/oca1.rrd", BASE) == "netflix"
        assert ch.category_for(BASE / "DNS_Resolvers/Google.rrd", BASE) == "dns"
        assert ch.category_for(BASE / "Custom/Thing.rrd", BASE) == "custom"
        assert ch.category_for(BASE / "HTTP/Google_h1.rrd", BASE) == "http"
        assert ch.category_for(BASE / "TCP/Google_tcp443.rrd", BASE) == "tcp"

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
                     "DNS_Resolvers/G.rrd", "Custom/C.rrd", "odd/X.rrd",
                     "HTTP/G_h2.rrd", "TCP/G_tcp443.rrd"):
            assert ch.category_for(BASE / path, BASE) == \
                rrd2influx.category_for(str(BASE / path), str(BASE))
            assert ch.measurement_type_for(BASE / path, BASE) == \
                rrd2influx.measurement_for(str(BASE / path), str(BASE))


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


class _FakeClient:
    """Records the statements ensure_schema runs and the database it selects."""

    def __init__(self):
        self.commands = []
        self.database = None

    def command(self, sql):
        self.commands.append(" ".join(sql.split()))


class TestEnsureSchemaSelectsDatabase:
    """The client connects without a database so the schema can be created
    first; ensure_schema must then select it, or every unqualified insert
    goes to `default.latency`, which does not exist (the bug behind
    "Table default.latency does not exist" on a fresh ClickHouse)."""

    def _exporter(self, db, tmp_path):
        settings = ch.ExporterSettings(
            clickhouse_password="x", clickhouse_db=db, rrd_dir=tmp_path
        )
        exporter = ch.ClickHouseExporter.__new__(ch.ClickHouseExporter)
        exporter.settings = settings
        return exporter

    def test_selects_configured_database_after_creating_it(self, tmp_path):
        client = _FakeClient()
        self._exporter("smokeping", tmp_path).ensure_schema(client)
        assert client.commands[0] == "CREATE DATABASE IF NOT EXISTS smokeping"
        assert "CREATE TABLE IF NOT EXISTS smokeping.latency" in client.commands[1]
        assert client.database == "smokeping"

    def test_database_name_is_not_hardcoded(self, tmp_path):
        client = _FakeClient()
        self._exporter("pings", tmp_path).ensure_schema(client)
        assert "pings.latency" in client.commands[1]
        assert client.database == "pings"


class TestInsertColumnsMatchSchema:
    """Every column the exporter inserts must be declared by the table it
    creates; one stray name fails the whole batch (the `rrd_file` bug)."""

    def test_every_insert_column_is_in_create_table(self, tmp_path):
        client = _FakeClient()
        settings = ch.ExporterSettings(clickhouse_password="x", rrd_dir=tmp_path)
        exporter = ch.ClickHouseExporter.__new__(ch.ClickHouseExporter)
        exporter.settings = settings
        exporter.ensure_schema(client)
        create = client.commands[1]
        body = create[create.index("(") + 1 : create.rindex(") ENGINE")]
        declared = {line.strip().split()[0] for line in body.split(",") if line.strip()}
        missing = set(ch.INSERT_COLUMNS) - declared
        assert not missing, f"inserted but not in schema: {sorted(missing)}"

    def test_insert_columns_are_data_point_fields(self):
        assert set(ch.INSERT_COLUMNS) <= set(ch.RRDDataPoint.model_fields)
        assert "rrd_file" not in ch.INSERT_COLUMNS
