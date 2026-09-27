"""The dashboard's DNS observer card: hidden without an observer, an
invitation while it is off, its state once it runs."""

from conftest import login

from app.routes import dashboard as dashboard_module
from test_connection import _stub_dashboard

# config-manager /dns/observer on the reference Pi, 2026-09-27.
OBSERVING = {
    "available": True, "enabled": True, "state": "observing", "live": True,
    "reason": "Receiving queries (last at 2026-09-27 17:15:09).", "fix": "",
    "observed_until": 1790525709.75,
    "coverage": "Network DNS only: queries that reach the Pi through the router.",
    "canary": {"enabled": True, "seen_24h": 1, "sent_24h": 2},
}


def _dashboard(client, monkeypatch, body):
    _stub_dashboard(monkeypatch, {"available": False, "reason": "stubbed"})
    monkeypatch.setattr(dashboard_module.config_api, "get_dns_observer", lambda: body)
    login(client)
    return client.get("/").get_data(as_text=True)


def test_no_card_where_the_edition_has_no_observer(client, monkeypatch):
    html = _dashboard(client, monkeypatch, {"available": False})
    assert 'id="dns-observer-card"' not in html


def test_off_it_invites_with_the_command_and_what_it_cannot_see(client, monkeypatch):
    html = _dashboard(client, monkeypatch, {"available": True, "enabled": False})
    assert 'id="dns-observer-card"' in html
    assert "smoking-pi dns enable" in html and "smoking-pi dns adopt" in html
    assert "never traffic" in html and "Private Relay" in html
    assert 'id="dns-observer-state"' not in html


def test_on_it_shows_the_state_the_reason_and_the_router_check(client, monkeypatch):
    html = _dashboard(client, monkeypatch, OBSERVING)
    assert '<span class="badge bg-success ms-2" id="dns-observer-state">observing</span>' in html
    assert "Receiving queries" in html
    assert "1 of 2 test queries came back" in html
    assert "smoking-pi dns enable" not in html


def test_a_problem_state_carries_its_fix(client, monkeypatch):
    body = {**OBSERVING, "state": "not_receiving", "live": False,
            "reason": "No queries and no canaries.",
            "fix": "Check the router's DNS setting; smoking-pi dns test"}
    html = _dashboard(client, monkeypatch, body)
    assert ">not receiving</span>" in html and "bg-warning" in html
    assert "What to do: <code>Check the router" in html


def test_every_observer_state_has_a_colour():
    # dns-observer/health.py's states, as docs/dns-observer.md lists them.
    for state in ("observing", "quiet", "partial", "upstream_fallback", "upstream_failing",
                  "idle", "not_receiving", "server_down", "starting", "stopped", "down"):
        assert state in dashboard_module.DNS_OBSERVER_BADGES


def test_config_manager_unreachable_leaves_the_card_out(client, monkeypatch):
    def boom():
        raise RuntimeError("down")
    monkeypatch.setattr(dashboard_module.config_api.client, "get_dns_observer", boom)
    assert dashboard_module.config_api.get_dns_observer() == {"available": False}
