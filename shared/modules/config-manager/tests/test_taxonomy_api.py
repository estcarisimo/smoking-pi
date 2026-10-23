"""The taxonomy endpoints and the label fields on targets (sqlite-backed)."""

import pytest
import yaml

import api as api_module
from models import DatabaseManager, TargetLabel
from scripts.migrate_yaml_to_db import run_migration

TARGETS = {
    "active_targets": {
        "top_sites": [
            {"name": "Google", "host": "google.com", "title": "Google",
             "probe": "FPing", "category": "top_sites"},
            {"name": "Mystery", "host": "example.org", "title": "Mystery box",
             "probe": "FPing", "category": "top_sites"},
        ],
    },
    "metadata": {"version": "1.0"},
}
PROBES = {
    "probes": {"FPing": {"binary": "/usr/sbin/fping", "step": 300, "pings": 10}},
    "default_probe": "FPing",
}
SOURCES = {"sources": {"topsites": {"display_name": "Top Sites", "enabled": True}}}


def _write_config(cfg):
    cfg.mkdir()
    for name, body in (("targets", TARGETS), ("probes", PROBES), ("sources", SOURCES)):
        (cfg / f"{name}.yaml").write_text(yaml.dump(body))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    _write_config(cfg)
    url = f"sqlite:///{tmp_path}/test.db"
    assert run_migration(config_dir=cfg, database_url=url) is True
    manager = DatabaseManager(url)
    monkeypatch.setattr(api_module, "get_db_session", manager.get_session)
    monkeypatch.delenv("CONFIG_API_TOKEN", raising=False)
    monkeypatch.setattr(api_module.api, "_check_database_available", lambda: True)
    api_module.api.refresh_database_mode()
    reloads = []
    monkeypatch.setattr(api_module.api, "_regenerate_smokeping_config",
                        lambda: reloads.append(True) or True)
    api_module.app.config["TESTING"] = True
    with api_module.app.test_client() as c:
        c.session = manager.get_session
        c.reloads = reloads
        yield c
    api_module.api.refresh_database_mode()


@pytest.fixture()
def yaml_client(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    _write_config(cfg)
    monkeypatch.setattr(api_module, "CONFIG_DIR", cfg)
    monkeypatch.delenv("CONFIG_API_TOKEN", raising=False)
    monkeypatch.setattr(api_module.api, "_check_database_available", lambda: False)
    api_module.api.refresh_database_mode()
    api_module.app.config["TESTING"] = True
    with api_module.app.test_client() as c:
        yield c
    api_module.api.refresh_database_mode()


def _target(client, name):
    body = client.get("/targets").get_json()
    return next(t for t in body["targets"] if t["name"] == name)


def test_get_taxonomy_serves_the_vocabulary(client):
    body = client.get("/taxonomy").get_json()
    assert body["version"] == 2
    ids = [c["id"] for c in body["categories"]]
    assert "video" in ids and ids[-1] == "unknown"
    assert all(c["label"] and c["description"] for c in body["categories"])


def test_targets_carry_their_resolved_label(client):
    google = _target(client, "Google")
    assert google["service"] == "Google"
    assert google["categories"] == ["web_search"]
    assert google["label_source"] == "manual"
    assert google["label_confidence"] == "medium"
    assert google["label_rule"] == "host_suffix:google.com"
    assert google["category"] == "top_sites"  # the technical bucket is untouched


def test_unknown_lists_only_the_unlabeled(client):
    body = client.get("/taxonomy/unknown").get_json()
    assert [t["name"] for t in body["targets"]] == ["Mystery"]
    assert body["targets"][0]["service"] == "Mystery box"


def test_an_override_replaces_the_label_and_reset_restores_it(client):
    tid = _target(client, "Mystery")["id"]
    r = client.put(f"/targets/{tid}/labels",
                   json={"service": "Plex", "categories": ["video"], "note": "home"})
    assert r.status_code == 200, r.get_json()
    t = r.get_json()["target"]
    assert (t["service"], t["categories"], t["label_source"], t["label_rule"],
            t["label_note"]) == ("Plex", ["video"], "manual", "override", "home")
    assert _target(client, "Mystery")["service"] == "Plex"
    assert client.get("/taxonomy/unknown").get_json()["targets"] == []
    assert client.reloads == []  # a label never touches SmokePing

    r = client.delete(f"/targets/{tid}/labels")
    assert r.status_code == 200
    assert r.get_json()["removed"] is True
    assert r.get_json()["target"]["categories"] == ["unknown"]
    assert client.delete(f"/targets/{tid}/labels").get_json()["removed"] is False


def test_a_bad_override_is_refused_with_literal_reasons(client):
    tid = _target(client, "Google")["id"]
    r = client.put(f"/targets/{tid}/labels", json={"service": "", "categories": ["news"]})
    assert r.status_code == 400
    body = r.get_json()
    assert body["error"] == "Invalid labels" and body["error_id"]
    assert body["reasons"] == [
        "service is required",
        "categories must come from GET /taxonomy (and cannot be unknown)"]


def test_a_missing_target_is_404(client):
    r = client.put("/targets/9999/labels", json={"service": "S", "categories": ["video"]})
    assert r.status_code == 404
    assert client.delete("/targets/9999/labels").status_code == 404


def test_put_targets_does_not_write_labels(client):
    tid = _target(client, "Google")["id"]
    client.put(f"/targets/{tid}", json={"service": "Evil", "categories": ["video"]})
    assert _target(client, "Google")["service"] == "Google"


def test_the_override_follows_a_rename_and_goes_with_a_delete(client):
    tid = _target(client, "Mystery")["id"]
    client.put(f"/targets/{tid}/labels", json={"service": "Plex", "categories": ["video"]})
    r = client.put(f"/targets/{tid}", json={"name": "Plex"})
    assert r.status_code == 200, r.get_json()
    assert _target(client, "Plex")["label_rule"] == "override"
    assert client.delete(f"/targets/{tid}").status_code == 200
    session = client.session()
    assert session.query(TargetLabel).count() == 0
    session.close()


def test_yaml_mode_reads_work_and_writes_are_501(yaml_client):
    assert yaml_client.get("/taxonomy").status_code == 200
    targets = yaml_client.get("/taxonomy/targets").get_json()["targets"]
    assert {t["name"]: t["categories"] for t in targets} == {
        "Google": ["web_search"], "Mystery": ["unknown"]}
    assert [t["name"] for t in
            yaml_client.get("/taxonomy/unknown").get_json()["targets"]] == ["Mystery"]
    for r in (yaml_client.put("/targets/1/labels",
                              json={"service": "S", "categories": ["video"]}),
              yaml_client.delete("/targets/1/labels")):
        assert r.status_code == 501
        assert r.get_json()["error"] == api_module.LABELS_NEED_DATABASE


def test_a_rename_onto_a_labeled_name_drops_the_old_label(client):
    session = client.session()
    session.add(TargetLabel(target_name="Plex", service="Old Plex", categories=["video"]))
    session.commit()
    session.close()
    tid = _target(client, "Mystery")["id"]
    client.put(f"/targets/{tid}/labels", json={"service": "Mine", "categories": ["audio"]})
    assert client.put(f"/targets/{tid}", json={"name": "Plex"}).status_code == 200
    assert _target(client, "Plex")["service"] == "Old Plex"
    session = client.session()
    assert [r.target_name for r in session.query(TargetLabel)] == ["Plex"]
    session.close()
