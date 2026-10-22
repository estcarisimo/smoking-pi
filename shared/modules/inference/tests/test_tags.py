"""Operator and kind per target: classification and the lookup cache.
No test resolves anything: the DNS lookups are fakes."""

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
