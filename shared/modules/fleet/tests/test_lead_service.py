"""The lead's five routes, in-process: enrollment and its token, mTLS
identity, revocation, duplicates, renewal, leaving, and every error being a
protocol ErrorResponse with a fixed message."""

import io
import json
import logging
import threading

import pytest

from fleet import admin, pki, protocol, registry
from leadkit import (INSTANCE, INSTANCE2, LEAD_URL, NODE, NODE2, Clock, call, csr_pem,
                     enroll, enroll_body, heartbeat_body, make_lead, new_join, rebuild)


@pytest.fixture
def lead(tmp_path):
    clock = Clock()
    paths, service = make_lead(tmp_path, clock=clock)
    return paths, service, clock


def error_of(status, body, code):
    assert status == protocol.ERROR_STATUS[code], body
    err = protocol.decode(protocol.ErrorResponse, body)
    assert err.code == code and err.message == protocol.ERROR_MESSAGE[code]
    return err


def leaf_of(resp) -> str:
    return resp.cert_chain[0]


# -- root and enrollment ---------------------------------------------------------


def test_root_is_what_the_join_string_pins(lead):
    paths, service, _ = lead
    status, body = call(service, "GET", "/fleet/root")
    assert status == 200
    root = protocol.decode(protocol.RootResponse, body)
    assert pki.fingerprint(pki.load_cert(root.root_pem)) == new_join(paths).root_sha256


def test_enroll_issues_a_chain_and_says_no_store_yet(lead):
    paths, service, _ = lead
    join = new_join(paths)
    assert join.lead_urls == (LEAD_URL,) and join.fleet_id == service.fleet.fleet_id
    key, body = enroll_body(join)
    status, out = call(service, "POST", "/fleet/enroll", body)
    assert status == 200, out
    resp = protocol.decode(protocol.EnrollResponse, out)
    assert resp.node_id == NODE and resp.fleet_id == service.fleet.fleet_id
    assert len(resp.cert_chain) == 2
    leaf = pki.load_cert(resp.cert_chain[0])
    assert pki.node_id_of(leaf) == NODE
    assert pki.same_public_key(leaf.public_key(), key.public_key())
    assert pki.load_cert(resp.cert_chain[1]) == service.ca.intermediate
    assert resp.store is None and "store" not in out
    assert resp.lead_endpoints == service.endpoints
    assert resp.lead_endpoints.current == LEAD_URL and resp.lead_endpoints.lead_mode == "node"
    node = service.registry.node(NODE)
    assert node["site_name"] == "Home" and node["site_id"] == resp.site_id
    assert node["node_name"] == "Kitchen Pi" and node["instance_id"] == INSTANCE


def test_a_token_is_single_use(lead):
    paths, service, _ = lead
    join = new_join(paths)
    _k, body = enroll_body(join)
    assert call(service, "POST", "/fleet/enroll", body)[0] == 200
    _k, body = enroll_body(join, node_id=NODE2)
    error_of(*call(service, "POST", "/fleet/enroll", body), "token_invalid")
    assert service.registry.node(NODE2) is None


def test_an_expired_token_is_refused(lead):
    paths, service, clock = lead
    join = new_join(paths, ttl=120)
    clock.advance(121)
    _k, body = enroll_body(join)
    error_of(*call(service, "POST", "/fleet/enroll", body), "token_invalid")


def test_a_wrong_token_or_fleet_is_refused(lead):
    paths, service, _ = lead
    join = new_join(paths)
    _k, body = enroll_body(join)
    body["join_token"] = "x" * 43
    error_of(*call(service, "POST", "/fleet/enroll", body), "token_invalid")
    _k, body = enroll_body(join, fleet_id="0b1e2f3a-0000-4000-8000-000000000001")
    error_of(*call(service, "POST", "/fleet/enroll", body), "token_invalid")
    # Neither attempt spent the real token.
    _k, body = enroll_body(join)
    assert call(service, "POST", "/fleet/enroll", body)[0] == 200


