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


# --- the routing table (originet over RouteViews prefix-to-AS, PeeringDB IXPs) ---

import gzip
import json

import pytest

from common import asn as table_mod

SNAPSHOT = [
    "10.0.0.0\t8\t100",
    "10.1.0.0\t16\t200",        # inside 10/8: wins for its own addresses
    "10.1.2.0\t24\t300",        # inside 10.1/16
    "11.0.0.0\t24\t400_401",    # multi-origin: originet reports the first
]


class FakeTable:
    """What the code uses of an originet table."""

    def __init__(self, routes=None, prefixes=1_200_000, file="routeviews-rv2-20261021-0800.pfx2as.gz"):
        self.routes = routes or {"10.1.2.3": 300, "8.8.8.8": 15169}
        self.len_v4 = prefixes
        self.snapshots = [{"family": "ipv4", "file": file}]

    def lookup(self, ip):
        asn = self.routes.get(ip)
        return type("M", (), {"asn": asn})() if asn else None


class Loader:
    def __init__(self, online=True, cached=True, table=None):
        self.online, self.cached, self.table, self.calls = online, cached, table, []

    def __call__(self, cache_dir, offline, ipv6=False):
        self.calls.append(offline)
        if offline and not self.cached or not offline and not self.online:
            raise OSError("no snapshot")
        return self.table or FakeTable()


IXS = "\n".join([
    "# CAIDA IXP dataset",
    json.dumps({"ix_id": 1, "name": "Wide IX", "city": "Dallas",
                "prefixes": {"ipv4": ["198.32.0.0/16"], "ipv6": ["2001:db8:1::/64"]}}),
    json.dumps({"ix_id": 2, "name": "Inner IX", "prefixes": {"ipv4": ["198.32.10.0/24"]}}),
    json.dumps({"ix_id": 3, "name": "", "prefixes": {"ipv4": ["192.0.2.0/24"]}}),
])
MEMBERS = "\n".join([
    json.dumps({"asn": 64500, "ipv4": ["198.32.10.5"], "ipv6": ["2001:db8:1::5"], "ix_id": 2}),
    json.dumps({"asn": "x", "ipv4": ["198.32.10.6"], "ix_id": 2}),
    json.dumps({"asn": 64501, "ipv4": ["198.32.10.7"], "ix_id": 2}),     # listed twice:
    json.dumps({"asn": 64502, "ipv4": ["198.32.10.7"], "ix_id": 2}),     # names neither
])
AS2ORG = "\n".join(json.dumps(o) for o in [
    {"type": "Organization", "organizationId": "EX-1", "name": "Example, Inc.", "country": "US"},
    {"type": "ASN", "asn": "300", "name": "EXAMPLE-NET", "organizationId": "EX-1"},
    {"type": "ASN", "asn": "64500", "name": "MEMBER", "organizationId": "NONE"},
])


class _Net:
    """CAIDA's IXP dataset and AS2Org, faked; ``months`` lists the IXP months
    published (YYYYMM)."""

    def __init__(self, fail=False, months=("202610",)):
        self.fail, self.months, self.asked = fail, months, []

    def __call__(self, url):
        self.asked.append(url)
        if self.fail:
            raise OSError("offline")
        if url.endswith("/as-organizations/"):
            return b'<a href="20260901.as-org2info.jsonl.gz">x</a> 20261001.as-org2info.jsonl.gz'
        if url.endswith(".as-org2info.jsonl.gz"):
            return gzip.compress(AS2ORG.encode())
        for month in self.months:
            if url.endswith(f"ixs_{month}.jsonl"):
                return IXS.encode()
            if url.endswith(f"ix-asns_{month}.jsonl"):
                return MEMBERS.encode()
        raise OSError("404")


@pytest.fixture(autouse=True)
def _few_names(monkeypatch):
    monkeypatch.setattr(table_mod, "NAMES_MIN", 1)


def test_originet_answers_by_the_longest_prefix(tmp_path):
    originet = pytest.importorskip("originet")
    snap = tmp_path / "routeviews-rv2-20261021-0800.pfx2as.gz"
    snap.write_bytes(gzip.compress("\n".join(SNAPSHOT).encode() + b"\n"))
    table = originet.Table.from_files(ipv4=str(snap))
    asns = table.lookup_asns(["10.0.0.1", "10.1.0.1", "10.1.2.3", "11.0.0.9", "12.0.0.1"])
    assert asns == [100, 200, 300, 400, None]


