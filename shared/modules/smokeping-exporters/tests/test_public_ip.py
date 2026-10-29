"""Unit tests for public_ip.py -- no dig, network, IPinfo or InfluxDB.

Addresses are from the documentation ranges (RFC 5737, RFC 3849), not the
reference Pi's.
"""

import json
import pathlib
import sys
import urllib.error
from types import SimpleNamespace

import pytest

MODULE_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

import public_ip as pub  # noqa: E402
from resolver_identity import OwnerCache  # noqa: E402

V4 = "198.51.100.23"
NAME = "EXAMPLE-NET - Example ISP, US"
PLACE = {"city": "Exampleville", "region": "Example Region", "country": "ZZ"}


def fake_dig(answers):
    def lookup(server, name, rtype, port=53):
        return answers.get((server, name))
    return lookup


LOOKUP = fake_dig({
    ("216.239.32.10", "o-o.myaddr.l.google.com"): [f'"{V4}"'],
    # No IPv6 route: dig gets no reply.
    ("2001:4860:4802:32::a", "o-o.myaddr.l.google.com"): None,
})


class Table:
    """A loaded routing table stand-in."""

    ready = True
    names = {64500: NAME}

    def lookup(self, ip):
        return 64500 if ip == V4 else None


def owners():
    return OwnerCache(table=Table())


def geo(place=PLACE, calls=None):
    def fetch(ip, token):
        if calls is not None:
            calls.append(ip)
        return dict(place)
    return pub.GeoCache(fetch=fetch)


def test_the_address_comes_from_googles_authoritative_server_by_family():
    assert pub.own_address("ipv4", LOOKUP) == V4
    assert pub.own_address("ipv6", LOOKUP) == ""


def test_an_answer_in_the_wrong_family_is_not_taken():
    lookup = fake_dig({("2001:4860:4802:32::a", "o-o.myaddr.l.google.com"): [f'"{V4}"']})
    assert pub.own_address("ipv6", lookup) == ""


def test_probe_names_the_network_and_the_place():
    p = pub.probe("ipv4", owners(), geo(), LOOKUP)
    assert (p.ip, p.asn, p.owner) == (V4, 64500, "EXAMPLE-NET - Example ISP, US")
    assert (p.city, p.region, p.country) == ("Exampleville", "Example Region", "ZZ")


def test_a_family_without_a_route_is_not_ok_and_asks_nobody():
    calls = []
    p = pub.probe("ipv6", owners(), geo(calls=calls), LOOKUP)
    assert not p.ok and calls == []


def test_ipinfo_is_asked_once_per_address_and_day():
    calls = []
    cache = geo(calls=calls)
    cache.place(V4)
    cache.place(V4)
    assert calls == [V4]


def test_a_failed_place_is_asked_again_not_cached():
    calls = []
    cache = geo(place={}, calls=calls)
    cache.place(V4)
    cache.place(V4)
    assert calls == [V4, V4]


def test_geolocation_off_sends_nothing_to_ipinfo():
    calls = []
    cache = pub.GeoCache(fetch=lambda ip, t: calls.append(ip) or PLACE, enabled=False)
    assert cache.place(V4) == {} and calls == []


@pytest.mark.parametrize("value", ["0", "false", "off", "no", "FALSE", "Off",
                                   " no ", "False\n"])
def test_every_off_spelling_turns_geolocation_off(value):
    # Only "0" used to: PUBLIC_IP_GEO=false kept sending the address.
    assert pub.geo_enabled({"PUBLIC_IP_GEO": value}) is False


@pytest.mark.parametrize("value", ["1", "true", "on", "yes", "TRUE", "", "  "])
def test_on_spellings_and_empty_leave_geolocation_on(value):
    assert pub.geo_enabled({"PUBLIC_IP_GEO": value}) is True


def test_unset_leaves_geolocation_on():
    assert pub.geo_enabled({}) is True


def test_main_builds_the_cache_from_the_setting(monkeypatch):
    seen = {}

    class Cache:
        def __init__(self, enabled=True, token=""):
            seen["enabled"] = enabled

    monkeypatch.setattr(pub, "GeoCache", Cache)
    monkeypatch.setattr(pub, "probe", lambda family, owners, geo: SimpleNamespace())
    monkeypatch.setattr(pub, "routing_table", lambda name: None)
    monkeypatch.setenv("PUBLIC_IP_GEO", "false")
    assert pub.main(["--once"]) == 0
    assert seen["enabled"] is False


class _Resp:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self, *a):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_ipinfo_keeps_only_the_place_not_coordinates_postal_or_hostname():
    body = {**PLACE, "ip": V4, "loc": "0.0,0.0", "postal": "00000", "hostname": "h.example.net",
            "org": "AS64500 X"}
    seen = {}

    def opener(req, timeout):
        seen["url"], seen["auth"] = req.full_url, req.get_header("Authorization")
        return _Resp(body)

    assert pub.ipinfo(V4, token="t0k", opener=opener) == PLACE
    assert seen == {"url": f"https://ipinfo.io/{V4}/json", "auth": "Bearer t0k"}


def test_ipinfo_failure_or_bogon_is_empty():
    def down(req, timeout):
        raise urllib.error.URLError("offline")
    assert pub.ipinfo(V4, opener=down) == {}
    assert pub.ipinfo(V4, opener=lambda req, timeout: _Resp({"bogon": True})) == {}


def test_previous_only_on_a_real_change_never_after_a_restart():
    p = pub.probe("ipv4", owners(), geo(), LOOKUP)
    same = pub.ChangeTracker({"ipv4": f"{V4} AS64500"})
    assert same.previous_for(p) is None
    moved = pub.ChangeTracker({"ipv4": "203.0.113.9 AS64501"})
    assert moved.previous_for(p) == "203.0.113.9 AS64501"
    assert pub.ChangeTracker({}).previous_for(p) is None


def test_the_same_address_with_its_network_not_loaded_yet_is_not_a_change():
    loaded = pub.probe("ipv4", owners(), geo(), LOOKUP)
    cold = pub.probe("ipv4", OwnerCache(table=SimpleNamespace(ready=False)), geo(), LOOKUP)
    assert cold.ok and cold.asn == 0
    assert pub.ChangeTracker({"ipv4": f"{V4} AS64500"}).previous_for(cold) is None
    assert pub.ChangeTracker({"ipv4": f"{V4} AS0"}).previous_for(loaded) is None
    assert pub.ChangeTracker({"ipv4": "203.0.113.9 AS0"}).previous_for(loaded) == "203.0.113.9 AS0"


def test_the_point_carries_what_the_overview_reads():
    p = pub.probe("ipv4", owners(), geo(), LOOKUP)
    line = pub.build_point(p, "203.0.113.9 AS64501", 1_790_000_000).to_line_protocol()
    assert line.startswith("public_ip,family=ipv4 ")
    for needle in (f'ip="{V4}"', "asn=64500i", 'city="Exampleville"', 'country="ZZ"',
                   'previous="203.0.113.9 AS64501"'):
        assert needle in line
