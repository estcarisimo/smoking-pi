#!/usr/bin/env python3
"""Which public resolver answers for this house, over time.

The router's DNS server is rarely what does the resolving. Most home routers
forward to someone -- the ISP, Google, Cloudflare, or the Pi's own DNS
observer -- and that resolver's *public* address is what every website's DNS
server sees. CDNs choose the edge they send the house to from it (and from
the client subnet it passes on, EDNS Client Subnet), so a change of resolver
can move every CDN-backed measurement at once.

A few names answer with the address of whoever asked them:

* ``whoami.akamai.net`` (A): the resolver's egress address, as Akamai's
  authoritative servers saw it;
* ``o-o.myaddr.l.google.com`` (TXT): the same from Google's, plus
  ``edns0-client-subnet <prefix>`` when the resolver passed one on.

Big resolvers are anycast pools, so the egress address changes from query to
query (three queries through the reference router gave three Google
addresses). What stays put is who owns them. So each probe resolves the
owner, as an ASN and a name, from the routing table (originet over CAIDA's
RouteViews snapshot, with CAIDA's AS2Org names) looked up locally, and a
*change* is a change of owner, not of address.

Paths:

* ``router``: through the default gateway, i.e. what the house uses today;
* ``observer``: through the Pi's own DNS observer (port 53 on this host),
  when it answers -- which upstream AdGuard Home really reaches.

Writes measurement ``dns_resolver`` (tag ``path``) every RESOLVER_INTERVAL
seconds. On the point where the owner changed it also writes ``previous``,
the owner before, which is what the dashboards' annotation selects. It
follows host_uplink in wifi_link.py.

Pro edition only (requires InfluxDB). Uses ``dig``, which the SmokePing
image carries for its DNS probes.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import os
import socket
import struct
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

log = logging.getLogger("resolver_identity")

PROC_ROUTE = Path("/proc/net/route")
REQUIRED_ENV = ("INFLUX_URL", "INFLUX_TOKEN", "INFLUX_ORG", "INFLUX_BUCKET")

DEFAULT_INTERVAL = 900
# Egress addresses sampled per path and cycle: a pool shows several.
PROBES = 3
LOOKBACK = "-30d"
# The latest result per path, for host_facts.py (the web admin's Connection
# card) to read without querying InfluxDB on every page load.
SNAPSHOT = Path(os.environ.get("RESOLVER_SNAPSHOT", "/tmp/resolver_identity.json"))
DIG_TIMEOUT = 3


@dataclass
class Resolver:
    """What one path showed in one cycle."""

    path: str
    via: str
    egress: list[str] = field(default_factory=list)
    ecs: str = ""
    asn: int = 0  # the owner of most egress addresses
    org: str = ""
    # Every owner seen, (asn, name), most addresses first. A resolver that
    # spreads queries across providers (AdGuard over 1.1.1.1 and 8.8.8.8)
    # shows more than one.
    owners: list[tuple[int, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.egress)

    @property
    def asns(self) -> set[int]:
        return {asn for asn, _ in self.owners}


def default_gateway(route_file: Path = PROC_ROUTE) -> str | None:
    """The IPv4 default gateway, lowest metric first (host network)."""
    best: tuple[int, str] | None = None
    try:
        lines = route_file.read_text().splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        f = line.split()
        if len(f) < 7 or f[1] != "00000000" or not int(f[3], 16) & 0x2:
            continue
        gw = socket.inet_ntoa(struct.pack("<L", int(f[2], 16)))
        if best is None or int(f[6]) < best[0]:
            best = (int(f[6]), gw)
    return best[1] if best else None


def dig(server: str, name: str, rtype: str, port: int = 53) -> list[str] | None:
    """``dig +short`` answers, one per line; None when no reply came back
    (a timeout, or dig itself failing), [] when a reply carried no answer.
    ``+short`` hides the response code, so NXDOMAIN, SERVFAIL and REFUSED
    all read as []: callers must not treat [] as a final answer."""
    cmd = ["dig", "+short", f"+time={DIG_TIMEOUT}", "+tries=1", "-p", str(port),
           f"@{server}", name, rtype]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=DIG_TIMEOUT * 2 + 2, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("dig %s @%s failed: %s", name, server, exc)
        return None
    if out.returncode != 0:
        # 9: no reply from the server. Anything else is dig's own trouble.
        return None
    lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    if any(ln.startswith(";;") for ln in lines):
        return None
    return lines


def _unquote(txt: str) -> str:
    return txt.strip().strip('"')


def parse_google_txt(lines: list[str]) -> tuple[list[str], str]:
    """o-o.myaddr.l.google.com TXT -> (egress addresses, client subnet)."""
    egress, ecs = [], ""
    for line in lines:
        value = _unquote(line)
        if value.startswith("edns0-client-subnet"):
            ecs = value.split(None, 1)[1] if " " in value else ""
            continue
        if _is_ip(value):
            egress.append(value)
    return egress, ecs


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def routing_table(name: str):
    """This process's routing table (``common.asn.use_table``: originet over
    CAIDA's RouteViews snapshot, CAIDA's IXP dataset and AS2Org names), kept
    in ``<SMOKEPING_CONFIG_DIR>/routing/<name>``, IPv6 included: a resolver's
    egress is often IPv6. One directory per process, so two never write the
    same download. None in an image without ``common/``."""
    try:
        from common import asn
    except ImportError:  # pragma: no cover -- an older image under a newer checkout
        return None
    base = Path(os.environ.get("SMOKEPING_CONFIG_DIR", "/config")) / "routing"
    return asn.use_table(base / name, ipv6=True)


class OwnerCache:
    """ASN and name per address, looked up locally in the routing table
    (:func:`routing_table`); 0 and "" until it is loaded or without a route.
    Nothing is asked of anyone else, so there is nothing to cache."""

    def __init__(self, table=None) -> None:
        self._table = table

    def _loaded(self):
        table = self._table
        if table is None:
            try:
                from common import asn
            except ImportError:  # pragma: no cover
                return None
            table = asn._TABLE
        return table if table is not None and table.ready else None

    def asn(self, ip: str) -> int:
        table = self._loaded()
        if table is None:
            return 0
        try:
            return table.lookup(ip) or 0
        except ValueError:
            return 0

    def name(self, asn: int) -> str:
        table = self._loaded()
        if not asn or table is None:
            return ""
        return table.names.get(int(asn), "")


def probe(path: str, server: str, owners: OwnerCache, port: int = 53,
          lookup=dig) -> Resolver:
    """One cycle for one path."""
    r = Resolver(path=path, via=server if port == 53 else f"{server}:{port}")
    seen: list[str] = []
    for _ in range(PROBES):
        lines = lookup(server, "whoami.akamai.net", "A", port)
        seen += [ln for ln in (lines or []) if _is_ip(ln)]
    txt = lookup(server, "o-o.myaddr.l.google.com", "TXT", port)
    if txt:
        google_egress, r.ecs = parse_google_txt(txt)
        seen += google_egress
    r.egress = sorted(set(seen), key=lambda ip: (ipaddress.ip_address(ip).version,
                                                 ipaddress.ip_address(ip)))
    if not r.egress:
        return r
    counts = Counter(owners.asn(ip) for ip in r.egress)
    counts.pop(0, None)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    r.owners = [(asn, owners.name(asn)) for asn, _ in ranked]
    if r.owners:
        r.asn, r.org = r.owners[0]
    return r


def owner_label(r: Resolver) -> str:
    """How the owners are written: 'AS15169 GOOGLE - Google LLC, US', or
    several joined with ' + ' (in ASN order, so the same set always reads
    the same)."""
    if not r.owners:
        return "unknown"
    return " + ".join(f"AS{asn} {name}".strip() for asn, name in sorted(r.owners))


def parse_label(label: str) -> dict[int, str]:
    """An owner label back into {asn: name} (what last_owners reads)."""
    out: dict[int, str] = {}
    for part in (label or "").split(" + "):
        m = re.match(r"\s*AS(\d+)\s*(.*)", part)
        if m:
            out[int(m.group(1))] = m.group(2).strip()
    return out


def build_point(r: Resolver, previous: str | None, ts: int) -> Point:
    pt = (Point("dns_resolver")
          .tag("path", r.path)
          .field("ok", int(r.ok))
          .field("via", r.via)
          .field("egress", ",".join(r.egress))
          .field("ecs", r.ecs)
          .field("asn", int(r.asn))
          .field("owner", owner_label(r) if r.ok else ""))
    if previous is not None:
        pt.field("previous", previous)
    return pt.time(ts, WritePrecision.S)


def snapshot_entry(r: Resolver, ts: int) -> dict:
    return {
        "via": r.via,
        "ok": r.ok,
        "owner": owner_label(r) if r.ok else "",
        "owners": [{"asn": a, "name": n} for a, n in r.owners],
        "egress": r.egress,
        "ecs": r.ecs,
        "updated": ts,
    }


def write_snapshot(entries: dict[str, dict], path: Path = SNAPSHOT) -> None:
    """Atomic, like every other state file the exporters share."""
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(entries, sort_keys=True))
        tmp.replace(path)
    except OSError as exc:
        log.warning("could not write %s: %s", path, exc)


def last_owners(query_api, bucket: str) -> dict[str, str]:
    """The newest owner per path, so a change across a restart is marked."""
    query = (f'from(bucket: "{bucket}") |> range(start: {LOOKBACK}) '
             '|> filter(fn: (r) => r._measurement == "dns_resolver" and r._field == "owner") '
             '|> filter(fn: (r) => r._value != "") |> last()')
    out: dict[str, str] = {}
    try:
        for table in query_api.query(query):
            for record in table.records:
                path = record.values.get("path")
                if path and isinstance(record.get_value(), str):
                    out[path] = record.get_value()
    except Exception as exc:
        log.warning("could not read the last resolver owners (%s); a change across "
                    "this restart will not be marked", exc)
    return out


class ChangeTracker:
    """Marks ``previous`` when a path's resolver changed hands.

    It remembers, per path, every owner seen since the last change. A cycle
    that shares any of them is the same resolver: a pool that spreads
    queries shows a varying mix (Cloudflare and Google one cycle, only
    Cloudflare the next), and marking that would put a false "changed" on
    every other cycle. A cycle that shares none is a change; ``previous``
    is what was remembered, and the memory restarts from the new owners. A
    failed probe (no answer) never counts.

    The price: a resolver that *adds* a provider (the router pointed at the
    Pi's observer, which also uses Google) is not marked. The ``owner``
    field shows it, and the observer's canary is what proves that path.
    """

    def __init__(self, last: dict[str, str]) -> None:
        self.seen: dict[str, dict[int, str]] = {p: parse_label(v) for p, v in last.items()}

    def previous_for(self, r: Resolver) -> str | None:
        before = self.seen.get(r.path)
        if not r.ok or not r.asns or not before or set(before) & r.asns:
            return None
        return " + ".join(f"AS{a} {n}".strip() for a, n in sorted(before.items()))

    def written(self, r: Resolver, changed: bool) -> None:
        if not r.ok or not r.asns:
            return
        if changed or r.path not in self.seen:
            self.seen[r.path] = dict(r.owners)
        else:
            self.seen[r.path].update(r.owners)


def paths(env: dict[str, str] | None = None) -> list[tuple[str, str, int]]:
    """(path, server, port) to probe this cycle."""
    env = dict(os.environ if env is None else env)
    out: list[tuple[str, str, int]] = []
    via = env.get("RESOLVER_VIA", "").strip().lower() or "auto"
    if via != "off":
        server = default_gateway() if via == "auto" else via
        if server:
            out.append(("router", server, 53))
    if env.get("RESOLVER_OBSERVER", "").strip().lower() != "off":
        port = int(env.get("DNS_PORT", "").strip() or 53)
        out.append(("observer", "127.0.0.1", port))
    return out


def _env_int(name: str, default: int) -> int:
    try:
        return max(60, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        log.error("Missing required environment variables: %s", ", ".join(missing))
        return 1
    interval = _env_int("RESOLVER_INTERVAL", DEFAULT_INTERVAL)
    bucket = os.environ["INFLUX_BUCKET"]
    client = InfluxDBClient(url=os.environ["INFLUX_URL"], token=os.environ["INFLUX_TOKEN"],
                            org=os.environ["INFLUX_ORG"], timeout=10_000)
    write_api = client.write_api(write_options=SYNCHRONOUS)
    tracker = ChangeTracker(last_owners(client.query_api(), bucket))
    table = routing_table("resolver_identity")
    owners = OwnerCache()
    log.info("resolver identity collector started (every %ds)", interval)
    announced: dict[str, str] = {}
    snapshot: dict[str, dict] = {}

    while True:
        started = time.time()
        if table is not None:
            table.refresh()  # never raises; owners stay unknown until it loads
        for path, server, port in paths():
            # One try for the whole path: whatever goes wrong with one path
            # costs that path this cycle, never the other path or the process.
            try:
                r = probe(path, server, owners, port)
                if path == "observer" and not r.ok:
                    # Nothing on port 53 here: the observer is not enabled.
                    # Not a failure, and not a point either, or every host
                    # without it would carry an always-failing series.
                    continue
                previous = tracker.previous_for(r)
                write_api.write(bucket=bucket, record=build_point(r, previous, int(started)))
                tracker.written(r, previous is not None)
                snapshot[path] = snapshot_entry(r, int(started))
                label = owner_label(r) if r.ok else "no answer"
                if previous is not None:
                    log.info("resolver via %s changed: %s -> %s", path, previous, label)
                elif announced.get(path) != label:
                    log.info("resolver via %s (%s): %s, egress %s%s", path, r.via, label,
                             ",".join(r.egress) or "-", f", ECS {r.ecs}" if r.ecs else "")
                announced[path] = label
            except Exception as exc:
                # The type only, as elsewhere: an InfluxDB error's message
                # can carry the token.
                log.error("resolver path %s via %s failed this cycle: %s",
                          path, server, type(exc).__name__)
        # A path that stopped being probed (the observer disabled) drops out.
        live = {p for p, _, _ in paths()}
        write_snapshot({p: e for p, e in snapshot.items() if p in live})
        time.sleep(max(1.0, interval - (time.time() - started)))


if __name__ == "__main__":
    sys.exit(main())