def test_a_valid_token_always_enrolls_and_only_invalid_ones_are_throttled(lead, caplog):
    """Behind Docker's port publishing every node may share one address:
    invalid tokens from it are throttled, a valid one never is."""
    paths, service, clock = lead
    join = new_join(paths, ttl=3600)
    _k, bad = enroll_body(join)
    bad["join_token"] = "y" * 43
    for _ in range(registry.LOCKOUT_PER_SOURCE - 1):
        error_of(*call(service, "POST", "/fleet/enroll", bad), "token_invalid")
    with caplog.at_level(logging.WARNING, logger="fleet.lead"):
        err = error_of(*call(service, "POST", "/fleet/enroll", bad), "rate_limited")
        error_of(*call(service, "POST", "/fleet/enroll", bad), "rate_limited")
    assert 0 < err.retry_after <= registry.LOCKOUT_WINDOW
    assert caplog.text.count("invalid join tokens in 15 minutes") == 1  # said once
    # The same address, a valid token: enrolled.
    _k, good = enroll_body(join)
    assert call(service, "POST", "/fleet/enroll", good)[0] == 200
    # Many invalid tokens from everywhere lock nobody out either.
    for i in range(60):
        call(service, "POST", "/fleet/enroll", bad, remote=f"203.0.113.{i}")
    # (The token is made on the real clock, the lead runs on the test one.)
    _k, good2 = enroll_body(new_join(paths, ttl=3600), node_id=NODE2, instance=INSTANCE2)
    assert call(service, "POST", "/fleet/enroll", good2, remote="203.0.113.200")[0] == 200
    # The window slides: back to token_invalid.
    clock.advance(registry.LOCKOUT_WINDOW + 1)
    error_of(*call(service, "POST", "/fleet/enroll", bad), "token_invalid")


def test_eight_threads_one_token_exactly_one_enrollment(lead):
    paths, service, _ = lead
    join = new_join(paths)
    nodes = [f"6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a{i:02d}" for i in range(8)]
    bodies = [enroll_body(join, node_id=n)[1] for n in nodes]
    barrier = threading.Barrier(8)
    results = [None] * 8

    def go(i):
        barrier.wait()
        results[i] = call(service, "POST", "/fleet/enroll", bodies[i], remote=f"192.0.2.{i}")[0]

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200] + [403] * 7
    assert sum(service.registry.node(n) is not None for n in nodes) == 1


def test_a_bad_csr_is_refused_and_does_not_spend_the_token(lead):
    paths, service, _ = lead
    join = new_join(paths)
    _k, other = csr_pem(cn=f"node-{NODE2}")
    _k, body = enroll_body(join, csr=other)
    err = error_of(*call(service, "POST", "/fleet/enroll", body), "bad_request")
    assert err.reasons == ("csr_pem: csr: names another node",)
    _k, body = enroll_body(join)
    assert call(service, "POST", "/fleet/enroll", body)[0] == 200


def test_a_node_id_enrolled_here_cannot_enroll_again(lead):
    """A clone joining with a fresh token: node_exists (docs/fleet.md)."""
    paths, service, _ = lead
    enroll(service, paths)
    _k, body = enroll_body(new_join(paths), instance=INSTANCE2)
    error_of(*call(service, "POST", "/fleet/enroll", body), "node_exists")


