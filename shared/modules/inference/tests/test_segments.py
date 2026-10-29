"""Shared path segments: a hypothesis from the traces, never from the network."""

import pandas as pd

import segments

T0 = 1_790_000_000
END = T0 + 3 * 3600
# The house's first hops, private ones after a public one included, as the
# staging Pi's traces read: hops 1-5 every destination crosses.
PREFIX = ["192.168.86.1", "136.25.220.1", "10.248.2.13", "10.248.0.77", "216.21.171.16"]


def _iso(epoch):
    return pd.Timestamp(epoch, unit="s", tz="UTC")


def _row(target, hops, asns, at=T0 - 600, reached=True, path_hash=None):
    path = "|".join(hops)
    return {"_time": _iso(at), "target": target, "path": path,
            "as_path": " ".join(str(a) for a in asns), "reached": reached,
            "path_hash": path_hash or path}


def _via_he(target, i):
    return _row(target, PREFIX + ["*", "184.105.81.237", f"184.105.82.{i}"],
                [19165, 16591, 6939, 64500 + i])


def _direct(target, i):
    return _row(target, PREFIX + ["*", "23.255.224.64", f"23.255.225.{i}"],
                [19165, 16591, 64600 + i])


def _period(loss):
    return {"start": T0, "end": END, "scope": "shared", "mean_loss_pct": 3.0,
            "target_loss": loss}


def _case(n_lost=9, n_clean=20):
    """The roadmap's example: the destinations that lost all go through
    AS6939, those that did not go straight on from the access network."""
    rows = [_via_he(f"L{i}", i) for i in range(n_lost)]
    rows += [_direct(f"C{i}", i) for i in range(n_clean)]
    loss = {f"L{i}": 4.0 for i in range(n_lost)}
    loss.update({f"C{i}": 0.0 for i in range(n_clean)})
    return rows, loss


def test_the_segment_only_the_destinations_that_lost_cross_is_the_hypothesis():
    rows, loss = _case()
    found = segments.paths(rows, [], T0, END)
    h = segments.hypothesis(_period(loss), found, name_of={6939: "HURRICANE"}.get)
    assert h["confidence"] == "strong"
    # AS6939 and its router 184.105.81.237 tie; the network goes first.
    assert h["segment"] == "AS6939 (HURRICANE)" and h["segment_kind"] == "as"
    assert (h["affected"], h["unaffected"], h["affected_through"], h["unaffected_through"]) \
        == (9, 20, 9, 0)
    assert h["recall"] == 1.0 and h["spill"] == 0.0 and h["coverage"] == 1.0
    assert h["common_hops"] == 5  # TTL 6 is silent everywhere
    assert "router 184.105.81.237" in h["alternatives"]
    assert "9 lost, 20 did not" in h["hypothesis"]
    assert "The 9 that lost share hops 1–5 and AS19165 AS16591 AS6939; " \
        "the 20 that did not share hops 1–5 and AS19165 AS16591." in h["hypothesis"]
    assert h["common_as"] == "AS19165 AS16591 AS6939"
    assert h["clean_common_as"] == "AS19165 AS16591" and h["clean_common_hops"] == 5
    assert "Hypothesis (strong): AS6939 (HURRICANE)" in h["hypothesis"]


def test_what_every_destination_crosses_explains_nothing():
    # The same paths for everyone: the house's hops, Google Fiber, then each
    # its own way. Nothing sets the lossy ones apart.
    rows = [_direct(f"L{i}", i) for i in range(9)] + [_direct(f"C{i}", 20 + i) for i in range(20)]
    loss = {f"L{i}": 4.0 for i in range(9)} | {f"C{i}": 0.0 for i in range(20)}
    h = segments.hypothesis(_period(loss), segments.paths(rows, [], T0, END))
    assert h["confidence"] == "none" and h["segment"] == "" and h["segment_kind"] == "none"
    assert "no common cause on the path is suggested" in h["hypothesis"]
    assert h["common_hops"] == 5


def test_when_everyone_lost_there_is_nothing_to_contrast():
    rows = [_via_he(f"L{i}", i) for i in range(10)]
    h = segments.hypothesis(_period({f"L{i}": 5.0 for i in range(10)}),
                            segments.paths(rows, [], T0, END))
    assert h["confidence"] == "none" and h["unaffected"] == 0
    assert "No traced destination stayed clean" in h["hypothesis"]


