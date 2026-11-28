"""Joining: the root is pinned, the chain checked, the key stays, nothing of
a failed join is kept, and the token never shows."""

import io
import json
import logging
import stat

import pytest
from fakelead import CA, FLEET, LEAD, LEAD2, NODE, SITE, TOKEN, FakeLead, error, join_string

from fleet import join, layout, protocol, settings, statefile


@pytest.fixture
def paths(tmp_path):
    return layout.node_paths(tmp_path / "state")


@pytest.fixture
def cfg():
    return settings.load({"FLEET_NODE_ID": NODE, "FLEET_NODE_NAME": "Kitchen Pi",
                          "FLEET_SITE_NAME": "Home"})


def node_files(paths):
    d = paths.enrollment.parent
    return sorted(f.name for f in d.iterdir()) if d.is_dir() else []


def run_join(text, paths, lead, env=None):
    out, err = io.StringIO(), io.StringIO()
    code = join.main([], stdin=io.StringIO(text + "\n"), stdout=out, stderr=err,
                     transport=lead, paths=paths)
    return code, json.loads(out.getvalue()), err.getvalue()


@pytest.fixture(autouse=True)
def node_env(monkeypatch):
    monkeypatch.setenv("FLEET_NODE_ID", NODE)


def test_a_join_pins_the_root_enrolls_and_keeps_the_key(paths, cfg):
    lead = FakeLead()
    lead.serve_all()
    js = protocol.JoinString.parse(join_string(lead.ca))
    result = join.enroll(js, cfg, paths, lead)
    assert result["joined"] and result["fleet_id"] == FLEET and result["site_id"] == SITE
    assert result["lead_urls"] == [LEAD] and result["lead_in_use"] == LEAD
    # The root was fetched before anything was pinned, then everything else
    # was verified against that root alone.
    (root_call,), (enroll_call,) = lead.to("/fleet/root"), lead.to("/fleet/enroll")
    assert root_call.tls.root is None and root_call.method == "GET"
    assert enroll_call.tls.root == paths.root_cert and enroll_call.tls.chain is None
    body = enroll_call.body
    assert body["node_id"] == NODE and body["fleet_id"] == FLEET
    assert body["node_name"] == "Kitchen Pi" and body["site_name"] == "Home"
    assert body["software"] == {"version": "dev", "edition": "pro"}
    assert "PRIVATE KEY" not in json.dumps(body)
    # Files: key 0600, enrollment last and complete.
    assert node_files(paths) == ["chain.pem", "enrollment.json", "key.pem", "root.pem"]
    assert stat.S_IMODE(paths.key.stat().st_mode) == 0o600
    assert stat.S_IMODE(paths.enrollment.stat().st_mode) == 0o600
    e = statefile.read_object(paths.enrollment)
    assert e["node_id"] == NODE and e["fleet_id"] == FLEET and e["site_id"] == SITE
    assert e["root_sha256"] == lead.ca.fingerprint and e["heartbeat_interval"] == 60
    assert e["lead_endpoints"]["urls"] == [LEAD] and e["lead_endpoints"]["epoch"] == 1
    assert TOKEN not in paths.enrollment.read_text()
    assert not any(TOKEN in (paths.enrollment.parent / f).read_text() for f in node_files(paths))


def test_the_command_reads_the_join_string_from_stdin_and_never_prints_the_token(
        paths, caplog):
    lead = FakeLead()
    lead.serve_all()
    with caplog.at_level(logging.DEBUG):
        code, result, err = run_join(join_string(lead.ca), paths, lead)
    assert code == 0 and result["joined"] is True
    for text in (json.dumps(result), err, caplog.text):
        assert TOKEN not in text
        assert "PRIVATE KEY" not in text


def test_a_root_that_is_not_the_pinned_one_is_refused_before_anything_is_sent(paths, cfg):
    lead = FakeLead()
    lead.serve_all()
    js = protocol.JoinString.parse(join_string(lead.ca, fingerprint=CA().fingerprint))
    with pytest.raises(join.JoinFailed) as err:
        join.enroll(js, cfg, paths, lead)
    assert err.value.code == "root_mismatch" and "REFUSED" in err.value.message
    assert lead.to("/fleet/enroll") == []
    assert node_files(paths) == []


