import paths
from fakes import ACCESS, PREFIX, ROUTER


def test_prefix_is_what_every_path_shares_even_with_a_shy_router():
    a = [ROUTER, *ACCESS, "34.0.0.1", "34.0.0.9"]
    b = [None, *ACCESS, "35.0.0.1", "35.0.0.9"]      # the router rate-limited this one
    c = [ROUTER, *ACCESS, "36.0.0.1"]
    assert paths.learn_prefix([a, b, c]) == PREFIX


def test_prefix_stops_where_paths_diverge_or_few_answer():
    a = [ROUTER, "23.0.0.1", "23.0.0.2"]
    b = [ROUTER, "23.0.0.1", "24.0.0.2"]
    assert paths.learn_prefix([a, b]) == [ROUTER, "23.0.0.1"]
    quiet = [[None, "23.0.0.1"], [None, "23.0.0.1"], [ROUTER, "23.0.0.1"]]
    assert paths.learn_prefix(quiet) == [None, "23.0.0.1"]     # 1 of 3: unknown, kept
    assert paths.learn_prefix([a]) == []


def test_a_silent_hop_stays_in_the_prefix_as_unknown():
    a = [None, "23.0.0.1", "34.0.0.9"]
    b = [None, "23.0.0.1", "35.0.0.9"]
    assert paths.learn_prefix([a, b]) == [None, "23.0.0.1"]
    assert paths.learn_prefix([[None, None, "x"], [None, None, "y"]]) == []   # two in a row


def test_one_answer_among_silent_hops_is_not_a_shared_hop():
    many = [[ROUTER, *ACCESS, None, f"3{i}.0.0.9"] for i in range(6)]
    many[0][5] = "184.0.0.1"                       # one destination's router answered
    many.append([ROUTER, *ACCESS, "184.0.0.1", "38.0.0.9"])
    assert paths.learn_prefix(many) == PREFIX


def test_a_destination_inside_the_prefix_does_not_cut_it_short():
    cpe = [ROUTER, ACCESS[0]]
    far = [[ROUTER, *ACCESS, "34.0.0.9"], [ROUTER, *ACCESS, "35.0.0.9"]]
    assert paths.learn_prefix([cpe, *far]) == PREFIX


def test_joined_parted_and_cut():
    own = [None] * 5 + ["34.0.0.1", "34.0.0.9"]
    assert paths.joined(PREFIX, own, 6) == [*PREFIX, "34.0.0.1", "34.0.0.9"]
    assert not paths.parted(PREFIX, [None, *ACCESS])
    assert paths.parted(PREFIX, ["192.168.9.1", *ACCESS])
    assert paths.cut(PREFIX, [[ROUTER, ACCESS[0], "23.9.9.9"]]) == PREFIX[:2]
    odd = [[ROUTER, *ACCESS], [ROUTER, *ACCESS], ["192.168.1.254", *ACCESS]]
    assert paths.cut(PREFIX, odd) == PREFIX               # one odd reply: kept
    assert paths.cut(PREFIX, [[None, *ACCESS]]) == PREFIX
    assert paths.cut([ROUTER, None, "x"], [["192.168.9.1"]]) == []


def test_as_path_skips_private_and_unknown_and_collapses_repeats():
    asn = {"23.0.0.1": 64500, "23.0.0.2": 64500, "34.0.0.1": 64501}.get
    assert paths.as_path([ROUTER, "23.0.0.1", None, "23.0.0.2", "34.0.0.1", "35.0.0.1"],
                         asn) == [64500, 64501]


ASN = {"34.0.0.1": 1, "34.0.0.9": 2, "35.0.0.1": 3, "23.0.0.1": 9, "23.0.0.2": 9,
       "23.0.0.3": 9, "23.0.0.4": 9}


def _trace(state, hops, distance=None, dst="34.0.0.9", own_from=1, asn=ASN.get):
    return paths.track(state, dst, list(hops), distance or len(hops), asn, own_from)


