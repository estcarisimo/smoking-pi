import names


def test_public_addresses_only_cached_a_week_and_budgeted(monkeypatch):
    asked = []

    def resolve(ip):
        asked.append(ip)
        return {"23.0.0.1": "ae-1.r02.chcgil09.us.bb.example.net"}.get(ip, "")

    n = names.Names(clock=lambda: 1000, resolve=resolve)
    assert n.refresh({"23.0.0.1", "34.0.0.9", "192.168.1.1", "10.248.2.13", "junk"}) == 2
    assert sorted(asked) == ["23.0.0.1", "34.0.0.9"]
    assert n.of("23.0.0.1") == "ae-1.r02.chcgil09.us.bb.example.net"
    assert n.of("34.0.0.9") is None and n.of("192.168.1.1") is None
    again = names.Names(clock=lambda: 1000 + 3600, resolve=resolve)          # from the file
    assert again.refresh({"23.0.0.1", "34.0.0.9"}) == 0
    later = names.Names(clock=lambda: 1000 + names.NONE_TTL, resolve=resolve)
    assert later.refresh({"23.0.0.1", "34.0.0.9"}) == 1                       # the nameless one
    monkeypatch.setattr(names, "BUDGET", 3)
    many = {f"34.0.{i}.1" for i in range(10)}
    assert names.Names(clock=lambda: 0, resolve=resolve).refresh(many) == 3


def test_a_failed_query_keeps_a_known_name_and_is_retried_in_an_hour():
    calls = []

    def flaky(ip):
        calls.append(ip)
        if len(calls) > 1:
            raise names.Failed("Timeout")
        return "edge-1.example.net"

    n = names.Names(clock=lambda: 0, resolve=flaky)
    n.refresh({"23.0.0.1"})
    n2 = names.Names(clock=lambda: names.TTL, resolve=flaky)
    n2.refresh({"23.0.0.1"})
    assert n2.of("23.0.0.1") == "edge-1.example.net"
    assert names.Names(clock=lambda: names.TTL + 60, resolve=flaky).refresh({"23.0.0.1"}) == 0
    assert names.Names(clock=lambda: names.TTL + names.FAILED_TTL,
                       resolve=flaky).refresh({"23.0.0.1"}) == 1


def test_an_unexpected_error_is_one_failure_not_all():
    def broken(ip):
        if ip == "23.0.0.1":
            raise RuntimeError("bad packet")
        return "ok.example.net"
    n = names.Names(clock=lambda: 0, resolve=broken)
    assert n.refresh({"23.0.0.1", "34.0.0.1"}) == 2 and n.of("34.0.0.1") == "ok.example.net"


def test_prune_forgets_addresses_off_every_path():
    n = names.Names(clock=lambda: 0, resolve=lambda ip: "x.example.net")
    n.refresh({"23.0.0.1", "34.0.0.1"})
    n.prune({"23.0.0.1"})
    assert n.of("34.0.0.1") is None and n.of("23.0.0.1") == "x.example.net"


def test_names_are_written_with_their_hop_and_not_in_the_path():
    import store
    pts = store.trace_points("X", "c", 1000, "34.0.0.9", ["23.0.0.1", "34.0.0.9"], [1.0, 2.0],
                             True, 2, 1, "23.0.0.1|34.0.0.9", "h", [], {}, lambda a: None,
                             None, {"23.0.0.1": "ae-1.example.net"}.get)
    lines = [p.to_line_protocol() for p in pts]
    hop1 = next(line for line in lines if line.startswith("path_hop") and "ttl=01" in line)
    assert 'ptr="ae-1.example.net"' in hop1
    trace = next(line for line in lines if line.startswith("path_trace"))
    assert "ae-1" not in trace
