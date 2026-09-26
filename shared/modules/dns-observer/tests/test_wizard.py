import base64
import json
import math
import os

import dns.message
import dns.rrset
import pytest

import wizard

HOUR = 3600
T0 = 1790380800  # 2026-09-26 00:00:00 UTC, an hour boundary


def answer(name, cnames=(), addr="192.0.2.1"):
    r = dns.message.make_response(dns.message.make_query(name, "A"))
    owner = name.rstrip(".") + "."
    for t in cnames:
        r.answer.append(dns.rrset.from_text(owner, 60, "IN", "CNAME", t + "."))
        owner = t + "."
    if addr:
        r.answer.append(dns.rrset.from_text(owner, 60, "IN", "A", addr))
    return base64.b64encode(r.to_wire()).decode()


def line(name, ts, qtype="A", cached=False, **kw):
    iso = __import__("datetime").datetime.fromtimestamp(ts, __import__("datetime").UTC)
    return json.dumps({"T": iso.isoformat().replace("+00:00", ".5Z"), "QH": name, "QT": qtype,
                       "Cached": cached, "Answer": answer(name, **kw)})


class NoLookups(wizard.Owners):
    def __init__(self, table=None):
        super().__init__()
        self.table = table or {}

    def _lookup(self, net):
        return self.table.get(net)


@pytest.fixture
def wiz(tmp_path):
    logdir, state = tmp_path / "data", tmp_path / "state"
    logdir.mkdir()
    state.mkdir()

    def make(**kw):
        return wizard.Wizard(str(logdir), str(state), canary_domain="canary.smoking-pi.home.arpa",
                             owners=kw.pop("owners", NoLookups()), **kw)

    make.log = logdir / "querylog.json"
    make.state = state
    return make


def write(path, lines, mode="a"):
    with open(path, mode) as fh:
        fh.write("".join(x + "\n" for x in lines))


# -- units -----------------------------------------------------------------------


@pytest.mark.parametrize(("name", "svc"), [
    ("nrdp.logs.netflix.com", "netflix.com"), ("www.bbc.co.uk", "bbc.co.uk")])
def test_service_is_etld_plus_one(name, svc):
    assert wizard.service_of(name) == svc


def test_cdn_uses_icann_suffixes_only():
    assert wizard.cdn_of("x.example", ["x.example.com.cdn.cloudflare.net"]) == "cloudflare.net"
    assert wizard.cdn_of("x.example", ["d3p8zr0ffa9t17.cloudfront.net"]) == "cloudfront.net"
    assert wizard.cdn_of("api.example.org", []) == "example.org"


@pytest.mark.parametrize(("name", "own"), [
    ("api.telegram.org", True), ("ghcr.io", True), ("whoami.akamai.net", True),
    ("x1.canary.smoking-pi.home.arpa", True), ("a1.w10.akamai.net", False), ("netflix.com", False)])
def test_own_traffic(name, own):
    patterns = wizard.OWN_TRAFFIC + (".canary.smoking-pi.home.arpa",)
    assert wizard.is_own(name, wizard.service_of(name), patterns) is own


def test_diversity_uniform_and_empty():
    d = wizard.diversity([1.0] * 4)
    assert d["richness"] == 4
    assert math.isclose(d["effective_hhi"], 4) and math.isclose(d["effective_shannon"], 4)
    assert math.isclose(d["cov5"], 1.0)
    assert wizard.diversity([])["richness"] == 0


# -- incremental reading ------------------------------------------------------------


def test_second_pass_reads_only_what_was_appended(wiz):
    write(wiz.log, [line("www.netflix.com", T0 + 10)])
    w = wiz()
    assert w.ingest_new() == 1
    assert w.ingest_new() == 0
    write(wiz.log, [line("www.bbc.co.uk", T0 + 20), line("api.telegram.org", T0 + 30)])
    assert w.ingest_new() == 1  # telegram is the Pi's own


def test_a_partial_last_line_waits_for_the_next_pass(wiz):
    full = line("www.netflix.com", T0 + 10)
    with open(wiz.log, "w") as fh:
        fh.write(full[:40])
    w = wiz()
    assert w.ingest_new() == 0
    with open(wiz.log, "a") as fh:
        fh.write(full[40:] + "\n")
    assert w.ingest_new() == 1


def test_rotation_reads_the_rest_of_the_old_file(wiz):
    write(wiz.log, [line("a.netflix.com", T0 + 1)])
    w = wiz()
    w.ingest_new()
    write(wiz.log, [line("b.bbc.co.uk", T0 + 2)])  # appended after the pass
    os.rename(wiz.log, str(wiz.log) + ".1")  # AdGuard rotates
    write(wiz.log, [line("c.wikipedia.org", T0 + 3)], mode="w")
    assert w.ingest_new() == 2


