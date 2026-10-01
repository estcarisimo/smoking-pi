import responder
import wire

OURS = ["192.168.1.10", "fd00::10"]
OWN = {"192.168.1.10", "172.17.0.1"}
PEER = ("192.168.1.20", 5353)


def claimed(name="smoking-pi"):
    r = responder.Responder(name, OURS)
    r.start(0.0)
    t = 0.0
    while r.state != "announced":
        r.tick(t)
        t += 0.25
    return r


def answer_from(name, addresses, ttl=120):
    return wire.Message(flags=wire.FLAG_RESPONSE | wire.FLAG_AUTHORITATIVE,
                        answers=wire.address_records(name, addresses, ttl))


def query(name, qtype=wire.TYPE_A, known=()):
    return wire.Message(questions=[wire.Question(name, qtype)], answers=list(known))


def test_three_probes_then_two_announcements():
    r = responder.Responder("smoking-pi", OURS)
    r.start(0.0)
    sent = []
    for step in range(12):
        sent += r.tick(step * 0.25)
    kinds = ["probe" if not o.message.is_response else "announce" for o in sent]
    assert kinds == ["probe", "probe", "probe", "announce", "announce"]
    probe = sent[0].message
    assert probe.questions[0].name == "smoking-pi.local" and probe.questions[0].unicast
    assert {r.address() for r in probe.authority} == set(OURS)
    assert r.state == "announced"
    assert all(rec.flush for rec in sent[-1].message.answers)


def test_a_dotlocal_suffix_in_the_setting_is_not_doubled():
    assert responder.Responder("smoking-pi.local", OURS).name == "smoking-pi.local"


def test_another_host_answering_while_we_probe_moves_us_to_dash_two():
    r = responder.Responder("smoking-pi", OURS)
    r.start(0.0)
    r.tick(0.0)
    r.handle(answer_from("smoking-pi.local", ["192.168.1.20"]), PEER, OWN, 0.1)
    assert r.name == "smoking-pi-2.local"
    assert r.state == "probing" and r.conflicts == 1


def test_our_own_echo_is_never_a_conflict():
    # What renamed Avahi to smokingpi-2: the host hearing itself.
    r = responder.Responder("smoking-pi", OURS)
    r.start(0.0)
    r.tick(0.0)
    r.handle(answer_from("smoking-pi.local", ["10.9.9.9"]), ("192.168.1.10", 5353), OWN, 0.1)
    r.handle(answer_from("smoking-pi.local", ["10.9.9.9"]), ("172.17.0.1", 5353), OWN, 0.1)
    assert r.name == "smoking-pi.local"


def test_identical_records_from_elsewhere_are_not_a_conflict():
    r = claimed()
    r.handle(answer_from("smoking-pi.local", OURS), PEER, OWN, 5.0)
    assert r.name == "smoking-pi.local" and r.conflicts == 0


def test_a_goodbye_from_another_host_is_not_a_conflict():
    r = claimed()
    r.handle(answer_from("smoking-pi.local", ["192.168.1.20"], ttl=0), PEER, OWN, 5.0)
    assert r.name == "smoking-pi.local"


def test_a_conflict_after_the_claim_reprobes_under_the_next_name():
    r = claimed()
    r.handle(answer_from("smoking-pi.local", ["192.168.1.20"]), PEER, OWN, 5.0)
    assert (r.name, r.state) == ("smoking-pi-2.local", "probing")


def test_simultaneous_probe_lower_data_loses():
    r = responder.Responder("smoking-pi", ["192.168.1.10"])
    r.start(0.0)
    r.tick(0.0)
    theirs = wire.Message(
        questions=[wire.Question("smoking-pi.local", wire.TYPE_ANY, unicast=True)],
        authority=wire.address_records("smoking-pi.local", ["192.168.1.99"], flush=False))
    r.handle(theirs, PEER, OWN, 0.1)
    assert r.name == "smoking-pi-2.local"


def test_simultaneous_probe_higher_data_wins():
    r = responder.Responder("smoking-pi", ["192.168.1.99"])
    r.start(0.0)
    r.tick(0.0)
    theirs = wire.Message(
        questions=[wire.Question("smoking-pi.local", wire.TYPE_ANY, unicast=True)],
        authority=wire.address_records("smoking-pi.local", ["192.168.1.10"], flush=False))
    r.handle(theirs, PEER, set(), 0.1)
    assert r.name == "smoking-pi.local"


def test_gives_up_after_the_last_suffix():
    r = responder.Responder("smoking-pi", OURS)
    r.start(0.0)
    for i in range(responder.MAX_SUFFIX + 2):
        r.handle(answer_from(r.name, ["192.168.1.20"]), PEER, OWN, float(i))
    assert r.state == "gave_up"
    assert r.tick(100.0) == []


def test_answers_a_question_for_its_name_by_multicast():
    r = claimed()
    out = r.handle(query("Smoking-Pi.local", wire.TYPE_A), PEER, OWN, 5.0)
    assert len(out) == 1 and out[0].to is None
    assert [x.address() for x in out[0].message.answers] == ["192.168.1.10"]


def test_any_question_gets_both_families():
    r = claimed()
    out = r.handle(query("smoking-pi.local", wire.TYPE_ANY), PEER, OWN, 5.0)
    assert {x.address() for x in out[0].message.answers} == set(OURS)


def test_other_names_get_nothing():
    r = claimed()
    assert r.handle(query("printer.local"), PEER, OWN, 5.0) == []


def test_no_answer_before_the_name_is_claimed():
    r = responder.Responder("smoking-pi", OURS)
    r.start(0.0)
    r.tick(0.0)
    assert r.handle(query("smoking-pi.local"), PEER, OWN, 0.1) == []


def test_known_answers_are_not_repeated():
    r = claimed()
    known = wire.address_records("smoking-pi.local", ["192.168.1.10"], ttl=100)
    assert r.handle(query("smoking-pi.local", wire.TYPE_A, known), PEER, OWN, 5.0) == []


def test_legacy_unicast_query_gets_a_unicast_short_lived_reply():
    r = claimed()
    q = query("smoking-pi.local", wire.TYPE_A)
    q.id = 4242
    out = r.handle(q, ("192.168.1.20", 41234), OWN, 5.0)
    assert out[0].to == ("192.168.1.20", 41234)
    reply = out[0].message
    assert reply.id == 4242 and reply.questions[0].name == "smoking-pi.local"
    assert all(x.ttl == responder.LEGACY_TTL and not x.flush for x in reply.answers)


def test_new_addresses_are_announced_again():
    r = claimed()
    r.set_addresses(["192.168.1.11"], 10.0)
    out = r.tick(10.0)
    assert [x.address() for x in out[0].message.answers] == ["192.168.1.11"]


def test_goodbye_has_ttl_zero():
    r = claimed()
    (bye,) = r.goodbye()
    assert all(x.ttl == 0 for x in bye.message.answers)
    assert responder.Responder("x", OURS).goodbye() == []
