"""Admission: a change that would add cost past a budget ceiling is refused
with the numbers, unless forced (sqlite-backed; the budget's current totals
are given, not computed from Docker)."""

import pytest
import yaml

import api as api_module
import budget
from models import DatabaseManager, Probe, Target
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
# The generated Probes, as config_generator writes them: CurlHTTP2 is a
# sub-probe of Curl, so it is priced at Curl's 12.5 KB per sample.
PROBES_TEXT = "*** Probes ***\n+ FPing\nstep = 300\n+ Curl\n++ CurlHTTP2\n"
# One FPing target: 10 pings / 300 s = 120 samples/hour, 0.48 MB/day.
FPING = (120.0, 120 * 24 * 168 / 1e6)


def budget_body(mb=100.0, samples=1000.0, mb_ceiling=1000.0, samples_ceiling=20000.0):
    return {"available": True, "mb_per_day": mb, "samples_per_hour": samples,
            "ceiling": {"mb_per_day": mb_ceiling, "samples_per_hour": samples_ceiling}}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir()
    for name, body in (("targets", TARGETS), ("probes", PROBES), ("sources", SOURCES)):
        (cfg / f"{name}.yaml").write_text(yaml.dump(body))
    out = tmp_path / "output"
    out.mkdir()
    (out / "Probes").write_text(PROBES_TEXT)
    monkeypatch.setattr(api_module, "OUTPUT_DIR", out)
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
    state = {"budget": budget_body()}
    monkeypatch.setattr(api_module.api, "measurement_budget", lambda: state["budget"])
    api_module.app.config["TESTING"] = True
    with api_module.app.test_client() as c:
        c.reloads = reloads
        c.session = manager.get_session
        c.state = state
        yield c
    api_module.api.refresh_database_mode()


def _new_target(session, probe="FPing", **extra):
    p = session.query(Probe).filter_by(name=probe).one()
    t = session.query(Target).filter_by(name="Google").one()
    return {"name": "Example", "host": "www.example.com", "title": "Example",
            "category_id": t.category_id, "probe_id": p.id, **extra}


def test_a_target_that_fits_is_created(client):
    s = client.session()
    r = client.post("/targets", json=_new_target(s))
    assert r.status_code == 201, r.get_json()
    s.close()


def test_a_target_over_the_ceiling_is_refused_with_the_numbers(client):
    client.state["budget"] = budget_body(mb=999.8)
    s = client.session()
    r = client.post("/targets", json=_new_target(s))
    assert r.status_code == 409
    body = r.get_json()
    assert body["reason"] == "over_budget" and body["error"] == "Over the measurement budget"
    assert body["requested"] == {"samples_per_hour": 120.0, "mb_per_day": 0.48}
    assert body["after"]["mb_per_day"] == 1000.28 and body["after"]["bandwidth_pct"] == 100.0
    assert body["headroom"]["mb_per_day"] == 0.2
    assert "Repeat it with force" in body["message"]
    assert "MEASUREMENT_BUDGET_MB_PER_DAY" in body["message"]
    assert s.query(Target).filter_by(name="Example").count() == 0
    assert client.reloads == []
    s.close()


def test_force_admits_it_anyway(client):
    client.state["budget"] = budget_body(mb=999.8)
    s = client.session()
    r = client.post("/targets?force=1", json=_new_target(s))
    assert r.status_code == 201
    s.close()


def test_samples_count_as_much_as_bytes(client):
    client.state["budget"] = budget_body(samples=19950.0)
    s = client.session()
    r = client.post("/targets", json=_new_target(s))
    assert r.status_code == 409
    assert "samples/hour" in r.get_json()["message"]
    s.close()


def test_an_inactive_target_costs_nothing(client):
    client.state["budget"] = budget_body(mb=1500.0)  # already over
    s = client.session()
    r = client.post("/targets", json=_new_target(s, is_active=False))
    assert r.status_code == 201
    s.close()


