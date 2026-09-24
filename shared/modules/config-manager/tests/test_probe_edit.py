"""PUT /probes/<name>: change a probe's step and pings (sqlite-backed)."""

import pytest
import yaml

import api as api_module
from models import DatabaseManager, Probe
from scripts.migrate_yaml_to_db import run_migration

TARGETS = {
    "active_targets": {
        "top_sites": [
            {"name": "Google", "host": "google.com", "title": "Google",
             "probe": "FPing", "category": "top_sites"},
            {"name": "NYT", "host": "nytimes.com", "title": "NYT",
             "probe": "FPing", "category": "top_sites"},
        ],
    },
    "metadata": {"version": "1.0"},
}
PROBES = {
    "probes": {
        "FPing": {"binary": "/usr/sbin/fping", "step": 300, "pings": 10},
        "CurlHTTP2": {"binary": "/usr/local/bin/curl-h3", "step": 300,
                      "pings": 5, "module": "Curl", "timeout": 10},
    },
    "default_probe": "FPing",
}
SOURCES = {"sources": {"topsites": {"display_name": "Top Sites", "enabled": True}}}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir()
    for name, body in (("targets", TARGETS), ("probes", PROBES), ("sources", SOURCES)):
        (cfg / f"{name}.yaml").write_text(yaml.dump(body))
    url = f"sqlite:///{tmp_path}/test.db"
    assert run_migration(config_dir=cfg, database_url=url) is True
    manager = DatabaseManager(url)
    monkeypatch.setattr(api_module, "get_db_session", manager.get_session)
    monkeypatch.delenv("CONFIG_API_TOKEN", raising=False)
    monkeypatch.setattr(api_module.api, "_check_database_available", lambda: True)
    api_module.api.refresh_database_mode()
    reloads = []

    def regenerate():
        reloads.append(True)
        api_module.api.last_rrd_guard = {"ran": True, "checked": 2, "errors": [],
                                         "archived": [{"rrd": "websites/Google.rrd"}]}
        return True

    monkeypatch.setattr(api_module.api, "_regenerate_smokeping_config", regenerate)
    api_module.app.config["TESTING"] = True
    with api_module.app.test_client() as c:
        c.reloads = reloads
        c.session = manager.get_session
        yield c
    api_module.api.refresh_database_mode()


def test_a_step_change_is_saved_regenerated_and_reported(client):
    r = client.put("/probes/FPing", json={"step_seconds": 60})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["changed"] is True
    assert body["previous"] == {"step_seconds": 300, "pings": 10}
    assert body["current"] == {"step_seconds": 60, "pings": 10}
    assert body["targets"] == 2 and body["active_targets"] == 2
    assert body["rrd_guard"]["archived"] == [{"rrd": "websites/Google.rrd"}]
    assert client.reloads == [True]
    session = client.session()
    assert session.query(Probe).filter_by(name="FPing").one().step_seconds == 60
    session.close()


def test_no_change_does_not_reload(client):
    r = client.put("/probes/FPing", json={"step_seconds": 300, "pings": 10})
    assert r.get_json()["changed"] is False
    assert client.reloads == []


@pytest.mark.parametrize("body, needle", [
    ({"step_seconds": 45}, "step_seconds must be one of"),
    ({"step_seconds": "300"}, "step_seconds must be one of"),
    ({"pings": 2}, "pings must be between 3 and 20"),
    ({"pings": 21}, "pings must be between 3 and 20"),
    ({"pings": True}, "pings must be between"),
    ({}, "Send step_seconds and/or pings"),
    ({"binary_path": "/bin/sh", "pings": 5}, "Only step_seconds and pings"),
])
def test_bad_requests_are_refused_without_a_reload(client, body, needle):
    r = client.put("/probes/FPing", json=body)
    assert r.status_code == 400
    assert needle in r.get_json()["error"]
    assert client.reloads == []


def test_a_cycle_that_could_outrun_the_step_is_refused(client):
    # 5 fetches x 10 s timeout = 50 s: fine on 60 s, not with 10 pings.
    assert client.put("/probes/CurlHTTP2", json={"step_seconds": 60}).status_code == 200
    r = client.put("/probes/CurlHTTP2", json={"pings": 10})
    assert r.status_code == 400
    assert "can take up to 100 s when they time out, longer than a 60 s step" in (
        r.get_json()["error"])


def test_the_outrun_rule_uses_smokepings_defaults_and_batches():
    from types import SimpleNamespace as P
    fping = P(name="FPing", module=None, options=None, forks=None)
    dns = P(name="DNS", module=None, options=None, forks=5)
    curl = P(name="CurlHTTP1", module="Curl", options={"timeout": 10}, forks=5)
    # fping: every target at once, 1 s between packets.
    assert api_module.probe_worst_seconds(fping, 20, 30) == 20
    # dig: 5 s per query, 5 targets at a time -- 12 resolvers are 3 batches.
    assert api_module.probe_worst_seconds(dns, 5, 3) == 25
    assert api_module.probe_worst_seconds(dns, 5, 12) == 75
    assert api_module.probe_worst_seconds(curl, 5, 4) == 50
    assert api_module.probe_cadence_problem(60, 5, lambda n: 75.0) is not None
    assert api_module.probe_cadence_problem(120, 5, lambda n: 75.0) is None


def test_a_failed_regeneration_keeps_the_previous_cycle(client, monkeypatch):
    def boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(api_module.api, "_regenerate_smokeping_config", boom)
    r = client.put("/probes/FPing", json={"step_seconds": 60})
    assert r.status_code == 500
    assert "keeps its previous cycle" in r.get_json()["error"]
    session = client.session()
    assert session.query(Probe).filter_by(name="FPing").one().step_seconds == 300
    session.close()


def test_an_unknown_probe_is_404(client):
    assert client.put("/probes/Nope", json={"pings": 5}).status_code == 404


def test_get_probes_counts_targets(client):
    probes = {p["name"]: p for p in client.get("/probes").get_json()["probes"]}
    assert probes["FPing"]["targets"] == 2 and probes["FPing"]["active_targets"] == 2
    assert probes["CurlHTTP2"]["targets"] == 0
