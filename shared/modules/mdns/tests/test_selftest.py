import selftest
import wire


def parse(data):
    return wire.parse(data)


def run_round(t, now, loops_back):
    msg = parse(t.query(now))
    if loops_back:
        assert t.observe(msg)
    t.settle(now + (0.1 if loops_back else selftest.WAIT))


def test_query_is_a_question_for_a_name_nobody_holds():
    t = selftest.SelfTest()
    msg = parse(t.query(0.0))
    assert not msg.is_response
    assert msg.questions[0].name.startswith("selftest-")
    assert msg.questions[0].name.endswith(".local")


def test_heard_query_resets_misses_and_waits_for_the_next_interval():
    t = selftest.SelfTest()
    t.misses = 2
    assert t.due(0.0)
    run_round(t, 0.0, loops_back=True)
    assert t.misses == 0 and not t.deaf
    assert not t.due(selftest.INTERVAL - 1) and t.due(selftest.INTERVAL + 1)


def test_unheard_queries_turn_deaf_then_rebuild_once_then_give_up():
    t = selftest.SelfTest()
    now = 0.0
    seen = []
    for _ in range(selftest.EXIT_AFTER):
        run_round(t, now, loops_back=False)
        seen.append((t.deaf, t.should_rebuild(), t.give_up))
        now += selftest.INTERVAL + selftest.WAIT
    assert seen[1] == (False, False, False)
    assert seen[2] == (True, True, False)           # third miss: deaf, rebuild
    assert seen[3] == (True, False, False)          # not again
    assert seen[-1][2] is True
    # one heard query forgives everything, and re-arms the rebuild
    run_round(t, now, loops_back=True)
    assert t.misses == 0 and not t.deaf


def test_other_packets_are_not_the_probe():
    t = selftest.SelfTest()
    t.query(0.0)
    other = wire.Message(questions=[wire.Question("smoking-pi.local", wire.TYPE_A)])
    assert not t.observe(parse(wire.build(other)))
    own_answer = wire.Message(flags=wire.FLAG_RESPONSE,
                              questions=[wire.Question(f"{t.token}.local", wire.TYPE_A)])
    assert not t.observe(parse(wire.build(own_answer)))


def test_nothing_is_pending_before_the_first_query():
    t = selftest.SelfTest()
    t.settle(100.0)
    assert t.misses == 0
    assert not t.observe(wire.Message())
