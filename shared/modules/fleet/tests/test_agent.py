"""The agent once joined: heartbeats, lead failover and backoff, directives,
renewal, revocation, an expired certificate, and never crashing."""

import json
import logging
import random
from datetime import timedelta

import pytest
from fakelead import CA, FLEET, LEAD, LEAD2, NODE, FakeLead, enrolled, error, now

from fleet import agent, layout, nodestate, protocol, settings, statefile, status
from fleet.transport import TransportError

LEAD3 = "https://lead3.example"


class Clock:
    """Real time plus ``offset``: by default just past the enrollment's
    first heartbeat interval (60 s), so the first step sends one."""

    def __init__(self, offset: float = 61.0):
        self.t = now().timestamp() + offset

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def paths(tmp_path):
    return layout.node_paths(tmp_path / "state")


def make(paths, lead, clock=None, env=None, **cfg_env):
    cfg = settings.load({"FLEET_NODE_ID": NODE, **cfg_env})
    return agent.Agent(cfg, paths, lead, clock=clock or Clock(), rng=random.Random(7),
                       env=env or {"SMOKING_PI_VERSION": "2.56.0",
                                   "COMPOSE_PROFILES": "influxdb,mcp,fleet"})


def enrollment(paths):
    return statefile.read_object(paths.enrollment)


# -- heartbeats ----------------------------------------------------------------


def test_a_heartbeat_goes_over_mtls_and_says_what_the_node_is(paths):
    lead = enrolled(paths)
    clock = Clock()
    a = make(paths, lead, clock)
    delay = a.step()
    (call,) = lead.to("/fleet/heartbeat")
    assert call.tls.root == paths.root_cert and call.tls.chain == paths.chain
    assert call.tls.key == paths.key
    body = call.body
    assert body["node_id"] == NODE and body["lead_epoch"] == 1
    assert body["instance_id"] == a.instance_id and protocol.valid_uuid(a.instance_id)
    inv = body["inventory"]
    assert inv == {"node_name": "node-6f1c2a3b", "site_name": "default", "version": "2.56.0",
                   "edition": "pro", "profiles": ["influxdb", "mcp", "fleet"],
                   "capabilities": ["influxdb"]}
    # Not known to the agent yet: absent, never invented.
    for absent in ("doctor", "measuring", "uplink"):
        assert absent not in inv
    for absent in ("replication", "seed", "store_id"):
        assert absent not in body
    protocol.decode(protocol.HeartbeatRequest, body)
    report = a.report()
    assert report["state"] == "enrolled" and report["health"] == "ok"
    assert report["lead_in_use"] == LEAD and report["last_heartbeat"]
    assert report["fleet_id"] == FLEET and report["failures"] == 0
    assert a.next_attempt == pytest.approx(clock() + 60)
    assert 1 <= delay <= status.TICK_SECONDS
    # Not before next_heartbeat.
    clock.advance(30)
    a.step()
    assert len(lead.to("/fleet/heartbeat")) == 1
    clock.advance(31)
    a.step()
    assert len(lead.to("/fleet/heartbeat")) == 2


def test_the_interval_is_the_one_the_lead_returns(paths):
    lead = enrolled(paths)
    lead.serve_heartbeat(next_heartbeat=300)
    clock = Clock()
    a = make(paths, lead, clock)
    a.step()
    assert a.next_attempt == pytest.approx(clock() + 300)


def test_the_first_heartbeat_waits_for_the_enrollments_interval(paths):
    lead = enrolled(paths)
    clock = Clock(0)
    a = make(paths, lead, clock)
    a.step()  # enrolled just now, heartbeat_interval 60
    assert lead.to("/fleet/heartbeat") == [] and a.report()["health"] == "ok"
    clock.advance(61)
    a.step()
    assert len(lead.to("/fleet/heartbeat")) == 1