def test_refresh_loads_once_a_day_and_keeps_the_table_offline(tmp_path):
    clock = [1_792_700_000.0]  # October 2026: an IXP month is published
    loader = Loader()
    t = table_mod.RoutingTable(tmp_path, fetch=_Net(), clock=lambda: clock[0], loader=loader)
    assert t.refresh() and t.lookup("10.1.2.3") == 300 and t.ixp("198.32.10.5") == "Inner IX"
    assert t.names[300] == "EXAMPLE-NET - Example, Inc., US" and t.names[64500] == "MEMBER"
    assert t.snapshot.endswith("20261021-0800.pfx2as.gz")
    assert t.refresh() and loader.calls == [False]                 # not again within the day
    clock[0] += table_mod.TABLE_REFRESH
    loader.online = False
    assert t.refresh() and t.lookup("10.1.2.3") == 300             # kept, network down
    assert loader.calls == [False, False]


def test_a_restart_without_network_uses_the_cached_snapshot(tmp_path):
    loader = Loader(online=False)
    t = table_mod.RoutingTable(tmp_path, fetch=_Net(fail=True), clock=lambda: 0, loader=loader)
    assert t.refresh() and loader.calls == [False, True]


def test_nothing_anywhere_means_cymru(tmp_path):
    t = table_mod.RoutingTable(tmp_path, fetch=_Net(fail=True), clock=lambda: 0,
                               loader=Loader(online=False, cached=False))
    assert not t.refresh() and t.lookup("10.1.2.3") is None


def test_a_snapshot_too_small_is_refused(tmp_path):
    t = table_mod.RoutingTable(tmp_path, fetch=_Net(), clock=lambda: 0,
                               loader=Loader(table=FakeTable(prefixes=10)))
    assert not t.refresh()


def test_a_loader_that_breaks_never_stops_the_caller(tmp_path):
    def broken(cache_dir, offline):
        raise RuntimeError("panic in the extension")
    t = table_mod.RoutingTable(tmp_path, fetch=_Net(fail=True), clock=lambda: 0, loader=broken)
    assert t.refresh() is False


def test_the_hand_rolled_tables_files_are_removed(tmp_path):
    for name in ("pfx2as.gz", "pfx2as.name", "pfx2as.ranges"):
        (tmp_path / name).write_bytes(b"x")
    table_mod.RoutingTable(tmp_path, fetch=_Net(), clock=lambda: 0, loader=Loader()).refresh()
    assert not list(tmp_path.glob("pfx2as.*"))


def test_old_snapshots_are_pruned(tmp_path):
    for day in ("20261019-1200", "20261020-1000", "20261021-0800"):
        d = tmp_path / "routeviews-prefix2as" / "2026" / "10"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"routeviews-rv2-{day}.pfx2as.gz").write_bytes(b"x")
    (tmp_path / "routeviews-prefix2as" / "pfx2as-creation.log").write_text("index")
    assert table_mod.prune_snapshots(tmp_path, keep=2) == 1
    left = sorted(p.name for p in tmp_path.rglob("*.pfx2as.gz"))
    assert left == ["routeviews-rv2-20261020-1000.pfx2as.gz", "routeviews-rv2-20261021-0800.pfx2as.gz"]
    assert (tmp_path / "routeviews-prefix2as" / "pfx2as-creation.log").exists()


def test_the_ixp_dataset_names_exchanges_cities_and_members(tmp_path):
    data = table_mod.ixp_data(IXS.splitlines(), MEMBERS.splitlines())
    t = table_mod.RoutingTable.__new__(table_mod.RoutingTable)
    t._ixp, t._members = data["ranges"], data["members"]
    assert t.ixp("198.32.200.1") == "Wide IX (Dallas)" and t.ixp("198.32.10.5") == "Inner IX"
    assert t.ixp("198.32.11.0") == "Wide IX (Dallas)" and t.ixp("198.33.0.1") is None
    assert t.ixp("192.0.2.1") is None and t.ixp("2001:db8:1::5") is None   # v4 only here
    assert t.ixp_member("198.32.10.5") == 64500 and t.ixp_member("198.32.10.6") is None
    v6 = table_mod.ixp_data(IXS.splitlines(), MEMBERS.splitlines(), ipv6=True)
    assert "2001:db8:1::5" in v6["members"]


def test_an_address_on_an_exchange_lan_is_its_members(tmp_path):
    t = table_mod.RoutingTable(tmp_path, fetch=_Net(), clock=lambda: 1_792_700_000,
                               loader=Loader())
    assert t.refresh() and t.lookup("198.32.10.5") == 64500


