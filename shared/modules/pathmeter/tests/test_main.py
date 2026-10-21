import json

import main
import owners
import status
import store
from fakes import PREFIX, Network, query_for

TARGETS = {"A": "a.example", "B_h2": "https://b.example/", "C": "c.example", "CPE": "cpe.example"}
IPS = {"a.example": "34.0.0.9", "b.example": "35.0.0.9", "c.example": "36.0.0.9",
       "cpe.example": "23.0.0.1"}


def routes():
    return {
        "34.0.0.9": [*PREFIX, "34.0.0.1", "34.0.0.9"],
        "35.0.0.9": [*PREFIX, "35.0.0.1", "35.0.0.2", "35.0.0.9"],
        "36.0.0.9": [None, *PREFIX[1:], "36.0.0.9"],
        "23.0.0.1": PREFIX[:2],
    }


def run_cycle(net, now, written):
    return main.cycle(now, query=query_for(TARGETS), run=net,
                      resolve=lambda host: (IPS[host], []), write=written.extend,
                      owners=owners.Owners(clock=lambda: now, origin_fn=lambda ip: (64500, "X")),
                      rate=20, every=3600)


def lines(points):
    return [p.to_line_protocol() for p in points]


def test_first_cycle_is_full_and_learns_the_prefix():
    net, written = Network(routes()), []
    report = run_cycle(net, 1000, written)
    assert report["full"] and report["destinations"] == 4 and report["traces"] == 4
    assert net.calls == [(["23.0.0.1", "34.0.0.9", "35.0.0.9", "36.0.0.9"], 1)]
    assert store.load(main.STATE)["prefix"] == PREFIX
    trace_lines = [line for line in lines(written) if line.startswith("path_trace")]
    assert len(trace_lines) == 4
    assert any(line.startswith("path_trace,category=custom,target=B ") for line in trace_lines)
    assert any(line.startswith("pathmeter_pass") for line in lines(written))
    assert status.read()["probes_per_hour"] == report["probes"]


def test_next_cycles_trace_the_prefix_once():
    net, written = Network(routes()), []
    first = run_cycle(net, 1000, written)
    net.calls.clear()
    written.clear()
    second = run_cycle(net, 4600, written)
    assert not second["full"] and second["prefix_hops"] == len(PREFIX)
    (ref, ref_hop), (others, skip) = net.calls
    assert ref_hop == 1 and len(ref) == 1 and skip == len(PREFIX) + 1
    assert "23.0.0.1" not in ref + others              # inside the prefix: not traced
    assert second["probes"] < first["probes"]
    # The joined path is the whole path, prefix included.
    a = next(line for line in lines(written)
             if line.startswith("path_trace") and "target=A " in line)
    assert 'path="192.168.1.1|23.0.0.1|23.0.0.2|23.0.0.3|23.0.0.4|34.0.0.1|34.0.0.9"' in a
    assert "changed=false" in a


def test_a_reference_off_the_prefix_retraces_everything():
    net, written = Network(routes()), []
    run_cycle(net, 1000, written)
    reference = store.load(main.STATE)["reference"]
    net.routes[reference] = [PREFIX[0], "23.9.9.9", *net.routes[reference][2:]]
    net.calls.clear()
    report = run_cycle(net, 4600, written)
    assert report["prefix_moved"] and report["full"]
    assert net.calls[-1][1] == 1 and len(net.calls[-1][0]) == 4


def test_a_day_later_the_cycle_is_full_again():
    net, written = Network(routes()), []
    run_cycle(net, 1000, written)
    assert run_cycle(net, 1000 + 86400, written)["full"]


def test_a_change_is_flagged_on_the_second_trace_that_shows_it():
    net, written = Network(routes()), []
    run_cycle(net, 1000, written)
    net.routes["34.0.0.9"] = [*PREFIX, "34.0.0.7", "34.0.0.9"]
    assert run_cycle(net, 4600, written)["changed"] == 0
    assert run_cycle(net, 8200, written)["changed"] == 1


def test_scamper_failing_costs_the_cycle_not_the_process():
    def broken(*a, **k):
        raise RuntimeError("scamper died")
    written = []
    report = main.cycle(1000, query=query_for(TARGETS), run=broken,
                        resolve=lambda host: (IPS[host], []), write=written.extend,
                        owners=owners.Owners(origin_fn=lambda ip: None), rate=20, every=3600)
    assert report["failed"] == 1 and report["traces"] == 0
    assert [line for line in lines(written) if line.startswith("pathmeter_pass")]


def test_names_that_do_not_resolve_or_are_v6_are_skipped():
    got = main.addresses({"A": ("c", "a"), "B": ("c", "b"), "C": ("c", "c"), "D": ("c", "a")},
                         resolve=lambda h: {"a": ("34.0.0.9", []), "b": None,
                                            "c": ("2001:db8::1", [])}[h])
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
    assert set(raw["paths"]) == {"A", "B", "C", "CPE"}
