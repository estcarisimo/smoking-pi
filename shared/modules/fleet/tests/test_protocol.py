"""The protocol contract: strict validation, round trips, compatibility rules."""

import json
import secrets

import pytest

from fleet import protocol as p

NODE = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
FLEET = "0b1e2f3a-0000-4000-8000-000000000001"
STORE = "11111111-2222-4333-8444-555555555555"
INSTANCE = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
FP = "ab" * 32
T0 = "2026-11-27T12:00:00Z"
T1 = "2026-11-28T12:00:00Z"
CERT = "-----BEGIN CERTIFICATE-----\nMIIBszCCAVmgAwIBAgIU\n-----END CERTIFICATE-----\n"
CSR = ("-----BEGIN CERTIFICATE REQUEST-----\nMIIBDzCBtgIBADAU\n"
       "-----END CERTIFICATE REQUEST-----\n")
WRITE_TOKEN = "wT0k3n-" + "x" * 40
JOIN_TOKEN = secrets.token_urlsafe(32)


def lead_endpoints(epoch=1, mode="dedicated"):
    return p.LeadEndpoints(epoch=epoch, current="https://lead.example:8443",
                           urls=("https://lead.example:8443", "https://[2001:db8::1]:8443"),
                           lead_mode=mode)


def store():
    return p.StoreDescriptor(store_id=STORE, url="https://store.example:8086",
                             org_id="0123456789abcdef", bucket_id="fedcba9876543210",
                             bucket_name=f"node-{NODE}", write_token=p.Secret(WRITE_TOKEN),
                             ca_pem=CERT, max_queue_bytes=256 << 20)


def inventory():
    return p.Inventory(
        node_name="Kitchen Pi", site_name="Home (Chicago)", version="2.55.0", edition="pro",
        profiles=("influxdb", "fleet"), capabilities=("influxdb", "replication"),
        doctor=p.DoctorSummary(ok=40, warn=2, fail=0, skipped=3, at=T0),
        measuring=p.Freshness(state="measuring", targets=180, stale=0, newest_age_seconds=42),
        uplink=p.Uplink(interface="wlan0", kind="wifi"))


def heartbeat(**kw):
    base = dict(
        protocol=1, protocol_min=1, node_id=NODE, instance_id=INSTANCE, sent_at=T0,
        lead_epoch=1, store_id=STORE, inventory=inventory(),
        replication=p.ReplicationState(store_id=STORE, remaining_bytes=0,
                                       latest_response_code=204, latest_error=None,
                                       queue_full_seen=False, max_queue_bytes=256 << 20),
        seed=p.SeedProgress(state="running", since=T0, until=T1, cursor=T0, bytes_sent=10),
        directive_results=(p.DirectiveResult(id="d1", status="done"),))
    base.update(kw)
    return p.HeartbeatRequest(**base)


ALL_MESSAGES = [
    lambda: p.RootResponse(protocol=1, protocol_min=1, protocol_max=1, root_pem=CERT),
    lambda: p.EnrollRequest(protocol=1, protocol_min=1, join_token=p.Secret(JOIN_TOKEN),
                            fleet_id=FLEET, node_id=NODE, instance_id=INSTANCE,
                            node_name="Kitchen Pi", site_name="Home", csr_pem=CSR,
                            software=p.Software(version="2.55.0", edition="pro")),
    lambda: p.EnrollResponse(protocol=1, fleet_id=FLEET, node_id=NODE, site_id=STORE,
                             cert_chain=(CERT, CERT), not_after=T1,
                             lead_endpoints=lead_endpoints(), heartbeat_interval=60,
                             store=store()),
    lambda: p.EnrollResponse(protocol=1, fleet_id=FLEET, node_id=NODE, site_id=STORE,
                             cert_chain=(CERT, CERT), not_after=T1,
                             lead_endpoints=lead_endpoints(), heartbeat_interval=60, store=None),
    heartbeat,
    lambda: heartbeat(replication=None, seed=None, store_id=None, directive_results=()),
    lambda: p.RenewRequest(protocol=1, protocol_min=1, node_id=NODE, instance_id=INSTANCE,
                           csr_pem=CSR),
    lambda: p.RenewResponse(protocol=1, cert_chain=(CERT, CERT, CERT), not_after=T1),
    lambda: p.LeaveRequest(protocol=1, protocol_min=1, node_id=NODE, instance_id=INSTANCE,
                           drained=True, remaining_bytes=0),
    lambda: p.LeaveResponse(protocol=1, state="left"),
    lambda: p.error("protocol_unsupported", supported_min=1, supported_max=1)[1],
    lambda: p.error("not_current_lead", lead_endpoints=lead_endpoints(2))[1],
    lambda: p.error("bad_request", reasons=("node_id: malformed",))[1],
]


