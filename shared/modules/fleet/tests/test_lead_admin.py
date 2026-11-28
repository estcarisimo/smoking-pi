"""``python -m fleet.admin`` (what `smoking-pi fleet …` runs on the lead), the
lead entrypoint's states, its housekeeping, and the server's TLS options."""

import io
import json
import logging
import ssl
import sys
import threading

import pytest

from fleet import admin, layout, lead, pki, protocol, server, settings, status
from leadkit import LEAD_URL, NODE, NODE2, INSTANCE2, enroll, make_lead, rebuild


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("FLEET_LEAD_URLS", LEAD_URL)
    return layout.lead_paths()


def run(argv, capsys, stdin: bytes | None = None, monkeypatch=None):
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(stdin)))
    code = admin.main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def test_init_then_token_prints_the_join_string_alone(env, capsys):
    code, out, err = run(["init", "--name", "Home fleet"], capsys)
    assert code == 0, err
    made = json.loads(out)
    assert made["name"] == "Home fleet" and len(made["root_sha256"]) == 64
    assert run(["init"], capsys)[0] == 1
    code, out, err = run(["token", "--name", "Office Pi", "--site", "Office", "--ttl", "2h"],
                         capsys)
    assert code == 0, err
    join = protocol.JoinString.parse(out)
    assert out.count("\n") == 1 and out.startswith(protocol.JOIN_PREFIX)
    assert join.root_sha256 == made["root_sha256"] and join.lead_urls == (LEAD_URL,)
    about = json.loads(err)
    assert about["node_name"] == "Office Pi" and about["expires_at"] == join.expires_at
    # The token is on stdout only, once.
    assert join.token.reveal() not in err


@pytest.mark.parametrize("ttl, ok", [("15", 900), ("90s", 90), ("2h", 7200), ("24h", 86400),
                                     ("30s", None), ("25h", None), ("1d", None), ("-5", None)])
def test_ttl(ttl, ok):
    if ok is None:
        with pytest.raises(admin.Refusal):
            admin.parse_ttl(ttl)
    else:
        assert admin.parse_ttl(ttl) == ok


def test_commands_refuse_without_a_fleet_and_on_bad_usage(env, capsys):
    for argv in (["token"], ["nodes"], ["revoke", NODE], ["ca", "status"]):
        code, _out, err = run(argv, capsys)
        assert code == 1 and "smoking-pi fleet init" in err
    assert run(["frobnicate"], capsys)[0] == 2
    assert run(["token", "--name", "bad\tname"], capsys)[0] == 1


def test_nodes_and_revoke(tmp_path, env, capsys):
    paths, service = make_lead(env.root)
    code, out, _ = run(["nodes"], capsys)
    assert code == 0 and "No nodes yet" in out
    enroll(service, paths, name="Kitchen Pi")
    enroll(service, paths, node_id=NODE2, instance=INSTANCE2, name="Office Pi", site="Office")
    code, out, _ = run(["nodes"], capsys)
    assert code == 0
    lines = out.splitlines()
    assert lines[0].split()[:3] == ["NODE", "NAME", "SITE"]
    assert any(NODE[:8] in line and "Kitchen Pi" in line and "active" in line for line in lines)
    code, out, _ = run(["nodes", "--json"], capsys)
    views = {v["node_id"]: v for v in json.loads(out)}
    assert views[NODE2]["site_name"] == "Office" and views[NODE]["state"] == "active"
    assert views[NODE]["cert_not_after"].endswith("Z")
    # Revoke by name; an unknown or too-short reference changes nothing.
    assert run(["revoke", "Nobody"], capsys)[0] == 1
    assert run(["revoke", NODE[:4]], capsys)[0] == 1
    code, out, _ = run(["revoke", "Office Pi"], capsys)
    assert code == 0 and NODE2 in out
    code, out, _ = run(["revoke", NODE2], capsys)
    assert code == 0 and "already revoked" in out
    states = {v["node_id"]: v["state"] for v in json.loads(run(["nodes", "--json"], capsys)[1])}
    assert states == {NODE: "active", NODE2: "revoked"}


