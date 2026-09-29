import base64
import datetime
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
    ("nrdp.logs.netflix.com", "netflix.com"), ("www.bbc.co.uk", "bbc.co.uk"),
    # A private suffix is a site of its own ...
    ("abc.github.io", "abc.github.io"), ("a.b.s3.amazonaws.com", "b.s3.amazonaws.com"),
    # ... unless a CDN endpoint embeds a customer's domain: the service is
    # the CDN, not "com.cdn.cloudflare.net" (adopted on the reference Pi).
    ("www.x.com.cdn.cloudflare.net", "cloudflare.net"), ("e1.x.com.akadns.net", "akadns.net"),
    ("www.shop.co.uk.cdn.cloudflare.net", "cloudflare.net"),
    ("x.app.cdn.cloudflare.net", "cloudflare.net"),
    # A two-letter site name is not a TLD: still its own site.
    ("www.ab.github.io", "ab.github.io")])
def test_service_is_etld_plus_one(name, svc):
    assert wizard.service_of(name) == svc


def test_cdn_uses_icann_suffixes_only():
    assert wizard.cdn_of("x.example", ["x.example.com.cdn.cloudflare.net"]) == "cloudflare.net"
    assert wizard.cdn_of("x.example", ["d3p8zr0ffa9t17.cloudfront.net"]) == "cloudfront.net"
    assert wizard.cdn_of("api.example.org", []) == "example.org"


@pytest.mark.parametrize(("name", "own"), [
    ("api.telegram.org", True), ("ghcr.io", True), ("whoami.akamai.net", True),
    ("x1.canary.smoking-pi.home.arpa", True), ("a1.w10.akamai.net", False), ("netflix.com", False),
    ("config-manager", True), ("nas.internal", True), ("github.io", False),
    ("www.piwheels.org", True), ("grafana.com", True), ("stats.grafana.org", True)])
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


