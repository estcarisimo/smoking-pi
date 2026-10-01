import responder
import wire

OURS = ["192.168.1.10", "fd00::10"]
OWN = {"192.168.1.10", "172.17.0.1"}
PEER = ("192.168.1.20", 5353)


def NO_JITTER():
    return 0.0


def claimed(name="smoking-pi"):
    r = responder.Responder(name, OURS, jitter=NO_JITTER)
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
    r = responder.Responder("smoking-pi", OURS, jitter=NO_JITTER)
    r.start(0.0)
    sent = []
    for step in range(12):
        sent += r.tick(step * 0.25)
    kinds = ["probe" if not o.message.is_response else "announce" for o in sent]
    assert kinds == ["probe", "probe", "probe", "announce", "announce"]
    probe = sent[0].message
    # QU clear: a unicast reply could land on Avahi's socket instead of ours.
    assert probe.questions[0].name == "smoking-pi.local" and not probe.questions[0].unicast
    assert {r.address() for r in probe.authority} == set(OURS)
    assert r.state == "announced"
    assert all(rec.flush for rec in sent[-1].message.answers)


def test_a_dotlocal_suffix_in_the_setting_is_not_doubled():
    assert responder.Responder("smoking-pi.local", OURS).name == "smoking-pi.local"


def test_another_host_answering_while_we_probe_moves_us_to_dash_two():
    r = responder.Responder("smoking-pi", OURS, jitter=NO_JITTER)
    r.start(0.0)
    r.tick(0.0)
    r.handle(answer_from("smoking-pi.local", ["192.168.1.20"]), PEER, OWN, 0.1)
    assert r.name == "smoking-pi-2.local"
    assert r.state == "probing" and r.conflicts == 1


def test_our_own_echo_is_never_a_conflict():
    # What renamed Avahi to smokingpi-2: the host hearing itself.
    r = responder.Responder("smoking-pi", OURS, jitter=NO_JITTER)
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
    r = responder.Responder("smoking-pi", ["192.168.1.10"], jitter=NO_JITTER)
    r.start(0.0)
    r.tick(0.0)
    theirs = wire.Message(
        questions=[wire.Question("smoking-pi.local", wire.TYPE_ANY, unicast=True)],
        authority=wire.address_records("smoking-pi.local", ["192.168.1.99"], flush=False))
    r.handle(theirs, PEER, OWN, 0.1)
    assert r.name == "smoking-pi-2.local"


def test_simultaneous_probe_higher_data_wins():
    r = responder.Responder("smoking-pi", ["192.168.1.99"], jitter=NO_JITTER)
    r.start(0.0)
    r.tick(0.0)
    theirs = wire.Message(
        questions=[wire.Question("smoking-pi.local", wire.TYPE_ANY, unicast=True)],
        authority=wire.address_records("smoking-pi.local", ["192.168.1.10"], flush=False))
    r.handle(theirs, PEER, set(), 0.1)
    assert r.name == "smoking-pi.local"


def test_gives_up_after_the_last_suffix():
    r = responder.Responder("smoking-pi", OURS, jitter=NO_JITTER)
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
    r = responder.Responder("smoking-pi", OURS, jitter=NO_JITTER)
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


def test_new_addresses_are_probed_then_announced():
    r = claimed()
    r.set_addresses(["192.168.1.11"], 10.0)
    assert r.state == "probing"
    sent = []
    for k in range(12):
        sent += r.tick(10.0 + k * 0.25)
    assert not sent[0].message.is_response
    assert [x.address() for x in sent[-1].message.answers] == ["192.168.1.11"]


def test_no_address_holds_the_probe_instead_of_claiming_in_silence():
    r = responder.Responder("smoking-pi", [], jitter=NO_JITTER)
    r.start(0.0)
    for k in range(40):
        assert r.tick(k * 0.25) == []
    assert r.state == "probing"
    r.set_addresses(["192.168.1.10"], 10.0)
    out = r.tick(10.0)
    assert out and out[0].message.questions[0].name == "smoking-pi.local"


def test_the_first_probe_waits_a_random_moment():
    r = responder.Responder("smoking-pi", OURS, jitter=lambda: 0.2)
    r.start(0.0)
    assert r.tick(0.1) == []
    assert len(r.tick(0.2)) == 1


def test_a_response_not_from_port_5353_is_never_a_conflict():
    r = claimed()
    r.handle(answer_from("smoking-pi.local", ["192.168.1.20"]), ("192.168.1.20", 40000), OWN, 5.0)
    assert r.name == "smoking-pi.local"


def test_giving_up_is_not_forever():
    r = responder.Responder("smoking-pi", OURS, jitter=NO_JITTER)
    r.start(0.0)
    for i in range(responder.MAX_SUFFIX + 2):
        r.handle(answer_from(r.name, ["192.168.1.20"]), PEER, OWN, 100.0)
    assert r.state == "gave_up"
    assert r.tick(100.0 + responder.GIVE_UP_RETRY - 1) == []
    r.tick(100.0 + responder.GIVE_UP_RETRY)
    assert (r.name, r.state) == ("smoking-pi.local", "probing")


def test_a_burst_of_conflicts_pauses_probing():
    r = responder.Responder("smoking-pi", OURS, jitter=NO_JITTER)
    r.start(0.0)
    for i in range(responder.RATE_CONFLICTS):
        r.handle(answer_from(r.name, ["192.168.1.20"]), PEER, OWN, 1.0)
    assert r.tick(1.0) == []
    assert r.tick(1.0 + responder.RATE_PAUSE)


def test_a_missing_family_is_denied_with_nsec():
    r = responder.Responder("smoking-pi", ["192.168.1.10"], jitter=NO_JITTER)
    r.start(0.0)
    t = 0.0
    while r.state != "announced":
        r.tick(t)
        t += 0.25
    (out,) = r.handle(query("smoking-pi.local", wire.TYPE_AAAA), PEER, OWN, 50.0)
    (nsec,) = out.message.answers
    assert nsec.rtype == wire.TYPE_NSEC
    # Next name = itself, window 0, bitmap with only A (type 1) set.
    assert nsec.rdata.endswith(bytes([0, 1, 0x40]))
    (both,) = r.handle(query("smoking-pi.local", wire.TYPE_ANY), PEER, OWN, 60.0)
    assert [x.rtype for x in both.message.answers] == [wire.TYPE_A]
    assert [x.rtype for x in both.message.additional] == [wire.TYPE_NSEC]


def test_a_record_is_multicast_at_most_once_a_second():
    r = claimed()
    assert r.handle(query("smoking-pi.local"), PEER, OWN, 50.0)
    assert r.handle(query("smoking-pi.local"), PEER, OWN, 50.5) == []
    assert r.handle(query("smoking-pi.local"), PEER, OWN, 51.0)


def test_answers_resume_after_the_first_announcement():
    r = responder.Responder("smoking-pi", OURS, jitter=NO_JITTER)
    r.start(0.0)
    for k in range(4):
        r.tick(k * 0.25)
    assert r.state == "announcing"
    assert r.handle(query("smoking-pi.local"), PEER, OWN, 0.8)


def test_goodbye_has_ttl_zero():
    r = claimed()
    (bye,) = r.goodbye()
    assert all(x.ttl == 0 for x in bye.message.answers)
    assert responder.Responder("x", OURS).goodbye() == []
