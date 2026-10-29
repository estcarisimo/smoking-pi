from datetime import datetime, timezone

import pytest

import episodes
import main
import owners
import status
import store
from fakes import PREFIX, Network, query_for
from test_main import FAR, IPS, TARGETS, routes, run_cycle

START = 100_000


def iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def episode_at(now, targets=("D0", "D1")):
    """What detect() returns while an episode that began at START lasts."""
    return {"start": iso(START), "targets": set(targets)}


@pytest.fixture(autouse=True)
def _cap(monkeypatch):
    monkeypatch.setattr(main, "FULL_PER_CYCLE", 2)
    monkeypatch.setattr(main, "WARMUP_PATHS", 3)


def learned(net):
    """Four hourly cycles: the prefix is learned and every destination has
    its address."""
    written = []
    for n in range(4):
        run_cycle(net, 1000 + 3600 * n, written)
    net.calls.clear()


def go(net, now, written, detect=episode_at, limit=60):
    return episodes.round_(now, query=query_for(TARGETS), run=net, write=written.extend,
                           detect_fn=lambda at, query: detect(at),
                           owners=owners.Owners(table=None),
                           rate=20, limit=limit, full_per_cycle=main.FULL_PER_CYCLE)


def lines(points):
    return [p.to_line_protocol() for p in points]


# --- the episode is the alerter's widespread run --------------------------------

def answer(start, end, by_target):
    return {"widespread": [{"start": iso(start), "end": iso(end)}],
            "by_target": [{"target": t, "last_time": iso(at)} for t, at in by_target]}


def test_a_run_whose_last_step_is_recent_is_under_way():
    now = START + 1800
    found = episodes.under_way(answer(START, now - 300, [("D0_tcp443", now - 300),
                                                         ("D1", START - 600)]), now, 300)
    # Its targets are base names with an event since it began.
    assert found["start"] == iso(START) and found["targets"] == {"D0"}


def test_a_run_that_ended_is_not():
    now = START + 3600
    stale = now - (episodes.ONGOING_STEPS + 1) * 300
    assert episodes.under_way(answer(START, stale, [("D0", stale)]), now, 300) is None
    assert episodes.under_way({"widespread": []}, now, 300) is None


def test_detect_asks_the_shared_loss_events(monkeypatch):
    seen = {}

    def fake(query, hours, threshold=None, cadences=None):
        seen["hours"] = hours
        return answer(START, START + 600, [("D2", START + 600)])
    monkeypatch.setattr(episodes.loss_events, "answer", fake)
    found = episodes.detect(START + 900, query=lambda flux: [])
    assert seen["hours"] == episodes.WINDOW_HOURS and found["targets"] == {"D2"}


# --- the rounds ----------------------------------------------------------------

def test_no_episode_no_trace():
    net, written = Network(routes()), []
    learned(net)
    report = go(net, START, written, detect=lambda at: None)
    assert report["traces"] == 0 and net.calls == [] and written == []
    assert status.read()["episodes"]["cap_per_hour"] == 60


def test_a_round_traces_the_affected_first_one_from_hop_1_the_rest_past_the_prefix():
    net, written = Network(routes()), []
    learned(net)
    report = go(net, START, written)
    (full, one), (past, skip) = net.calls
    assert one == 1 and len(full) == 1 and skip == len(PREFIX) + 1
    assert {FAR["D0"], FAR["D1"]} <= set(full) | set(past)
    assert report["traces"] == episodes.per_round(60) == 5 and report["full_traces"] == 1
    # The CPE sits inside the prefix: never traced by a round.
    assert IPS["cpe.example"] not in full + past


def test_round_points_are_tagged_and_never_flag_a_change():
    net, written = Network(routes()), []
    learned(net)
    go(net, START, written)
    traces = [line for line in lines(written) if line.startswith("path_trace")]
    hops = [line for line in lines(written) if line.startswith("path_hop")]
    assert traces and all(",trigger=episode" in line for line in traces + hops)
    assert all(f'episode="{iso(START)}"' in line for line in traces)
    assert all("changed=false" in line and "as_changed=false" in line for line in traces)
    # A trace past the prefix is joined with it, as the hourly ones are.
    joined = next(line for line in traces if "first_hop=6i" in line)
    assert 'path="' + "|".join(PREFIX) in joined
    passes = [line for line in lines(written) if line.startswith("pathmeter_pass")]
    assert len(passes) == 1 and "trigger=episode" in passes[0] and "capped=" in passes[0]


def test_hourly_points_carry_no_trigger_tag():
    net, written = Network(routes()), []
    run_cycle(net, 1000, written)
    assert not any("trigger=" in line for line in lines(written))


def test_the_cap_holds_over_any_hour_and_over_a_restart():
    net, written = Network(routes()), []
    learned(net)
    traced = []
    for n in range(24):                       # two hours of rounds, 12 an hour
        traced.append(go(net, START + n * episodes.EVERY, written, limit=8)["traces"])
    # 8 an hour: a round's share is 1, and the ledger stops any hour at 8.
    for n in range(24):
        assert sum(traced[n:n + 12]) <= 8
    assert sum(traced) > 0
    # The ledger lives in the state volume: a new process counts it too.
    assert store.load(episodes.STATE)["rounds"]