def test_one_or_two_clean_destinations_are_too_few_to_contrast():
    # The staging replay: every destination lost but the gateway, whose
    # trace ends at hop 2. That is the gateway's own rule, not a segment.
    rows = [_via_he(f"L{i}", i) for i in range(17)]
    rows.append(_row("CPE_IPv4", PREFIX[:2], [19165]))
    loss = {f"L{i}": 4.5 for i in range(17)} | {"CPE_IPv4": 0.0}
    h = segments.hypothesis(_period(loss), segments.paths(rows, [], T0, END))
    assert h["confidence"] == "none"
    assert "Only 1 traced destination stayed clean (CPE_IPv4)" in h["hypothesis"]


def test_mild_destinations_count_on_neither_side():
    rows, loss = _case(n_lost=5, n_clean=5)
    # A clean-path destination at 0.8 %: neither lost nor clean.
    rows.append(_direct("M0", 99))
    loss["M0"] = 0.8
    h = segments.hypothesis(_period(loss), segments.paths(rows, [], T0, END))
    assert (h["affected"], h["unaffected"], h["mild"]) == (5, 5, 1)
    assert h["traced"] == 10 and h["confidence"] == "strong"
    assert ", 1 in between" in h["hypothesis"]


def test_untraced_destinations_lower_the_coverage_and_the_confidence():
    rows, loss = _case(n_lost=5, n_clean=5)
    loss |= {f"X{i}": 0.0 for i in range(10)}  # never traced
    h = segments.hypothesis(_period(loss), segments.paths(rows, [], T0, END))
    assert h["untraced"] == 10 and h["coverage"] == 10 / 20
    assert h["confidence"] == "weak"


def test_nothing_traced_is_no_hypothesis():
    rows, loss = _case()
    assert segments.hypothesis(_period(loss), {}) is None


def test_an_unaffected_destination_silent_where_the_segment_sits_counts_against_it():
    rows, loss = _case(n_lost=5, n_clean=5)
    found = segments.paths(rows, [], T0, END)
    # Clean destinations silent at TTL 7, where the lossy ones meet 184.105.81.237:
    # they may cross it too.
    for i in range(3):
        found[f"C{i}"]["hops"][6] = "*"
    seg = ("router", "184.105.81.237", "")
    assert segments._unknown(seg, {7}, found["C0"])
    assert not segments._unknown(seg, {7}, found["C3"])
    h = segments.hypothesis(_period(loss), found)
    # The AS is still known not to be on their (complete) paths, so it stays.
    assert h["segment"] == "AS6939" and h["spill"] == 0.0


def test_an_unfinished_trace_may_cross_an_as_it_does_not_show():
    rows, loss = _case(n_lost=5, n_clean=5)
    found = segments.paths(rows, [], T0, END)
    for i in range(5):
        found[f"C{i}"]["reached"] = False
    assert segments._unknown(("as", 6939), {0}, found["C0"])
    h = segments.hypothesis(_period(loss), found)
    # The AS may sit past where the clean traces went silent, but they
    # answered at TTL 7 from another router: the router still stands.
    assert h["segment"] == "router 184.105.81.237" and h["confidence"] == "strong"
    assert "AS6939" not in h["alternatives"]
    # Silent at TTL 7 as well: nothing is known about the clean ones there.
    for i in range(5):
        found[f"C{i}"]["hops"][6] = "*"
    assert segments.hypothesis(_period(loss), found)["confidence"] == "none"


def test_private_hops_are_named_by_the_public_hop_before_them():
    trace = {"hops": ["192.168.1.1", "100.64.0.1", "184.105.60.137", "10.0.0.1", "*", "10.0.0.1"],
             "asns": [64500], "ixps": {}, "reached": True}
    segs = segments.segments(trace)
    assert ("router", "192.168.1.1", "") in segs        # the house's own
    assert ("router", "100.64.0.1", "") in segs         # CGNAT before any public hop
    assert ("router", "10.0.0.1", "184.105.60.137") in segs
    assert segs[("router", "10.0.0.1", "184.105.60.137")] == 4
    assert not any(s[1] == "*" for s in segs)
    assert segments.label(("router", "10.0.0.1", "184.105.60.137")) == \
        "router 10.0.0.1 after 184.105.60.137"