@pytest.mark.parametrize("make", ALL_MESSAGES)
def test_every_message_round_trips_through_json(make):
    msg = make()
    wire = json.loads(p.encode_json(msg))
    assert p.decode(type(msg), wire) == msg


def test_heartbeat_response_round_trips_with_every_directive():
    resp = p.HeartbeatResponse(protocol=1, server_time=T0, next_heartbeat=60, directives=(
        p.Revoked(id="r1", reason="operator"),
        p.RepointStore(id="r2", store=store()),
        p.SetLeadEndpoints(id="r3", lead_endpoints=lead_endpoints(2)),
        p.RenewNow(id="r4"),
        p.StartSeed(id="r5", since=T0, until=T1, max_bytes_per_second=65536),
    ))
    wire = json.loads(p.encode_json(resp))
    assert [d["type"] for d in wire["directives"]] == [
        "revoked", "repoint_store", "lead_endpoints", "renew_now", "start_seed"]
    assert p.HeartbeatResponse.from_wire(wire) == resp
    assert p.decode(p.HeartbeatResponse, wire) == resp


# -- join string -------------------------------------------------------------


def join(**kw):
    base = dict(fleet_id=FLEET, lead_urls=("https://lead.example:8443",),
                token=p.Secret(JOIN_TOKEN), root_sha256=FP, expires_at=T1)
    base.update(kw)
    return p.JoinString(**base)


def test_join_string_round_trips():
    j = join()
    text = j.to_string()
    assert text.startswith("spfleet1_")
    assert "=" not in text and " " not in text
    assert p.JoinString.parse(text) == j
    assert p.JoinString.parse("  " + text + "\n") == j


def test_join_string_without_expiry_round_trips():
    j = join(expires_at=None)
    assert p.JoinString.parse(j.to_string()) == j


@pytest.mark.parametrize("bad", [
    "", "spfleet2_abc", "spfleet1_", "spfleet1_!!!", "spfleet1_" + "A" * 5000,
    "spfleet1_bm90IGpzb24",  # "not json"
])
def test_join_string_refuses_malformed(bad):
    with pytest.raises(p.ValidationError):
        p.JoinString.parse(bad)


@pytest.mark.parametrize("field,value", [
    ("root_sha256", "AB" * 32),        # uppercase
    ("root_sha256", "ab" * 31),        # short
    ("lead_urls", ("http://lead.example",)),
    ("lead_urls", ("https://user:pw@lead.example",)),
    ("lead_urls", ()),
    ("fleet_id", "not-a-uuid"),
])
def test_join_string_validates_each_field(field, value):
    text = p.JoinString.to_string(join(**{field: value}))
    with pytest.raises(p.ValidationError):
        p.JoinString.parse(text)


def test_join_string_refuses_a_short_token():
    text = join(token=p.Secret("short")).to_string()
    with pytest.raises(p.ValidationError) as err:
        p.JoinString.parse(text)
    assert "short" not in str(err.value)


# -- strictness and compatibility ---------------------------------------------


def test_unknown_fields_are_ignored_at_every_level():
    wire = json.loads(p.encode_json(heartbeat()))
    wire["from_the_future"] = {"x": 1}
    wire["inventory"]["new_optional"] = [1, 2]
    wire["replication"]["another"] = True
    assert p.decode(p.HeartbeatRequest, wire) == heartbeat()


@pytest.mark.parametrize("mutate,reason", [
    (lambda w: w.pop("node_id"), "node_id: required"),
    (lambda w: w.update(node_id="NOT-A-UUID"), "node_id: malformed"),
    (lambda w: w.update(lead_epoch=True), "lead_epoch: must be an integer"),
    (lambda w: w.update(lead_epoch=-1), "lead_epoch: out of range"),
    (lambda w: w.update(inventory=None), "inventory: must not be null"),
    (lambda w: w["inventory"].update(edition="Pro"), "inventory.edition: malformed"),
    (lambda w: w["inventory"].update(node_name="bad\nname"), "inventory.node_name: malformed"),
    (lambda w: w["inventory"]["doctor"].update(fail=-1), "inventory.doctor.fail: out of range"),
    (lambda w: w["replication"].update(latest_error="a\x00b"),
     "replication.latest_error: malformed"),
    (lambda w: w.update(protocol_min=2, protocol=1), "protocol_min: above protocol"),
    (lambda w: w.update(sent_at="2026-11-27 12:00:00"), "sent_at: malformed"),
    (lambda w: w.update(sent_at="2026-02-30T12:00:00Z"), "sent_at: malformed"),
])
def test_known_fields_are_strict(mutate, reason):
    wire = json.loads(p.encode_json(heartbeat()))
    mutate(wire)
    with pytest.raises(p.ValidationError) as err:
        p.decode(p.HeartbeatRequest, wire)
    assert reason in err.value.reasons


