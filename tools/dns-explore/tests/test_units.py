import pytest

from dns_explore import units
from dns_explore.logread import parse_line


@pytest.mark.parametrize(
    ("name", "service"),
    [
        ("nrdp.logs.netflix.com", "netflix.com"),
        ("www.bbc.co.uk", "bbc.co.uk"),
        ("netflix.com", "netflix.com"),
    ],
)
def test_service_is_etld_plus_one(name, service):
    assert units.service_of(name) == service


def test_cdn_is_the_end_of_the_chain_with_icann_suffixes_only(line):
    q = parse_line(line("www.shop.example", cnames=("shop.edgekey.net", "e1.a.akamaiedge.net")))
    assert units.cdn_of(q) == "akamaiedge.net"
    # cdn.cloudflare.net is a private PSL suffix: still folds into cloudflare.net.
    q = parse_line(line("x.example", cnames=("x.example.com.cdn.cloudflare.net",)))
    assert units.cdn_of(q) == "cloudflare.net"
    q = parse_line(line("api.service.example"))
    assert units.cdn_of(q) == "service.example"


@pytest.mark.parametrize(
    ("name", "random"),
    [
        ("4b574871442a5359d494-pod-chxtvlcbjzeuhkcxoddpm6l4ka-1340.us12.cursorvm.com", True),
        ("d3p8zr0ffa9t17.cloudfront.net", True),
        ("www.google.com", False),
        ("mmx-ds.cdn.whatsapp.net", False),
    ],
)
def test_looks_random(name, random):
    assert units.looks_random(name) is random


@pytest.mark.parametrize(
    ("name", "own"),
    [
        ("api.telegram.org", True),
        ("ghcr.io", True),
        ("abc.canary.smoking-pi.home.arpa", True),
        ("example.com", True),
        ("whoami.akamai.net", True),
        ("o-o.myaddr.l.google.com", True),
        # The same providers' CDNs are the house's traffic, not the Pi's.
        ("a1.w10.akamai.net", False),
        ("www.google.com", False),
        ("netflix.com", False),
    ],
)
def test_default_exclusions(line, name, own):
    assert units.excluded(parse_line(line(name)), units.DEFAULT_EXCLUDE) is own


class FakeAsn(units.AsnLookup):
    def __init__(self, table):
        super().__init__()
        self.table = table

    def owner(self, addr):
        return self.table[addr]


def test_asn_levels_skip_answers_without_address(line):
    asn = FakeAsn({"192.0.2.1": units.Owner("AS64500", "EXAMPLE-NET")})
    with_addr = parse_line(line("a.example.org"))
    without = parse_line(line("a.example.org", qtype="HTTPS", addrs=()))
    assert units.unit_fn("asn", asn)(with_addr) == "AS64500"
    assert units.unit_fn("org", asn)(with_addr) == "EXAMPLE-NET"
    assert units.unit_fn("asn", asn)(without) is None


def test_asn_level_needs_a_lookup():
    with pytest.raises(ValueError, match="needs an AsnLookup"):
        units.unit_fn("asn")
    with pytest.raises(ValueError, match="unknown level"):
        units.unit_fn("country")


@pytest.mark.parametrize(
    ("addr", "prefix"),
    [("192.0.2.77", "192.0.2.0/24"), ("2001:db8:1:2::5", "2001:db8:1::/48")],
)
def test_one_lookup_per_prefix(addr, prefix):
    assert units.AsnLookup.prefix(addr) == prefix


def test_as_names_keep_their_spaces(monkeypatch):
    look = units.AsnLookup()
    answers = {
        "2.0.192.origin.asn.cymru.com": "64500 | 192.0.2.0/24 | US | arin | 2020-01-01",
        "AS64500.asn.cymru.com": "64500 | US | arin | 2020-01-01 | Example Networks Inc, US",
    }
    monkeypatch.setattr(look, "_txt", answers.get)
    assert look.owner("192.0.2.9") == units.Owner("AS64500", "Example Networks Inc")