def test_state_survives_a_restart(wiz):
    write(wiz.log, [line("www.netflix.com", T0 + 10)])
    wiz().run_once(now=T0 + 100)
    w = wiz()
    assert w.ingest_new() == 0
    assert set(w.state.hours[str(T0 // HOUR)]) == {"netflix.com"}


# -- the snapshot -------------------------------------------------------------------


def test_presence_ranks_and_own_share(wiz):
    lines = [line("chatty.alpha.org", T0 + i) for i in range(50)]  # one hour, a burst
    lines += [line("steady.beta.net", T0 + h * HOUR + 5) for h in range(5)]  # five hours
    lines += [line("ghcr.io", T0 + 7)] * 5  # the Pi's own
    write(wiz.log, lines)
    snap = wiz().run_once(now=T0 + 5 * HOUR)
    top = [t["service"] for t in snap["top"]]
    assert top[0] == "beta.net"  # presence beats volume
    assert snap["top"][0]["presence_h"] == 5
    assert snap["top"][0]["host"] == "steady.beta.net"
    assert math.isclose(snap["own_share_24h"], 5 / 60)
    assert snap["diversity"]["service"]["richness"] == 2


def test_random_names_are_never_the_endpoint(wiz):
    write(wiz.log, [line("d3p8zr0ffa9t17.cdnhost.org", T0 + 1)] * 3
          + [line("www.cdnhost.org", T0 + 2)])
    snap = wiz().run_once(now=T0 + HOUR)
    assert snap["top"][0]["host"] == "www.cdnhost.org"


def test_old_hours_are_pruned(wiz):
    write(wiz.log, [line("old.alpha.org", T0), line("new.beta.net", T0 + 8 * 24 * HOUR)])
    snap = wiz().run_once(now=T0 + 8 * 24 * HOUR + 60)
    assert [t["service"] for t in snap["top"]] == ["beta.net"]


def test_networks_and_churn(wiz):
    owners = NoLookups({"192.0.2.0/24": ["AS64500", "EXAMPLE-NET"],
                        "198.51.100.0/24": ["AS64501", "OTHER-NET"]})
    write(wiz.log, [line("a.alpha.org", T0 + 1, addr="192.0.2.1"),
                    line("b.beta.net", T0 + 2, addr="198.51.100.1")])
    w = wiz(owners=owners)
    first = w.run_once(now=T0 + 60)
    assert first["churn"]["top10_jaccard_vs_yesterday"] is None
    assert first["top"][0]["asn"] in ("AS64500", "AS64501")
    assert first["behind_top10"]["asn"] == 2
    write(wiz.log, [line("c.gamma.com", T0 + 24 * HOUR + 1, addr="192.0.2.9")])
    second = w.run_once(now=T0 + 24 * HOUR + 60)
    # Yesterday {alpha.org, beta.net}; today the same two plus gamma.com.
    assert math.isclose(second["churn"]["top10_jaccard_vs_yesterday"], 2 / 3)


def test_snapshot_file_is_readable_by_others(wiz):
    write(wiz.log, [line("www.netflix.com", T0 + 10)])
    wiz().run_once(now=T0 + 100)
    path = wiz.state / "wizard.json"
    assert oct(path.stat().st_mode & 0o777) == "0o644"
    assert json.loads(path.read_text())["top"][0]["service"] == "netflix.com"


def test_owners_are_cached_and_misses_retried():
    o = NoLookups({"192.0.2.0/24": ["AS64500", "EXAMPLE-NET"]})
    o.resolve({"192.0.2.7", "203.0.113.5"})
    assert o.owner("192.0.2.99") == ["AS64500", "EXAMPLE-NET"]
    assert "203.0.113.0/24" not in o.cache  # a miss is not cached
    assert o.owner(None) is None


def test_owner_cache_is_pruned_with_the_services(wiz):
    owners = NoLookups({"192.0.2.0/24": ["AS64500", "A"], "198.51.100.0/24": ["AS64501", "B"]})
    write(wiz.log, [line("old.alpha.org", T0, addr="192.0.2.1"),
                    line("new.beta.net", T0 + 8 * 24 * HOUR, addr="198.51.100.1")])
    w = wiz(owners=owners)
    w.run_once(now=T0 + 60)
    assert "192.0.2.0/24" in w.owners.cache
    w.run_once(now=T0 + 8 * 24 * HOUR + 60)
    assert set(w.owners.cache) == {"198.51.100.0/24"}


def test_a_missed_rotation_is_logged(wiz, caplog):
    write(wiz.log, [line("a.alpha.org", T0 + 1)])
    w = wiz()
    w.ingest_new()
    os.rename(wiz.log, str(wiz.log) + ".2")
    write(str(wiz.log) + ".1", [line("b.beta.net", T0 + 2)], mode="w")
    write(wiz.log, [line("c.gamma.com", T0 + 3)], mode="w")
    import logging
    with caplog.at_level(logging.INFO, logger="dns-observer.wizard"):
        assert w.ingest_new() == 1
    assert "rotated more than once" in caplog.text