def test_reasons_never_echo_the_received_value():
    wire = json.loads(p.encode_json(heartbeat()))
    wire["node_id"] = "<script>attacker-controlled</script>"
    with pytest.raises(p.ValidationError) as err:
        p.decode(p.HeartbeatRequest, wire)
    assert "attacker" not in str(err.value)


def test_a_body_that_is_not_an_object_is_refused():
    for body in ([], "x", 1, None):
        with pytest.raises(p.ValidationError):
            p.decode(p.HeartbeatRequest, body)
    with pytest.raises(p.ValidationError):
        p.decode_json(p.HeartbeatRequest, b"{not json")


def test_open_vocabularies_accept_values_this_version_does_not_know():
    ep = p.decode(p.LeadEndpoints, {**p.encode(lead_endpoints()), "lead_mode": "elected"})
    assert ep.lead_mode == "elected"
    inv = p.decode(p.Inventory, {**p.encode(inventory()), "capabilities": ["teleport"]})
    assert inv.capabilities == ("teleport",)


def test_lead_endpoints_current_must_be_listed():
    wire = p.encode(lead_endpoints())
    wire["current"] = "https://elsewhere.example"
    with pytest.raises(p.ValidationError):
        p.decode(p.LeadEndpoints, wire)


@pytest.mark.parametrize("url,ok", [
    ("https://lead.example", True),
    ("https://lead.example.", True),
    ("https://127.0.0.1:8443", True),
    ("https://a..b", False),
    ("https://-x.example", False),
    ("https://x-.example", False),
    ("https://" + "a" * 64 + ".example", False),
    ("https://[fe80::1%25eth0]:8443", False),
    ("https://[fe80::1]:8443", False),
    ("https://[::]:8443", False),
    ("https://0.0.0.0", False),
    ("https://169.254.1.1", False),
    ("https://999.1.1.1", False),
    ("https://lead.example:8443/fleet", True),
    ("https://192.0.2.10:8443", True),
    ("https://[2001:db8::1]:8443", True),
    ("http://lead.example", False),
    ("https://lead.example/?q=1", False),
    ("https://lead.example/#x", False),
    ("https://a@lead.example", False),
    ("https://lead.example:99999", False),
    ("https://", False),
    ("https://le ad.example", False),
    ("https://[zz::1]", False),
])
def test_https_urls(url, ok):
    assert p.valid_https_url(url) is ok


# -- directives ----------------------------------------------------------------


def test_unknown_directive_types_are_kept_to_be_reported_ignored():
    resp = p.HeartbeatResponse.from_wire({
        "protocol": 1, "server_time": T0, "next_heartbeat": 60,
        "directives": [{"id": "x1", "type": "teleport", "where": "moon"},
                       {"id": "x2", "type": "renew_now"}]})
    assert resp.directives == (p.UnknownDirective("x1", "teleport"), p.RenewNow(id="x2"))


def test_a_known_directive_with_bad_arguments_is_invalid_not_fatal():
    resp = p.HeartbeatResponse.from_wire({
        "protocol": 1, "server_time": T0, "next_heartbeat": 60,
        "directives": [{"id": "s1", "type": "start_seed", "since": T1, "until": T0,
                        "max_bytes_per_second": 65536},
                       {"id": "s2", "type": "repoint_store", "store": {"url": "http://x"}},
                       {"id": "s3", "type": "renew_now"}]})
    first, second, third = resp.directives
    assert isinstance(first, p.InvalidDirective) and first.type == "start_seed"
    assert isinstance(second, p.InvalidDirective)
    assert third == p.RenewNow(id="s3")


def test_one_malformed_directive_never_costs_the_others():
    """The review's case: a directive without an id beside a revoked. The
    revoked still applies; the malformed one is dropped and reported."""
    resp = p.HeartbeatResponse.from_wire({
        "protocol": 1, "server_time": T0, "next_heartbeat": 60,
        "directives": [{"type": "x"}, "nope", {"id": "bad id!", "type": "renew_now"},
                       {"id": "t1", "type": "Renew"},
                       {"id": "r", "type": "revoked", "reason": "operator"}]})
    assert resp.directives == (p.InvalidDirective("t1", "", ("directive.type: malformed",)),
                               p.Revoked(id="r", reason="operator"))
    assert [m.index for m in resp.malformed] == [0, 1, 2]
    assert resp.malformed[0].reasons == ("directive.id: malformed",)
    assert "nope" not in repr(resp.malformed) and "bad id" not in repr(resp.malformed)


