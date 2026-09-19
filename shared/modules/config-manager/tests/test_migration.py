"""Migration idempotency tests (sqlite-backed, no live PostgreSQL needed)."""

from pathlib import Path

import pytest
import yaml

from models import (
    DatabaseManager, Probe, SystemMetadata, Target, TargetRepository,
)
from scripts.migrate_yaml_to_db import run_migration

TARGETS = {
    "active_targets": {
        "top_sites": [
            {"name": "Google", "host": "google.com", "title": "Google",
             "probe": "FPing", "category": "top_sites"},
        ],
        "custom": [
            {"name": "MyHost", "host": "myhost.example", "title": "Mine",
             "probe": "FPing", "category": "custom"},
        ],
    },
    "metadata": {"version": "1.0"},
}

PROBES_V1 = {
    "probes": {
        "FPing": {"binary": "/usr/sbin/fping", "step": 300, "pings": 10},
        "DNS": {"binary": "/usr/bin/dig", "step": 300, "pings": 5},
    },
    "default_probe": "FPing",
}

PROBES_V2 = {
    "probes": {
        "FPing": {"binary": "/usr/sbin/fping", "step": 300, "pings": 10},
        "FPing6": {"binary": "/usr/sbin/fping", "step": 300, "pings": 10},
        "DNS": {"binary": "/usr/bin/dig", "step": 300, "pings": 5},
    },
    "default_probe": "FPing",
}

SOURCES = {
    "sources": {
        "topsites": {"display_name": "Top Sites", "enabled": True},
        "custom": {"display_name": "Custom", "enabled": True},
    }
}


@pytest.fixture()
def config_dir(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "targets.yaml").write_text(yaml.dump(TARGETS))
    (cfg / "probes.yaml").write_text(yaml.dump(PROBES_V1))
    (cfg / "sources.yaml").write_text(yaml.dump(SOURCES))
    return cfg


@pytest.fixture()
def db_url(tmp_path):
    return f"sqlite:///{tmp_path}/test.db"


def _session(db_url):
    return DatabaseManager(db_url).get_session()


def test_migration_populates_database(config_dir, db_url):
    assert run_migration(config_dir=config_dir, database_url=db_url) is True

    session = _session(db_url)
    try:
        assert session.query(Target).count() == 2
        probe_names = {p.name for p in session.query(Probe).all()}
        assert probe_names == {"FPing", "DNS"}
        markers = session.query(SystemMetadata).filter(
            SystemMetadata.key == "yaml_migration_completed"
        ).all()
        assert len(markers) == 1
    finally:
        session.close()


def test_migration_is_idempotent(config_dir, db_url):
    assert run_migration(config_dir=config_dir, database_url=db_url) is True
    assert run_migration(config_dir=config_dir, database_url=db_url) is True

    session = _session(db_url)
    try:
        # No duplicate targets/probes/markers on repeated runs
        assert session.query(Target).count() == 2
        assert session.query(Probe).count() == 2
        markers = session.query(SystemMetadata).filter(
            SystemMetadata.key == "yaml_migration_completed"
        ).all()
        assert len(markers) == 1
    finally:
        session.close()


def test_target_repository_field_allowlist(config_dir, db_url):
    """Mass-assignment of non-writable columns must be ignored."""
    assert run_migration(config_dir=config_dir, database_url=db_url) is True

    session = _session(db_url)
    try:
        repo = TargetRepository(session)
        target = repo.get_all()[0]
        original_id = target.id
        original_created = target.created_at

        updated = repo.update(target.id, {
            "title": "Updated Title",
            "id": 9999,                    # not writable
            "created_at": "1970-01-01",    # not writable
            "category": "hijacked",        # relationship, not writable
        })
        assert updated.title == "Updated Title"
        assert updated.id == original_id
        assert updated.created_at == original_created

        created = repo.create({
            "name": "Allowlisted",
            "host": "allow.example",
            "title": "Allowlisted",
            "category_id": target.category_id,
            "probe_id": target.probe_id,
            "id": 4242,                    # not writable -> ignored
            "bogus_field": "ignored",      # unknown -> ignored
        })
        assert created.name == "Allowlisted"
        assert created.id != 4242
    finally:
        session.close()


def test_missing_probes_upserted_after_migration(config_dir, db_url):
    """Existing deployments must pick up new probes (e.g. FPing6)."""
    assert run_migration(config_dir=config_dir, database_url=db_url) is True

    # probes.yaml gains FPing6 (as shipped in newer templates)
    (Path(config_dir) / "probes.yaml").write_text(yaml.dump(PROBES_V2))
    assert run_migration(config_dir=config_dir, database_url=db_url) is True

    session = _session(db_url)
    try:
        probe_names = {p.name for p in session.query(Probe).all()}
        assert "FPing6" in probe_names
        # Marker-present runs must not duplicate targets
        assert session.query(Target).count() == 2
    finally:
        session.close()


