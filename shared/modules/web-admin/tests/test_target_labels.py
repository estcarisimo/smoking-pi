"""Service labels on the Targets page: columns, filter, CSV, the labels form.

config-manager is mocked at the gateway; nothing leaves the process.
"""

import pytest

from conftest import login

VOCABULARY = {"version": 1, "categories": [
    {"id": "video", "label": "Video streaming", "description": "Video?"},
    {"id": "web_search", "label": "Web search", "description": "Search?"},
    {"id": "dns", "label": "DNS", "description": "Lookups?"},
    {"id": "unknown", "label": "Unknown", "description": "No rule."},
]}
DB_TARGETS = {"targets": [
    {"id": 1, "name": "Google", "host": "google.com", "title": "Google",
     "category": "top_sites", "probe": "FPing", "is_active": True,
     "service": "Google", "categories": ["web_search"], "label_source": "manual",
     "label_confidence": "high", "label_rule": "name:Google", "label_note": None},
    {"id": 2, "name": "Mystery", "host": "example.org", "title": "Mystery box",
     "category": "custom", "probe": "FPing", "is_active": True,
     "service": "Mystery box", "categories": ["unknown"], "label_source": None,
     "label_confidence": None, "label_rule": None, "label_note": None},
]}


@pytest.fixture()
def targets_module(monkeypatch):
    from app.routes import targets as module
    gw = module.config_api
    monkeypatch.setattr(gw, "is_database_available", lambda: True)
    monkeypatch.setattr(gw, "get_service_status", lambda: {"status": "ok"})
    monkeypatch.setattr(gw, "get_all_targets_from_db", lambda: DB_TARGETS)
    monkeypatch.setattr(gw, "get_probes_from_db",
                        lambda: {"probes": [{"id": 1, "name": "FPing"}]})
    monkeypatch.setattr(gw, "get_taxonomy", lambda: VOCABULARY)
    return module


def test_the_list_shows_service_columns_and_filter(client, targets_module):
    login(client)
    html = client.get("/targets/").get_data(as_text=True)
    assert "<th>Service</th>" in html and "<th>Service categories</th>" in html
    assert 'id="service-category-filter"' in html
    # Only the categories in use are offered, in vocabulary order.
    assert '<option value="web_search">Web search</option>' in html
    assert '<option value="video">' not in html
    assert 'data-service-categories="web_search"' in html
    assert 'data-label-rule="name:Google"' in html
    # The labels form offers every assignable category, never unknown.
    assert 'id="labels-cat-video"' in html and 'id="labels-cat-unknown"' not in html
    assert "service_categories" in html  # the CSV header


def test_yaml_mode_merges_labels_by_name(client, monkeypatch):
    from app.routes import targets as module
    gw = module.config_api
    monkeypatch.setattr(gw, "is_database_available", lambda: False)
    monkeypatch.setattr(gw, "get_service_status", lambda: {"status": "ok"})
    monkeypatch.setattr(gw, "get_targets_config", lambda: {"active_targets": {
        "custom": [{"name": "uba_ar", "host": "uba.ar", "title": "uba.ar",
                    "probe": "FPing", "category": "custom"}]}})
    monkeypatch.setattr(gw, "get_taxonomy", lambda: {"categories": [
        {"id": "academic", "label": "Academic and research", "description": "x"}]})
    monkeypatch.setattr(gw, "get_taxonomy_targets", lambda: {"targets": [
        {"name": "uba_ar", "service": "Universidad de Buenos Aires",
         "categories": ["academic"], "label_source": "manual",
         "label_rule": "name:uba_ar"}]})
    login(client)
    html = client.get("/targets/").get_data(as_text=True)
    assert "Universidad de Buenos Aires" in html
    assert ">Academic and research</span>" in html


