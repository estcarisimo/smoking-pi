"""The add form states each probe's cadence from its real settings."""

from conftest import login

from app.routes import targets as targets_module


def _probes(monkeypatch, probes=None, fail=False):
    gw = targets_module.config_api
    monkeypatch.setattr(gw, "is_database_available", lambda: True)

    def get_probes():
        if fail:
            raise RuntimeError("config-manager down")
        return {"probes": probes or []}

    monkeypatch.setattr(gw, "get_probes_from_db", get_probes)


def test_dns_says_five_queries_not_ten(client, monkeypatch):
    _probes(monkeypatch, fail=True)
    login(client)
    html = client.get("/targets/add").get_data(as_text=True)
    assert "5 queries every 5 minutes" in html
    assert "10 queries" not in html
    assert html.count("10 pings every 5 minutes") == 2


def test_the_form_reads_the_configured_probes(client, monkeypatch):
    _probes(monkeypatch, [
        {"name": "FPing", "pings": 20, "step_seconds": 60},
        {"name": "DNS", "pings": 3, "step_seconds": 600},
    ])
    login(client)
    html = client.get("/targets/add").get_data(as_text=True)
    assert "20 pings every minute" in html
    assert "3 queries every 10 minutes" in html


def test_describe_cadence_handles_uneven_steps():
    assert targets_module.describe_cadence(10, 90) == "10 pings every 90 seconds"