def test_turning_a_target_on_is_admitted_like_adding_it(client):
    s = client.session()
    google = s.query(Target).filter_by(name="Google").one()
    google.is_active = False
    s.commit()
    gid = google.id
    s.close()
    client.state["budget"] = budget_body(mb=999.9)
    assert client.post(f"/targets/{gid}/toggle").status_code == 409
    assert client.post(f"/targets/{gid}/toggle?force=1").status_code == 200


def test_cutting_cost_is_always_admitted_even_over_the_ceiling(client):
    client.state["budget"] = budget_body(mb=1500.0)
    s = client.session()
    gid = s.query(Target).filter_by(name="Google").one().id
    s.close()
    assert client.post(f"/targets/{gid}/toggle").status_code == 200  # turned off
    assert client.put("/probes/FPing", json={"step_seconds": 600}).status_code == 200


def test_moving_a_target_to_a_costlier_probe_prices_the_difference(client):
    # FPing -> CurlHTTP2: 60 samples/hour at 12.5 KB = 18 MB/day, minus 0.48.
    client.state["budget"] = budget_body(mb=990.0)
    s = client.session()
    google = s.query(Target).filter_by(name="Google").one()
    curl = s.query(Probe).filter_by(name="CurlHTTP2").one()
    r = client.put(f"/targets/{google.id}", json={"probe_id": curl.id})
    assert r.status_code == 409
    assert r.get_json()["requested"]["mb_per_day"] == round(18.0 - FPING[1], 2)
    s.close()


def test_a_faster_probe_is_priced_for_every_active_target(client):
    # Two FPing targets, 300 s -> 60 s: +480 samples/hour each.
    client.state["budget"] = budget_body(samples=19500.0)
    r = client.put("/probes/FPing", json={"step_seconds": 60})
    assert r.status_code == 409
    assert r.get_json()["requested"]["samples_per_hour"] == 960.0
    s = client.session()
    assert s.query(Probe).filter_by(name="FPing").one().step_seconds == 300
    s.close()
    assert client.put("/probes/FPing?force=1", json={"step_seconds": 60}).status_code == 200


def test_no_budget_never_blocks(client, monkeypatch):
    client.state["budget"] = {"available": False, "reason": "no generated Targets file yet"}
    s = client.session()
    assert client.post("/targets", json=_new_target(s)).status_code == 201
    s.close()

    def broken():
        raise RuntimeError("docker socket gone")

    monkeypatch.setattr(api_module.api, "measurement_budget", broken)
    s = client.session()
    r = client.post("/targets", json={**_new_target(s), "name": "Example2"})
    assert r.status_code == 201
    s.close()


# --- the pure part ------------------------------------------------------------


def test_probe_cost_prices_as_the_report_does():
    text = PROBES_TEXT + "++ CurlHTTP3\nextraargs = --http3-only\n"
    assert budget.probe_cost(text, "FPing", 300, 10) == pytest.approx(FPING)
    assert budget.probe_cost(text, "CurlHTTP2", 300, 5) == pytest.approx((60.0, 18.0))
    assert budget.probe_cost(text, "CurlHTTP3", 300, 5) == pytest.approx((60.0, 25.92))
    # A wizard probe not generated yet is priced as the curated one it copies.
    assert budget.probe_cost(text, "WizardHTTP3", 300, 5, like="CurlHTTP3") == \
        pytest.approx((60.0, 25.92))
    # An unpriced class costs samples, not bytes.
    assert budget.probe_cost(text, "Mystery", 300, 5) == pytest.approx((60.0, 0.0))


def test_admission_lets_through_what_fits_and_what_cuts():
    body = budget_body(mb=999.0)
    assert budget.admission(body, 100.0, 0.5) is None
    assert budget.admission(body, -500.0, -10.0) is None
    refusal = budget.admission(body, 100.0, 2.0)
    assert refusal["after"]["mb_per_day"] == 1001.0
    assert refusal["headroom"] == {"samples_per_hour": 19000.0, "mb_per_day": 1.0}