def test_the_same_private_address_in_two_networks_is_two_routers():
    a = {"hops": ["216.21.171.1", "10.0.0.1"], "asns": [], "ixps": {}, "reached": True}
    b = {"hops": ["131.125.181.85", "10.0.0.1"], "asns": [], "ixps": {}, "reached": True}
    assert set(segments.segments(a)) & set(segments.segments(b)) == set()


def test_an_exchange_is_a_segment():
    rows, loss = _case(n_lost=5, n_clean=5)
    ixp = [{"_time": _iso(T0 - 600), "target": f"L{i}", "ttl": "07", "_value": "Equinix Dallas"}
           for i in range(4)]
    found = segments.paths(rows, ixp, T0, END)
    assert found["L0"]["ixps"] == {7: "Equinix Dallas"}
    assert ("ixp", "Equinix Dallas") in segments.segments(found["L0"])
    h = segments.hypothesis(_period(loss), found)
    assert "exchange Equinix Dallas" in h["alternatives"]


def test_the_trace_read_is_the_last_one_near_the_period():
    old = _direct("A", 1)
    old["_time"] = _iso(T0 - segments.LOOKBACK_S - 60)          # too old
    before = _direct("A", 2) | {"_time": _iso(T0 - 1800)}
    inside = _via_he("A", 3) | {"_time": _iso(T0 + 1800)}
    after = _direct("A", 4) | {"_time": _iso(END + 60)}           # after the end
    found = segments.paths([inside, old, after, before], [], T0, END)
    assert found["A"]["hops"][-1] == "184.105.82.3"
    assert found["A"]["asns"] == [19165, 16591, 6939, 64503]
    assert found["A"]["changed"] is True  # two paths inside the window
    only = segments.paths([old, after], [], T0, END)
    assert only == {}


def test_malformed_rows_are_skipped():
    rows = [{"_time": None, "target": "A", "path": "1.1.1.1"},
            {"_time": _iso(T0), "target": "", "path": "1.1.1.1"},
            {"_time": _iso(T0), "target": "B", "path": ""},
            {"_time": _iso(T0), "target": "C", "path": "192.0.2.1|*", "as_path": "x 64500",
             "reached": "false"}]
    ixp = [{"_time": _iso(T0), "target": "C", "ttl": "x", "_value": "IX"}]
    found = segments.paths(rows, ixp, T0, END)
    assert list(found) == ["C"]
    assert found["C"]["asns"] == [64500] and found["C"]["reached"] is False


def test_probe_names_share_their_base_names_trace():
    rows, loss = _case(n_lost=5, n_clean=5)
    loss = {(k + "_icmp" if k.startswith("L") else k): v for k, v in loss.items()}
    h = segments.hypothesis(_period(loss), segments.paths(rows, [], T0, END))
    assert h["traced"] == 10 and h["confidence"] == "strong"


def test_common_hops_stop_where_answers_part():
    a = {"hops": ["r1", "*", "r3", "x"]}
    b = {"hops": ["r1", "r2", "r3", "y"]}
    assert segments.common_hops([a, b]) == 3  # half answered at TTL 2
    c = {"hops": ["r1", "*", "r3", "x"]}
    assert segments.common_hops([a, b, c]) == 1  # one in three is no evidence
    assert segments.common_hops([a, {"hops": ["r1"]}]) == 1
    assert segments.common_hops([{"hops": ["*", "*"]}, {"hops": ["*", "*"]}]) == 0


def test_run_reads_only_shared_periods_and_skips_when_there_are_no_traces():
    rows, loss = _case(n_lost=5, n_clean=5)
    asked = []

    def query(flux):
        asked.append(flux)
        return rows if "path_trace" in flux else []

    result = {"periods": [_period(loss),
                          _period(loss) | {"scope": "dominated"},
                          {"start": T0, "end": END, "scope": "shared"}]}
    out = segments.run(result, query=query)
    assert len(out) == 1 and out[0]["confidence"] == "strong"
    assert sum("path_trace" in f for f in asked) == 1
    assert segments.run(result, query=lambda f: []) == []


