import ipaddress
import json

import pytest

import main
import owners
import status
import store
from fakes import PREFIX, Network, query_for

# Six destinations past the shared hops, and the ISP's first hop as a target.
FAR = {f"D{i}": f"3{i}.0.0.9" for i in range(6)}
TARGETS = {**{name: name.lower() + ".example" for name in FAR}, "CPE": "cpe.example"}
IPS = {**{name.lower() + ".example": ip for name, ip in FAR.items()},
       "cpe.example": "23.0.0.1"}


def routes():
    out = {ip: [*PREFIX, ip.replace(".9", ".1"), ip] for ip in FAR.values()}
    out["30.0.0.9"] = [None, *PREFIX[1:], "30.0.0.1", "30.0.0.9"]     # router silent once
    out["23.0.0.1"] = PREFIX[:2]
    return out


@pytest.fixture(autouse=True)
def _cap(monkeypatch):
    monkeypatch.setattr(main, "FULL_PER_CYCLE", 2)
    monkeypatch.setattr(main, "WARMUP_PATHS", 3)


class AnyTable:
    ready = True

    def lookup(self, ip):
        return 64500 if ipaddress.ip_address(ip).is_global else None


def run_cycle(net, now, written, write=None):
    return main.cycle(now, query=query_for(TARGETS), run=net,
                      resolve=lambda host: [IPS[host]], write=write or written.extend,
                      owners=owners.Owners(table=AnyTable()),
                      rate=20, every=3600)


def lines(points):
    return [p.to_line_protocol() for p in points]


def from_hop_1(net):
    return [dsts for dsts, first in net.calls if first == 1]


def test_hop_1_sees_at_most_the_cap_every_cycle():
    net, written = Network(routes()), []
    assert run_cycle(net, 1000, written)["full_traces"] == 3      # the warm-up, once
    for n in range(1, 8):
        assert run_cycle(net, 1000 + 3600 * n, written)["full_traces"] <= 2
    assert all(len(dsts) <= 3 for dsts in from_hop_1(net))


def test_the_warm_up_is_the_default_eight_then_four(monkeypatch):
    monkeypatch.setattr(main, "FULL_PER_CYCLE", 4)
    monkeypatch.setattr(main, "WARMUP_PATHS", 8)
    many = {f"4{i}.0.{j}.9": [*PREFIX, f"4{i}.0.{j}.1", f"4{i}.0.{j}.9"]
            for i in range(5) for j in range(4)}
    names = {f"N{k}": ip for k, ip in enumerate(sorted(many))}
    net, written = Network(many), []

    def go(now):
        return main.cycle(now, query=query_for({n: n.lower() for n in names}), run=net,
                          resolve=lambda host: [names[host.upper()]], write=written.extend,
                          owners=owners.Owners(table=None), rate=20, every=3600)
    assert go(1000)["full_traces"] == 8
    assert go(4600)["full_traces"] == 4


def test_the_first_cycle_learns_from_its_few_and_traces_the_rest_past_them():
    net, written = Network(routes()), []
    report = run_cycle(net, 1000, written)
    (full, one), (rest, skip) = net.calls
    assert one == 1 and len(full) == 3 and skip == report["prefix_hops"] + 1 > 1
    traced = set(full) | set(rest)
    # Every destination was traced this cycle, or sits inside the prefix.
    assert traced | {"23.0.0.1"} == set(IPS.values()) and report["traces"] == len(traced)
    assert report["waiting"] == 0


def test_every_destination_gets_its_turn_from_hop_1():
    net, written = Network(routes()), []
    for n in range(4):
        run_cycle(net, 1000 + 3600 * n, written)
    full_at = store.load(main.STATE)["full_at"]
    # The CPE may be among the first (no prefix yet), never needed after.
    assert set(FAR.values()) <= set(full_at) <= set(FAR.values()) | {"23.0.0.1"}


def test_the_prefix_grows_to_what_every_destination_shares():
    net, written = Network(routes()), []
    for n in range(4):
        report = run_cycle(net, 1000 + 3600 * n, written)
    assert store.load(main.STATE)["prefix"] == PREFIX and report["prefix_hops"] == 5


def test_an_access_path_that_moves_moves_the_prefix_at_once():
    net, written = Network(routes()), []
    for n in range(4):
        run_cycle(net, 1000 + 3600 * n, written)
    for ip in FAR.values():
        net.routes[ip][3] = "23.9.9.9"
    report = run_cycle(net, 1000 + 3600 * 4, written)
    assert report["prefix_moved"] and report["access_moved"]
    assert store.load(main.STATE)["prefix"][3] == "23.9.9.9"


