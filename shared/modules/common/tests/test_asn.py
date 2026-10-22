"""classify(): KNOWN, then the Linnaeus-derived roles, then the AS name."""

from common import asn


def test_known_wins_over_the_roles_file():
    # Linnaeus labels Netflix's AS an entertainment enterprise and Akamai a
    # cloud and a CDN; KNOWN says what answers a measurement from them.
    assert asn.classify(2906, "AS-SSI", "1.2.3.4", []) == ("Netflix", asn.CACHE)
    assert asn.classify(20940, "AKAMAI-ASN1", "x", []) == ("Akamai", asn.CDN)


def test_the_roles_file_names_the_rest():
    assert asn.classify(3449, "AS3449 - Universidad Nacional de Buenos Aires, AR", "x",
                        []) == ("University of Buenos Aires", asn.ACADEMIC)
    assert asn.classify(7922, "COMCAST-7922", "x", [])[1] == asn.ACCESS
    assert asn.classify(3356, "LEVEL3", "x", [])[1] == asn.TRANSIT


def test_an_as_in_neither_is_its_own_operator():
    assert asn.classify(64500, "EXAMPLE-NET - Example Hosting, US", "x",
                        []) == ("EXAMPLE-NET", asn.OTHER)
    assert asn.classify(64501, "AS64501 - Universidad de Ejemplo, AR", "x",
                        []) == ("Universidad de Ejemplo", asn.ACADEMIC)
    assert asn.classify(None, "", "x", []) == (asn.UNKNOWN, asn.UNKNOWN)
    assert asn.classify(0, "", "x", []) == (asn.UNKNOWN, asn.UNKNOWN)


def test_amazon_is_decided_by_the_cname_chain_not_the_table():
    assert asn.classify(16509, "AMAZON-02", "d1.cloudfront.net", []) == (
        "Amazon CloudFront", asn.CDN)
    assert asn.classify(16509, "AMAZON-02", "api.example.com", []) == ("AWS", asn.CLOUD)


def test_the_roles_file_is_well_formed():
    table = asn.roles()
    assert len(table) > 500
    assert {kind for _, kind in table.values()} <= set(asn.KINDS)


def test_a_missing_or_broken_roles_file_is_an_empty_table(tmp_path):
    assert asn.roles(tmp_path / "absent.csv") == {}
    bad = tmp_path / "roles.csv"
    bad.write_text("# header\nasn,operator,kind\nnot-a-number,X,cdn\n64500,Y,made_up\n"
                   "64501,,transit\n")
    assert asn.roles(bad) == {64501: ("AS64501", asn.TRANSIT)}


# --- the routing table (RouteViews prefix-to-AS, PeeringDB IXPs) -------------

import gzip
import json

import pytest

from common import asn as table_mod

SNAPSHOT = [
    "10.0.0.0\t8\t100",
    "10.1.0.0\t16\t200",        # inside 10/8: wins for its own addresses
    "10.1.2.0\t24\t300",        # inside 10.1/16
    "10.2.0.0\t16\t100",        # same AS as its parent: one range
    "11.0.0.0\t24\t400_401",    # multi-origin: the last, as map-ip-to-asn takes it
    "12.0.0.0\t24\t500,501",    # AS set: the last
    "2001:db8::\t32\t600",      # IPv6: skipped
    "junk line",
]


def _table(lines=SNAPSHOT):
    t = table_mod.RoutingTable.__new__(table_mod.RoutingTable)
    t._ranges, t._ixp = table_mod.flatten(lines), []
    return t


@pytest.mark.parametrize("ip,want", [
    ("10.0.0.1", 100), ("10.1.0.1", 200), ("10.1.2.3", 300), ("10.1.3.0", 200),
    ("10.2.5.5", 100), ("10.255.255.255", 100), ("11.0.0.9", 401), ("12.0.0.1", 501),
    ("9.255.255.255", None), ("13.0.0.0", None),
])
def test_longest_prefix_wins(ip, want):
    assert _table().lookup(ip) == want


def test_ixp_ranges_name_the_exchange():
    ranges = table_mod.ixp_ranges(
        [{"prefix": "206.223.115.0/24", "ixlan_id": 1}, {"prefix": "2001:db8::/64", "ixlan_id": 1},
         {"prefix": "bad", "ixlan_id": 1}, {"prefix": "198.32.0.0/24", "ixlan_id": 9}],
        [{"id": 1, "ix_id": 7}], [{"id": 7, "name": "Example IX"}])
    assert ranges == [[3470750464, 3470750719, "Example IX"]]
    t = _table()
    t._ixp = ranges
    assert t.ixp("206.223.115.40") == "Example IX" and t.ixp("206.223.116.1") is None