def test_leads_are_tried_in_order_and_the_one_that_answered_is_kept(paths):
    lead = enrolled(paths, None, LEAD, LEAD2)
    lead.down.add(LEAD)
    clock = Clock()
    a = make(paths, lead, clock)
    a.step()
    assert [c.url for c in lead.calls] == [LEAD + "/fleet/heartbeat",
                                           LEAD2 + "/fleet/heartbeat"]
    assert a.in_use == LEAD2 and a.report()["lead_in_use"] == LEAD2
    lead.calls.clear()
    lead.down.discard(LEAD)
    clock.advance(61)
    a.step()
    assert [c.url for c in lead.calls] == [LEAD2 + "/fleet/heartbeat"]


def test_fleet_lead_urls_are_tried_after_the_leads_own_list(paths):
    lead = enrolled(paths)
    lead.down.add(LEAD)
    lead.serve_heartbeat(LEAD3)
    a = make(paths, lead, FLEET_LEAD_URLS=f"{LEAD},{LEAD3}")
    assert a.lead_order(a.local.endpoints if a.local else
                        nodestate.lead_endpoints(enrollment(paths))) == [LEAD, LEAD3]
    a.step()
    assert a.in_use == LEAD3


def test_no_lead_backs_off_doubling_with_jitter_and_logs_once(paths, caplog):
    lead = enrolled(paths, None, LEAD, LEAD2)
    lead.down.update({LEAD, LEAD2})
    clock = Clock()
    a = make(paths, lead, clock)
    waits = []
    with caplog.at_level(logging.INFO, logger="fleet"):
        for _ in range(9):
            a.step()
            waits.append(a.next_attempt - clock())
            clock.advance(a.next_attempt - clock() + 0.1)
    bases = [30, 60, 120, 240, 480, 960, 1800, 1800, 1800]
    for w, b in zip(waits, bases):
        assert 0.8 * b <= w <= 1.2 * b
    assert a.failures == 9 and a.report()["last_error"] == "heartbeat: no lead answered"
    assert caplog.text.count("heartbeat failed") <= 2  # once, and once again an hour on
    # Every round tried every lead, one heartbeat at a time.
    assert len(lead.calls) == 18
    # Grace passed: health is failing, and the healthcheck says so.
    report = a.report()
    assert report["health"] == "failing"
    assert not status.healthy(dict(report, at=clock()), now=clock())
    # The lead comes back: the next attempt resets everything.
    lead.down.clear()
    clock.advance(a.next_attempt - clock() + 0.1)
    with caplog.at_level(logging.INFO, logger="fleet"):
        a.step()
    assert a.failures == 0 and a.backoff == 0 and a.report()["health"] == "ok"
    assert "answered again, after 9 failed attempt(s)" in caplog.text


def test_backoff_answers_are_retried_on_the_same_lead_after_retry_after(paths):
    lead = enrolled(paths, None, LEAD, LEAD2)
    status_code, body = error("rate_limited", retry_after=600)
    lead.answer("POST", LEAD + "/fleet/heartbeat", status_code, body)
    clock = Clock()
    a = make(paths, lead, clock)
    a.step()
    assert [c.url for c in lead.calls] == [LEAD + "/fleet/heartbeat"]
    assert a.next_attempt - clock() >= 600 and a.in_use == LEAD
    assert "rate_limited" in a.report()["last_error"]


def test_not_current_lead_with_a_newer_list_moves_to_it(paths):
    lead = enrolled(paths, None, LEAD)
    status_code, body = error("not_current_lead", lead_endpoints=protocol.LeadEndpoints(
        epoch=2, current=LEAD2, urls=(LEAD2, LEAD), lead_mode="node"))
    lead.answer("POST", LEAD + "/fleet/heartbeat", status_code, body)
    lead.serve_heartbeat(LEAD2)
    a = make(paths, lead)
    a.step()
    assert [c.url for c in lead.calls] == [LEAD + "/fleet/heartbeat",
                                           LEAD2 + "/fleet/heartbeat"]
    e = enrollment(paths)["lead_endpoints"]
    assert e["epoch"] == 2 and e["current"] == LEAD2


