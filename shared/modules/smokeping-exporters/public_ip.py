#!/usr/bin/env python3
"""The house's public address: which network it leaves from, and roughly where.

For the Grafana landing dashboard ("Smoking Pi -- Overview"): the address the
Internet sees, the network (AS) that announces it, and an approximate place.

* **Address.** ``o-o.myaddr.l.google.com`` TXT, asked of Google's
  authoritative server directly (not through any resolver), answers with the
  address the question came from: this host's public address. Once over IPv4
  and once over IPv6; a family with no route here is simply absent.
* **Network.** Team Cymru's origin lookup, as resolver_identity.py does for
  resolvers (the same helpers, the same cache).
* **Place.** IPinfo (``https://ipinfo.io/<ip>/json``): city, region and
  country. This is geolocation *by address*, often only
  the ISP's point of presence, never the house. Asked only when the address
  changes or once a day; ``PUBLIC_IP_GEO`` set to ``0``, ``false``, ``off``
  or ``no`` turns it off (nothing leaves for ipinfo.io then) and ``IPINFO_TOKEN`` is used when set. The coordinates,
  postal code and reverse hostname IPinfo also returns are not kept.

Writes measurement ``public_ip`` (tag ``family``: ``ipv4``/``ipv6``) every
PUBLIC_IP_INTERVAL seconds (900). On the point where the address or its
network changed it also writes ``previous``, like resolver_identity.py.

Pro edition only (requires InfluxDB). Uses ``dig`` (in the SmokePing image)
and the standard library for HTTPS.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from resolver_identity import OwnerCache, _is_ip, _unquote, dig

log = logging.getLogger("public_ip")

REQUIRED_ENV = ("INFLUX_URL", "INFLUX_TOKEN", "INFLUX_ORG", "INFLUX_BUCKET")
DEFAULT_INTERVAL = 900
GEO_TTL = 86400  # seconds a place is kept for an unchanged address
# ns1.google.com, by address: asking for its name would go through a resolver
# and could come back in the other family.
GOOGLE_NS = {"ipv4": "216.239.32.10", "ipv6": "2001:4860:4802:32::a"}
MYADDR = "o-o.myaddr.l.google.com"
IPINFO_URL = "https://ipinfo.io/{ip}/json"
GEO_FIELDS = ("city", "region", "country")


@dataclass
class Public:
    family: str
    ip: str = ""
    asn: int = 0
    owner: str = ""
    city: str = ""
    region: str = ""
    country: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.ip)

    def key(self) -> str:
        """What a change is: the address or its network."""
        return f"{self.ip} AS{self.asn}" if self.ok else ""


def own_address(family: str, lookup=dig) -> str:
    """This host's public address in one family, or '' when there is none."""
    lines = lookup(GOOGLE_NS[family], MYADDR, "TXT")
    for line in lines or []:
        value = _unquote(line)
        if _is_ip(value) and (":" in value) == (family == "ipv6"):
            return value
    return ""