def test_the_queries_read_the_window_and_refuse_odd_bucket_names(monkeypatch):
    flux = segments.traces_flux(T0, END)
    assert f"range(start: {T0}, stop: {END + 1})" in flux
    assert 'r._measurement == "path_trace"' in flux
    assert 'r._field == "ixp"' in segments.ixp_flux(T0, END)
    # The episode rounds (trigger=episode) lose hops to the loss itself.
    assert "not exists r.trigger" in flux and "not exists r.trigger" in segments.ixp_flux(T0, END)
    monkeypatch.setenv("INFLUX_BUCKET", 'x"y')
    try:
        segments.traces_flux(T0, END)
    except ValueError:
        pass
    else:
        raise AssertionError("a quote in the bucket must be refused")


def test_one_destination_counts_once_by_the_mean_of_its_voters():
    rows, loss = _case(n_lost=5, n_clean=5)
    # Two voters of one destination on either side of the line: one
    # destination, its mean (0.75 %) in between, on neither side.
    loss.pop("C0")
    loss |= {"C0": 1.2, "C0_icmp": 0.3}
    groups = segments.classify(loss)
    assert "C0" in groups["mild"] and "C0" not in groups["affected"] + groups["unaffected"]
    assert sum(g.count("C0") for g in groups.values()) == 1
    h = segments.hypothesis(_period(loss), segments.paths(rows, [], T0, END))
    assert (h["affected"], h["unaffected"], h["mild"]) == (5, 4, 1)


def test_a_clean_gateway_is_no_contrast():
    # Five lost through AS6939; the clean side is three gateways/LAN hosts
    # whose paths never leave the house: they would make every remote
    # segment look clean.
    rows = [_via_he(f"L{i}", i) for i in range(5)]
    rows += [_row(f"G{i}", ["192.168.86.1", "*"], []) for i in range(3)]
    loss = {f"L{i}": 4.0 for i in range(5)} | {f"G{i}": 0.0 for i in range(3)}
    h = segments.hypothesis(_period(loss), segments.paths(rows, [], T0, END))
    assert h["confidence"] == "none" and h["untraced"] == 3 and h["traced"] == 5


def test_a_coincidence_among_few_is_not_a_hypothesis():
    # 4 of 5 that lost happen to share an AS none of 3 clean ones cross:
    # 5 / 70 = 7 % by chance alone.
    rows = [_via_he(f"L{i}", i) for i in range(4)] + [_direct("L4", 4)]
    rows += [_direct(f"C{i}", 10 + i) for i in range(3)]
    loss = {f"L{i}": 2.0 for i in range(5)} | {f"C{i}": 0.0 for i in range(3)}
    h = segments.hypothesis(_period(loss), segments.paths(rows, [], T0, END))
    assert h["confidence"] == "none"
    assert segments.p_value(4, 4, 5, 8) == 5 / 70


def test_the_p_value_is_the_hypergeometric_tail():
    assert segments.p_value(9, 9, 9, 29) == 1 / __import__("math").comb(29, 9)
    assert segments.p_value(29, 9, 9, 29) == 1.0     # everyone crosses it
    assert segments.p_value(0, 0, 9, 29) == 1.0
    rows, loss = _case()
    h = segments.hypothesis(_period(loss), segments.paths(rows, [], T0, END))
    assert h["p_value"] < 1e-6


def test_a_router_one_hop_further_on_another_path_is_still_that_router():
    rows, loss = _case(n_lost=5, n_clean=5)
    found = segments.paths(rows, [], T0, END)
    # L0 reaches 184.105.81.237 at TTL 8 instead of 7.
    found["L0"]["hops"] = PREFIX + ["*", "*", "184.105.81.237", "184.105.82.0"]
    # C0 answered at 7 but is silent at 8: it may cross the router there.
    found["C0"]["hops"] = PREFIX + ["*", "23.255.224.64", "*", "23.255.225.0"]
    seg = ("router", "184.105.81.237", "")
    assert segments.segments(found["L0"])[seg] == 8
    assert segments._unknown(seg, {7, 8}, found["C0"])
    assert not segments._unknown(seg, {7}, found["C0"])
    h = segments.hypothesis(_period(loss), found)
    # The AS stays the hypothesis; the router now spills onto C0.
    assert h["segment"] == "AS6939" and "router 184.105.81.237 5/5 vs 0/5" in h["alternatives"]
