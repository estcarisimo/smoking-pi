"""The dashboard's "Your connection" card, and the add form it pre-fills."""

from urllib.parse import parse_qs, urlsplit

from conftest import login

from app.routes import dashboard as dashboard_module
from app.routes import targets as targets_module

# config-manager's answer on the reference Pi, 2026-09-24.
BODY = {
    "available": True,
    "uplink": {"interface": "wlan0", "wireless": True},
    "suggested": 1,
    "items": [
        {"kind": "gateway", "host": "192.168.86.1", "status": "suggested",
         "measured_as": None, "why": "Your router.",
         "suggest": {"target_type": "icmp", "name": "Router",
                     "hostname": "192.168.86.1", "title": "Router (192.168.86.1)"}},
        {"kind": "cpe", "host": "136.25.220.1", "status": "automatic",
         "measured_as": "CPE_IPv4", "suggest": None, "why": "ISP."},
        {"kind": "resolver", "host": "1.1.1.1", "status": "measured",
         "measured_as": "CloudflareDNS", "suggest": None, "why": "DNS."},
        {"kind": "resolver", "host": "127.0.0.53", "status": "local",
         "measured_as": None, "suggest": None, "why": "On this host."},
        {"kind": "gateway6", "host": "fe80::1", "status": "not_measurable",
         "measured_as": None, "suggest": None, "why": "Link-local."},
    ],
}


def _stub_dashboard(monkeypatch, connection=None):
    gw = dashboard_module.config_api
    monkeypatch.setattr(gw, "get_targets_config",
                        lambda: {"active_targets": {}, "metadata": {}})
    monkeypatch.setattr(gw, "get_service_status",
                        lambda: {"smokeping": {"running": True}, "using_database": True})
    monkeypatch.setattr(gw, "get_measurements",
                        lambda: {"available": False, "reason": "stubbed"})
    if connection is not None:
        monkeypatch.setattr(gw, "get_recommendations", lambda: connection)


def test_the_card_names_the_uplink_and_what_each_address_is(client, monkeypatch):
    _stub_dashboard(monkeypatch, BODY)
    login(client)
    html = client.get("/").get_data(as_text=True)
    assert "Measuring over <strong>wlan0</strong>" in html and "(Wi-Fi)" in html
    assert "192.168.86.1" in html and "Add…" in html
    assert "as CloudflareDNS" in html
    assert "automatically" in html
    assert "not needed" in html and "not possible" in html


def test_add_links_to_the_form_pre_filled(client, monkeypatch):
    _stub_dashboard(monkeypatch, BODY)
    login(client)
    with client.application.test_request_context():
        card = dashboard_module.summarize_connection(BODY)
    url = card["entries"][0]["add_url"]
    parts = urlsplit(url)
    assert parts.path == "/targets/add"
    assert parse_qs(parts.query) == {"target_type": ["icmp"], "name": ["Router"],
                                     "hostname": ["192.168.86.1"],
                                     "title": ["Router (192.168.86.1)"]}
    # Nothing that is already measured gets a link.
    assert [i["add_url"] for i in card["entries"][1:]] == [None] * 4


def test_the_form_shows_the_suggestion(client, monkeypatch):
    monkeypatch.setattr(targets_module.config_api, "is_database_available", lambda: True)
    login(client)
    html = client.get("/targets/add?target_type=dns&name=Resolver_1_1_1_1"
                      "&hostname=1.1.1.1&dns_query=google.com"
                      "&title=System+resolver").get_data(as_text=True)
    assert 'value="1.1.1.1"' in html
    assert 'value="Resolver_1_1_1_1"' in html
    assert 'value="google.com"' in html
    assert 'value="System resolver"' in html
    assert '<option value="dns" selected' in html


def test_the_form_ignores_parameters_it_does_not_have(client, monkeypatch):
    monkeypatch.setattr(targets_module.config_api, "is_database_available", lambda: True)
    login(client)
    response = client.get("/targets/add?using_database=0&probes=x")
    assert response.status_code == 200


def test_a_suggestion_is_escaped_like_any_other_value(client, monkeypatch):
    monkeypatch.setattr(targets_module.config_api, "is_database_available", lambda: True)
    login(client)
    html = client.get('/targets/add?hostname="><script>x</script>').get_data(as_text=True)
    assert "<script>x</script>" not in html


def test_the_card_when_smokeping_cannot_see_the_host(client, monkeypatch):
    _stub_dashboard(monkeypatch, {
        "available": False,
        "reason": "SmokePing runs on a Docker network in this edition"})
    login(client)
    html = client.get("/").get_data(as_text=True)
    assert "Could not check: SmokePing runs on a Docker network" in html


def test_the_dashboard_renders_when_config_manager_is_down(client, monkeypatch):
    _stub_dashboard(monkeypatch)

    def unreachable():
        raise ConnectionError("Cannot connect to config manager")
    monkeypatch.setattr(dashboard_module.config_api.client, "get_recommendations",
                        unreachable)
    login(client)
    response = client.get("/")
    assert response.status_code == 200
    assert "config-manager unreachable" in response.get_data(as_text=True)


def test_a_host_with_no_route(client, monkeypatch):
    _stub_dashboard(monkeypatch, {"available": True, "suggested": 0, "items": [],
                                  "uplink": {"interface": None, "wireless": False}})
    login(client)
    assert "No default route" in client.get("/").get_data(as_text=True)