def ipinfo(ip: str, token: str = "", opener=urllib.request.urlopen) -> dict[str, str]:
    """City, region and country for an address; {} on any failure."""
    headers = {"Accept": "application/json", "User-Agent": "smoking-pi"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with opener(urllib.request.Request(IPINFO_URL.format(ip=ip), headers=headers),
                    timeout=10) as resp:
            body = json.load(resp)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        log.warning("ipinfo lookup failed: %s", type(exc).__name__)
        return {}
    if not isinstance(body, dict) or body.get("bogon"):
        return {}
    return {k: str(body.get(k) or "") for k in GEO_FIELDS}


class GeoCache:
    """One IPinfo question per address and day, at most."""

    def __init__(self, fetch=ipinfo, clock=time.monotonic, enabled: bool = True,
                 token: str = "") -> None:
        self._fetch = fetch
        self._clock = clock
        self._enabled = enabled
        self._token = token
        self._hits: dict[str, tuple[float, dict[str, str]]] = {}

    def place(self, ip: str) -> dict[str, str]:
        if not self._enabled or not ip:
            return {}
        hit = self._hits.get(ip)
        if hit and self._clock() - hit[0] < GEO_TTL:
            return hit[1]
        value = self._fetch(ip, self._token)
        if value:  # a failure is asked again next cycle, not cached
            self._hits[ip] = (self._clock(), value)
        return value


def probe(family: str, owners: OwnerCache, geo: GeoCache, lookup=dig) -> Public:
    p = Public(family=family, ip=own_address(family, lookup))
    if not p.ok:
        return p
    p.asn = owners.asn(p.ip)
    p.owner = owners.name(p.asn)
    for k, v in geo.place(p.ip).items():
        setattr(p, k, v)
    return p


def build_point(p: Public, previous: str | None, ts: int) -> Point:
    pt = (Point("public_ip")
          .tag("family", p.family)
          .field("ip", p.ip)
          .field("asn", int(p.asn))
          .field("owner", p.owner)
          .field("city", p.city)
          .field("region", p.region)
          .field("country", p.country))
    if previous is not None:
        pt.field("previous", previous)
    return pt.time(ts, WritePrecision.S)


class ChangeTracker:
    """The last key per family; ``previous`` is set only on a real change,
    never on the first point after a start (the last one is read back)."""

    def __init__(self, last: dict[str, str]) -> None:
        self._last = dict(last)

    def previous_for(self, p: Public) -> str | None:
        before = self._last.get(p.family)
        return before if before and p.ok and before != p.key() else None

    def written(self, p: Public) -> None:
        if p.ok:
            self._last[p.family] = p.key()


def last_keys(query_api, bucket: str) -> dict[str, str]:
    """The last address and network per family, from InfluxDB."""
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "public_ip" and (r._field == "ip" or r._field == "asn"))
  |> last()
  |> pivot(rowKey: ["family"], columnKey: ["_field"], valueColumn: "_value")
'''
    out: dict[str, str] = {}
    try:
        for table in query_api.query(flux):
            for rec in table.records:
                ip, asn = rec.values.get("ip"), rec.values.get("asn")
                if ip:
                    out[rec.values.get("family")] = f"{ip} AS{int(asn or 0)}"
    except Exception as exc:
        log.warning("could not read the last public address: %s", type(exc).__name__)
    return out


def geo_enabled(env=os.environ) -> bool:
    """PUBLIC_IP_GEO: on unless it says off, in any of the usual spellings.

    Only ``0`` used to turn it off, so ``PUBLIC_IP_GEO=false`` kept sending the
    address to ipinfo.io. Empty or unset is the default, on; anything else not
    in the off list is on, as NETMETER reads its own switch.
    """
    return (env.get("PUBLIC_IP_GEO") or "").strip().lower() not in ("0", "false", "off", "no")


def _env_int(name: str, default: int) -> int:
    try:
        return max(60, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    geo = GeoCache(enabled=geo_enabled(),
                   token=os.environ.get("IPINFO_TOKEN", ""))
    owners = OwnerCache()
    if "--once" in argv:
        # One cycle printed, nothing written: to check a host by hand.
        for family in GOOGLE_NS:
            print(json.dumps(probe(family, owners, geo).__dict__, sort_keys=True))
        return 0
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        log.error("Missing required environment variables: %s", ", ".join(missing))
        return 1
    interval = _env_int("PUBLIC_IP_INTERVAL", DEFAULT_INTERVAL)
    bucket = os.environ["INFLUX_BUCKET"]
    client = InfluxDBClient(url=os.environ["INFLUX_URL"], token=os.environ["INFLUX_TOKEN"],
                            org=os.environ["INFLUX_ORG"], timeout=10_000)
    write_api = client.write_api(write_options=SYNCHRONOUS)
    tracker = ChangeTracker(last_keys(client.query_api(), bucket))
    log.info("public address collector started (every %ds, geolocation %s)",
             interval, "on" if geo._enabled else "off")
    while True:
        started = time.time()
        for family in GOOGLE_NS:
            try:
                p = probe(family, owners, geo)
                if not p.ok:
                    # No route in this family (most houses have no IPv6):
                    # no point, or the dashboard would show a failure.
                    continue
                previous = tracker.previous_for(p)
                write_api.write(bucket=bucket, record=build_point(p, previous, int(started)))
                tracker.written(p)
                if previous is not None:
                    log.info("public %s changed: %s -> %s", family, previous, p.key())
            except Exception as exc:
                # The type only: an InfluxDB error's message can carry the token.
                log.error("public %s failed this cycle: %s", family, type(exc).__name__)
        time.sleep(max(1.0, interval - (time.time() - started)))


if __name__ == "__main__":
    sys.exit(main())