@pytest.mark.parametrize("code", ["unauthenticated", "duplicate_node", "bad_request"])
def test_a_stop_answer_stops_sending_and_shows_in_status(paths, code):
    lead = enrolled(paths)
    status_code, body = error(code)
    lead.answer("POST", LEAD + "/fleet/heartbeat", status_code, body)
    clock = Clock()
    a = make(paths, lead, clock)
    a.step()
    report = a.report()
    assert report["state"] == "stopped" and report["health"] == "failing"
    assert code in report["detail"] and report["enrolled"] is True
    for _ in range(10):
        clock.advance(1800)
        a.step()
    assert len(lead.to("/fleet/heartbeat")) == 1
    clock.advance(agent.STOP_RETRY_SECONDS)
    lead.serve_heartbeat()
    a.step()
    assert len(lead.to("/fleet/heartbeat")) == 2 and a.report()["state"] == "enrolled"


# -- directives ----------------------------------------------------------------


def _results(call):
    return {r["id"]: r["status"] for r in call.body.get("directive_results", [])}


def test_directives_are_applied_in_order_and_reported_next_time(paths, caplog):
    lead = enrolled(paths, None, LEAD, LEAD2)
    store = {"store_id": "11111111-2222-4333-8444-555555555555",
             "url": "https://store.example", "org_id": "0" * 16, "bucket_id": "1" * 16,
             "bucket_name": f"node-{NODE}", "write_token": "WRITEtokenWRITEtoken",
             "ca_pem": lead.ca.root_pem}
    lead.directives.append([
        {"id": "a", "type": "teleport"},
        {"id": "b", "type": "renew_now", "extra": 1},
        {"id": "c", "type": "lead_endpoints", "lead_endpoints": {
            "epoch": 5, "current": LEAD2, "urls": [LEAD2, LEAD], "lead_mode": "node"}},
        {"id": "d", "type": "lead_endpoints", "lead_endpoints": {
            "epoch": 4, "current": LEAD, "urls": [LEAD], "lead_mode": "node"}},
        {"id": "e", "type": "repoint_store", "store": store},
        {"id": "f", "type": "start_seed", "since": "2026-01-01T00:00:00Z",
         "until": "2026-02-01T00:00:00Z", "max_bytes_per_second": 4096},
        {"id": "g", "type": "repoint_store", "store": {"url": "nope"}},
        {"type": "renew_now"},
    ])
    old_key = paths.key.read_text()
    clock = Clock()
    a = make(paths, lead, clock)
    with caplog.at_level(logging.INFO, logger="fleet"):
        a.step()
    assert "directive #7 dropped" in caplog.text
    # renew_now: a new key, a new certificate.
    assert paths.key.read_text() != old_key and len(lead.to("/fleet/renew")) == 1
    # lead_endpoints: epoch 5 taken, 4 not.
    e = enrollment(paths)
    assert e["lead_endpoints"]["epoch"] == 5 and e["lead_endpoints"]["current"] == LEAD2
    # repoint_store / start_seed kept, not applied; the token in its own file.
    assert e["store"]["applied"] is False and "write_token" not in e["store"]
    assert paths.store_token.read_text() == "WRITEtokenWRITEtoken"
    assert "WRITEtoken" not in paths.enrollment.read_text()
    assert e["seed_requested"]["max_bytes_per_second"] == 4096
    clock.advance(61)
    a.step()
    second = lead.to("/fleet/heartbeat")[-1]
    assert second.url == LEAD2 + "/fleet/heartbeat"
    assert _results(second) == {"a": "ignored", "b": "done", "c": "done", "d": "ignored",
                                "e": "ignored", "f": "ignored", "g": "invalid"}
    assert second.body["lead_epoch"] == 5
    clock.advance(61)
    a.step()
    assert _results(lead.to("/fleet/heartbeat")[-1]) == {}
    assert "WRITEtoken" not in caplog.text


def test_results_wait_for_a_heartbeat_that_gets_through(paths):
    lead = enrolled(paths)
    lead.directives.append([{"id": "x", "type": "teleport"}])
    clock = Clock()
    a = make(paths, lead, clock)
    a.step()
    lead.down.add(LEAD)
    clock.advance(61)
    a.step()
    lead.down.clear()
    clock.advance(a.next_attempt - clock() + 1)
    a.step()
    assert _results(lead.to("/fleet/heartbeat")[-1]) == {"x": "ignored"}


