"""The welcome tour (first-run stage C) and the dashboard's redirect to it."""

from conftest import login

from app.routes import dashboard as dashboard_module
from app.routes import welcome as welcome_module

TARGETS = [
    {"id": 1, "name": "Google", "host": "google.com", "title": "Google",
     "category": "top_sites", "probe": "FPing", "is_active": True},
    {"id": 2, "name": "GoogleDNS", "host": "8.8.8.8", "title": "Google DNS",
     "category": "dns_resolvers", "probe": "DNS", "is_active": False},
    {"id": 3, "name": "Mine", "host": "example.org", "title": "Mine",
     "category": "lab", "probe": "FPing", "is_active": True},
]

CONNECTION = {
    "available": True, "suggested": 1,
    "uplink": {"interface": "wlan0", "wireless": True},
    "items": [
        {"kind": "gateway", "host": "192.168.86.1", "status": "suggested",
         "measured_as": None, "why": "Your router.",
         "suggest": {"target_type": "icmp", "name": "Router",
                     "hostname": "192.168.86.1", "title": "Router (192.168.86.1)"}},
        {"kind": "resolver", "host": "1.1.1.1", "status": "measured",
         "measured_as": "CloudflareDNS", "suggest": None, "why": "DNS."},
    ],
}


def _stub(monkeypatch, pending=True, recorded=None):
    gw = dashboard_module.config_api
    monkeypatch.setattr(gw, "get_targets_config",
                        lambda: {"active_targets": {}, "metadata": {}})
    monkeypatch.setattr(gw, "get_service_status",
                        lambda: {"smokeping": {"running": True}, "using_database": True})
    monkeypatch.setattr(gw, "get_measurements",
                        lambda: {"available": True, "measuring": True, "total": 3,
                                 "counts": {"fresh": 3}, "targets": []})
    monkeypatch.setattr(gw, "get_recommendations", lambda: CONNECTION)
    monkeypatch.setattr(gw, "is_database_available", lambda: True)
    monkeypatch.setattr(gw, "get_all_targets_from_db", lambda: {"targets": TARGETS})
    monkeypatch.setattr(gw, "tour_pending", lambda: pending)
    calls = [] if recorded is None else recorded
    monkeypatch.setattr(gw, "set_first_run", lambda outcome: calls.append(outcome) or {})
    return calls


def test_a_first_login_lands_in_the_tour(client, monkeypatch):
    _stub(monkeypatch, pending=True)
    login(client)
    response = client.get("/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/welcome/")


def test_after_the_tour_the_dashboard_is_the_dashboard(client, monkeypatch):
    _stub(monkeypatch, pending=False)
    login(client)
    response = client.get("/")
    assert response.status_code == 200
    assert "Welcome tour" in response.get_data(as_text=True)


def test_tour_off_is_the_way_out_when_recording_failed(client, monkeypatch):
    _stub(monkeypatch, pending=True)
    login(client)
    assert client.get("/?tour=off").status_code == 200


def test_the_tour_shows_all_three_steps(client, monkeypatch):
    _stub(monkeypatch)
    login(client)
    html = client.get("/welcome/").get_data(as_text=True)
    assert "3 of 3 targets updated" in html
    # Step 2: the suggestion is checked by default, carrying the form's fields.
    assert 'data-target-type="icmp"' in html and 'data-hostname="192.168.86.1"' in html
    assert "as CloudflareDNS" in html
    # Step 3: seeded targets by category, known ones first, a paused one off.
    assert html.index("top_sites") < html.index("dns_resolvers") < html.index(">lab<")
    assert 'id="target-1" data-id="1" data-name="Google"\n                   checked' in html
    assert 'id="target-2" data-id="2" data-name="GoogleDNS"\n                   >' in html


def test_finish_and_skip_are_recorded(client, monkeypatch):
    recorded = _stub(monkeypatch, recorded=[])
    login(client)
    for outcome in ("done", "skipped"):
        response = client.post("/welcome/finish", data={"outcome": outcome})
        assert response.status_code == 302 and response.headers["Location"].endswith("/")
    assert recorded == ["done", "skipped"]


def test_an_unknown_outcome_records_nothing(client, monkeypatch):
    recorded = _stub(monkeypatch, recorded=[])
    login(client)
    response = client.post("/welcome/finish", data={"outcome": "delete-everything"})
    assert response.headers["Location"].endswith("/welcome/")
    assert recorded == []


def test_when_recording_fails_the_dashboard_still_opens(client, monkeypatch):
    _stub(monkeypatch)

    def fail(outcome):
        raise RuntimeError("config-manager down")
    monkeypatch.setattr(dashboard_module.config_api, "set_first_run", fail)
    login(client)
    response = client.post("/welcome/finish", data={"outcome": "done"})
    assert response.headers["Location"].endswith("/?tour=off")


def test_the_dashboard_button_reopens_the_tour(client, monkeypatch):
    recorded = _stub(monkeypatch, pending=False, recorded=[])
    login(client)
    response = client.post("/welcome/again")
    assert response.headers["Location"].endswith("/welcome/")
    assert recorded == ["reset"]


def test_an_unreachable_config_manager_never_traps_a_login(client, monkeypatch):
    """tour_pending itself: if /first-run cannot be asked, do not redirect."""
    gw = dashboard_module.config_api

    def unreachable():
        raise ConnectionError("down")
    monkeypatch.setattr(gw.client, "get_first_run", unreachable)
    assert gw.tour_pending() is False
    monkeypatch.setattr(gw.client, "get_first_run", lambda: {"completed": False})
    assert gw.tour_pending() is True


def test_group_targets_orders_known_categories_first():
    groups = welcome_module.group_targets(TARGETS)
    assert [g[0] for g in groups] == ["top_sites", "dns_resolvers", "lab"]
    assert groups[2][1] == ""
