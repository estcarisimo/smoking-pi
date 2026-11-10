"""Operator and kind per target: classification and the lookup cache.
No test resolves anything: the DNS lookups are fakes."""

import pytest

from common import operators

import tags


def test_cloudfront_and_ec2_share_an_as_and_the_cname_tells_them_apart():
    assert tags.classify(16509, "AMAZON-02", "d1.cloudfront.net", []) == ("Amazon CloudFront", tags.CDN)
    assert tags.classify(16509, "AMAZON-02", "www.amazon.com",
                         ["tp.x-frontier.amazon.com"]) == ("Amazon CloudFront", tags.CDN)
    assert tags.classify(16509, "AMAZON-02", "api.example.com", []) == ("AWS", tags.CLOUD)


def test_known_networks_and_the_rest():
    assert tags.classify(54113, "FASTLY - Fastly, Inc., US", "nytimes.com", []) == ("Fastly", tags.CDN)
    assert tags.classify(2906, "AS-SSI", "1.2.3.4", []) == ("Netflix", tags.CACHE)
    # Linnaeus's roles (common/data/asn_roles.csv) name it before the AS name does.
    assert tags.classify(3449, "AS3449 - Universidad Nacional de Buenos Aires, AR", "ns1.uba.ar",
                         []) == ("University of Buenos Aires", tags.ACADEMIC)
    assert tags.classify(64501, "AS64501 - Universidad de Ejemplo, AR", "x",
                         []) == ("Universidad de Ejemplo", tags.ACADEMIC)
    assert tags.classify(64500, "EXAMPLE-NET - Example Hosting, US", "x", []) == ("EXAMPLE-NET", tags.OTHER)
    assert tags.classify(None, "", "x", []) == (tags.UNKNOWN, tags.UNKNOWN)


def test_an_address_is_its_own_answer():
    assert tags.resolve("23.246.3.208") == ("23.246.3.208", [])


def make(tmp_path, clock, answers, owners, calls):
    def resolve_fn(host):
        calls.append(("A", host))
        return answers.get(host)

    def origin_fn(ip):
        calls.append(("AS", ip))
        return owners.get(ip)

    return tags.Tags(tmp_path / "tags.json", clock=clock, resolve_fn=resolve_fn, origin_fn=origin_fn)


def test_lookups_are_cached_and_survive_a_restart(tmp_path):
    now = [1_000_000.0]
    calls = []
    answers = {"nytimes.com": ("151.101.1.1", [])}
    owners = {"151.101.1.1": (54113, "FASTLY - Fastly, Inc., US")}
    hosts = {"NYT": "nytimes.com"}
    t = make(tmp_path, lambda: now[0], answers, owners, calls)
    t.refresh(hosts)
    assert t.of("NYT", hosts) == ("Fastly", tags.CDN)
    assert len(calls) == 2
    t2 = make(tmp_path, lambda: now[0], answers, owners, calls)
    t2.refresh(hosts)
    assert len(calls) == 2  # read from tags.json, nothing asked again
    assert t2.of("NYT", hosts) == ("Fastly", tags.CDN)
    now[0] += tags.ADDRESS_TTL + 1
    t2.refresh(hosts)
    assert calls[-1] == ("A", "nytimes.com")  # the address is asked again after a day


def test_a_failed_lookup_reads_unknown_and_is_asked_again_after_an_hour(tmp_path):
    now = [1.0]
    calls = []
    hosts = {"X": "x.example"}
    t = make(tmp_path, lambda: now[0], {}, {}, calls)
    t.refresh(hosts)
    assert t.of("X", hosts) == (tags.UNKNOWN, tags.UNKNOWN)
    t.refresh(hosts)
    assert calls == [("A", "x.example")]
    now[0] += tags.FAILED_TTL + 1
    t.refresh(hosts)
    assert calls == [("A", "x.example"), ("A", "x.example")]


def test_one_lookup_that_raises_loses_none_of_the_others(tmp_path):
    def resolve_fn(host):
        if host == "bad":
            raise RuntimeError("resolver blew up")
        return ("104.16.0.1", [])

    t = tags.Tags(tmp_path / "tags.json", clock=lambda: 1.0, resolve_fn=resolve_fn,
                  origin_fn=lambda ip: (13335, "CLOUDFLARENET"))
    hosts = {"A": "good", "B": "bad"}
    t.refresh(hosts)
    assert t.of("A", hosts) == ("Cloudflare", tags.CDN)
    assert (tmp_path / "tags.json").exists()


def test_hosts_that_never_resolve_do_not_starve_the_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(operators, "BUDGET", 2)
    now = [1.0]
    calls = []
    answers = {"z": ("104.16.0.1", [])}
    owners = {"104.16.0.1": (13335, "CLOUDFLARENET")}
    t = make(tmp_path, lambda: now[0], answers, owners, calls)
    hosts = {"A": "a-dead", "B": "b-dead", "Z": "z"}
    for _ in range(3):
        t.refresh(hosts)
        now[0] += tags.FAILED_TTL + 1
    assert t.of("Z", hosts) == ("Cloudflare", tags.CDN)


def test_one_owner_lookup_per_slash_24(tmp_path):
    calls = []
    answers = {"a": ("104.16.0.1", []), "b": ("104.16.0.2", [])}
    owners = {"104.16.0.1": (13335, "CLOUDFLARENET"), "104.16.0.2": (13335, "CLOUDFLARENET")}
    t = make(tmp_path, lambda: 1.0, answers, owners, calls)
    hosts = {"A": "a", "B": "b"}
    t.refresh(hosts)
    assert sum(1 for c in calls if c[0] == "AS") == 1
    assert t.of("B", hosts) == ("Cloudflare", tags.CDN)