@pytest.mark.parametrize("directives", ["x", [{}] * 33])
def test_a_bad_directives_envelope_still_rejects_the_response(directives):
    with pytest.raises(p.ValidationError):
        p.HeartbeatResponse.from_wire({"protocol": 1, "server_time": T0,
                                       "next_heartbeat": 60, "directives": directives})


def test_received_unknown_and_invalid_directives_encode_and_redact():
    """The forward-compatibility path: a response holding directives this
    version does not apply can still be logged (redacted) and re-encoded."""
    resp = p.HeartbeatResponse.from_wire({
        "protocol": 1, "server_time": T0, "next_heartbeat": 60,
        "directives": [{"id": "x1", "type": "teleport"},
                       {"id": "s1", "type": "start_seed", "since": T1, "until": T0,
                        "max_bytes_per_second": 65536}]})
    assert p.redacted(resp)["directives"] == [{"id": "x1", "type": "teleport"},
                                              {"id": "s1", "type": "start_seed"}]
    assert p.encode(resp)["directives"] == p.redacted(resp)["directives"]


def test_redacted_hides_a_write_token_inside_a_directive():
    resp = p.HeartbeatResponse(protocol=1, server_time=T0, next_heartbeat=60,
                               directives=(p.RepointStore(id="r2", store=store()),))
    assert WRITE_TOKEN not in json.dumps(p.redacted(resp))
    assert p.redacted(resp)["directives"][0]["store"]["write_token"] == "<redacted>"
    assert WRITE_TOKEN in p.encode_json(resp)


def test_heartbeat_interval_is_bounded():
    for n in (p.HEARTBEAT_MIN - 1, p.HEARTBEAT_MAX + 1):
        with pytest.raises(p.ValidationError):
            p.HeartbeatResponse.from_wire({"protocol": 1, "server_time": T0,
                                           "next_heartbeat": n})


# -- negotiation and errors --------------------------------------------------


@pytest.mark.parametrize("peer,ours,want", [
    ((1, 1), (1, 1), 1),
    ((1, 3), (1, 2), 2),   # a newer agent talking to an older lead
    ((1, 1), (1, 3), 1),   # an older agent talking to a newer lead
    ((2, 3), (1, 1), None),
    ((1, 1), (2, 3), None),
])
def test_negotiate(peer, ours, want):
    assert p.negotiate(*peer, ours=ours) == want


def test_every_error_code_has_a_status_a_message_and_an_action():
    assert set(p.ERROR_STATUS) == set(p.ERROR_MESSAGE) == set(p.ERROR_ACTION)
    for code in p.ERROR_STATUS:
        status, body = p.error(code)
        assert 400 <= status < 600
        assert body.message == p.ERROR_MESSAGE[code]
        assert len(body.error_id) == 16


def test_unknown_error_codes_are_handled_as_internal():
    assert p.agent_action("brand_new") == p.agent_action("internal") == "backoff"
    body = p.decode(p.ErrorResponse, {"protocol": 1, "code": "brand_new",
                                      "message": "x", "error_id": "0" * 16})
    assert body.code == "brand_new"


def test_error_refuses_an_undefined_code():
    with pytest.raises(KeyError):
        p.error("whatever")


def test_instance_ids_are_fresh_uuids():
    a, b = p.new_instance_id(), p.new_instance_id()
    assert a != b
    p.decode(p.RenewRequest, {"protocol": 1, "protocol_min": 1, "node_id": NODE,
                              "instance_id": a, "csr_pem": CSR})


# -- secrets -------------------------------------------------------------------


def test_secrets_never_show_in_repr_str_format_or_redacted():
    msgs = [store(), join(), ALL_MESSAGES[1]()]
    for msg in msgs:
        for text in (repr(msg), str(msg), f"{msg}", json.dumps(p.redacted(msg)),
                     "%s %r" % (msg, msg)):
            assert WRITE_TOKEN not in text
            assert JOIN_TOKEN not in text
    assert WRITE_TOKEN in p.encode_json(store())  # the wire is the one place
    assert str(p.Secret("abc")) == "<redacted>"
    assert "abc" not in repr(p.Secret("abc"))


def test_secrets_compare_and_refuse_pickling():
    import pickle
    assert p.Secret("a" * 20) == p.Secret("a" * 20)
    assert p.Secret("a" * 20) != p.Secret("b" * 20)
    with pytest.raises(TypeError):
        pickle.dumps(p.Secret("abc"))
    with pytest.raises(TypeError):
        p.Secret(123)


