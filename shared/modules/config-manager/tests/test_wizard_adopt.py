"""POST /wizard/adopt: the DNS wizard's selection becomes targets (sqlite-backed)."""

import json
import time

import pytest
import yaml

import api as api_module
import wizard_adopt
from models import DatabaseManager, Probe, Target, TargetCategory
from scripts.migrate_yaml_to_db import run_migration

CURL_OPTS = {"timeout": 10, "urlformat": "https://%host%/", "expect": "HTTPv=2"}
TARGETS = {
    "active_targets": {
        "top_sites": [
            {"name": "Google", "host": "google.com", "title": "Google",
             "probe": "FPing", "category": "top_sites"},
        ],
    },
    "metadata": {"version": "1.0"},
}
PROBES = {
    "probes": {
        "FPing": {"binary": "/usr/sbin/fping", "step": 300, "pings": 10},
        "TCPPing": {"binary": "/usr/bin/tcpping", "step": 300, "pings": 5, "forks": 5, "port": 443},
        **{f"CurlHTTP{v}": {"binary": "/usr/local/bin/curl-h3", "step": 300, "pings": 5,
                            "forks": 5, "module": "Curl", **CURL_OPTS} for v in (1, 2, 3)},
    },
    "default_probe": "FPing",
}
SOURCES = {"sources": {"topsites": {"display_name": "Top Sites", "enabled": True}}}


def snapshot(services, generated=None):
    return {
        "generated": generated or time.time(),
        "selection": {"services": [
            {"service": s, "host": f"www.{s}", "cdn": "", "asn": "AS64500", "reason": "coverage"}
            for s in services
        ]},
    }


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir()
    for name, body in (("targets", TARGETS), ("probes", PROBES), ("sources", SOURCES)):
        (cfg / f"{name}.yaml").write_text(yaml.dump(body))
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
    snap_path = tmp_path / "wizard.json"
    monkeypatch.setattr(wizard_adopt, "SNAPSHOT", snap_path)

    class Env:
        client = api_module.app.test_client()
        session = staticmethod(manager.get_session)

        @staticmethod
        def write(snap):
            snap_path.write_text(json.dumps(snap))

    Env.reloads = reloads
    return Env


def names(session, category="dns_wizard"):
    cat = session.query(TargetCategory).filter_by(name=category).first()
    return sorted(t.name for t in session.query(Target).filter_by(category_id=cat.id)) if cat else []


def test_target_names_are_stable_and_fit():
    assert wizard_adopt.target_base("netflix.com") == "W_netflix_com"
    long = wizard_adopt.target_base("a-very-long-service-name.example-cdn.com")
    assert len(long + "_icmp") <= wizard_adopt.NAME_MAX
    assert long == wizard_adopt.target_base("a-very-long-service-name.example-cdn.com")
    assert long != wizard_adopt.target_base("a-very-long-service-name.example-cdn.net")


def test_adopt_creates_the_whole_suite_once_with_one_reload(env):
    env.write(snapshot(["netflix.com", "bbc.co.uk"]))
    r = env.client.post("/wizard/adopt")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["targets_added"] == 10 and body["services_added"] == 2
    assert len(env.reloads) == 1  # one regeneration, not one per target
    s = env.session()
    assert names(s) == sorted(f"W_{b}_{x}" for b in ("netflix_com", "bbc_co_uk")
                              for x in ("icmp", "tcp", "h1", "h2", "h3"))
    t = s.query(Target).filter_by(name="W_netflix_com_h2").one()
    probe = s.query(Probe).get(t.probe_id)
    assert (t.host, probe.name, probe.forks, probe.module) == ("www.netflix.com", "WizardHTTP2", 20, "Curl")
    assert probe.options["timeout"] == 5 and probe.options["expect"] == "HTTPv=2"
    assert s.query(Probe).filter_by(name="CurlHTTP2").one().forks == 5  # curated untouched
    icmp = s.query(Target).filter_by(name="W_netflix_com_icmp").one()
    assert s.query(Probe).get(icmp.probe_id).name == "FPing"


def test_adopt_is_add_only(env):
    env.write(snapshot(["netflix.com", "bbc.co.uk"]))
    env.client.post("/wizard/adopt")
    env.write(snapshot(["netflix.com", "wikipedia.org"]))  # bbc dropped out of the selection
    body = env.client.post("/wizard/adopt").get_json()
    assert body["services_added"] == 1 and body["already_adopted"] == 2
    assert "W_bbc_co_uk_icmp" in names(env.session())  # still measured
    body = env.client.post("/wizard/adopt").get_json()
    assert body["targets_added"] == 0
    assert len(env.reloads) == 2  # nothing new: no reload


def test_cap_on_services(env, monkeypatch):
    monkeypatch.setenv("DNS_WIZARD_MAX", "2")
    env.write(snapshot(["a.com", "b.com", "c.com"]))
    body = env.client.post("/wizard/adopt").get_json()
    assert body["services_added"] == 2 and body["over_cap"] == ["c.com"]


def test_dry_run_changes_nothing(env):
    env.write(snapshot(["netflix.com"]))
    body = env.client.post("/wizard/adopt?dry_run=1").get_json()
    assert body["dry_run"] and body["targets_added"] == 5
    assert names(env.session()) == [] and env.reloads == []


@pytest.mark.parametrize("snap,msg", [
    (None, "no_snapshot"),
    ({"generated": time.time() - 3 * 86400, "selection": {"services": [{}]}}, "stale_snapshot"),
    ({"generated": time.time(), "selection": {"services": []}}, "no_selection"),
])
def test_refuses_without_a_usable_snapshot(env, snap, msg):
    if snap is not None:
        env.write(snap)
    r = env.client.post("/wizard/adopt")
    body = r.get_json()
    assert r.status_code == 409 and body["code"] == msg
    assert body["error"] == wizard_adopt.UNAVAILABLE[msg]


def test_the_generator_gives_the_wizard_its_own_section():
    from scripts.config_generator import build_category_context
    cats = build_category_context({"dns_wizard": [{"name": "W_x_com_icmp", "host": "x.com"}]})
    assert cats[0]["section"] == "DNS_Wizard"