# --- resolution: SmokePing's resolvers, not the Pi's filtering one -------------

class FakeAnswer:
    """What dnspython's resolve() returns, as far as common.asn reads it."""

    def __init__(self, ips, chain=()):
        self.ips = list(ips)
        cname = type("RRset", (), {"rdtype": 5, "__iter__": lambda s: iter(
            [type("R", (), {"target": c + "."})() for c in chain])})()
        self.response = type("Resp", (), {"answer": [cname] if chain else []})()

    def __len__(self):
        return len(self.ips)

    def __getitem__(self, i):
        return self.ips[i]

    def __iter__(self):
        return iter(self.ips)


class FakeResolver:
    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def resolve(self, host, rdtype):
        self.asked.append(host)
        found = self.answers.get(host)
        if found is None:
            raise RuntimeError("NXDOMAIN or timeout")
        return found


def test_a_blocked_answer_is_no_answer():
    pi = FakeResolver({"stats.g.doubleclick.net": FakeAnswer(["0.0.0.0"])})
    assert tags.resolve("stats.g.doubleclick.net", pi) is None
    assert tags.blocked("0.0.0.0") and tags.blocked("::")
    assert not tags.blocked("142.250.0.1") and not tags.blocked("not-an-address")


def test_the_public_resolver_answers_first_and_the_pi_is_not_asked():
    public = FakeResolver({"stats.g.doubleclick.net": FakeAnswer(["142.250.0.154"])})
    pi = FakeResolver({"stats.g.doubleclick.net": FakeAnswer(["0.0.0.0"])})
    assert tags.lookup("stats.g.doubleclick.net", public, pi) == ("142.250.0.154", [])
    assert pi.asked == []


def test_the_host_resolver_is_the_fallback_and_its_blocked_answer_is_none():
    public = FakeResolver({})  # unreachable: every query fails
    pi = FakeResolver({"intranet.example": FakeAnswer(["10.0.0.5"]),
                       "sentry.io": FakeAnswer(["0.0.0.0"])})
    assert tags.lookup("intranet.example", public, pi) == ("10.0.0.5", [])
    assert tags.lookup("sentry.io", public, pi) is None
    assert public.asked == ["intranet.example", "sentry.io"]


def test_the_cname_chain_comes_from_the_resolver_that_answered():
    public = FakeResolver({"www.amazon.com": FakeAnswer(
        ["18.155.0.1"], chain=["tp.x-frontier.amazon.com"])})
    assert tags.lookup("www.amazon.com", public, FakeResolver({})) == (
        "18.155.0.1", ["tp.x-frontier.amazon.com"])


def test_nameservers_are_smokeping_s(monkeypatch, caplog):
    assert tags.nameservers() == ["1.1.1.1", "8.8.8.8"]
    monkeypatch.setenv("SMOKEPING_DNS", "9.9.9.9")
    monkeypatch.setenv("SMOKEPING_DNS_FALLBACK", "not an address")
    with caplog.at_level("WARNING", logger="inference.tags"):
        assert tags.nameservers() == ["9.9.9.9"]
        assert tags.nameservers() == ["9.9.9.9"]
    assert caplog.text.count("SMOKEPING_DNS_FALLBACK is not an IP address") == 1
    monkeypatch.setenv("SMOKEPING_DNS_FALLBACK", "9.9.9.9")
    assert tags.nameservers() == ["9.9.9.9"]


def test_a_cached_blocked_address_is_asked_again_and_reads_unknown(tmp_path):
    # tags.json from before this fix holds 0.0.0.0 for a day: the next pass
    # asks again rather than wait it out, and until then it is no operator,
    # not "local network"
    import json
    (tmp_path / "tags.json").write_text(json.dumps({
        "addresses": {"sentry.io": {"ip": "0.0.0.0", "chain": [], "at": 1.0}}, "owners": {}}))
    calls = []
    hosts = {"Sentry": "sentry.io"}
    t = make(tmp_path, lambda: 2.0, {"sentry.io": ("35.186.247.156", [])},
             {"35.186.247.156": (396982, "GOOGLE-CLOUD-PLATFORM")}, calls)
    assert t.owner("Sentry", hosts) == (tags.UNKNOWN, tags.UNKNOWN, 0)
    assert t.untagged(["Sentry"], hosts) == ["Sentry"]
    t.refresh(hosts)
    assert calls[0] == ("A", "sentry.io")
    assert t.of("Sentry", hosts) == ("Google Cloud", tags.CLOUD)
    assert t.untagged(["Sentry"], hosts) == []


def test_the_gateway_is_not_counted_as_untagged(tmp_path):
    hosts = {"CPE": "192.168.1.1", "X": "x.example"}
    t = make(tmp_path, lambda: 1.0, {"192.168.1.1": ("192.168.1.1", [])}, {}, [])
    t.refresh(hosts)
    assert t.untagged(["CPE", "X"], hosts) == ["X"]


def test_the_public_resolver_reads_nothing_from_the_host(monkeypatch):
    pytest.importorskip("dns.resolver")
    monkeypatch.setenv("SMOKEPING_DNS", "9.9.9.9")
    r = tags._resolver(tags.nameservers())
    assert [str(n) for n in r.nameservers] == ["9.9.9.9", "8.8.8.8"]
    # the second server gets a full try even when the first times out
    assert r.lifetime >= 2 * r.timeout