def test_a_token_can_name_the_node_and_its_site(lead):
    paths, service, _ = lead
    join = new_join(paths, name="Office Pi", site="Office")
    _k, body = enroll_body(join)
    _s, out = call(service, "POST", "/fleet/enroll", body)
    cert = protocol.decode(protocol.EnrollResponse, out).cert_chain[0]
    node = service.registry.node(NODE)
    assert (node["node_name"], node["site_name"]) == ("Office Pi", "Office")
    # The operator's names stay...
    status, _ = call(service, "POST", "/fleet/heartbeat",
                     heartbeat_body(node_name="Renamed", site_name="Elsewhere"), cert_pem=cert)
    assert status == 200
    node = service.registry.node(NODE)
    assert (node["node_name"], node["site_name"]) == ("Office Pi", "Office")
    # ...while a node-chosen name follows the node.
    _k, other = enroll(service, paths, node_id=NODE2, instance=INSTANCE2)
    call(service, "POST", "/fleet/heartbeat",
         heartbeat_body(node_id=NODE2, instance=INSTANCE2, node_name="Renamed",
                        site_name="Elsewhere"), cert_pem=other.cert_chain[0])
    node = service.registry.node(NODE2)
    assert (node["node_name"], node["site_name"]) == ("Renamed", "Elsewhere")


# -- mTLS identity ---------------------------------------------------------------


@pytest.fixture
def enrolled(lead):
    paths, service, clock = lead
    key, resp = enroll(service, paths)
    return paths, service, clock, key, resp


def test_heartbeat_needs_a_certificate_issued_here_for_that_node(enrolled, lead):
    paths, service, _clock, _key, resp = enrolled
    body = heartbeat_body()
    error_of(*call(service, "POST", "/fleet/heartbeat", body), "unauthenticated")
    # Another node's certificate, presented with this node's id.
    _k, other = enroll(service, paths, node_id=NODE2, instance=INSTANCE2)
    error_of(*call(service, "POST", "/fleet/heartbeat", body, cert_pem=leaf_of(other)),
             "unauthenticated")
    # A certificate the lead never issued (another fleet with its own CA).
    _p, stranger = make_lead(paths.root.parent / "other")
    _k, foreign = enroll(stranger, _p)
    error_of(*call(service, "POST", "/fleet/heartbeat", body, cert_pem=leaf_of(foreign)),
             "unauthenticated")
    status, out = call(service, "POST", "/fleet/heartbeat", body, cert_pem=leaf_of(resp))
    assert status == 200, out


def test_heartbeat_records_the_node_and_answers_in_its_version(enrolled):
    _paths, service, _clock, _key, resp = enrolled
    body = heartbeat_body(epoch=service.endpoints.epoch)
    body["protocol"] = 3  # a newer agent that also speaks 1
    status, out = call(service, "POST", "/fleet/heartbeat", body, cert_pem=leaf_of(resp))
    assert status == 200
    hb = protocol.HeartbeatResponse.from_wire(out)
    assert hb.protocol == 1 and hb.directives == () and hb.next_heartbeat == 60
    node = service.registry.node(NODE)
    assert node["version"] == "2.56.0" and node["lead_epoch"] == service.endpoints.epoch
    summary = json.loads(node["last_heartbeat"])
    assert summary["inventory"]["profiles"] == ["influxdb", "fleet"]


def test_heartbeat_sends_the_lead_list_when_the_node_holds_an_older_one(enrolled):
    paths, service, clock, _key, resp = enrolled
    status, out = call(service, "POST", "/fleet/heartbeat", heartbeat_body(epoch=0),
                       cert_pem=leaf_of(resp))
    (d,) = protocol.HeartbeatResponse.from_wire(out).directives
    assert isinstance(d, protocol.SetLeadEndpoints) and d.lead_endpoints == service.endpoints
    # A changed FLEET_LEAD_URLS is a new epoch, and the node is told.
    moved = rebuild(paths, urls=("https://new.example", LEAD_URL), clock=clock)
    assert moved.endpoints.epoch == service.endpoints.epoch + 1
    status, out = call(moved, "POST", "/fleet/heartbeat",
                       heartbeat_body(epoch=service.endpoints.epoch), cert_pem=leaf_of(resp))
    (d,) = protocol.HeartbeatResponse.from_wire(out).directives
    assert d.lead_endpoints.current == "https://new.example"