def test_a_chain_that_does_not_reach_the_pinned_root_is_refused(paths, cfg):
    lead = FakeLead()
    lead.serve_all()
    lead.serve_enroll(issuer=CA("impostor"))
    with pytest.raises(join.JoinFailed) as err:
        join.enroll(protocol.JoinString.parse(join_string(lead.ca)), cfg, paths, lead)
    assert err.value.code == "chain_invalid"
    assert "does not lead to the pinned root" in err.value.message
    assert node_files(paths) == []


def test_a_certificate_for_another_key_is_refused(paths, cfg):
    from cryptography.hazmat.primitives.asymmetric import ec
    lead = FakeLead()
    lead.serve_all()
    lead.serve_enroll(public_key=ec.generate_private_key(ec.SECP256R1()).public_key())
    with pytest.raises(join.JoinFailed) as err:
        join.enroll(protocol.JoinString.parse(join_string(lead.ca)), cfg, paths, lead)
    assert err.value.code == "chain_invalid" and "not for this node's key" in err.value.message
    assert node_files(paths) == []


def test_a_certificate_for_another_node_is_refused(paths, cfg):
    lead = FakeLead()
    lead.serve_all()

    def enroll(body, tls):
        leaf_chain = lead.ca.issue(body["csr_pem"], "11111111-2222-4333-8444-555555555555")
        return 200, protocol.EnrollResponse(
            protocol=1, fleet_id=FLEET, node_id=NODE, site_id=SITE, cert_chain=leaf_chain,
            not_after=protocol.now_rfc3339(),
            lead_endpoints=protocol.LeadEndpoints(epoch=1, current=LEAD, urls=(LEAD,),
                                                  lead_mode="node"),
            heartbeat_interval=60)
    lead.routes[("POST", LEAD + "/fleet/enroll")] = enroll
    with pytest.raises(join.JoinFailed) as err:
        join.enroll(protocol.JoinString.parse(join_string(lead.ca)), cfg, paths, lead)
    assert err.value.code == "chain_invalid" and "names another node" in err.value.message


def test_an_already_expired_certificate_is_refused(paths, cfg):
    from datetime import timedelta

    from fakelead import now
    lead = FakeLead()
    lead.serve_all()
    lead.serve_enroll(start=now() - timedelta(days=100), days=90)
    with pytest.raises(join.JoinFailed) as err:
        join.enroll(protocol.JoinString.parse(join_string(lead.ca)), cfg, paths, lead)
    assert err.value.code == "chain_invalid" and "not valid at this time" in err.value.message


@pytest.mark.parametrize("code,words", [
    ("token_invalid", "unknown, already used, or expired"),
    ("node_exists", "already has a node with this node id"),
    ("duplicate_node", "two installations"),
])
def test_token_and_node_errors_end_in_a_clear_message_and_a_clean_state(paths, code, words):
    lead = FakeLead()
    lead.serve_root()
    status_code, body = error(code)
    lead.answer("POST", LEAD + "/fleet/enroll", status_code, body)
    rc, result, err = run_join(join_string(lead.ca), paths, lead)
    assert rc == 1 and result == {"joined": False, "code": code}
    assert words in err and TOKEN not in err
    assert node_files(paths) == []


def test_no_lead_answering_changes_nothing_and_says_the_token_is_still_good(paths):
    lead = FakeLead()
    rc, result, err = run_join(join_string(lead.ca, LEAD, LEAD2), paths, lead)
    assert rc == 1 and result["code"] == "unreachable"
    assert LEAD in err and LEAD2 in err and "still unused" in err
    assert node_files(paths) == []


def test_the_leads_are_tried_in_order(paths, cfg):
    lead = FakeLead()
    lead.down.add(LEAD)
    lead.serve_all(LEAD2)
    js = protocol.JoinString.parse(join_string(lead.ca, LEAD, LEAD2))
    result = join.enroll(js, cfg, paths, lead)
    assert result["lead_in_use"] == LEAD2
    assert [c.url for c in lead.calls] == [LEAD + "/fleet/root", LEAD2 + "/fleet/root",
                                           LEAD2 + "/fleet/enroll"]