def test_an_unreachable_taxonomy_leaves_the_page_working(client, targets_module,
                                                         monkeypatch):
    def boom():
        raise ConnectionError("Cannot connect to config manager")
    monkeypatch.setattr(targets_module.config_api, "get_taxonomy", boom)
    login(client)
    r = client.get("/targets/")
    assert r.status_code == 200
    assert 'id="labels-service"' not in r.get_data(as_text=True)


def test_saving_labels_goes_through_the_gateway(client, targets_module, monkeypatch):
    calls = {}

    def fake(target_id, labels):
        calls[target_id] = labels
        return {"success": True, "target": {"id": target_id, "service": "Plex"}}
    monkeypatch.setattr(targets_module.config_api, "set_target_labels", fake)
    login(client)
    r = client.put("/targets/2/labels", json={
        "service": "Plex", "categories": ["video"], "note": "home", "id": 9})
    assert r.status_code == 200
    assert r.get_json()["success"] is True
    assert calls == {2: {"service": "Plex", "categories": ["video"], "note": "home"}}


def test_a_refusal_passes_the_reasons_on(client, targets_module, monkeypatch):
    monkeypatch.setattr(targets_module.config_api, "set_target_labels",
                        lambda tid, labels: {"refused": "invalid",
                                             "reasons": ["service is required"]})
    login(client)
    r = client.put("/targets/2/labels", json={"service": "", "categories": []})
    assert r.status_code == 400
    body = r.get_json()
    assert body["error"] == "Invalid labels" and body["error_id"]
    assert body["reasons"] == ["service is required"]


def test_a_missing_target_is_404_and_a_failure_hides_its_detail(
        client, targets_module, monkeypatch):
    def missing(tid, labels):
        raise ValueError("Target not found")
    monkeypatch.setattr(targets_module.config_api, "set_target_labels", missing)
    login(client)
    assert client.put("/targets/9/labels",
                      json={"service": "S", "categories": ["video"]}).status_code == 404

    def broken(tid):
        raise RuntimeError("postgresql://smokeping:secret@postgres")
    monkeypatch.setattr(targets_module.config_api, "reset_target_labels", broken)
    r = client.delete("/targets/9/labels")
    assert r.status_code == 500
    assert "secret" not in r.get_data(as_text=True)


def test_reset_goes_through_the_gateway(client, targets_module, monkeypatch):
    calls = []
    monkeypatch.setattr(targets_module.config_api, "reset_target_labels",
                        lambda tid: calls.append(tid) or {"success": True, "target": {}})
    login(client)
    r = client.delete("/targets/1/labels")
    assert r.status_code == 200 and calls == [1]


def test_labels_need_database_mode(client, targets_module, monkeypatch):
    monkeypatch.setattr(targets_module.config_api, "is_database_available",
                        lambda: False)
    login(client)
    assert client.put("/targets/1/labels",
                      json={"service": "S", "categories": ["video"]}).status_code == 400
    assert client.delete("/targets/1/labels").status_code == 400


def test_the_client_maps_config_manager_answers(monkeypatch):
    from app.services.config_api import ConfigManagerClient

    class Resp:
        def __init__(self, status, body):
            self.status_code, self._body = status, body

        def json(self):
            return self._body

    client = ConfigManagerClient("http://config-manager.invalid:5000")
    answers = iter([
        Resp(200, {"success": True}),
        Resp(400, {"error": "Invalid labels", "reasons": ["service is required", 3]}),
        Resp(501, {"error": "YAML mode"}),
        Resp(404, {}),
    ])
    seen = []
    monkeypatch.setattr(client, "_make_request",
                        lambda method, path, **kw: seen.append((method, path, kw))
                        or next(answers))
    assert client.set_target_labels(4, {"service": "S"}) == {"success": True}
    assert seen[0] == ("PUT", "/targets/4/labels", {"json": {"service": "S"}})
    assert client.set_target_labels(4, {}) == {"refused": "invalid",
                                               "reasons": ["service is required"]}
    assert client.reset_target_labels(4)["refused"] == "yaml_mode"
    with pytest.raises(ValueError):
        client.reset_target_labels(4)