def test_heartbeat_asks_for_renewal_near_expiry(enrolled):
    _paths, service, clock, _key, resp = enrolled
    body = heartbeat_body(epoch=service.endpoints.epoch)
    clock.advance(75 * 86400)
    _s, out = call(service, "POST", "/fleet/heartbeat", body, cert_pem=leaf_of(resp))
    (d,) = protocol.HeartbeatResponse.from_wire(out).directives
    assert isinstance(d, protocol.RenewNow)
    clock.advance(16 * 86400)  # expired: no longer a credential
    error_of(*call(service, "POST", "/fleet/heartbeat", body, cert_pem=leaf_of(resp)),
             "unauthenticated")


# -- revocation and duplicates ----------------------------------------------------


def test_a_revoked_node_is_told_to_stop_and_cannot_renew(enrolled):
    paths, service, _clock, _key, resp = enrolled
    assert "Revoked node" in admin.revoke(paths, NODE[:8])
    status, out = call(service, "POST", "/fleet/heartbeat", heartbeat_body(),
                       cert_pem=leaf_of(resp))
    assert status == 200
    (d,) = protocol.HeartbeatResponse.from_wire(out).directives
    assert isinstance(d, protocol.Revoked) and d.reason == "operator"
    _k, csr = csr_pem()
    renew = {"protocol": 1, "protocol_min": 1, "node_id": NODE, "instance_id": INSTANCE,
             "csr_pem": csr}
    error_of(*call(service, "POST", "/fleet/renew", renew, cert_pem=leaf_of(resp)), "revoked")
    # Only a new join brings it back, and the old certificate stays dead.
    _k, again = enroll(service, paths, instance=INSTANCE2)
    _s, out = call(service, "POST", "/fleet/heartbeat", heartbeat_body(), cert_pem=leaf_of(resp))
    assert isinstance(protocol.HeartbeatResponse.from_wire(out).directives[0], protocol.Revoked)
    status, out = call(service, "POST", "/fleet/heartbeat",
                       heartbeat_body(instance=INSTANCE2, epoch=service.endpoints.epoch),
                       cert_pem=leaf_of(again))
    assert status == 200 and protocol.HeartbeatResponse.from_wire(out).directives == ()


def test_two_installations_with_one_node_id_are_duplicate_node(enrolled):
    _paths, service, _clock, _key, resp = enrolled
    cert = leaf_of(resp)
    third = "33333333-4444-4555-8666-777777777777"
    assert call(service, "POST", "/fleet/heartbeat", heartbeat_body(), cert_pem=cert)[0] == 200
    # A restart: a new instance replaces the old one.
    assert call(service, "POST", "/fleet/heartbeat", heartbeat_body(instance=INSTANCE2),
                cert_pem=cert)[0] == 200
    # The replaced instance speaks again: two live copies.
    error_of(*call(service, "POST", "/fleet/heartbeat", heartbeat_body(), cert_pem=cert),
             "duplicate_node")
    # From then on every instance of that node is refused, until a person acts.
    for inst in (INSTANCE2, third):
        error_of(*call(service, "POST", "/fleet/heartbeat", heartbeat_body(instance=inst),
                       cert_pem=cert), "duplicate_node")
    assert service.registry.node(NODE)["state"] == "conflict"


# -- renewal and leaving -------------------------------------------------------