def test_a_busy_lead_is_not_a_refusal(paths):
    lead = FakeLead()
    lead.serve_root()
    status_code, body = error("unavailable", retry_after=30)
    lead.answer("POST", LEAD + "/fleet/enroll", status_code, body)
    rc, result, err = run_join(join_string(lead.ca), paths, lead)
    assert rc == 1 and result["code"] == "lead_unavailable" and "try the same join" in err
    assert node_files(paths) == []


def test_joining_twice_is_refused_and_keeps_the_first_enrollment(paths):
    lead = FakeLead()
    lead.serve_all()
    assert run_join(join_string(lead.ca), paths, lead)[0] == 0
    before = paths.enrollment.read_text(), paths.key.read_text()
    rc, result, err = run_join(join_string(lead.ca), paths, lead)
    assert rc == 1 and result["code"] == "already_joined" and "fleet leave" in err
    assert (paths.enrollment.read_text(), paths.key.read_text()) == before
    assert len(lead.to("/fleet/enroll")) == 1


@pytest.mark.parametrize("content,code", [
    ("{broken", "state_corrupt"),
    (json.dumps({"node_id": "11111111-2222-4333-8444-555555555555"}), "identity_mismatch"),
    (json.dumps({"node_id": NODE, "fleet_id": FLEET, "revoked": {"at": "x"}}), "revoked"),
])
def test_a_join_never_writes_over_a_state_that_waits_for_a_person(paths, content, code):
    statefile.write_bytes(paths.enrollment, content.encode())
    lead = FakeLead()
    lead.serve_all()
    rc, result, _ = run_join(join_string(lead.ca), paths, lead)
    assert rc == 1 and result["code"] == code
    assert paths.enrollment.read_text() == content and lead.calls == []


@pytest.mark.parametrize("text", ["", "hello", "spfleet1_", "spfleet1_!!!"])
def test_something_that_is_not_a_join_string(paths, text):
    lead = FakeLead()
    rc, result, err = run_join(text, paths, lead)
    assert rc == 2 and result["code"] == "bad_join_string" and lead.calls == []


def test_without_a_node_id_nothing_is_sent(paths, monkeypatch):
    monkeypatch.delenv("FLEET_NODE_ID")
    lead = FakeLead()
    lead.serve_all()
    rc, result, _ = run_join(join_string(lead.ca), paths, lead)
    assert rc == 1 and result["code"] == "no_identity" and lead.calls == []


def test_a_store_in_the_enroll_answer_is_kept_not_applied(paths, cfg):
    lead = FakeLead()
    lead.serve_all()
    store = protocol.StoreDescriptor(
        store_id=SITE, url="https://store.example", org_id="0" * 16, bucket_id="1" * 16,
        bucket_name=f"node-{NODE}", write_token=protocol.Secret("W" * 32), ca_pem=lead.ca.root_pem)
    lead.serve_enroll(store=store)
    result = join.enroll(protocol.JoinString.parse(join_string(lead.ca)), cfg, paths, lead)
    assert result["store_received"] is True
    e = statefile.read_object(paths.enrollment)
    assert e["store"]["applied"] is False and e["store"]["store_id"] == SITE
    assert "write_token" not in e["store"] and "W" * 32 not in paths.enrollment.read_text()
    assert paths.store_token.read_text() == "W" * 32
    assert stat.S_IMODE(paths.store_token.stat().st_mode) == 0o600


def test_an_unexpected_error_keeps_nothing_and_prints_no_traceback(paths, monkeypatch):
    lead = FakeLead()
    lead.serve_all()

    def boom(*a, **k):
        raise RuntimeError(TOKEN)  # even an error that quotes the token
    monkeypatch.setattr(join.nodestate, "write_enrollment", boom)
    rc, result, err = run_join(join_string(lead.ca), paths, lead)
    assert rc == 1 and result["code"] == "internal" and "error id" in err
    assert TOKEN not in err and node_files(paths) == []
