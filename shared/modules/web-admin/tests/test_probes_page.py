"""The Probes page: each probe's cycle, and changing it with a warning."""

import pytest
from conftest import login

from app.routes import probes as probes_module
from app.services.config_api import ProbeChangeRefused

PROBES = [
    {"name": "FPing", "step_seconds": 300, "pings": 10, "module": None,
     "targets": 20, "active_targets": 18},
    {"name": "DNS", "step_seconds": 300, "pings": 5, "module": None,
     "targets": 3, "active_targets": 3},
    {"name": "CurlHTTP2", "step_seconds": 300, "pings": 5, "module": "Curl",
     "targets": 4, "active_targets": 4},
]


# config-manager /budget for PROBES: a priced row per running probe.
BUDGET = {
    "available": True,
    "by_probe": [
        {"probe": "CurlHTTP2", "mb_per_day": 69.12},
        {"probe": "FPing", "mb_per_day": 8.71},
        {"probe": "DNS", "mb_per_day": 1.3},
    ],
}


def _stub(monkeypatch, result=None, error=None, budget=None):
    gw = probes_module.config_api
    monkeypatch.setattr(gw, "get_probes_from_db",
                        lambda: {"probes": [dict(p) for p in PROBES]})
    monkeypatch.setattr(gw, "get_budget", lambda: BUDGET if budget is None else budget)
    calls = []

    def update(name, changes):
        calls.append((name, changes))
        if error:
            raise error
        return result

    monkeypatch.setattr(gw, "update_probe", update)
    return calls


def test_the_list_says_each_probes_cycle_in_its_own_words(client, monkeypatch):
    _stub(monkeypatch)
    login(client)
    html = client.get("/probes/").get_data(as_text=True)
    assert "10 pings every 5 minutes" in html
    assert "5 queries every 5 minutes" in html
    assert "5 fetches every 5 minutes" in html
    assert "(20 with paused)" in html


def test_traffic_is_the_budgets_measured_cost(client, monkeypatch):
    # It used to count 64 bytes a sample: an HTTPS HEAD is ~12 KB.
    _stub(monkeypatch)
    login(client)
    html = client.get("/probes/").get_data(as_text=True)
    assert "~69.1 MB/day" in html and "~8.7 MB/day" in html
    assert "kbit/s" not in html


def test_a_probe_with_nothing_running_costs_nothing(client, monkeypatch):
    # In /probes but not in the budget: no target of it is in the
    # generated config (all paused, or IPv6-gated). Known, and zero.
    _stub(monkeypatch, budget={**BUDGET, "by_probe": BUDGET["by_probe"][:2]})
    login(client)
    html = client.get("/probes/").get_data(as_text=True)
    assert ">0 MB/day<" in html and "—" not in html


def test_traffic_without_a_budget_is_a_dash_not_a_guess(client, monkeypatch):
    _stub(monkeypatch, budget={"available": False, "reason": "stubbed"})
    login(client)
    html = client.get("/probes/").get_data(as_text=True)
    assert "10 pings every 5 minutes" in html
    assert "MB/day" not in html and "—" in html


def test_the_form_warns_before_anything_is_saved(client, monkeypatch):
    _stub(monkeypatch)
    login(client)
    html = client.get("/probes/FPing/edit").get_data(as_text=True)
    assert "new SmokePing history" in html
    assert "/data/.archive/" in html
    assert "Grafana keeps everything" in html
    assert '<option value="60" >Every minute</option>' in html or "Every minute" in html
    assert 'name="confirm"' in html


def test_nothing_is_saved_without_the_confirmation(client, monkeypatch):
    calls = _stub(monkeypatch)
    login(client)
    r = client.post("/probes/FPing/edit", data={"step_seconds": "60", "pings": "10"})
    assert r.status_code == 200
    assert calls == []
    assert "Confirm that" in r.get_data(as_text=True)