class _Net:
    def __init__(self, lines=SNAPSHOT * 1, fail=False):
        self.lines, self.fail, self.asked = lines, fail, []

    def __call__(self, url):
        self.asked.append(url)
        if self.fail:
            raise OSError("offline")
        if url.endswith("pfx2as-creation.log"):
            return b"1\t1\t2026/10/routeviews-rv2-20261021-0800.pfx2as.gz\n"
        if url.endswith(".pfx2as.gz"):
            return gzip.compress("\n".join(self.lines).encode())
        what = url.split("/api/")[1].split("?")[0]
        data = {"ixpfx": [{"prefix": "206.223.115.0/24", "ixlan_id": 1}],
                "ixlan": [{"id": 1, "ix_id": 7}], "ix": [{"id": 7, "name": "Example IX"}]}
        return json.dumps({"data": data[what]}).encode()


def test_refresh_downloads_loads_and_keeps_the_last_good(tmp_path, monkeypatch):
    monkeypatch.setattr(table_mod, "TABLE_MIN_PREFIXES", 3)
    clock = [1_000_000.0]
    t = table_mod.RoutingTable(tmp_path, fetch=_Net(), clock=lambda: clock[0])
    assert t.refresh() and t.lookup("10.1.2.3") == 300 and t.ixp("206.223.115.1") == "Example IX"
    assert t.snapshot.endswith("20261021-0800.pfx2as.gz")
    # A day later the network is down: the files there stay, loaded.
    clock[0] += 2 * table_mod.TABLE_REFRESH
    t2 = table_mod.RoutingTable(tmp_path, fetch=_Net(fail=True), clock=lambda: clock[0])
    assert t2.refresh() and t2.lookup("10.1.2.3") == 300


def test_a_snapshot_too_small_is_refused(tmp_path):
    t = table_mod.RoutingTable(tmp_path, fetch=_Net(lines=SNAPSHOT[:2]), clock=lambda: 0)
    assert not t.refresh() and not (tmp_path / "pfx2as.gz").exists()


def test_an_unexpected_snapshot_name_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(table_mod, "TABLE_MIN_PREFIXES", 3)
    net = _Net()
    real = net.__call__

    def evil(url):
        if url.endswith("pfx2as-creation.log"):
            return b"1\t1\t../../etc/passwd\n"
        return real(url)
    t = table_mod.RoutingTable(tmp_path, fetch=evil, clock=lambda: 0)
    assert not t.refresh()


def test_origin_uses_the_table_then_cymru(monkeypatch):
    asked = []
    monkeypatch.setattr(table_mod, "_txt", lambda q, r=None: asked.append(q) or (
        "64500 | 64.0.0.0/8 | US | arin | 2000-01-01" if q.endswith("origin.asn.cymru.com")
        else "300 | US | arin | 2000-01-01 | EXAMPLE-NET - Example, US"))
    monkeypatch.setattr(table_mod, "_TABLE", _table())
    monkeypatch.setattr(table_mod, "_NAMES", {})
    assert table_mod.origin("10.1.2.3") == (300, "EXAMPLE-NET - Example, US")
    assert not [q for q in asked if q.endswith("origin.asn.cymru.com")]       # local
    assert table_mod.origin("64.1.1.1")[0] == 64500                            # not in it
    monkeypatch.setattr(table_mod, "_TABLE", None)
    assert table_mod.origin("10.1.2.3")[0] == 64500                            # no table


def test_a_restart_reads_the_built_ranges_instead_of_flattening_again(tmp_path, monkeypatch):
    monkeypatch.setattr(table_mod, "TABLE_MIN_PREFIXES", 3)
    t = table_mod.RoutingTable(tmp_path, fetch=_Net(), clock=lambda: 0)
    assert t.refresh() and (tmp_path / "pfx2as.ranges").exists()
    monkeypatch.setattr(table_mod, "flatten", lambda lines: pytest.fail("flattened again"))
    t2 = table_mod.RoutingTable(tmp_path, fetch=_Net(fail=True), clock=lambda: 0)
    assert t2.refresh() and t2.lookup("10.1.2.3") == 300 and t2.lookup("13.0.0.0") is None