def test_the_ixp_dataset_of_last_month_when_this_one_is_not_out(tmp_path):
    net = _Net(months=("202609",))
    t = table_mod.RoutingTable(tmp_path, fetch=net, clock=lambda: 1_792_700_000,
                               loader=Loader())
    t.refresh()
    assert t.ixp("198.32.10.5") == "Inner IX" and (tmp_path / "ixp.source").read_text() == "202609"


def test_as2org_names_take_the_handle_and_the_organization():
    names = table_mod.as2org_names(AS2ORG.splitlines() + ["junk"])
    assert names == {300: "EXAMPLE-NET - Example, Inc., US", 64500: "MEMBER"}


def test_origin_uses_the_table_then_cymru(monkeypatch, tmp_path):
    asked = []

    def is_origin_query(q):
        # The label, not the host as text (CodeQL reads a host substring as
        # an incomplete URL check, even in a fake).
        return q.split(".")[-4] == "origin"

    monkeypatch.setattr(table_mod, "_txt", lambda q, r=None: asked.append(q) or (
        "64500 | 64.0.0.0/8 | US | arin | 2000-01-01" if is_origin_query(q)
        else "300 | US | arin | 2000-01-01 | EXAMPLE-NET - Example, US"))
    t = table_mod.RoutingTable(tmp_path, fetch=_Net(), clock=lambda: 0, loader=Loader())
    t.refresh()
    monkeypatch.setattr(table_mod, "_TABLE", t)
    monkeypatch.setattr(table_mod, "_NAMES", {})
    assert table_mod.origin("10.1.2.3") == (300, "EXAMPLE-NET - Example, Inc., US")  # AS2Org
    assert not asked                                                            # nothing asked
    assert table_mod.origin("64.1.1.1") is None                               # no route: none
    monkeypatch.setattr(table_mod, "_TABLE", None)
    assert table_mod.origin("10.1.2.3")[0] == 64500                            # no table


def test_a_cached_table_loaded_offline_is_retried_online_next_time(tmp_path):
    clock = [0.0]
    loader = Loader(online=False)
    t = table_mod.RoutingTable(tmp_path, fetch=_Net(fail=True), clock=lambda: clock[0],
                               loader=loader)
    assert t.refresh() and loader.calls == [False, True]
    clock[0] += 3600
    loader.online = True
    assert t.refresh() and loader.calls == [False, True, False]     # not a day later
    clock[0] += 3600
    assert t.refresh() and loader.calls == [False, True, False]     # now a day's wait


def test_a_members_port_on_an_announced_exchange_lan_is_the_members():
    class Announced(FakeTable):
        def lookup(self, ip):
            return type("M", (), {"asn": 18734})() if ip.startswith("198.32.") else None

    t = table_mod.RoutingTable.__new__(table_mod.RoutingTable)
    t._table, t._members = Announced(), {"198.32.10.5": 32098}
    assert t.lookup("198.32.10.5") == 32098 and t.lookup("198.32.10.9") == 18734


def test_an_address_two_ases_claim_names_neither():
    data = table_mod.ixp_data(IXS.splitlines(), MEMBERS.splitlines())
    assert "198.32.10.7" not in data["members"] and data["members"]["198.32.10.5"] == 64500


def test_a_month_whose_member_file_is_missing_falls_back_to_the_month_before(tmp_path):
    class HalfPublished(_Net):
        def __call__(self, url):
            if url.endswith("ix-asns_202610.jsonl"):
                self.asked.append(url)
                raise OSError("404")
            return super().__call__(url)

    net = HalfPublished(months=("202610", "202609"))
    t = table_mod.RoutingTable(tmp_path, fetch=net, clock=lambda: 1_792_700_000, loader=Loader())
    t.refresh()
    assert (tmp_path / "ixp.source").read_text() == "202609" and t.ixp("198.32.10.5")


def test_a_failed_download_is_retried_an_hour_later_not_every_call(tmp_path):
    clock = [1_792_700_000.0]
    net = _Net(fail=True)
    t = table_mod.RoutingTable(tmp_path, fetch=net, clock=lambda: clock[0], loader=Loader())
    t.refresh()
    first = len(net.asked)
    t.refresh()
    assert len(net.asked) == first                                   # no new attempt
    clock[0] += 3600
    t.refresh()
    assert len(net.asked) > first