def test_joined_paths_carry_the_prefix_and_no_reply_time_for_it():
    net, written = Network(routes()), []
    for n in range(4):
        run_cycle(net, 1000 + 3600 * n, written)
    written.clear()
    net.calls.clear()
    run_cycle(net, 1000 + 3600 * 4, written)
    full = set(from_hop_1(net)[0])
    past = next(ip for ip in sorted(FAR.values()) if ip not in full and ip != "30.0.0.9")
    base = next(name for name, ip in FAR.items() if ip == past)
    trace = next(line for line in lines(written)
                 if line.startswith("path_trace") and f"target={base} " in line)
    assert 'path="' + "|".join([*PREFIX, past.replace(".9", ".1"), past]) + '"' in trace
    hop2 = next(line for line in lines(written)
                if line.startswith("path_hop") and f"target={base}," in line and "ttl=02" in line)
    assert "rtt_ms" not in hop2


def test_no_prefix_means_the_rest_wait_rather_than_cross_it_at_once(monkeypatch):
    monkeypatch.setattr(main, "WARMUP_PATHS", 2)
    lone = {"30.0.0.9": [*PREFIX, "30.0.0.9"], "31.0.0.9": ["192.168.9.9", "31.0.0.9"],
            "32.0.0.9": [*PREFIX, "32.0.0.9"]}
    net, written = Network(lone), []
    ips = {"a": "30.0.0.9", "b": "31.0.0.9", "c": "32.0.0.9"}
    report = main.cycle(1000, query=query_for({"A": "a", "B": "b", "C": "c"}), run=net,
                        resolve=lambda host: [ips[host]], write=written.extend,
                        owners=owners.Owners(table=None), rate=20, every=3600)
    assert report["prefix_hops"] == 0 and report["waiting"] == 1
    assert len(net.calls) == 1


def test_a_change_is_flagged_on_the_second_trace_that_shows_it():
    net, written = Network(routes()), []
    run_cycle(net, 1000, written)
    net.routes["31.0.0.9"] = [*PREFIX, "31.0.0.7", "31.0.0.9"]
    assert run_cycle(net, 4600, written)["changed"] == 0
    assert run_cycle(net, 8200, written)["changed"] == 1


def test_a_failed_write_keeps_the_change_for_the_next_cycle():
    net, written = Network(routes()), []
    run_cycle(net, 1000, written)
    net.routes["31.0.0.9"] = [*PREFIX, "31.0.0.7", "31.0.0.9"]
    run_cycle(net, 4600, written)

    def broken(points):
        raise RuntimeError("influx down")
    report = run_cycle(net, 8200, written, write=broken)
    assert report["failed"] == 1 and report["failed_cycles"] == 1
    assert run_cycle(net, 11800, written)["changed"] == 1


def _broken_cycle(now, written):
    def broken(*a, **k):
        raise RuntimeError("scamper died")
    return main.cycle(now, query=query_for(TARGETS), run=broken,
                      resolve=lambda host: [IPS[host]], write=written.extend,
                      owners=owners.Owners(table=None), rate=20, every=3600)


def test_scamper_failing_costs_the_cycle_and_invents_nothing():
    net, written = Network(routes()), []
    for n in range(4):
        run_cycle(net, 1000 + 3600 * n, written)
    written.clear()
    report = _broken_cycle(1000 + 3600 * 4, written)
    assert report["failed"] == 1 and report["traces"] == 0
    assert not [line for line in lines(written) if line.startswith("path_trace")]
    assert [line for line in lines(written) if line.startswith("pathmeter_pass")]


def test_three_failed_cycles_in_a_row_are_unhealthy():
    for now in (1000, 4600, 8200):
        _broken_cycle(now, [])
    assert status.read()["failed_cycles"] == 3
    assert not status.healthy(status.read(), now=8300)


def test_the_budget_figure_is_the_days_mean():
    net, written = Network(routes()), []
    first = run_cycle(net, 1000, written)
    second = run_cycle(net, 4600, written)
    assert second["probes_per_hour"] == round((first["probes"] + second["probes"]) / 2, 1)
    later = run_cycle(net, 1000 + 86400 + 10, written)
    assert later["probes_per_hour"] == later["probes"]