def test_every_secret_field_is_marked_and_typed_secret():
    for cls in p.REFERENCE:
        for name, s in p._specs(cls):
            assert (s.kind == "secret") == s.secret, f"{cls.__name__}.{name}"
            if "token" in name and cls is not p.ErrorResponse:
                assert s.kind == "secret", f"{cls.__name__}.{name} looks like a credential"


# -- patterns are matched whole, in ASCII --------------------------------------


@pytest.mark.parametrize("field,value", [
    ("node_id", NODE + "\n"),
    ("node_id", NODE[:-1] + "\u0663"),          # ARABIC-INDIC DIGIT THREE
    ("sent_at", T0 + "\n"),
    ("sent_at", "\uff12" + T0[1:]),             # FULLWIDTH DIGIT TWO
    ("store_id", STORE + "\n"),
])
def test_ids_and_times_refuse_a_trailing_newline_and_other_digits(field, value):
    wire = json.loads(p.encode_json(heartbeat()))
    wire[field] = value
    with pytest.raises(p.ValidationError):
        p.decode(p.HeartbeatRequest, wire)


@pytest.mark.parametrize("cls_field,value", [
    (("edition",), "pro\n"),
    (("doctor", "at"), T0 + "\n"),
])
def test_tokens_refuse_a_trailing_newline(cls_field, value):
    wire = p.encode(inventory())
    target = wire
    for key in cls_field[:-1]:
        target = target[key]
    target[cls_field[-1]] = value
    with pytest.raises(p.ValidationError):
        p.decode(p.Inventory, wire)


@pytest.mark.parametrize("field,value", [
    ("org_id", "0123456789abcdef\n"),
    ("bucket_id", "fedcba987654321\u0663"),
    ("write_token", WRITE_TOKEN + "\n"),
    ("bucket_name", f"node-{NODE}\n"),
])
def test_store_fields_are_matched_whole(field, value):
    wire = p.encode(store())
    wire[field] = value
    with pytest.raises(p.ValidationError):
        p.decode(p.StoreDescriptor, wire)


def test_join_token_and_fingerprint_are_matched_whole():
    for kw in ({"token": p.Secret(JOIN_TOKEN + "\n")}, {"root_sha256": FP + "\n"}):
        with pytest.raises(p.ValidationError):
            p.JoinString.parse(join(**kw).to_string())
    resp = p.HeartbeatResponse.from_wire({
        "protocol": 1, "server_time": T0, "next_heartbeat": 60,
        "directives": [{"id": "a\n", "type": "renew_now"},
                       {"id": "b", "type": "renew_now\n"}]})
    assert len(resp.malformed) == 1 and isinstance(resp.directives[0], p.InvalidDirective)


@pytest.mark.parametrize("name,ok", [
    ("Kitchen Pi", True),
    ("Zürich", True),                     # names are any script, on purpose
    ("Cocina de Mamá (2)", True),
    ("東京", True),
    ("Zu\u0308rich", False),              # not NFC
    ("Kitchen\n", False),
    (" Kitchen", False),
    ("a\u2028b", False),
    ("x" * 65, False),
])
def test_names(name, ok):
    assert p.valid_name(name) is ok


@pytest.mark.parametrize("bad", ["a\u2028b", "a\u2029b", "a\u0085b", "a\x7fb", "a\nb",
                                 "a\u200bb\u202e"])
def test_text_refuses_controls_and_separators(bad):
    wire = json.loads(p.encode_json(heartbeat()))
    wire["replication"]["latest_error"] = bad
    with pytest.raises(p.ValidationError):
        p.decode(p.HeartbeatRequest, wire)


def test_optional_fields_take_null_or_absence_alike_and_required_never_null():
    wire = json.loads(p.encode_json(heartbeat()))
    wire["store_id"] = None
    wire["replication"] = None
    wire["seed"] = None
    wire["directive_results"] = None
    got = p.decode(p.HeartbeatRequest, wire)
    assert got.store_id is None and got.replication is None
    wire["node_id"] = None
    with pytest.raises(p.ValidationError):
        p.decode(p.HeartbeatRequest, wire)


def test_bodies_above_the_limit_are_refused_unread():
    big = b'{"x": "' + b"a" * p.MAX_BODY_BYTES + b'"}'
    with pytest.raises(p.ValidationError) as err:
        p.decode_json(p.HeartbeatRequest, big)
    assert err.value.reasons == ("body: too large",)
    assert p.error("too_large")[0] == 413