def test_revoked_overrides_the_rest_and_is_for_good(paths, caplog):
    lead = enrolled(paths)
    lead.directives.append([{"id": "r0", "type": "renew_now"},
                            {"id": "r1", "type": "revoked", "reason": "operator"}])
    clock = Clock()
    a = make(paths, lead, clock)
    with caplog.at_level(logging.INFO, logger="fleet"):
        a.step()
    assert lead.to("/fleet/renew") == []
    report = a.report()
    assert report["state"] == "revoked" and report["revoked_at"]
    assert report["health"] == "failing" and "fleet leave" in report["detail"]
    assert enrollment(paths)["revoked"]["reason"] == "operator"
    assert "revoked this node" in caplog.text
    # Nothing is sent again, not by this process, not by the next one.
    for _ in range(5):
        clock.advance(3600)
        a.step()
    b = make(paths, lead, clock)
    b.step()
    assert len(lead.to("/fleet/heartbeat")) == 1
    assert b.report()["state"] == "revoked"
    # Its credentials stay for `fleet leave`; nothing else is touched.
    assert paths.key.exists() and paths.chain.exists()


def test_a_revoked_error_answer_revokes_too(paths):
    lead = enrolled(paths)
    status_code, body = error("revoked")
    lead.answer("POST", LEAD + "/fleet/heartbeat", status_code, body)
    a = make(paths, lead)
    a.step()
    assert a.report()["state"] == "revoked" and "revoked" in enrollment(paths)


def test_a_malformed_response_is_a_failed_round_not_a_crash(paths):
    lead = enrolled(paths)
    lead.answer("POST", LEAD + "/fleet/heartbeat", 200, b"{not json")
    a = make(paths, lead)
    a.step()
    assert a.failures == 1 and a.report()["state"] == "enrolled"


# -- renewal -------------------------------------------------------------------


def test_renewal_starts_at_two_thirds_with_a_new_key(paths):
    lead = enrolled(paths)
    clock = Clock()
    a = make(paths, lead, clock)
    a.step()
    assert lead.to("/fleet/renew") == []
    old_key, old_chain = paths.key.read_text(), paths.chain.read_text()
    clock.advance(61 * 86400)
    a.step()
    (renew,) = lead.to("/fleet/renew")
    assert renew.tls.chain == paths.chain and renew.body["node_id"] == NODE
    assert "PRIVATE" not in json.dumps(renew.body)
    assert paths.key.read_text() != old_key and paths.chain.read_text() != old_chain
    assert enrollment(paths)["not_after"] != ""
    assert a.report()["state"] == "enrolled"
    assert not list(paths.enrollment.parent.glob("*.new"))


@pytest.mark.parametrize("how", ["down", "foreign_ca", "wrong_key"])
def test_a_failed_renewal_keeps_the_old_pair(paths, how):
    lead = enrolled(paths)
    if how == "down":
        lead.routes.pop(("POST", LEAD + "/fleet/renew"))
    elif how == "foreign_ca":
        lead.serve_renew(issuer=CA("impostor"))
    else:
        from cryptography.hazmat.primitives.asymmetric import ec
        lead.serve_renew(public_key=ec.generate_private_key(ec.SECP256R1()).public_key())
    old = paths.key.read_text(), paths.chain.read_text(), paths.enrollment.read_text()
    clock = Clock(61 * 86400)
    a = make(paths, lead, clock)
    a.step()
    assert len(lead.to("/fleet/renew")) == 1
    assert (paths.key.read_text(), paths.chain.read_text(),
            paths.enrollment.read_text()) == old
    assert a.report()["state"] == "enrolled"
    # Retried later, not on every turn.
    clock.advance(61)
    a.step()
    assert len(lead.to("/fleet/renew")) == 1
    clock.advance(agent.RENEW_RETRY_SECONDS)
    a.step()
    assert len(lead.to("/fleet/renew")) == 2