def test_a_confirmed_change_is_sent_and_reported(client, monkeypatch):
    result = {"changed": True, "current": {"step_seconds": 60, "pings": 10},
              "reloaded": True,
              "rrd_guard": {"ran": True, "errors": [],
                            "archived": [{"rrd": "a"}, {"rrd": "b"}]}}
    calls = _stub(monkeypatch, result=result)
    login(client)
    r = client.post("/probes/FPing/edit",
                    data={"step_seconds": "60", "pings": "10", "confirm": "yes"},
                    follow_redirects=True)
    assert calls == [("FPing", {"step_seconds": 60, "pings": 10})]
    html = r.get_data(as_text=True)
    assert "FPing now measures 10 pings every minute." in html
    assert "2 old SmokePing files were moved to /data/.archive/" in html


def test_a_refusal_says_why_in_the_pages_own_words(client, monkeypatch):
    refusal = ProbeChangeRefused(
        "cycle_outruns_step", {"pings": 10, "worst_seconds": 100.0, "step_seconds": 60})
    _stub(monkeypatch, error=refusal)
    login(client)
    r = client.post("/probes/CurlHTTP2/edit",
                    data={"step_seconds": "60", "pings": "10", "confirm": "yes"})
    assert ("10 pings can take up to 100 s when they time out, "
            "longer than a 60 s step") in r.get_data(as_text=True)


@pytest.mark.parametrize("reason, needle", [
    ("pings_out_of_range", "between 3 and 20"),
    ("step_not_allowed", "That step is not allowed"),
    ("nothing_to_change", "Choose a step and a number of pings."),
    ("field_not_editable", "Only the step and the number of pings"),
    ("probe_not_found", "has no probe by that name"),
    ("cycle_outruns_step", "choose fewer pings or a longer step"),  # no numbers
    ("http://config-manager:5000/secret", "config-manager refused the change"),
    ("", "config-manager refused the change"),
])
def test_a_refusal_is_one_of_the_pages_literals(client, monkeypatch, reason, needle):
    _stub(monkeypatch, error=ProbeChangeRefused(reason, {}))
    login(client)
    html = client.post("/probes/FPing/edit",
                       data={"step_seconds": "60", "pings": "10", "confirm": "yes"}
                       ).get_data(as_text=True)
    assert needle in html
    assert "secret" not in html


def test_any_other_failure_shows_no_exception_text(client, monkeypatch):
    # requests' JSONDecodeError is a ValueError: it used to be flashed as is.
    _stub(monkeypatch, error=ValueError("Expecting value: /var/lib/x line 1"))
    login(client)
    html = client.post("/probes/FPing/edit",
                       data={"step_seconds": "60", "pings": "10", "confirm": "yes"}
                       ).get_data(as_text=True)
    assert "config-manager did not save the change." in html
    assert "/var/lib/x" not in html


def test_a_guard_that_did_not_run_is_said(client, monkeypatch):
    result = {"changed": True, "current": {"step_seconds": 600, "pings": 10},
              "reloaded": False, "rrd_guard": {"ran": False}}
    _stub(monkeypatch, result=result)
    login(client)
    html = client.post("/probes/FPing/edit",
                       data={"step_seconds": "600", "pings": "10", "confirm": "yes"},
                       follow_redirects=True).get_data(as_text=True)
    assert "The RRD guard did not run" in html
    assert "did not confirm the reload" in html


def test_an_unknown_probe_goes_back_to_the_list(client, monkeypatch):
    _stub(monkeypatch)
    login(client)
    r = client.get("/probes/Nope/edit")
    assert r.status_code == 302 and r.headers["Location"].endswith("/probes/")


def test_the_page_opens_when_config_manager_is_down(client, monkeypatch):
    def down():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(probes_module.config_api, "get_probes_from_db", down)
    login(client)
    r = client.get("/probes/")
    assert r.status_code == 200
    assert "Could not load the probes" in r.get_data(as_text=True)


def test_the_add_form_and_the_probes_page_name_units_alike():
    from app.routes.targets import probe_unit
    assert probe_unit("DNS") == "queries"
    assert probe_unit("CurlHTTP3") == probe_unit("CurlHTTP3", "Curl") == "fetches"
    assert probe_unit("TCPPing") == "connections"
    assert probe_unit("FPing6") == "pings"