CURL_HTTP2 = {
    "module": "Curl",
    "binary": "/usr/local/bin/curl-h3",
    "step": 300,
    "pings": 5,
    "forks": 5,
    "urlformat": "https://%host%/",
    "extrare": "/;/",
    "extraargs": "--http2;-s;-o;/dev/null;-w;HTTPv=%{http_version}\\n",
    "expect": "HTTPv=2",
    "metadata": {"not": "a probe var"},
}


def test_probe_module_and_options_round_trip(config_dir, db_url):
    """A sub-probe's class and SmokePing variables survive YAML -> DB ->
    generator, and the generated Probes file nests it under its class."""
    probes = {"probes": dict(PROBES_V1["probes"], CurlHTTP2=CURL_HTTP2),
              "default_probe": "FPing"}
    (Path(config_dir) / "probes.yaml").write_text(yaml.dump(probes))
    assert run_migration(config_dir=config_dir, database_url=db_url) is True

    session = _session(db_url)
    try:
        row = session.query(Probe).filter(Probe.name == "CurlHTTP2").one()
        assert row.module == "Curl"
        assert row.options == {
            "urlformat": "https://%host%/",
            "extrare": "/;/",
            "extraargs": "--http2;-s;-o;/dev/null;-w;HTTPv=%{http_version}\\n",
            "expect": "HTTPv=2",
        }
        fping = session.query(Probe).filter(Probe.name == "FPing").one()
        assert fping.module is None and fping.options is None

        from scripts.config_generator import ConfigGenerator, probe_dict_from_row
        gen = ConfigGenerator()
        gen.probes_config = {
            "probes": {p.name: probe_dict_from_row(p)
                       for p in session.query(Probe).order_by(Probe.id)},
            "default_probe": "FPing",
        }
        content = gen.generate_probes_file()
    finally:
        session.close()

    assert "+ Curl\n" in content
    assert "++ CurlHTTP2\n" in content
    assert "expect = HTTPv=2" in content
    assert "module" not in content
    assert "metadata" not in content


def test_new_categories_are_seeded_after_migration(config_dir, db_url):
    """A category the DB has never seen is seeded from YAML on a
    marker-present run; categories it knows are left alone."""
    assert run_migration(config_dir=config_dir, database_url=db_url) is True

    targets = yaml.safe_load((Path(config_dir) / "targets.yaml").read_text())
    targets["active_targets"]["custom"].append(
        {"name": "LateCustom", "host": "late.example", "title": "Late",
         "probe": "FPing", "category": "custom"})
    targets["active_targets"]["tcp"] = [
        {"name": "Google_tcp443", "host": "www.google.com",
         "title": "Google TCP 443", "probe": "TCPPing", "category": "tcp"},
    ]
    (Path(config_dir) / "targets.yaml").write_text(yaml.dump(targets))
    probes = {"probes": dict(PROBES_V1["probes"], TCPPing={
        "binary": "/usr/bin/tcpping", "step": 300, "pings": 5, "port": 443}),
        "default_probe": "FPing"}
    (Path(config_dir) / "probes.yaml").write_text(yaml.dump(probes))
    assert run_migration(config_dir=config_dir, database_url=db_url) is True

    session = _session(db_url)
    try:
        names = {t.name for t in session.query(Target).all()}
        assert "Google_tcp443" in names          # new category: seeded
        assert "LateCustom" not in names         # known category: DB rules
        tcp = session.query(Target).filter(Target.name == "Google_tcp443").one()
        assert tcp.probe.name == "TCPPing"
        assert tcp.probe.options == {"port": 443}
    finally:
        session.close()


def test_existing_table_gains_new_probe_columns(config_dir, db_url):
    """create_tables() must add columns to a probes table created before
    they existed, instead of failing on the first SELECT."""
    from sqlalchemy import create_engine, inspect, text

    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE probes (id INTEGER PRIMARY KEY, name VARCHAR(50), "
            "binary_path VARCHAR(200), step_seconds INTEGER, pings INTEGER, "
            "forks INTEGER, is_default BOOLEAN, created_at DATETIME, "
            "updated_at DATETIME)"))
    engine.dispose()

    assert run_migration(config_dir=config_dir, database_url=db_url) is True
    cols = {c["name"] for c in inspect(create_engine(db_url)).get_columns("probes")}
    assert {"module", "options"} <= cols
