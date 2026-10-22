import owners


def test_one_lookup_per_slash24_cached_and_private_skipped():
    asked = []

    def origin(ip):
        asked.append(ip)
        return (64500, "EXAMPLE") if ip.startswith("23.") else None

    o = owners.Owners(clock=lambda: 1000, origin_fn=origin)
    assert o.refresh({"23.0.0.1", "23.0.0.2", "192.168.1.1", "34.0.0.1", "nonsense"}) == 2
    assert sorted(asked) == ["23.0.0.1", "34.0.0.1"] or sorted(asked) == ["23.0.0.2", "34.0.0.1"]
    assert o.of("23.0.0.200") == 64500 and o.of("34.0.0.1") is None and o.of(None) is None
    again = owners.Owners(clock=lambda: 1500, origin_fn=origin)
    assert again.refresh({"23.0.0.9"}) == 0            # a week's cache, from the file
    assert owners.Owners(clock=lambda: 1000 + 3601, origin_fn=origin).refresh({"34.0.0.5"}) == 1


def test_a_lookup_that_raises_counts_as_failed():
    def origin(ip):
        raise RuntimeError("boom")
    o = owners.Owners(clock=lambda: 0, origin_fn=origin)
    assert o.refresh({"23.0.0.1"}) == 1 and o.of("23.0.0.1") is None


def test_the_routing_table_answers_first_and_cymru_only_what_it_lacks():
    class Table:
        ready = True

        def lookup(self, ip):
            return 16591 if ip.startswith("23.") else None

    asked = []
    o = owners.Owners(clock=lambda: 0, origin_fn=lambda ip: asked.append(ip) or (64500, "X"),
                      table=Table())
    assert o.refresh({"23.0.0.1", "23.0.1.1", "34.0.0.1"}) == 1    # only the one it lacks
    assert asked == ["34.0.0.1"]
    assert o.of("23.9.9.9") == 16591 and o.of("34.0.0.1") == 64500
