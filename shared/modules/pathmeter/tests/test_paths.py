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
    assert paths.learn_prefix(quiet) == []          # 1 of 3 answered TTL 1
    assert paths.learn_prefix([a]) == []


def test_joined_and_agrees():
    own = [None] * 5 + ["34.0.0.1", "34.0.0.9"]
    assert paths.joined(PREFIX, own, 6) == [*PREFIX, "34.0.0.1", "34.0.0.9"]
    assert paths.agrees([None, *ACCESS, "x"], PREFIX)
    assert not paths.agrees([ROUTER, "23.0.0.9"], PREFIX)


def test_as_path_skips_private_and_unknown_and_collapses_repeats():
    asn = {"23.0.0.1": 64500, "23.0.0.2": 64500, "34.0.0.1": 64501}.get
    assert paths.as_path([ROUTER, "23.0.0.1", None, "23.0.0.2", "34.0.0.1", "35.0.0.1"],
                         asn) == [64500, 64501]


def _trace(state, hops, distance=None, asns=(1, 2), dst="34.0.0.9"):
    return paths.track(state, dst, list(hops), distance or len(hops), list(asns))


def test_one_different_trace_is_a_candidate_two_are_a_change():
    state = {}
    old = [*PREFIX, "34.0.0.1", "34.0.0.9"]
    new = [*PREFIX, "35.0.0.1", "34.0.0.9"]
    assert not any(_trace(state, old).values())
    assert not _trace(state, new)["changed"]          # once: a candidate
    assert not _trace(state, old)["changed"]          # back: dropped
    assert not _trace(state, new)["changed"]
    flags = _trace(state, new, asns=(1, 3))
    assert flags["changed"] and flags["as_changed"]
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
    flags = _trace(state, longer)
    assert flags["changed"] and not flags["as_changed"]


def test_a_new_address_starts_a_new_path():
    state = {}
    _trace(state, [*PREFIX, "34.0.0.9"])
    flags = _trace(state, [*PREFIX, "34.0.0.8"], dst="34.0.0.8")
    assert flags["dst_changed"] and not flags["changed"]


def test_digest_and_text():
    assert paths.text([ROUTER, None]) == "192.168.1.1|*"
    assert paths.digest([ROUTER]) == paths.digest([ROUTER]) != paths.digest([None])