def test_ca_commands(env, capsys, monkeypatch):
    run(["init"], capsys)
    code, out, _ = run(["ca", "status"], capsys)
    facts = json.loads(out)
    assert code == 0 and facts["root_key_online"] is True
    code, key, _ = run(["ca", "export-root-key"], capsys)
    assert code == 0 and key.startswith("-----BEGIN PRIVATE KEY-----")
    code, _out, err = run(["ca", "remove-root-key"], capsys, stdin=pki.key_pem(pki.new_key()),
                          monkeypatch=monkeypatch)
    assert code == 1 and "nothing was removed" in err and env.root_key.exists()
    code, out, _ = run(["ca", "remove-root-key"], capsys, stdin=key.encode(),
                       monkeypatch=monkeypatch)
    assert code == 0 and not env.root_key.exists()
    code, out, _ = run(["ca", "restore-root-key"], capsys, stdin=key.encode(),
                       monkeypatch=monkeypatch)
    assert code == 0 and env.root_key.exists()


# -- the entrypoint -------------------------------------------------------------


def test_lead_states(tmp_path):
    paths = layout.lead_paths(tmp_path)
    cfg = settings.load({"FLEET_LEAD_URLS": LEAD_URL})
    assert lead.describe(cfg, paths)[0]["state"] == "no_fleet"
    admin.init(paths, cfg, "Home")
    report, line = lead.describe(cfg, paths)
    assert report["state"] == "serving" and LEAD_URL in line
    assert report["lead"]["fleet_name"] == "Home" and report["lead"]["root_key_online"]
    assert report["lead"]["nodes"] == {}
    report, line = lead.describe(settings.load({}), paths)
    assert report["state"] == "no_urls" and "FLEET_LEAD_URLS is empty" in line
    paths.intermediate_key.unlink()
    report, line = lead.describe(cfg, paths)
    assert report["state"] == "ca_broken" and "missing or unreadable" in line


def test_main_serves_when_ready(env, monkeypatch, caplog):
    admin.init(env, settings.load({"FLEET_LEAD_URLS": LEAD_URL}), "Home")
    seen = {}

    def fake_serve(app, paths, worker_started):
        seen.update(app=app, paths=paths, hook=worker_started)

    monkeypatch.setattr(server, "serve", fake_serve)
    with caplog.at_level(logging.INFO, logger="fleet"):
        assert lead.main() == 0
    assert seen["app"].fleet.name == "Home" and "serving fleet" in caplog.text


def test_housekeeping_writes_a_status_without_secrets(tmp_path):
    paths, service = make_lead(tmp_path)
    enroll(service, paths)
    cfg = settings.load({"FLEET_LEAD_URLS": LEAD_URL})
    report, _line = lead.describe(cfg, paths)
    stop = threading.Event()
    stop.set()
    lead.housekeeping(report, paths, service.ca, service.fleet.lead_id,
                      ["lead.example"], service.registry, stop)
    written = status.read(paths.root)
    assert written["state"] == "serving" and status.healthy(written)
    assert written["lead"]["nodes"] == {"active": 1}
    text = status.path(paths.root).read_text()
    for name in ("root_key", "intermediate_key", "server_key"):
        body = getattr(paths, name).read_text().splitlines()[1]  # a line of key material
        assert body not in text


def test_server_tls_options(tmp_path):
    paths = layout.lead_paths(tmp_path)
    opts = server.options(paths, lambda _w: None)
    assert opts["cert_reqs"] == ssl.CERT_OPTIONAL
    assert opts["ca_certs"] == str(paths.root_cert)  # the root: any lead's chain verifies
    # Key and chain in one file: replaced together, never mismatched.
    assert opts["certfile"] == opts["keyfile"] == str(paths.server_key)
    assert opts["worker_class"] == "gthread" and opts["control_socket_disable"] is True
    assert opts["bind"] == [f"0.0.0.0:{settings.LEAD_LISTEN_PORT}"]
    # gunicorn knows every option (a renamed one would fail at start).
    app = server._Application(object(), opts)
    assert app.cfg.cert_reqs == ssl.CERT_OPTIONAL and app.cfg.worker_class_str == "gthread"


def test_a_rebuilt_lead_keeps_its_epoch_while_nothing_changed(tmp_path):
    paths, service = make_lead(tmp_path)
    assert rebuild(paths).endpoints.epoch == service.endpoints.epoch