def test_one_different_trace_is_a_candidate_two_are_a_change():
    state = {}
    old = [*PREFIX, "34.0.0.1", "34.0.0.9"]
    new = [*PREFIX, "35.0.0.1", "34.0.0.9"]
    assert not any(_trace(state, old).values())
    assert not _trace(state, new)["changed"]          # once: a candidate
    assert not _trace(state, old)["changed"]          # back: dropped
    assert not _trace(state, new)["changed"]
    flags = _trace(state, new)
    assert flags["changed"] and flags["as_changed"]   # AS 1 -> AS 3
    assert state["stable"]["hops"][5] == "35.0.0.1"


def test_silent_hops_are_not_a_change_and_are_filled_from_before():
    state = {}
    full = [*PREFIX, "34.0.0.1", "34.0.0.9"]
    _trace(state, full)
    holes = [None, *ACCESS, None, "34.0.0.9"]
    assert not _trace(state, holes)["changed"]
    assert not _trace(state, holes)["changed"]
    assert state["stable"]["hops"] == full


def test_a_longer_path_is_a_change_and_same_as_path_is_not_as_changed():
    state = {}
    _trace(state, [*PREFIX, "34.0.0.9"])
    longer = [*PREFIX, "34.0.0.1", "34.0.0.9"]
    _trace(state, longer)
    flags = _trace(state, longer, asn={**ASN, "34.0.0.1": 2}.get)
    assert flags["changed"] and not flags["as_changed"]


def test_a_new_address_starts_a_new_path():
    state = {}
    _trace(state, [*PREFIX, "34.0.0.9"])
    flags = _trace(state, [*PREFIX, "34.0.0.8"], dst="34.0.0.8")
    assert flags["dst_changed"] and not flags["changed"]


def test_a_borrowed_prefix_that_moved_is_not_this_destinations_change():
    state = {}
    _trace(state, [*PREFIX, "34.0.0.1", "34.0.0.9"], own_from=6)
    moved = [ROUTER, "23.9.9.9", *ACCESS[1:], "34.0.0.1", "34.0.0.9"]
    assert not _trace(state, moved, own_from=6)["changed"]
    assert not _trace(state, moved, own_from=6)["changed"]


def test_an_unknown_as_is_not_an_as_change():
    state = {}
    old = [*PREFIX, "34.0.0.1", "34.0.0.9"]
    new = [*PREFIX, "35.0.0.1", "34.0.0.9"]
    pending = {k: v for k, v in ASN.items() if k != "35.0.0.1"}.get
    _trace(state, old)
    _trace(state, new, asn=pending)
    flags = _trace(state, new, asn=pending)
    assert flags["changed"] and not flags["as_changed"]


def test_digest_and_text():
    assert paths.text([ROUTER, None]) == "192.168.1.1|*"
    assert paths.digest([ROUTER]) == paths.digest([ROUTER]) != paths.digest([None])


def test_a_borrowed_trace_does_not_overwrite_hops_the_destination_measured():
    state = {}
    mine = [ROUTER, "23.0.0.7", *ACCESS[1:], "34.0.0.1", "34.0.0.9"]
    _trace(state, mine)                                          # a full trace
    _trace(state, [*PREFIX, "34.0.0.1", "34.0.0.9"], own_from=6)  # borrowed 1-5
    assert state["stable"]["hops"][1] == "23.0.0.7"


def test_a_candidate_inside_the_prefix_waits_for_the_next_full_trace():
    state = {}
    old = [*PREFIX, "34.0.0.1", "34.0.0.9"]
    new = [ROUTER, "23.0.0.7", *ACCESS[1:], "34.0.0.1", "34.0.0.9"]
    _trace(state, old)
    _trace(state, new)                                           # a candidate at hop 2
    assert state["candidate"]
    _trace(state, old, own_from=6)                               # cannot judge hop 2
    assert state["candidate"]
    assert _trace(state, new)["changed"]                         # the next full trace
