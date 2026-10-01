"""The dashboard's Measurements card, and target changes that stop claiming
SmokePing picked them up when config-manager says it did not."""

from conftest import login

from app.routes import dashboard as dashboard_module
from app.routes import targets as targets_module
from app.services import config_api as config_api_module

BODY = {
    "available": True,
    "measuring": False,
    "total": 4,
    "counts": {"fresh": 1, "stale": 1, "pending": 1, "missing": 1},
    "targets": [
        {"section": "websites", "name": "NYT", "probe": "FPing", "step": 300,
         "age_seconds": 7200, "state": "stale"},
        {"section": "HTTP", "name": "Example", "probe": "CurlHTTP2", "step": 300,
         "age_seconds": None, "state": "missing"},
        {"section": "Custom", "name": "New", "probe": "FPing", "step": 300,
         "age_seconds": None, "state": "pending"},
        {"section": "websites", "name": "Google", "probe": "FPing", "step": 300,
         "age_seconds": 40, "state": "fresh"},
    ],
}


def test_humanize_age():
    h = dashboard_module.humanize_age
    assert h(None) == "never"
    assert h(40) == "40 s"
    assert h(600) == "10 min"
    assert h(7200) == "2 h"
    assert h(3 * 86400) == "3 d"


def test_summary_lists_only_what_is_not_fresh():
    s = dashboard_module.summarize_measurements(BODY)
    assert s["available"] and not s["measuring"]
    assert [p["name"] for p in s["problems"]] == ["NYT", "Example", "New"]
    assert s["problems"][0]["age"] == "2 h"


def test_summary_when_unavailable_keeps_the_reason():
    s = dashboard_module.summarize_measurements(
        {"available": False, "reason": "no generated Targets file yet"})
    assert s == {"available": False, "reason": "no generated Targets file yet"}


def _stub_dashboard(monkeypatch, measurements=None):
    """Stub what the dashboard asks config-manager for. With measurements
    None, get_measurements is left real, so the gateway's own handling of
    an unreachable config-manager is what gets tested."""
    gw = dashboard_module.config_api
    monkeypatch.setattr(gw, "get_targets_config",
                        lambda: {"active_targets": {}, "metadata": {}})
    monkeypatch.setattr(gw, "get_service_status",
                        lambda: {"smokeping": {"running": True}, "using_database": True})
    monkeypatch.setattr(gw, "get_dns_observer", lambda: {"available": False})
    monkeypatch.setattr(gw, "get_budget", lambda: {"available": False, "reason": "stubbed"})
    if measurements is not None:
        monkeypatch.setattr(gw, "get_measurements", lambda: measurements)


def test_dashboard_renders_the_problems(client, monkeypatch):
    _stub_dashboard(monkeypatch, BODY)
    login(client)
    html = client.get("/").get_data(as_text=True)
    assert "Not measuring everything" in html
    assert "websites / NYT" in html and "stopped updating" in html
    assert "2 h ago" in html
    assert "HTTP / Example" in html and "no data" in html
    assert "waiting for the first measurement" in html
    # A fresh target is not a row: the table is what to look at.
    assert "websites / Google" not in html


def test_dashboard_all_good(client, monkeypatch):
    _stub_dashboard(monkeypatch, {
        "available": True, "measuring": True, "total": 30,
        "counts": {"fresh": 30, "stale": 0, "pending": 0, "missing": 0},
        "targets": [],
    })
    login(client)
    html = client.get("/").get_data(as_text=True)
    assert "30 of 30 targets updated" in html
    assert "<table class=\"table table-sm mb-0\">" not in html


def test_dashboard_when_config_manager_is_down(client, monkeypatch):
    _stub_dashboard(monkeypatch)

    def unreachable():
        raise ConnectionError("Cannot connect to config manager")
    monkeypatch.setattr(dashboard_module.config_api.client, "get_measurements", unreachable)
    login(client)
    response = client.get("/")
    assert response.status_code == 200
    assert "Could not check: config-manager unreachable" in response.get_data(as_text=True)


# --- target changes ---------------------------------------------------------

def _db(monkeypatch):
    monkeypatch.setattr(targets_module.config_api, "is_database_available", lambda: True)


def test_toggle_passes_on_an_unconfirmed_reload(client, monkeypatch):
    _db(monkeypatch)
    monkeypatch.setattr(targets_module.config_api, "toggle_target_in_db", lambda i: {
        "success": True, "reloaded": False, "target": {"id": i, "is_active": True}})
    login(client)
    data = client.post("/targets/7/toggle").get_json()
    assert data["success"] is True
    assert data["reloaded"] is False
    assert "did not confirm the reload" in data["message"]


