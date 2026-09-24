"""The Probes page: each probe's cycle, and changing it with a warning."""

from conftest import login

from app.routes import probes as probes_module

PROBES = [
    {"name": "FPing", "step_seconds": 300, "pings": 10, "module": None,
     "targets": 20, "active_targets": 18},
    {"name": "DNS", "step_seconds": 300, "pings": 5, "module": None,
     "targets": 3, "active_targets": 3},
    {"name": "CurlHTTP2", "step_seconds": 300, "pings": 5, "module": "Curl",
     "targets": 4, "active_targets": 4},
]


def _stub(monkeypatch, result=None, error=None):
    gw = probes_module.config_api
    monkeypatch.setattr(gw, "get_probes_from_db",
                        lambda: {"probes": [dict(p) for p in PROBES]})
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


def test_a_refusal_shows_config_managers_reason(client, monkeypatch):
    _stub(monkeypatch, error=ValueError("5 pings with a 10 s timeout can take 50 s"))
    login(client)
    r = client.post("/probes/CurlHTTP2/edit",
                    data={"step_seconds": "60", "pings": "5", "confirm": "yes"})
    assert "can take 50 s" in r.get_data(as_text=True)


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