def test_a_renewal_interrupted_between_the_two_swaps_is_finished(paths):
    lead = enrolled(paths)
    a = make(paths, lead)
    a.step()
    a._renew(a.local, a.clock())
    new_key, new_chain = paths.key.read_bytes(), paths.chain.read_bytes()
    # Rebuild the crash: key already swapped, chain.new still waiting.
    paths.chain.with_name("chain.pem.new").write_bytes(new_chain)
    paths.chain.write_text(lead.ca.root_pem)  # stands for the old chain
    assert nodestate.finish_renewal(paths) == "completed"
    assert paths.key.read_bytes() == new_key and paths.chain.read_bytes() == new_chain
    # A key whose certificate never came is discarded; the old pair stays.
    paths.key.with_name("key.pem.new").write_text("half")
    assert nodestate.finish_renewal(paths) == "discarded"
    assert paths.key.read_bytes() == new_key
    assert make(paths, lead).report()["state"] == "enrolled"


# -- states that wait for a person ----------------------------------------------


def test_a_certificate_that_expired_while_the_lead_was_away(paths):
    lead = enrolled(paths)
    lead.down.add(LEAD)
    clock = Clock(91 * 86400)
    a = make(paths, lead, clock)
    a.step()
    report = a.report()
    assert report["state"] == "cert_expired" and report["health"] == "failing"
    assert "fleet leave --force" in report["detail"] and "fleet join" in report["detail"]
    assert lead.calls == []


def test_corrupt_credentials_send_nothing_and_never_raise(paths):
    lead = enrolled(paths)
    paths.chain.write_text("garbage")
    a = make(paths, lead)
    a.tick()
    assert a.report()["state"] == "state_corrupt" and lead.calls == []


def test_an_unexpected_error_is_logged_with_an_id_and_retried(paths, caplog):
    enrolled(paths)

    class Boom:
        def request(self, *a):
            raise RuntimeError("secret detail")
    a = make(paths, Boom())
    with caplog.at_level(logging.INFO, logger="fleet"):
        report, delay = a.tick()
    assert "unexpected RuntimeError" in caplog.text and "error id" in caplog.text
    assert "secret detail" not in caplog.text
    assert delay > 0 and report["state"] == "enrolled"


def test_not_enrolled_is_idle_and_healthy(paths):
    lead = FakeLead()
    a = make(paths, lead)
    report, delay = a.tick()
    assert report["state"] == "not_enrolled" and report["health"] == "idle"
    assert status.healthy(dict(report, at=1.0), now=1.0) and lead.calls == []
    assert delay == status.TICK_SECONDS


def test_a_join_while_the_agent_runs_is_picked_up(paths):
    lead = FakeLead()
    lead.serve_all()
    a = make(paths, lead)
    a.step()
    assert a.report()["state"] == "not_enrolled"
    enrolled(paths, lead)
    a.clock.advance(120)
    a.step()
    assert len(lead.to("/fleet/heartbeat")) == 1


# -- secrets ---------------------------------------------------------------------


def test_status_and_logs_never_show_the_key(paths, caplog):
    from fakelead import private_key_marker
    lead = enrolled(paths)
    lead.directives.append([{"id": "b", "type": "renew_now"}])
    marker_before = private_key_marker(paths)
    clock = Clock()
    a = make(paths, lead, clock)
    with caplog.at_level(logging.DEBUG):
        a.step()
        clock.advance(61)
        a.step()
        status.write(a.report())
    for marker in (marker_before, private_key_marker(paths)):
        assert marker not in caplog.text
        assert marker not in status.path().read_text()
        for call in lead.calls:
            assert marker not in json.dumps(call.body)


def test_transport_errors_carry_no_text():
    assert str(TransportError("SSLError")) == "SSLError"


def test_describe_still_reads_only_files(paths):
    enrolled(paths)
    report, line = agent.describe(settings.load({"FLEET_NODE_ID": NODE}), paths,
                                  now() + timedelta(days=1))
    assert report["state"] == "enrolled" and LEAD in line