def test_renew_issues_a_new_certificate_for_a_new_key(enrolled):
    _paths, service, _clock, key, resp = enrolled
    _k, same = csr_pem(key)
    body = {"protocol": 1, "protocol_min": 1, "node_id": NODE, "instance_id": INSTANCE,
            "csr_pem": same}
    err = error_of(*call(service, "POST", "/fleet/renew", body, cert_pem=leaf_of(resp)),
                   "bad_request")
    assert err.reasons == ("csr_pem: must be for a new key",)
    new_key, fresh = csr_pem()
    body["csr_pem"] = fresh
    status, out = call(service, "POST", "/fleet/renew", body, cert_pem=leaf_of(resp))
    assert status == 200, out
    renewed = protocol.decode(protocol.RenewResponse, out)
    leaf = pki.load_cert(renewed.cert_chain[0])
    assert pki.same_public_key(leaf.public_key(), new_key.public_key())
    assert pki.node_id_of(leaf) == NODE
    # The old certificate keeps working until it expires: a lost answer costs nothing.
    for cert in (leaf_of(resp), renewed.cert_chain[0]):
        assert call(service, "POST", "/fleet/heartbeat", heartbeat_body(),
                    cert_pem=cert)[0] == 200


def test_leave_is_final_and_idempotent(enrolled):
    _paths, service, _clock, _key, resp = enrolled
    body = {"protocol": 1, "protocol_min": 1, "node_id": NODE, "instance_id": INSTANCE,
            "drained": True, "remaining_bytes": 0}
    for _ in range(2):
        status, out = call(service, "POST", "/fleet/leave", body, cert_pem=leaf_of(resp))
        assert status == 200 and protocol.decode(protocol.LeaveResponse, out).state == "left"
    _s, out = call(service, "POST", "/fleet/heartbeat", heartbeat_body(), cert_pem=leaf_of(resp))
    (d,) = protocol.HeartbeatResponse.from_wire(out).directives
    assert isinstance(d, protocol.Revoked) and d.reason == "left"


# -- malformed requests and failures --------------------------------------------


class Unreadable(io.RawIOBase):
    def read(self, *_a):  # pragma: no cover - the point is that it is not called
        raise AssertionError("a too-large body must not be read")


def test_malformed_requests_get_protocol_errors(lead):
    _paths, service, _ = lead
    error_of(*call(service, "GET", "/fleet/enroll"), "bad_request")
    error_of(*call(service, "POST", "/fleet/nope", {}), "bad_request")
    error_of(*call(service, "POST", "/fleet/heartbeat", raw=b"{not json"), "bad_request")
    error_of(*call(service, "POST", "/fleet/heartbeat", raw=b"{}", length=""), "bad_request")
    error_of(*call(service, "POST", "/fleet/heartbeat", raw=b"{}", length="99"), "bad_request")
    status, body = call(service, "POST", "/fleet/heartbeat", raw=b"",
                        length=str(protocol.MAX_BODY_BYTES + 1))
    error_of(status, body, "too_large")
    err = error_of(*call(service, "POST", "/fleet/heartbeat",
                         {"protocol": 9, "protocol_min": 7}), "protocol_unsupported")
    assert (err.supported_min, err.supported_max) == (1, 1)
    err = error_of(*call(service, "POST", "/fleet/heartbeat",
                         {"protocol": 1, "protocol_min": 1, "node_id": "nope"}), "bad_request")
    assert "node_id: malformed" in err.reasons and "inventory: required" in err.reasons


def test_an_internal_failure_never_shows_its_text(lead, monkeypatch, caplog):
    _paths, service, _ = lead

    def boom(*_a):
        raise RuntimeError("DETAIL-THAT-STAYS-IN-THE-LOG")

    monkeypatch.setattr(service.registry, "enroll", boom)
    with caplog.at_level(logging.ERROR, logger="fleet.lead"):
        _k, body = enroll_body(new_join(_paths))
        status, body = call(service, "POST", "/fleet/enroll", body)
    err = error_of(status, body, "internal")
    assert "DETAIL" not in json.dumps(body)
    assert err.error_id in caplog.text and "DETAIL-THAT-STAYS-IN-THE-LOG" in caplog.text