def test_the_router_gets_at_most_one_trace_a_round():
    net, written = Network(routes()), []
    learned(net)
    for n in range(12):
        go(net, START + n * episodes.EVERY, written, limit=600)
    assert all(len(dsts) <= episodes.FULL_PER_ROUND for dsts, first in net.calls if first == 1)


def test_destinations_take_turns_over_a_long_episode():
    net, written = Network(routes()), []
    learned(net)
    seen = set()
    for n in range(6):
        go(net, START + n * episodes.EVERY, written, limit=12,   # one a round
           detect=lambda at: episode_at(at, targets=FAR))
        seen |= {ip for dsts, _ in net.calls for ip in dsts}
    assert seen == set(FAR.values())


def test_the_affected_go_before_the_others():
    net, written = Network(routes()), []
    learned(net)
    for n in range(4):
        go(net, START + n * episodes.EVERY, written, limit=12)
    assert {ip for dsts, _ in net.calls for ip in dsts} == {FAR["D0"], FAR["D1"]}


def test_one_episode_keeps_one_id_past_the_window_it_is_read_from():
    net, written = Network(routes()), []
    learned(net)
    go(net, START, written)
    later = {"start": iso(START + 3600), "targets": {"D0"}}
    report = go(net, START + episodes.EVERY, written, detect=lambda at: later)
    assert report["episode"] == iso(START)


def test_no_prefix_only_the_hop_1_trace_runs():
    net, written = Network(routes()), []
    learned(net)
    state = store.load(main.STATE)
    state["prefix"] = []
    store.save(main.STATE, state)
    report = go(net, START, written)
    assert [first for _, first in net.calls] == [1] and report["traces"] == 1


def test_off_means_not_even_asked():
    net, written = Network(routes()), []
    learned(net)

    def boom(at):
        raise AssertionError("asked")
    report = go(net, START, written, detect=boom, limit=0)
    assert report["traces"] == 0 and not report["failed"]


def test_a_failed_check_costs_the_round_only():
    net, written = Network(routes()), []
    learned(net)

    def boom(at):
        raise RuntimeError("influx down")
    assert go(net, START, written, detect=boom)["failed"] == 1


def test_the_hourly_cycle_keeps_the_rounds_report():
    net, written = Network(routes()), []
    learned(net)
    go(net, START, written)
    run_cycle(net, START + 600, written)
    assert status.read()["episodes"]["traces"] == 5


def test_cap_from_the_environment(monkeypatch):
    assert episodes.cap() == episodes.DEFAULT_CAP
    monkeypatch.setenv("PATHMETER_EPISODE_TRACES_PER_HOUR", "0")
    assert episodes.cap() == 0
    monkeypatch.setenv("PATHMETER_EPISODE_TRACES_PER_HOUR", "99999")
    assert episodes.cap() == episodes.MAX_CAP
    monkeypatch.setenv("PATHMETER_EPISODE_TRACES_PER_HOUR", "lots")
    assert episodes.cap() == episodes.DEFAULT_CAP


def test_the_hosts_own_uplink_or_dns_is_not_traced():
    now = START + 900
    for reading in episodes.NOT_ON_THE_PATH:
        run = answer(START, now - 300, [("D0", now - 300)])
        run["widespread"][0]["reading"] = reading
        assert episodes.under_way(run, now, 300) is None
    run = answer(START, now - 300, [("D0", now - 300)])
    run["widespread"][0]["reading"] = "internet_down"
    assert episodes.under_way(run, now, 300)["targets"] == {"D0"}


def test_a_failed_run_still_counts_against_the_cap():
    net, written = Network(routes()), []
    learned(net)

    def broken(dsts, rate, first_hop=1):
        raise RuntimeError("scamper died")
    for n in range(3):
        report = episodes.round_(START + n * episodes.EVERY, query=query_for(TARGETS),
                                 run=broken, write=written.extend,
                                 detect_fn=lambda at, query: episode_at(at),
                                 rate=20, limit=10, full_per_cycle=2)
        assert report["failed"] == 1
    # 10 an hour, a round's share 1: three rounds started three traces.
    assert sum(r[1] for r in store.load(episodes.STATE)["rounds"]) == 3


def test_turned_off_the_cycle_drops_the_rounds_report(monkeypatch):
    net, written = Network(routes()), []
    learned(net)
    go(net, START, written)
    monkeypatch.setenv("PATHMETER_EPISODE_TRACES_PER_HOUR", "0")
    run_cycle(net, START + 600, written)
    assert "episodes" not in status.read()


def test_a_cap_that_is_not_a_multiple_of_twelve_holds_over_any_hour():
    net, written = Network(routes()), []
    learned(net)
    traced = [go(net, START + n * episodes.EVERY, written,
                 detect=lambda at: episode_at(at, targets=FAR), limit=7)["traces"]
              for n in range(30)]
    assert all(sum(traced[n:n + 12]) <= 7 for n in range(30)) and sum(traced[:12]) == 7
