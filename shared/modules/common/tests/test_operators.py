"""The per-target cache: what is asked, what is kept, what a target reads."""

from common import asn, operators


def make(tmp_path, clock, answers, owners, calls):
    def resolve_fn(host):
        calls.append(("A", host))
        found = answers.get(host)
        if isinstance(found, Exception):
            raise found
        return found

    def origin_fn(ip):
        calls.append(("AS", ip))
        return owners.get(ip)

    return operators.Operators(tmp_path / "ops.json", clock=clock,
                               resolve_fn=resolve_fn, origin_fn=origin_fn)


def test_operator_kind_and_asn_of_a_target(tmp_path):
    calls = []
    t = make(tmp_path, lambda: 1.0, {"www.nytimes.com": ("151.101.1.164", [])},
             {"151.101.1.164": (54113, "FASTLY")}, calls)
    hosts = {"NYT": "www.nytimes.com"}
    t.refresh(hosts)
    assert t.owner("NYT", hosts) == ("Fastly", asn.CDN, 54113)
    assert t.of("NYT", hosts) == ("Fastly", asn.CDN)


def test_a_url_host_is_looked_up_by_its_hostname(tmp_path):
    calls = []
    t = make(tmp_path, lambda: 1.0, {"www.google.com": ("142.250.0.1", [])},
             {"142.250.0.1": (15169, "GOOGLE")}, calls)
    hosts = {"Google_h2": "https://www.google.com/generate_204"}
    t.refresh(hosts)
    assert calls[0] == ("A", "www.google.com")
    assert t.owner("Google_h2", hosts) == ("Google", asn.CONTENT, 15169)


def test_a_lan_address_is_local_and_never_asked(tmp_path):
    calls = []
    t = make(tmp_path, lambda: 1.0, {"192.168.1.1": ("192.168.1.1", [])}, {}, calls)
    hosts = {"Gateway": "192.168.1.1"}
    t.refresh(hosts)
    assert not [c for c in calls if c[0] == "AS"]
    assert t.owner("Gateway", hosts) == ("local network", asn.LOCAL, 0)


def test_ipv6_only_and_unresolvable_hosts_are_unknown(tmp_path):
    calls = []
    t = make(tmp_path, lambda: 1.0, {"v6only.example": None,
                                     "2001:db8::1": ("2001:db8::1", [])}, {}, calls)
    hosts = {"V6": "v6only.example", "Literal": "2001:db8::1", "Gone": "nx.example"}
    t.refresh(hosts)
    for name in hosts:
        assert t.owner(name, hosts) == (asn.UNKNOWN, asn.UNKNOWN, 0)
    assert t.owner("NotATarget", hosts) == (asn.UNKNOWN, asn.UNKNOWN, 0)


def test_a_failed_relookup_keeps_the_answer_and_retries_in_an_hour(tmp_path):
    now = [1.0]
    calls = []
    answers = {"x.example": ("104.16.0.1", [])}
    owners = {"104.16.0.1": (13335, "CLOUDFLARENET")}
    t = make(tmp_path, lambda: now[0], answers, owners, calls)
    hosts = {"X": "x.example"}
    t.refresh(hosts)
    assert t.of("X", hosts) == ("Cloudflare", asn.CDN)
    now[0] += operators.OWNER_TTL + 1
    answers["x.example"] = RuntimeError("timeout")
    owners.clear()
    t.refresh(hosts)
    assert t.of("X", hosts) == ("Cloudflare", asn.CDN)  # not relabeled by one timeout
    asked = len(calls)
    now[0] += 60
    t.refresh(hosts)
    assert len(calls) == asked  # not hammered
    now[0] += operators.FAILED_TTL
    t.refresh(hosts)
    assert len(calls) > asked  # asked again after an hour


def test_the_budget_bounds_a_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(operators, "BUDGET", 2)
    calls = []
    t = make(tmp_path, lambda: 1.0, {}, {}, calls)
    t.refresh({f"T{i}": f"h{i}.example" for i in range(5)})
    assert len(calls) == 2


def test_the_cache_survives_a_restart(tmp_path):
    calls = []
    answers = {"a.example": ("104.16.0.1", [])}
    owners = {"104.16.0.1": (13335, "CLOUDFLARENET")}
    hosts = {"A": "a.example"}
    make(tmp_path, lambda: 1.0, answers, owners, calls).refresh(hosts)
    again = make(tmp_path, lambda: 2.0, answers, owners, calls)
    again.refresh(hosts)
    assert len(calls) == 2
    assert again.owner("A", hosts) == ("Cloudflare", asn.CDN, 13335)