def test_the_join_token_reaches_no_log_and_no_file(lead, caplog):
    paths, service, _ = lead
    with caplog.at_level(logging.DEBUG):
        join = new_join(paths)
        _k, body = enroll_body(join)
        call(service, "POST", "/fleet/enroll", body)  # used
        call(service, "POST", "/fleet/enroll", body)  # refused: used
        body["join_token"] = "q" * 43
        call(service, "POST", "/fleet/enroll", body)  # refused: unknown
    secret = join.token.reveal()
    assert secret not in caplog.text and "q" * 43 not in caplog.text
    for f in paths.root.rglob("*"):
        if f.is_file():
            assert secret.encode() not in f.read_bytes(), f
    assert registry.token_hash(secret) in [r["token_hash"] for r in _token_rows(paths)]


def _token_rows(paths):
    import sqlite3
    conn = sqlite3.connect(paths.registry)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT token_hash FROM tokens")]
    finally:
        conn.close()


def test_what_the_lead_handles_prints_and_logs_redacted(lead):
    """The join string the lead made and the request it decodes carry the
    token as a Secret: a LogRecord formatted with either, or redacted(),
    never shows it. (Formatted here, never handed to a logger.)"""
    paths, _service, _ = lead
    join = new_join(paths)
    _k, body = enroll_body(join)
    req = protocol.decode(protocol.EnrollRequest, body)
    secret = join.token.reveal()
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    for obj in (join, req):
        for pattern in ("%s", "%r"):
            record = logging.LogRecord("fleet.lead", logging.INFO, __file__, 0, pattern,
                                       (obj,), None)
            assert secret not in fmt.format(record)
        assert secret not in json.dumps(protocol.redacted(obj))
    # The wire is the one place it goes.
    assert secret in protocol.encode_json(req)


def _revoke_after_claim(service, monkeypatch):
    """Revoke the node right after the request's instance check, i.e.
    between its checks and its write."""
    real_claim = service.registry.claim_instance

    def claim_then_revoke(node_id, instance_id):
        result = real_claim(node_id, instance_id)
        service.registry.set_state(node_id, "revoked", "operator")
        return result

    monkeypatch.setattr(service.registry, "claim_instance", claim_then_revoke)


def test_renew_for_a_node_revoked_between_check_and_write(enrolled, monkeypatch):
    paths, service, _clock, _key, resp = enrolled
    _revoke_after_claim(service, monkeypatch)
    before = _cert_count(paths)
    _k, csr = csr_pem()
    renew = {"protocol": 1, "protocol_min": 1, "node_id": NODE, "instance_id": INSTANCE,
             "csr_pem": csr}
    error_of(*call(service, "POST", "/fleet/renew", renew, cert_pem=leaf_of(resp)), "revoked")
    assert _cert_count(paths) == before  # the certificate signed is never recorded


def test_heartbeat_for_a_node_revoked_between_check_and_write(enrolled, monkeypatch):
    _paths, service, _clock, _key, resp = enrolled
    _revoke_after_claim(service, monkeypatch)
    status, out = call(service, "POST", "/fleet/heartbeat", heartbeat_body(),
                       cert_pem=leaf_of(resp))
    assert status == 200
    (d,) = protocol.HeartbeatResponse.from_wire(out).directives
    assert isinstance(d, protocol.Revoked) and d.reason == "operator"
    assert service.registry.node(NODE)["last_heartbeat"] is None


def test_leave_never_turns_revoked_into_left(enrolled, monkeypatch):
    _paths, service, _clock, _key, resp = enrolled
    _revoke_after_claim(service, monkeypatch)
    body = {"protocol": 1, "protocol_min": 1, "node_id": NODE, "instance_id": INSTANCE,
            "drained": True, "remaining_bytes": 0}
    error_of(*call(service, "POST", "/fleet/leave", body, cert_pem=leaf_of(resp)), "revoked")
    assert service.registry.node(NODE)["state"] == "revoked"


def _cert_count(paths):
    import sqlite3
    conn = sqlite3.connect(paths.registry)
    try:
        return conn.execute("SELECT COUNT(*) FROM certs").fetchone()[0]
    finally:
        conn.close()