def test_a_round_robin_name_keeps_the_address_it_was_traced_at():
    rr = {"a": ["34.0.0.9", "34.0.0.8"]}
    assert main.addresses({"A": ("c", "a")}, {"A": "34.0.0.8"}, lambda h: rr[h]) == \
        {"34.0.0.8": ["A"]}
    assert main.addresses({"A": ("c", "a")}, {"A": "34.0.0.1"}, lambda h: rr[h]) == \
        {"34.0.0.9": ["A"]}


def test_names_that_do_not_resolve_or_are_v6_are_skipped():
    got = main.addresses({"A": ("c", "a"), "B": ("c", "b"), "C": ("c", "c"), "D": ("c", "a")},
                         resolve=lambda h: {"a": ["34.0.0.9"], "b": [],
                                            "c": ["2001:db8::1"]}[h])
    assert got == {"34.0.0.9": ["A", "D"]}


def test_env_limits(monkeypatch):
    monkeypatch.setenv("PATHMETER_PPS", "1000")
    monkeypatch.setenv("PATHMETER_INTERVAL", "5")
    assert main.pps() == 100 and main.interval() == 600
    monkeypatch.setenv("PATHMETER_PPS", "x")
    assert main.pps() == main.DEFAULT_PPS


def test_state_is_json_on_disk():
    net, written = Network(routes()), []
    run_cycle(net, 1000, written)
    raw = json.loads((store.state_dir() / main.STATE).read_text())
    assert set(raw["paths"]) == set(TARGETS)


def test_a_new_gateway_at_hop_1_does_not_blind_the_meter():
    net, written = Network(routes()), []
    for n in range(4):
        run_cycle(net, 1000 + 3600 * n, written)
    for ip, route in net.routes.items():
        route[0] = "192.168.9.1"                       # a failover, a new router
    report = run_cycle(net, 1000 + 3600 * 4, written)
    assert report["access_moved"] and report["waiting"] == 0
    assert report["traces"] >= len(FAR) - 1           # everyone still traced
    assert store.load(main.STATE)["prefix"][0] == "192.168.9.1"


def test_mixed_answers_fall_back_to_the_previous_prefix_cut_where_contradicted():
    net, written = Network(routes()), []
    for n in range(4):
        run_cycle(net, 1000 + 3600 * n, written)
    # One full trace this cycle answers hop 3 from elsewhere; the others agree.
    state = store.load(main.STATE)
    far = [ip for ip in state["full_at"] if ip in FAR.values()]
    target = sorted(far, key=lambda ip: state["full_at"][ip])[0]
    net.routes[target][2] = "23.9.9.9"
    report = run_cycle(net, 1000 + 3600 * 4, written)
    assert report["waiting"] == 0 and 2 <= report["prefix_hops"] <= 5


def test_a_destination_list_that_halves_in_one_cycle_keeps_what_was_learned():
    net, written = Network(routes()), []
    for n in range(4):
        run_cycle(net, 1000 + 3600 * n, written)
    learned = store.load(main.STATE)["full_paths"]
    main.cycle(1000 + 3600 * 4, query=query_for({"D0": "d0.example"}), run=net,
               resolve=lambda host: [IPS[host]], write=written.extend,
               owners=owners.Owners(table=None), rate=20, every=3600)
    assert set(store.load(main.STATE)["full_paths"]) >= set(learned)


def test_waiting_is_recorded():
    line = store.pass_point(1000, {"waiting": 3}).to_line_protocol()
    assert "waiting=3i" in line


def test_one_odd_reply_at_hop_1_does_not_blind_the_meter():
    net, written = Network(routes()), []
    for n in range(4):
        run_cycle(net, 1000 + 3600 * n, written)
    state = store.load(main.STATE)
    far = [ip for ip in state["full_at"] if ip in FAR.values()]
    odd = sorted(far, key=lambda ip: state["full_at"][ip])[0]   # traced from hop 1 next
    net.routes[odd][0] = "192.168.1.254"                          # the router's other address
    for n in range(4, 7):
        report = run_cycle(net, 1000 + 3600 * n, written)
        assert report["waiting"] == 0 and report["prefix_hops"] == 5



def test_a_failed_or_partial_cycle_forgets_no_router_name():
    import names
    net, written = Network(routes()), []
    run_cycle(net, 1000, written)
    known = dict(store.load(names.FILE))
    assert known
    _broken_cycle(4600, [])
    assert store.load(names.FILE) == known
