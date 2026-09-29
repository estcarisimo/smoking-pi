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
    # layer_check.py in the SmokePing container, never Docker in a test:
    # by default every new layer answers and there is no silence data.
    layer = {"answers": lambda layer: True, "silence": {}, "down": False, "calls": []}

    def fake_layer_check(doc):
        layer["calls"].append(doc)
        if layer["down"]:
            return None
        return {"preflight": {c["name"]: layer["answers"](c) for c in doc.get("preflight", [])},
                "silence": {i["name"]: layer["silence"][i["name"]] for i in doc.get("silence", [])
                            if i["name"] in layer["silence"]},
                "errors": []}

    monkeypatch.setattr(api_module.api, "_layer_check", fake_layer_check)

    class Env:
        client = api_module.app.test_client()
        session = staticmethod(manager.get_session)

        @staticmethod
        def write(snap):
            snap_path.write_text(json.dumps(snap))

    Env.reloads = reloads
    Env.layer = layer
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


def test_bare_names_are_never_adopted(env):
    env.write(snapshot(["netflix.com", "config-manager", "nas.internal"]))  # an older observer's pick
    env.client.post("/wizard/adopt")
    assert not [n for n in names(env.session())
                if n.startswith(("W_config_manager", "W_nas_internal"))]
    assert "W_netflix_com_icmp" in names(env.session())


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


# -- layers a host does not serve --------------------------------------------------


def _active(env):
    session = env.session()
    try:
        return {t.name: t.is_active for t in session.query(Target).all()
                if t.name.startswith("W_")}
    finally:
        session.close()


def test_a_layer_the_host_does_not_answer_is_not_adopted(env):
    env.layer["answers"] = lambda c: c["layer"] not in ("h3", "icmp")
    env.write(snapshot(["example-shop.org"]))
    body = env.client.post("/wizard/adopt").get_json()
    assert body["preflight"] == "ran"
    assert sorted(body["not_served"]) == ["W_example_shop_org_h3", "W_example_shop_org_icmp"]
    assert sorted(_active(env)) == ["W_example_shop_org_h1", "W_example_shop_org_h2",
                                    "W_example_shop_org_tcp"]
    # What it tried was the snapshot's host, one layer each.
    tried = env.layer["calls"][-1]["preflight"]
    assert {c["host"] for c in tried} == {"www.example-shop.org"}
    assert len(tried) == 5


def test_without_the_check_every_layer_is_adopted_and_it_says_so(env):
    env.layer["down"] = True
    env.write(snapshot(["example-shop.org"]))
    body = env.client.post("/wizard/adopt").get_json()
    assert body["preflight"] == "unavailable" and body["targets_added"] == 5
    # And with layers to judge, none is deactivated on no evidence.
    body = env.client.post("/wizard/adopt").get_json()
    assert body["retire_held"] == "unavailable" and body["retired"] == []
    assert all(_active(env).values())


def _adopted_with(env, silence):
    env.write(snapshot(["alpha.org", "beta.org"]))
    env.client.post("/wizard/adopt")
    env.layer["silence"] = silence
    return env.client.post("/wizard/adopt?dry_run=1").get_json(), silence


DAY = {"rows": 288, "answered": 280}
SILENT = {"rows": 288, "answered": 0}


def test_a_layer_silent_for_a_day_is_deactivated_not_deleted(env):
    silence = {f"W_{s}_org_{x}": DAY for s in ("alpha", "beta") for x in ("icmp", "tcp", "h1", "h2", "h3")}
    silence["W_beta_org_h3"] = SILENT
    silence["W_alpha_org_icmp"] = SILENT
    dry, _ = _adopted_with(env, silence)
    assert dry["retired"] == ["W_alpha_org_icmp", "W_beta_org_h3"]
    assert all(_active(env).values())  # the dry run changed nothing
    body = env.client.post("/wizard/adopt").get_json()
    assert body["retired"] == ["W_alpha_org_icmp", "W_beta_org_h3"]
    active = _active(env)
    assert active["W_alpha_org_icmp"] is False and active["W_beta_org_h3"] is False
    assert active["W_alpha_org_tcp"] is True and len(active) == 10  # rows kept
    assert env.reloads  # SmokePing stops probing them
    # Deactivated, not asked again: the next pass does not count them.
    env.client.post("/wizard/adopt?dry_run=1")
    asked = [c for c in env.layer["calls"] if "silence" in c][-1]["silence"]
    assert "W_beta_org_h3" not in {i["name"] for i in asked} and len(asked) == 8


