"""Unit tests for resolver_identity.py -- no dig, network or InfluxDB.

The dig answers below are the shapes captured on the reference Pi on
2026-09-26 (through its router and through the DNS observer), with the
addresses kept: they are public anycast resolver addresses, not the house's.
"""

import pathlib
import sys

import pytest

MODULE_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

import resolver_identity as ri  # noqa: E402

GOOGLE_TXT = ['"172.253.198.156"', '"edns0-client-subnet 192.0.2.0/24"']
ORIGINS = {
    "172.253.240.119": 15169,
    "172.253.198.156": 15169,
    "172.71.169.57": 13335,
    "192.178.115.93": 15169,
}
NAMES = {
    15169: "GOOGLE - Google LLC, US",
    13335: "CLOUDFLARENET - Cloudflare, Inc., US",
}


class Table:
    """A loaded routing table stand-in (common.asn.RoutingTable's surface)."""

    ready = True
    names = NAMES

    def lookup(self, ip):
        if ip == "nonsense":
            raise ValueError(ip)
        return ORIGINS.get(ip)


def owners(table=None):
    return ri.OwnerCache(table=table or Table())


def fake_dig(answers):
    """A dig stand-in: answers[(server, name, rtype)] -> lines (or None)."""
    calls = []

    def lookup(server, name, rtype, port=53):
        calls.append((server, name, rtype, port))
        return answers.get((server, name, rtype))

    lookup.calls = calls
    return lookup


def test_parse_google_txt_with_and_without_ecs():
    assert ri.parse_google_txt(GOOGLE_TXT) == (["172.253.198.156"], "192.0.2.0/24")
    assert ri.parse_google_txt(['"104.22.150.12"']) == (["104.22.150.12"], "")


def test_owners_come_from_the_table_only():
    cache = owners()
    assert cache.asn("172.71.169.57") == 13335 and cache.name(13335) == NAMES[13335]
    assert cache.asn("198.51.100.1") == 0 and cache.asn("nonsense") == 0
    assert cache.name(0) == "" and cache.name(64500) == ""


def test_no_table_loaded_yet_names_no_owner():
    class Loading(Table):
        ready = False

    cache = owners(Loading())
    assert cache.asn("172.71.169.57") == 0 and cache.name(13335) == ""


def test_probe_router_is_google_with_ecs():
    lookup = fake_dig({
        ("192.168.1.1", "whoami.akamai.net", "A"): ["172.253.240.119"],
        ("192.168.1.1", "o-o.myaddr.l.google.com", "TXT"): GOOGLE_TXT,
    })
    r = ri.probe("router", "192.168.1.1", owners(), lookup=lookup)
    assert r.ok and r.asn == 15169
    assert r.egress == ["172.253.198.156", "172.253.240.119"]
    assert r.ecs == "192.0.2.0/24"
    assert ri.owner_label(r) == "AS15169 GOOGLE - Google LLC, US"
    # Owners are looked up locally: dig asks only the path being measured.
    assert {c[0] for c in lookup.calls} == {"192.168.1.1"}


def test_probe_observer_mixed_pool_names_every_owner():
    lookup = fake_dig({
        ("127.0.0.1", "whoami.akamai.net", "A"): ["172.71.169.57"],
        ("127.0.0.1", "o-o.myaddr.l.google.com", "TXT"): ['"192.178.115.93"'],
    })
    r = ri.probe("observer", "127.0.0.1", owners(), lookup=lookup)
    assert r.asns == {13335, 15169}
    assert ri.owner_label(r) == (
        "AS13335 CLOUDFLARENET - Cloudflare, Inc., US + AS15169 GOOGLE - Google LLC, US"
    )


def test_probe_no_answer():
    lookup = fake_dig({})
    r = ri.probe("router", "192.168.1.1", owners(), lookup=lookup)
    assert not r.ok and ri.owner_label(r) == "unknown"
    point = ri.build_point(r, None, 1_800_000_000).to_line_protocol()
    assert "ok=0i" in point and "previous" not in point


def _r(path, *owners):
    return ri.Resolver(path=path, via="x", egress=["192.0.2.1"],
                       asn=owners[0][0], org=owners[0][1], owners=list(owners))


G = (15169, "GOOGLE - Google LLC, US")
C = (13335, "CLOUDFLARENET - Cloudflare, Inc., US")
Q = (19281, "QUAD9-AS-1, US")


def test_tracker_a_varying_pool_is_not_a_change():
    t = ri.ChangeTracker({})
    for r in (_r("observer", C, G), _r("observer", C), _r("observer", G), _r("observer", C, G)):
        assert t.previous_for(r) is None
        t.written(r, False)


def test_tracker_marks_a_change_of_hands_once():
    t = ri.ChangeTracker({"router": "AS15169 GOOGLE - Google LLC, US"})
    r = _r("router", Q)
    assert t.previous_for(r) == "AS15169 GOOGLE - Google LLC, US"
    t.written(r, True)
    assert t.previous_for(_r("router", Q)) is None


def test_tracker_ignores_a_failed_probe_and_restores_across_restart():
    t = ri.ChangeTracker({"router": "AS15169 GOOGLE - Google LLC, US"})
    lost = ri.Resolver(path="router", via="x")
    assert t.previous_for(lost) is None
    t.written(lost, False)
    assert t.previous_for(_r("router", G)) is None


def test_parse_label_round_trip():
    r = _r("observer", C, G)
    assert ri.parse_label(ri.owner_label(r)) == dict(r.owners)


def test_point_fields():
    r = ri.Resolver(path="router", via="192.168.1.1", egress=["172.253.240.119"],
                    ecs="192.0.2.0/24", asn=15169, org=G[1], owners=[G])
    line = ri.build_point(r, "AS19281 QUAD9-AS-1, US", 1_800_000_000).to_line_protocol()
    assert line.startswith("dns_resolver,path=router ")
    for part in ('asn=15169i', 'ok=1i', 'ecs="192.0.2.0/24"', 'previous="AS19281 QUAD9-AS-1, US"'):
        assert part in line


ROUTE = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
wlan0\t00000000\t0101A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0
"""


def test_default_gateway(tmp_path):
    route = tmp_path / "route"
    route.write_text(ROUTE)
    assert ri.default_gateway(route) == "192.168.1.1"
    assert ri.default_gateway(tmp_path / "missing") is None


@pytest.mark.parametrize(
    "env,expected",
    [
        ({"RESOLVER_VIA": "off", "RESOLVER_OBSERVER": "off"}, []),
        ({"RESOLVER_VIA": "192.0.2.53", "RESOLVER_OBSERVER": "off"}, [("router", "192.0.2.53", 53)]),
        ({"RESOLVER_VIA": "off", "DNS_PORT": "5354"}, [("observer", "127.0.0.1", 5354)]),
    ],
)
def test_paths(env, expected):
    assert ri.paths(env) == expected