def test_names_counted_before_the_filter_are_never_ranked(wiz):
    # State written by an observer that still counted bare names as services.
    w = wiz()
    hour = str(int(T0 // HOUR))
    w.state.hours[hour] = {"config-manager": [9, 9], "netflix.com": [1, 1]}
    snap = w.snapshot(T0 + 1)
    assert [t["service"] for t in snap["top"]] == ["netflix.com"]


TARGETS = """\
*** Targets ***
+ Web
++ W_example_org_icmp
probe = FPing
host = www.alpha.org.
++ Google_dns
probe = DNS
host = 8.8.8.8
++ Multi
host = /Web/W_example_org_icmp /Web/Google_dns
"""


def test_measured_hosts_are_names_only(tmp_path):
    path = tmp_path / "Targets"
    path.write_text(TARGETS)
    assert wizard.measured_hosts(str(path)) == {"www.alpha.org"}
    assert wizard.measured_hosts(str(tmp_path / "missing")) == frozenset()
    assert wizard.measured_hosts(None) == frozenset()


def test_what_smokeping_measures_is_the_pis_own(wiz, tmp_path):
    # SmokePing re-resolves www.alpha.org every TTL, all night: without the
    # Targets file the wizard would rank it first on presence.
    targets = tmp_path / "Targets"
    lines = [line("www.alpha.org", T0 + h * HOUR + 5) for h in range(5)]
    lines += [line("mail.alpha.org", T0 + 9)]  # the house, same service
    lines += [line("steady.beta.net", T0 + h * HOUR + 7) for h in range(2)]
    write(wiz.log, lines)
    targets.write_text("")  # not measured yet
    snap = wiz(targets_path=str(targets)).run_once(now=T0 + 5 * HOUR)
    assert snap["top"][0]["service"] == "alpha.org"

    write(wiz.log, lines, mode="w")
    targets.write_text(TARGETS)  # adopted: now the Pi looks it up
    for f in wiz.state.iterdir():
        f.unlink()
    snap = wiz(targets_path=str(targets)).run_once(now=T0 + 5 * HOUR)
    assert [t["service"] for t in snap["top"]] == ["beta.net", "alpha.org"]
    assert snap["top"][1]["presence_h"] == 1  # the house's one lookup
    assert math.isclose(snap["own_share_24h"], 5 / 8)


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


# -- selection: K from the data ---------------------------------------------------


def cand(svc, score, asn="AS1", cdn="c.net"):
    return {"service": svc, "score": float(score), "asn": asn, "cdn": cdn, "host": "www." + svc}


def test_select_covers_the_activity_then_adds_diversity():
    cands = [cand("a.com", 50), cand("b.com", 30), cand("c.com", 10, asn="AS2"),
             cand("d.com", 6, asn="AS3", cdn="d.net"), cand("e.com", 4, asn="AS4")]
    picks = wizard.select(cands, coverage=0.8, floor=0.05, max_k=10)
    reasons = {p["service"]: p["reason"] for p in picks}
    # a+b = 80% of the score; AS2 (10%) and AS3 (6%) hold more than the 5% floor.
    assert reasons == {"a.com": "coverage", "b.com": "coverage",
                       "c.com": "network AS2", "d.com": "network AS3"}
    assert [p["service"] for p in picks] == ["a.com", "b.com", "c.com", "d.com"]


def test_select_k_grows_with_diversity():
    flat = [cand(f"s{i}.com", 1, asn=f"AS{i}") for i in range(40)]
    peaked = [cand("big.com", 90)] + [cand(f"s{i}.com", 0.25) for i in range(40)]
    assert len(wizard.select(flat, coverage=0.8, floor=0.005, max_k=100)) >= 32
    assert len(wizard.select(peaked, coverage=0.8, floor=0.005, max_k=100)) == 1


def test_select_budget_keeps_diversity_trims_the_tail():
    cands = [cand(f"s{i}.com", 10) for i in range(9)] + [cand("rare.com", 9, asn="AS9")]
    picks = wizard.select(cands, coverage=0.99, floor=0.05, max_k=4)
    assert len(picks) == 4
    assert any(p["service"] == "rare.com" for p in picks)


def test_snapshot_selects_by_volume_until_presence_means_something(wiz):
    lines = [line("www.big.org", T0 + i) for i in range(40)] + [line("www.small.net", T0 + 5)]
    lines += [line("4b574871442a5359d494-pod-x.gen.com", T0 + 6)] * 30  # no stable host
    write(wiz.log, lines)
    sel = wiz().run_once(now=T0 + HOUR)["selection"]
    assert sel["score"] == "queries"
    assert [s["service"] for s in sel["services"]][0] == "big.org"
    assert sel["skipped_no_host"] == 1
    assert all(s["host"] for s in sel["services"])


# -- resolution times ----------------------------------------------------------


def timed(name, ts, elapsed_ms, upstream="https://dns.cloudflare.com/dns-query", cached=False):
    e = json.loads(line(name, ts, cached=cached))
    e["Elapsed"] = int(elapsed_ms * 1e6)  # AdGuard writes nanoseconds
    e["Upstream"] = upstream
    return json.dumps(e)


@pytest.mark.parametrize("ms", [0.05, 0.1, 1.0, 12.3, 250.0, 4000.0])
def test_a_bin_holds_its_value_within_ten_percent(ms):
    assert abs(wizard.res_value(wizard.res_bin(ms)) / ms - 1) < 0.1


def test_bins_clamp_at_both_ends():
    assert wizard.res_bin(0) == 0
    assert wizard.res_bin(10 ** 6) == wizard.RES_BINS - 1


def test_quantiles_of_a_known_distribution():
    hist = {}
    for ms in range(1, 101):
        b = str(wizard.res_bin(float(ms)))
        hist[b] = hist.get(b, 0) + 1
    q = wizard.res_quantiles(hist)
    assert q["count"] == 100
    for p, expected in ((10, 10), (50, 50), (90, 90), (99, 99)):
        assert abs(q[f"p{p}"] / expected - 1) < 0.15, (p, q[f"p{p}"])
    assert q["p10"] <= q["p25"] <= q["p50"] <= q["p75"] <= q["p90"] <= q["p99"]
    assert wizard.res_quantiles({}) == {"count": 0}


@pytest.mark.parametrize("entry, path", [
    ({"Cached": True, "Upstream": "https://8.8.8.8:443/dns-query"}, "cache"),
    ({"Upstream": "https://dns.cloudflare.com/dns-query"}, "dns.cloudflare.com"),
    ({"Upstream": "https://8.8.8.8:443/dns-query"}, "8.8.8.8"),
    ({"Upstream": "tls://dns.quad9.net"}, "dns.quad9.net"),
    ({"Upstream": "1.1.1.1:53"}, "1.1.1.1"),
    # One IPv6 resolver, one name, however it is written.
    ({"Upstream": "2606:4700::1111"}, "2606:4700::1111"),
    ({"Upstream": "[2606:4700::1111]:53"}, "2606:4700::1111"),
    ({"Upstream": "https://[2606:4700::1111]:443/dns-query"}, "2606:4700::1111"),
    ({"Upstream": ""}, "local"),
    ({}, "local"),
])
def test_the_path_of_an_answer(entry, path):
    assert wizard.res_path(entry) == path


def test_resolution_times_of_the_house_per_path_complete_buckets_only(wiz):
    w = wiz()
    lines = [timed("www.example-shop.test.com", T0 + 10 + i, 20.0 + i) for i in range(10)]
    lines += [timed("api.example-video.net", T0 + 30, 0.1, cached=True) for _ in range(5)]
    lines += [timed("cdn.example-news.org", T0 + 40, 80.0, upstream="https://8.8.8.8:443/dns-query")]
    # The Pi's own lookups are not the house's, and do not count.
    lines += [timed("x.canary.smoking-pi.home.arpa", T0 + 50, 9999.0)]
    # A line without Elapsed is counted as a query but has no time; nor has
    # an impossible one (a corrupt line must not end the pass).
    lines += [line("www.example-shop.test.com", T0 + 60)]
    broken = json.loads(line("www.example-shop.test.com", T0 + 61))
    broken["Elapsed"] = float("inf")
    lines += [json.dumps(broken).replace("Infinity", "1e999")]
    broken["Elapsed"] = True
    lines += [json.dumps(broken)]
    # AdGuard's own answers (blocked, rewritten) are "local", never upstream.
    lines += [timed("ads.example-tracker.net", T0 + 70, 0.02, upstream="")]
    # The bucket still filling at snapshot time is not published.
    lines += [timed("www.example-shop.test.com", T0 + 610, 5.0)]
    write(wiz.log, lines)
    snap = w.run_once(now=T0 + 700)
    rows = {r["path"]: r for r in snap["resolution"]}
    assert {r["t"] for r in snap["resolution"]} == {T0}
    assert set(rows) == {"cache", "dns.cloudflare.com", "8.8.8.8", "local", "upstreams"}
    assert rows["local"]["count"] == 1
    assert rows["cache"]["count"] == 5 and rows["cache"]["p50"] < 0.2
    assert rows["dns.cloudflare.com"]["count"] == 10
    assert 20 <= rows["dns.cloudflare.com"]["p50"] <= 26
    assert rows["8.8.8.8"]["count"] == 1
    # Every upstream together, never the cache: 11 answers.
    assert rows["upstreams"]["count"] == 11
    assert all(r["p99"] < 9000 for r in snap["resolution"])
    # The state keeps the histograms across a restart.
    again = wiz()
    assert again.state.res and again.resolution(T0 + 700) == snap["resolution"]


def test_old_resolution_buckets_are_pruned(wiz):
    w = wiz()
    write(wiz.log, [timed("www.example-shop.test.com", T0 + 10, 20.0)])
    w.run_once(now=T0 + 700)
    assert str(T0) in w.state.res
    w.run_once(now=T0 + wizard.RES_KEEP + 700)
    assert str(T0) not in w.state.res


def test_a_bucket_whose_queries_reach_the_disk_late_is_still_published(wiz, caplog):
    # AdGuard flushes its log every 1000 queries: on a quiet network a
    # bucket's lines can be read hours after it closed. Five hours: past the
    # old two-hour window, inside the six the state keeps.
    w = wiz()
    w.run_once(now=T0 + 5 * HOUR)
    write(wiz.log, [timed("www.example-shop.test.com", T0 + 10, 20.0)])
    snap = w.run_once(now=T0 + 5 * HOUR)
    rows = {(r["t"], r["path"]): r for r in snap["resolution"]}
    assert set(rows) == {(T0, "dns.cloudflare.com"), (T0, "upstreams")}
    assert rows[(T0, "upstreams")]["count"] == 1
    # More of the same bucket, later still: the same bucket grows.
    write(wiz.log, [timed("www.example-shop.test.com", T0 + 20, 30.0)])
    snap = w.run_once(now=T0 + 5 * HOUR + 600)
    rows = {(r["t"], r["path"]): r for r in snap["resolution"]}
    assert rows[(T0, "upstreams")]["count"] == 2
    # Past what the state keeps: not published, and said in the log.
    write(wiz.log, [timed("www.example-shop.test.com", T0 + 30, 40.0)])
    with caplog.at_level("INFO"):
        snap = w.run_once(now=T0 + wizard.RES_KEEP + 600)
    assert not any(r["t"] == T0 for r in snap["resolution"])
    assert "1 query times reached the log more than 6 h late" in caplog.text


# -- the last hour ---------------------------------------------------------------


def api(name, ts, qtype="A"):
    """An entry of AdGuard's /control/querylog, which has not reached the file."""
    iso = datetime.datetime.fromtimestamp(ts, datetime.UTC)
    return {"time": iso.isoformat().replace("+00:00", ".5Z"),
            "question": {"name": name, "type": qtype, "class": "IN"}}


def test_the_last_hour_is_sixty_minutes_not_the_clock_hour(wiz):
    # At 01:10 the last hour starts at 00:15; the clock hour counted only
    # 01:00-01:10 and fell to zero at every hour boundary.
    write(wiz.log, [line("www.netflix.com", T0 + 5 * 60),           # 00:05, too old
                    line("www.netflix.com", T0 + 50 * 60),          # 00:50
                    line("www.bbc.co.uk", T0 + HOUR + 5 * 60)])     # 01:05
    snap = wiz().run_once(now=T0 + HOUR + 10 * 60)
    assert snap["queries_1h"] == 2
    rows = {t["service"]: t for t in snap["top"]}
    assert rows["netflix.com"]["queries_1h"] == 1
    assert rows["bbc.co.uk"]["queries_1h"] == 1


def test_queries_adguard_holds_in_memory_are_counted_once(wiz):
    now = T0 + HOUR + 10 * 60
    write(wiz.log, [line("www.netflix.com", T0 + HOUR)])
    memory = [api("www.bbc.co.uk", T0 + HOUR + 120), api("www.bbc.co.uk", T0 + HOUR + 60),
              api("ghcr.io", T0 + HOUR + 50),            # the Pi's own
              api("www.bbc.co.uk", T0 + HOUR + 40, "TXT"),  # not a kept type
              api("www.netflix.com", T0 + HOUR)]         # already in the file
    w = wiz()
    snap = w.run_once(now=now, unflushed=memory)
    assert snap["queries_1h"] == 3
    assert snap["queries_24h"] == 3
    # Not in what selects targets: bbc.co.uk has no row, nothing to measure yet.
    assert "bbc.co.uk" not in {t["service"] for t in snap["top"]}
    assert {t["service"]: t["queries_1h"] for t in snap["top"]}["netflix.com"] == 1
    # AdGuard flushes them: the file has them now, the API still lists them.
    write(wiz.log, [line("www.bbc.co.uk", T0 + HOUR + 60), line("www.bbc.co.uk", T0 + HOUR + 120)])
    snap = w.run_once(now=now + 60, unflushed=memory)
    assert snap["queries_1h"] == 3
    assert snap["queries_24h"] == 3
    # Nothing from the API (AdGuard restarting): the file alone.
    assert w.run_once(now=now + 120)["queries_1h"] == 3


def test_memory_is_counted_when_the_current_log_is_still_empty(wiz):
    # Right after a rotation the new file is empty: the old one's last line
    # says what was flushed.
    write(wiz.log, [line("www.netflix.com", T0 + HOUR)])
    w = wiz()
    w.run_once(now=T0 + HOUR + 60)
    os.replace(wiz.log, str(wiz.log) + ".1")
    write(wiz.log, [], mode="w")
    memory = [api("www.bbc.co.uk", T0 + HOUR + 30), api("www.netflix.com", T0 + HOUR)]
    assert w.run_once(now=T0 + HOUR + 120, unflushed=memory)["queries_1h"] == 2


def test_memory_older_than_the_last_hour_is_left_out(wiz):
    write(wiz.log, [line("www.netflix.com", T0)])
    snap = wiz().run_once(now=T0 + 2 * HOUR,
                          unflushed=[api("www.bbc.co.uk", T0 + 30 * 60)])
    assert snap["queries_1h"] == 0


def test_the_last_logged_line_skips_one_adguard_is_still_writing(wiz):
    write(wiz.log, [line("www.netflix.com", T0 + 10)])
    with open(wiz.log, "a") as fh:
        fh.write('{"T":"2026-09-26T00:00:20')  # no newline yet
    assert wiz().last_logged() == pytest.approx(T0 + 10.5)
    assert wizard.Wizard(str(wiz.state), str(wiz.state),
                         canary_domain="x.home.arpa").last_logged() is None


def test_a_state_from_before_the_last_hour_buckets_loads(wiz):
    write(wiz.log, [line("www.netflix.com", T0 + 10)])
    w = wiz()
    w.run_once(now=T0 + 60)
    doc = json.loads((wiz.state / "wizard-state.json").read_text())
    del doc["recent"]
    (wiz.state / "wizard-state.json").write_text(json.dumps(doc))
    # Counts from the upgrade on: the hour already read is not in the buckets.
    write(wiz.log, [line("www.bbc.co.uk", T0 + 70)])
    assert wiz().run_once(now=T0 + 120)["queries_1h"] == 1


def test_a_state_saved_under_old_names_merges_into_todays(tmp_path):
    # 2.15.4 counted x.com.cdn.cloudflare.net as "com.cdn.cloudflare.net";
    # on the reference Pi that name kept 44 of its hours after 2.15.5 and
    # ranked beside "cloudflare.net", which had one.
    path = tmp_path / "wizard-state.json"
    path.write_text(json.dumps({
        "hours": {"100": {"com.cdn.cloudflare.net": [3, 1], "netflix.com": [2, 2]},
                  "101": {"com.cdn.cloudflare.net": [1, 0], "cloudflare.net": [4, 2]}},
        "recent": {"0": {"com.cdn.cloudflare.net": 2, "cloudflare.net": 1}},
        "meta": {"com.cdn.cloudflare.net": {"cdn": "cloudflare.net", "host": "dynamic.x.com.cdn.cloudflare.net",
                                            "hosts": {"dynamic.x.com.cdn.cloudflare.net": 5}},
                 "cloudflare.net": {"hosts": {"dynamic.x.com.cdn.cloudflare.net": 1, "a.b.cdn.cloudflare.net": 2}}},
        "top_by_day": {"2026-09-29": ["netflix.com", "com.cdn.cloudflare.net", "cloudflare.net"]},
    }))
    st = wizard.State.load(str(path))
    assert st.hours == {"100": {"cloudflare.net": [3, 1], "netflix.com": [2, 2]},
                        "101": {"cloudflare.net": [5, 2]}}
    assert st.recent == {"0": {"cloudflare.net": 3}}
    assert list(st.meta) == ["cloudflare.net"]
    assert st.meta["cloudflare.net"]["hosts"] == {"dynamic.x.com.cdn.cloudflare.net": 6,
                                                   "a.b.cdn.cloudflare.net": 2}
    # What only the old entry knew is kept; the new one's own values win.
    assert st.meta["cloudflare.net"]["cdn"] == "cloudflare.net"
    assert st.top_by_day == {"2026-09-29": ["netflix.com", "cloudflare.net"]}


def test_the_ranking_after_an_upgrade_has_one_name_per_service(wiz):
    # Two passes; between them the state is rewritten as 2.15.4 saved it.
    write(wiz.log, [line("dynamic.x.com.cdn.cloudflare.net", T0 + 10)])
    wiz().run_once(now=T0 + 60)
    doc_path = wiz.state / "wizard-state.json"
    # Only the service key is exactly "cloudflare.net"; hosts are longer.
    doc = doc_path.read_text().replace('"cloudflare.net"', '"com.cdn.cloudflare.net"')
    doc_path.write_text(doc)
    write(wiz.log, [line("dynamic.x.com.cdn.cloudflare.net", T0 + 3700)])
    snap = wiz().run_once(now=T0 + 3760)
    services = [t["service"] for t in snap["top"]]
    assert "com.cdn.cloudflare.net" not in services
    assert services.count("cloudflare.net") == 1


def test_a_key_whose_hosts_still_give_it_is_not_renamed(tmp_path):
    # "play" is a TLD, but play.googleapis.com looked up directly is a name
    # of its own today too; and a key whose hosts disagree cannot be split.
    path = tmp_path / "wizard-state.json"
    path.write_text(json.dumps({
        "hours": {"100": {"play.googleapis.com": [1, 1], "com.akadns.net": [2, 1]}},
        "meta": {"play.googleapis.com": {"hosts": {"play.googleapis.com": 1}},
                 "com.akadns.net": {"hosts": {"com.akadns.net": 1, "e1.x.com.akadns.net": 1}}},
    }))
    st = wizard.State.load(str(path))
    assert st.hours == {"100": {"play.googleapis.com": [1, 1], "com.akadns.net": [2, 1]}}
    assert set(st.meta) == {"play.googleapis.com", "com.akadns.net"}


def test_a_state_of_an_unexpected_shape_loads_as_saved(tmp_path):
    path = tmp_path / "wizard-state.json"
    doc = {"hours": {"100": {"com.cdn.cloudflare.net": [1, 1]}},
           "meta": {"com.cdn.cloudflare.net": {"hosts": {"x.com.cdn.cloudflare.net": 1}},
                    "b.com": []}}
    path.write_text(json.dumps(doc))
    st = wizard.State.load(str(path))
    assert st.hours == doc["hours"] and st.meta == doc["meta"]


def test_chained_renames_end_where_the_chain_does():
    st = wizard.State(
        hours={"1": {"a.example": [1, 0], "b.example": [2, 0]}},
        meta={"a.example": {"hosts": {"h": 1}}, "b.example": {"hosts": {"i": 1}}})
    names = {"h": "b.example", "i": "c.example"}
    real = wizard.service_of
    wizard.service_of = names.get
    try:
        st.rename_services()
    finally:
        wizard.service_of = real
    assert st.hours == {"1": {"c.example": [3, 0]}}