def test_a_day_long_outage_deactivates_nothing(env):
    silence = {f"W_{s}_org_{x}": SILENT for s in ("alpha", "beta") for x in ("icmp", "tcp", "h1", "h2", "h3")}
    silence["W_alpha_org_tcp"] = DAY
    _adopted_with(env, silence)
    body = env.client.post("/wizard/adopt").get_json()
    assert body["retired"] == [] and body["retire_held"] == "network"
    assert all(_active(env).values())


def test_less_than_a_day_measured_is_no_evidence(env):
    silence = {f"W_{s}_org_{x}": DAY for s in ("alpha", "beta") for x in ("icmp", "tcp", "h1", "h2", "h3")}
    silence["W_beta_org_h3"] = {"rows": 100, "answered": 0}  # adopted this morning
    _adopted_with(env, silence)
    assert env.client.post("/wizard/adopt").get_json()["retired"] == []


def test_a_layer_the_check_had_no_time_for_is_adopted(env):
    # layer_check leaves out what it could not try within its budget: no
    # answer is not a "no".
    real = api_module.api._layer_check

    def partial(doc):
        report = real(doc)
        if report and doc.get("preflight"):
            report["preflight"] = {k: v for k, v in report["preflight"].items()
                                   if not k.endswith(("_h2", "_h3"))}
        return report

    env.layer["answers"] = lambda c: False
    api_module.api._layer_check = partial
    try:
        env.write(snapshot(["example-shop.org"]))
        body = env.client.post("/wizard/adopt").get_json()
    finally:
        api_module.api._layer_check = real
    assert sorted(body["not_served"]) == ["W_example_shop_org_h1", "W_example_shop_org_icmp",
                                          "W_example_shop_org_tcp"]
    assert sorted(_active(env)) == ["W_example_shop_org_h2", "W_example_shop_org_h3"]


def test_a_layer_silent_everywhere_is_a_block_and_is_left_alone(env):
    # QUIC dropped by a firewall for a day: every h3 silent, the rest answer.
    silence = {f"W_{s}_org_{x}": DAY for s in ("alpha", "beta") for x in ("icmp", "tcp", "h1", "h2")}
    silence.update({f"W_{s}_org_h3": SILENT for s in ("alpha", "beta")})
    silence["W_alpha_org_icmp"] = SILENT  # one host that really drops ping
    _adopted_with(env, silence)
    body = env.client.post("/wizard/adopt").get_json()
    assert body["retired"] == ["W_alpha_org_icmp"]
    assert body["retire_held"] == "layer:h3"
    assert _active(env)["W_alpha_org_h3"] is True


def test_a_failed_adoption_undoes_the_deactivations(env, monkeypatch):
    silence = {f"W_{s}_org_{x}": DAY for s in ("alpha", "beta") for x in ("icmp", "tcp", "h1", "h2", "h3")}
    silence["W_beta_org_h3"] = SILENT
    _adopted_with(env, silence)

    def broken(*a, **kw):
        raise RuntimeError("database went away")

    monkeypatch.setattr(wizard_adopt, "adopt", broken)
    assert env.client.post("/wizard/adopt").status_code == 500
    assert _active(env)["W_beta_org_h3"] is True  # not half-applied


def test_retire_only_deactivates_and_adopts_nothing(env):
    silence = {f"W_{s}_org_{x}": DAY for s in ("alpha", "beta") for x in ("icmp", "tcp", "h1", "h2", "h3")}
    silence["W_beta_org_h3"] = SILENT
    _adopted_with(env, silence)
    env.write(snapshot(["alpha.org", "beta.org", "gamma.org"]))  # a new service selected
    before = len(env.layer["calls"])
    body = env.client.post("/wizard/adopt?retire_only=1").get_json()
    assert body["retire_only"] is True and body["targets_added"] == 0
    assert body["retired"] == ["W_beta_org_h3"]
    active = _active(env)
    assert not any(n.startswith("W_gamma") for n in active) and active["W_beta_org_h3"] is False
    assert not any("preflight" in c for c in env.layer["calls"][before:])  # nothing tried


def test_retire_only_needs_no_snapshot(env):
    silence = {f"W_{s}_org_{x}": DAY for s in ("alpha", "beta") for x in ("icmp", "tcp", "h1", "h2", "h3")}
    silence["W_alpha_org_tcp"] = SILENT
    _adopted_with(env, silence)
    wizard_adopt.SNAPSHOT.unlink()  # the observer stopped
    assert env.client.post("/wizard/adopt").status_code == 409
    body = env.client.post("/wizard/adopt?retire_only=1").get_json()
    assert body["retired"] == ["W_alpha_org_tcp"]
