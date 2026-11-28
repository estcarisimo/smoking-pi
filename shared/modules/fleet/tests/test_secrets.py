"""Secrets stay where layout.py says: never in status.json, a log line or repr.

Every secret file is filled with a sentinel, then everything this release
writes or prints is searched for it."""

import json
import logging
import threading

from fleet import agent, layout, lead, protocol, runner, status

NODE = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
SENTINEL = "SENTINEL-SECRET-7f3e"


def fill_secrets(root):
    for paths, names in ((layout.node_paths(root), layout.NODE_SECRETS),
                         (layout.lead_paths(root), layout.LEAD_SECRETS)):
        for name in names:
            f = getattr(paths, name)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(SENTINEL)
    # An enrollment file that (wrongly) carried a token must not leak either.
    enrollment = layout.node_paths(root).enrollment
    enrollment.write_text(json.dumps({"node_id": NODE, "fleet_id": "x",
                                      "write_token": SENTINEL}))


_real_run = runner.run


def _run_once(report, line, problems=()):
    stop = threading.Event()
    stop.set()
    _real_run(report, line, problems, stop)


def test_status_and_log_never_carry_a_secret(tmp_path, monkeypatch, caplog):
    root = tmp_path / "state"
    fill_secrets(root)
    monkeypatch.setenv("FLEET_NODE_ID", NODE)
    monkeypatch.setattr(runner, "run", _run_once)
    stopped = threading.Event()
    stopped.set()
    for module, kw in ((agent, {"stop": stopped}), (lead, {})):
        with caplog.at_level(logging.DEBUG):
            module.main(**kw)
        assert status.read()["role"] == module.__name__.split(".")[-1]
        assert SENTINEL not in status.path(root).read_text()
        assert SENTINEL not in caplog.text


def test_status_fields_name_no_secret():
    for name in status.FIELDS:
        assert not any(w in name for w in ("token", "key", "secret", "password"))


def test_secret_files_are_declared_on_both_sides():
    assert set(layout.NODE_SECRETS) == {"key", "store_token"}
    assert {"root_key", "intermediate_key"} <= set(layout.LEAD_SECRETS)


# -- protocol objects ------------------------------------------------------------

JOIN_TOKEN = "J" * 40
WRITE_TOKEN = "SENTINEL-WRITE-TOKEN-7f3e"
CERT = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
CSR = "-----BEGIN CERTIFICATE REQUEST-----\nMIIB\n-----END CERTIFICATE REQUEST-----\n"
UUID = "11111111-2222-4333-8444-555555555555"


def _store():
    return protocol.StoreDescriptor(
        store_id=UUID, url="https://store.example", org_id="0" * 16, bucket_id="1" * 16,
        bucket_name=f"node-{NODE}", write_token=protocol.Secret(WRITE_TOKEN), ca_pem=CERT)


def secret_bearing():
    yield protocol.JoinString(fleet_id=UUID, lead_urls=("https://lead.example",),
                              token=protocol.Secret(JOIN_TOKEN), root_sha256="a" * 64)
    yield protocol.EnrollRequest(
        protocol=1, protocol_min=1, join_token=protocol.Secret(JOIN_TOKEN), fleet_id=UUID,
        node_id=NODE, instance_id=UUID, node_name="n", site_name="s", csr_pem=CSR,
        software=protocol.Software(version="2.55.0", edition="pro"))
    yield _store()
    endpoints = protocol.LeadEndpoints(epoch=1, current="https://lead.example",
                                       urls=("https://lead.example",), lead_mode="node")
    yield protocol.EnrollResponse(
        protocol=1, fleet_id=UUID, node_id=NODE, site_id=UUID, cert_chain=(CERT, CERT),
        not_after="2026-11-27T12:00:00Z", lead_endpoints=endpoints, heartbeat_interval=60,
        store=_store())
    yield protocol.HeartbeatResponse(
        protocol=1, server_time="2026-11-27T12:00:00Z", next_heartbeat=60,
        directives=(protocol.RepointStore(id="d1", store=_store()),
                    protocol.UnknownDirective("d2", "teleport")))


def _formatted(fmt: str, arg) -> str:
    """What a logging handler would write for ``log.info(fmt, arg)``: the
    same LogRecord, through the same Formatter.format() -> getMessage()
    -> ``fmt % args`` path, without handing the object to a logger."""
    record = logging.LogRecord("fleet.test", logging.INFO, __file__, 0, fmt, (arg,), None)
    return logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s").format(record)


def test_no_way_of_printing_or_logging_a_message_shows_its_secret():
    for msg in secret_bearing():
        texts = [repr(msg), str(msg), f"{msg}", f"{msg!r}", "%s" % (msg,),
                 "{} {!r}".format(msg, msg), json.dumps(protocol.redacted(msg)),
                 _formatted("message %s", msg), _formatted("message %r", msg),
                 _formatted("redacted %s", protocol.redacted(msg))]
        for text in texts:
            assert JOIN_TOKEN not in text, type(msg).__name__
            assert WRITE_TOKEN not in text, type(msg).__name__
        # The wire is the one place a secret goes.
        wire = json.dumps(protocol.encode(msg))
        assert JOIN_TOKEN in wire or WRITE_TOKEN in wire, type(msg).__name__