def test_toggle_from_an_older_config_manager_is_not_a_failure(client, monkeypatch):
    # No "reloaded" field at all: silence is not a failed reload.
    _db(monkeypatch)
    monkeypatch.setattr(targets_module.config_api, "toggle_target_in_db", lambda i: {
        "success": True, "message": "Target activated successfully",
        "target": {"id": i, "is_active": True}})
    login(client)
    data = client.post("/targets/7/toggle").get_json()
    assert data["reloaded"] is True
    assert data["message"] == "Target activated successfully"


def test_edit_passes_on_an_unconfirmed_reload(client, monkeypatch):
    _db(monkeypatch)
    monkeypatch.setattr(targets_module.config_api, "update_target_in_db",
                        lambda i, u: {"success": True, "reloaded": False})
    login(client)
    data = client.put("/targets/7", json={"title": "New title"}).get_json()
    assert data["reloaded"] is False
    assert "restart SmokePing" in data["message"]


def test_gateway_generate_passes_reloaded_through_only_when_sent(monkeypatch):
    gw = config_api_module.ConfigAPIGateway()
    monkeypatch.setattr(gw.client, "generate_config",
                        lambda: {"success": True, "reloaded": False, "message": "m"})
    assert gw.generate_config()["reloaded"] is False
    monkeypatch.setattr(gw.client, "generate_config",
                        lambda: {"success": True, "message": "m"})
    assert "reloaded" not in gw.generate_config()


def test_assistant_add_target_note_does_not_claim_an_unconfirmed_reload(monkeypatch):
    # The assistant repeats this note to the person verbatim.
    from app.services import ai_tools
    gw = ai_tools.gateway
    monkeypatch.setattr(gw, "get_categories_from_db",
                        lambda: {"categories": [{"id": 1, "name": "custom"}]})
    monkeypatch.setattr(gw, "get_probes_from_db",
                        lambda: {"probes": [{"id": 1, "name": "FPing", "is_default": True}]})
    monkeypatch.setattr(gw, "create_target_in_db",
                        lambda data: {"success": True, "reloaded": False, "target": {}})
    out = ai_tools._add_target({"name": "Example", "host": "example.com"})
    assert "did not confirm the reload" in out["note"]
    monkeypatch.setattr(gw, "create_target_in_db",
                        lambda data: {"success": True, "reloaded": True, "target": {}})
    assert "regenerated automatically" in ai_tools._add_target(
        {"name": "Example", "host": "example.com"})["note"]


def test_the_isp_first_hop_is_counted_as_automatic():
    """"78 of 78 targets updated" sat beside "Total Targets 77": the 78th is
    cpe_discovery's CPE section, which nobody added."""
    body = {**BODY, "targets": BODY["targets"] + [
        {"section": "CPE", "name": "CPE_IPv4", "probe": "FPing", "step": 300,
         "age_seconds": 30, "state": "fresh"}]}
    assert dashboard_module.summarize_measurements(body)["automatic"] == 1
    assert dashboard_module.summarize_measurements(BODY)["automatic"] == 0


def test_dashboard_says_which_measured_target_nobody_added(client, monkeypatch):
    _stub_dashboard(monkeypatch, {
        "available": True, "measuring": True, "total": 2,
        "counts": {"fresh": 2, "stale": 0, "pending": 0, "missing": 0},
        "targets": [
            {"section": "websites", "name": "Google", "probe": "FPing", "step": 300,
             "age_seconds": 40, "state": "fresh"},
            {"section": "CPE", "name": "CPE_IPv4", "probe": "FPing", "step": 300,
             "age_seconds": 40, "state": "fresh"},
        ]})
    login(client)
    html = client.get("/").get_data(as_text=True)
    assert "2 of 2 targets updated" in html
    assert "(1 of them is measured automatically: the ISP's first hop, found" in html


def test_both_first_hops_read_as_plural(client, monkeypatch):
    hop = {"section": "CPE", "probe": "FPing", "step": 300, "age_seconds": 40,
           "state": "fresh"}
    _stub_dashboard(monkeypatch, {
        "available": True, "measuring": True, "total": 3,
        "counts": {"fresh": 3, "stale": 0, "pending": 0, "missing": 0},
        "targets": [{**hop, "name": "CPE_IPv4"}, {**hop, "name": "CPE_IPv6"},
                    {**hop, "section": "Custom", "name": "CPE_lookalike"}]})
    login(client)
    html = client.get("/").get_data(as_text=True)
    assert "(2 of them are measured automatically: the ISP's first hops, over IPv4 and IPv6" in html
