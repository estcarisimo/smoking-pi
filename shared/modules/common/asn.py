"""Who announces an address, from public DNS data: shared by the modules
that ask (inference's operator tags, pathmeter's hops).

- :func:`resolve`: a host's first IPv4 address and its CNAME chain;
- :func:`origin`: an IPv4 address's origin AS and its name, from Team
  Cymru's DNS service (``<reversed /24>.origin.asn.cymru.com``), as the DNS
  observer and the resolver probe already do;
- :func:`classify`: the operator and its kind from :data:`KNOWN`, a table of
  well-known networks, then from :func:`roles`, the AS roles derived from
  Linnaeus's hand-labeled ASes (``data/asn_roles.csv``). An AS in neither
  is its own operator, of kind ``other``.

DNS queries, unless the process uses a routing table (:func:`use_table`):
originet over CAIDA's daily RouteViews prefix-to-AS snapshot, looked up
locally, and PeeringDB's exchange-point LANs. The callers cache what they learn
(``operators.py`` per target, pathmeter's ``owners.py`` per hop). Needs
dnspython, imported on first use, so importing this module needs nothing.
"""

from __future__ import annotations

import csv
import ipaddress
from functools import cache
from pathlib import Path

CDN = "cdn"                  # multi-tenant CDN
CONTENT = "content_network"  # a content provider's own network
CACHE = "embedded_cache"     # caches placed near the access network (Netflix OCA)
CLOUD = "cloud_origin"       # cloud, hosting, or the service's own servers
ACADEMIC = "academic"
ACCESS = "access"            # an access network: home, mobile or satellite ISP
TRANSIT = "transit"          # a transit carrier
DNS_INFRA = "dns_infrastructure"  # root, TLD or authoritative DNS operator
LOCAL = "local"              # an address on the local network (the gateway)
OTHER = "other"
UNKNOWN = "unknown"
KINDS = (CDN, CONTENT, CACHE, CLOUD, ACADEMIC, ACCESS, TRANSIT, DNS_INFRA, LOCAL,
         OTHER, UNKNOWN)

ROLES_FILE = Path(__file__).resolve().parent / "data" / "asn_roles.csv"

# ASN -> (operator, kind). Only networks whose kind is not in doubt; the
# rest are their own operator, of kind "other".
KNOWN: dict[int, tuple[str, str]] = {
    54113: ("Fastly", CDN), 13335: ("Cloudflare", CDN), 20940: ("Akamai", CDN),
    16625: ("Akamai", CDN), 36183: ("Akamai", CDN), 21342: ("Akamai", CDN),
    19551: ("Imperva", CDN), 15133: ("Edgio", CDN), 60068: ("CDN77", CDN),
    15169: ("Google", CONTENT), 36040: ("Google", CONTENT), 32934: ("Meta", CONTENT),
    714: ("Apple", CONTENT), 6185: ("Apple", CONTENT), 8075: ("Microsoft", CONTENT),
    8068: ("Microsoft", CONTENT), 2906: ("Netflix", CACHE), 40027: ("Netflix", CACHE),
    396982: ("Google Cloud", CLOUD), 14618: ("AWS", CLOUD), 8987: ("AWS", CLOUD),
    24940: ("Hetzner", CLOUD), 16276: ("OVH", CLOUD), 14061: ("DigitalOcean", CLOUD),
    63949: ("Akamai Cloud (Linode)", CLOUD), 20473: ("Vultr", CLOUD),
    37963: ("Alibaba", CLOUD), 45102: ("Alibaba", CLOUD), 31898: ("Oracle", CLOUD),
    1909: ("CAIDA", ACADEMIC), 11537: ("Internet2", ACADEMIC),
}
AMAZON = 16509  # CloudFront and EC2 share it: the CNAME chain decides
CLOUDFRONT_HINTS = ("cloudfront", "frontier.amazon")


@cache
def roles(path: Path = ROLES_FILE) -> dict[int, tuple[str, str]]:
    """ASN -> (operator, kind) from the vendored roles file; empty when the
    file is missing (an image that did not copy ``data/``) or unreadable.
    Lines starting with ``#`` are the file's provenance header."""
    table: dict[int, tuple[str, str]] = {}
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            rows = csv.DictReader(line for line in fh if not line.startswith("#"))
            for row in rows:
                try:
                    asn = int(row["asn"])
                except (KeyError, TypeError, ValueError):
                    continue
                if row.get("kind") in KINDS:
                    table[asn] = (row.get("operator") or f"AS{asn}", row["kind"])
    except OSError:
        return {}
    return table


def _resolver():
    import dns.resolver

    r = dns.resolver.Resolver()
    r.lifetime = 3.0
    return r


def resolve(host: str, resolver=None) -> tuple[str, list[str]] | None:
    """(first IPv4 address, CNAME chain) of ``host``; an address is its own
    answer. None when it does not resolve."""
    try:
        ipaddress.ip_address(host)
        return host, []
    except ValueError:
        pass
    try:
        answer = (resolver or _resolver()).resolve(host, "A")
    except Exception:  # noqa: BLE001 -- dnspython raises many kinds; all mean "no answer"
        return None
    chain = [str(item.target).rstrip(".") for rrset in answer.response.answer
             for item in rrset if rrset.rdtype == 5]  # CNAME
    return (str(answer[0]), chain) if len(answer) else None


def addresses(host: str, resolver=None) -> list[str]:
    """Every IPv4 address ``host`` resolves to, in the answer's order; an
    address is its own answer. Empty when it does not resolve."""
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    try:
        answer = (resolver or _resolver()).resolve(host, "A")
    except Exception:  # noqa: BLE001 -- dnspython raises many kinds; all mean "no answer"
        return []
    return [str(item) for item in answer]


def _txt(qname: str, resolver=None) -> str | None:
    try:
        answer = (resolver or _resolver()).resolve(qname, "TXT")
    except Exception:  # noqa: BLE001
        return None
    return b"".join(answer[0].strings).decode(errors="replace")


def origin(ip: str, resolver=None) -> tuple[int, str] | None:
    """(origin ASN, AS name) of an IPv4 address.

    From the process's routing table (:func:`use_table`) when it has one,
    looked up locally; else, or when the table has no route for it, from
    Team Cymru per /24. The AS name is Team Cymru's either way, asked once
    per AS."""
    if ipaddress.ip_address(ip).version != 4:
        return None
    if _TABLE is not None and _TABLE.ready:
        asn = _TABLE.lookup(ip)
        if asn:
            return asn, as_name(asn, resolver)
    reply = _txt(".".join(reversed(ip.split(".")[:3])) + ".origin.asn.cymru.com", resolver)
    if not reply:
        return None
    head = reply.split("|", 1)[0].split()
    if not head or not head[0].isdigit():
        return None
    asn = int(head[0])
    return asn, as_name(asn, resolver)


_NAMES: dict[int, str] = {}


def as_name(asn: int, resolver=None) -> str:
    """An AS's name from Team Cymru (``AS<n>.asn.cymru.com``), asked once
    per AS per process; empty when it does not answer (asked again later)."""
    if asn in _NAMES:
        return _NAMES[asn]
    parts = [p.strip() for p in (_txt(f"AS{asn}.asn.cymru.com", resolver) or "").split("|")]
    name = parts[4] if len(parts) >= 5 else ""
    if name:
        _NAMES[asn] = name
    return name


# ───────────────────── the routing table, local ─────────────────────
#
# originet (github.com/estcarisimo/originet, formerly map-ip-to-asn): CAIDA's
# daily RouteViews prefix-to-AS snapshot in a Rust longest-prefix-match table,
# with Python bindings. It reads CAIDA's index, downloads the newest snapshot
# into its cache (about 4 MB) and loads it (1.1 million IPv4 prefixes, two
# seconds on a Pi's half core); a lookup is the most specific announced
# prefix, every hop at once and nothing rate-limited. A prefix several
# networks announce reports the first of them, as originet does. Exchange-point
# LANs are not announced, so the routers' replies from them have no origin:
# PeeringDB's IXP prefixes name them instead. Both are downloaded by the Pi
# for its own use and never shipped in an image or the repository (CAIDA asks
# for a citation of the dataset; PeeringDB's terms allow operational use, not
# redistribution). originet is imported only when a table is loaded: images
# without it keep Team Cymru.

PEERINGDB = "https://www.peeringdb.com/api/"
TABLE_REFRESH = 86400       # seconds between checks for a newer snapshot
TABLE_MIN_PREFIXES = 500_000  # a snapshot with fewer is broken, not the Internet
SNAPSHOTS_KEPT = 2          # originet's cache keeps every day; this prunes it
FETCH_TIMEOUT = 60
# What the hand-rolled table this replaced left in the same directory.
_LEGACY = ("pfx2as.gz", "pfx2as.name", "pfx2as.ranges", "pfx2as.gz.tmp", "pfx2as.ranges.tmp")

_TABLE = None


def use_table(directory) -> RoutingTable:
    """Use a routing table kept in ``directory`` for :func:`origin` in this
    process (loaded and refreshed by :meth:`RoutingTable.refresh`)."""
    global _TABLE
    if _TABLE is None or _TABLE.directory != Path(directory):
        _TABLE = RoutingTable(Path(directory))
    return _TABLE


def _fetch(url: str) -> bytes:
    from urllib.request import Request, urlopen

    request = Request(url, headers={"User-Agent": "smoking-pi (routing table)"})
    with urlopen(request, timeout=FETCH_TIMEOUT) as response:  # fixed https URLs only
        return response.read()


def _originet(cache_dir: Path, offline: bool):
    """The newest IPv4 snapshot as an originet table (the Pi measures IPv4)."""
    from originet import Table

    return Table.from_caida(cache_dir=str(cache_dir), ipv6=False, offline=offline)


def _nested(items, emit) -> None:
    """Non-overlapping ranges from (start, end, value) items sorted by start,
    then widest first: inside a range, a narrower one wins for its own
    addresses. ``emit(start, end, value)`` receives them in order."""
    stack: list[tuple[int, int, object]] = []
    cursor = 0
    for start, end, value in items:
        while stack and stack[-1][1] < start:
            s, e, v = stack.pop()
            if max(cursor, s) <= e:
                emit(max(cursor, s), e, v)
            cursor = max(cursor, e + 1)
        if stack and max(cursor, stack[-1][0]) <= start - 1:
            emit(max(cursor, stack[-1][0]), start - 1, stack[-1][2])
        cursor = max(cursor, start)
        stack.append((start, end, value))
    while stack:
        s, e, v = stack.pop()
        if max(cursor, s) <= e:
            emit(max(cursor, s), e, v)
        cursor = max(cursor, e + 1)


def ixp_ranges(ixpfx: list[dict], ixlan: list[dict], ix: list[dict]) -> list[list]:
    """[start, end, exchange name] per IPv4 IXP LAN prefix, sorted."""
    names = {i.get("id"): i.get("name") for i in ix}
    lan_ix = {lan.get("id"): lan.get("ix_id") for lan in ixlan}
    out = []
    for pfx in ixpfx:
        try:
            net = ipaddress.ip_network(pfx.get("prefix") or "", strict=False)
        except ValueError:
            continue
        name = names.get(lan_ix.get(pfx.get("ixlan_id")))
        if net.version == 4 and name:
            out.append((int(net.network_address), int(net.broadcast_address), str(name)))
    # Flattened: PeeringDB does not promise that two exchanges' prefixes never
    # nest, and a bisection needs disjoint ranges.
    flat: list[list] = []
    _nested(sorted(out, key=lambda r: (r[0], -r[1])),
            lambda s, e, v: flat.append([s, e, v]))
    return flat


def prune_snapshots(cache_dir: Path, keep: int = SNAPSHOTS_KEPT) -> int:
    """Delete all but the ``keep`` newest snapshots in originet's cache (it
    keeps every day, about 4 MB each); returns how many went."""
    files = sorted(Path(cache_dir).glob("routeviews-prefix2as/*/*/*.pfx2as.gz"),
                   key=lambda p: p.name)
    gone = 0
    for old in files[:-keep] if keep > 0 else files:
        try:
            old.unlink()
            gone += 1
        except OSError:
            pass
    return gone


class RoutingTable:
    """originet's prefix-to-AS table and the IXP LANs, kept in ``directory``
    (``originet/`` for its cache, ``ixp.json``) and checked once a day. A
    download that fails keeps the table loaded, or the cached one."""

    def __init__(self, directory: Path, fetch=_fetch, clock=None, loader=_originet) -> None:
        import time

        self.directory = Path(directory)
        self._fetch = fetch
        self._clock = clock or time.time
        self._load = loader
        self._table = None
        self._ixp: list[list] = []
        self.checked_at = 0.0
        self.snapshot = ""

    @property
    def ready(self) -> bool:
        return self._table is not None

    @property
    def cache_dir(self) -> Path:
        return self.directory / "originet"

    def _stale(self, path: Path) -> bool:
        try:
            return self._clock() - path.stat().st_mtime >= TABLE_REFRESH
        except OSError:
            return True

    def _download_ixp(self, path: Path) -> None:
        import json

        def get(what: str, fields: str) -> list[dict]:
            return json.loads(self._fetch(f"{PEERINGDB}{what}?fields={fields}"))["data"]

        ranges = ixp_ranges(get("ixpfx", "prefix,ixlan_id"), get("ixlan", "id,ix_id"),
                            get("ix", "id,name"))
        if not ranges:
            raise ValueError("no IXP prefix")
        tmp = self.directory / "ixp.json.tmp"
        tmp.write_text(json.dumps(ranges))
        tmp.replace(path)

    def _load_table(self) -> None:
        """A newer snapshot when there is one, else the cached one."""
        import logging

        log = logging.getLogger("common.asn")
        try:
            table = self._load(self.cache_dir, False)
        except Exception:  # noqa: BLE001 -- offline, CAIDA down: what is cached will do
            log.warning("could not fetch a routing snapshot; trying the cached one",
                        exc_info=True)
            if self._table is not None:
                return
            try:
                table = self._load(self.cache_dir, True)
            except Exception:  # noqa: BLE001 -- nothing cached either
                log.warning("no routing table; AS lookups go to Team Cymru", exc_info=True)
                return
        if table.len_v4 < TABLE_MIN_PREFIXES:
            log.warning("routing snapshot of %d prefixes refused", table.len_v4)
            return
        self._table = table
        self.snapshot = str((table.snapshots or [{}])[0].get("file") or "")
        prune_snapshots(self.cache_dir)

    def refresh(self) -> bool:
        """Check for a newer snapshot once a day, refresh the IXPs when a day
        old; never raises. Whether a table is ready afterwards."""
        import json
        import logging

        log = logging.getLogger("common.asn")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            for name in _LEGACY:
                (self.directory / name).unlink(missing_ok=True)
            if self._table is None or self._clock() - self.checked_at >= TABLE_REFRESH:
                self.checked_at = self._clock()
                self._load_table()
            ixp = self.directory / "ixp.json"
            if not ixp.exists() or self._stale(ixp):
                try:
                    self._download_ixp(ixp)
                except Exception:  # noqa: BLE001 -- the previous file stays
                    log.warning("could not refresh ixp.json; keeping the one there is",
                                exc_info=True)
            try:
                self._ixp = json.loads(ixp.read_text())
            except (OSError, ValueError):
                pass
        except Exception:  # noqa: BLE001 -- a table is an aid, never a reason to stop
            log.warning("routing table refresh failed", exc_info=True)
        return self.ready

    def lookup(self, ip: str) -> int | None:
        """The origin AS of the longest announced prefix covering ``ip``."""
        if self._table is None:
            return None
        found = self._table.lookup(ip)
        return found.asn if found else None

    def ixp(self, ip: str) -> str | None:
        """The exchange point whose LAN ``ip`` is on, from PeeringDB."""
        import bisect

        x = int(ipaddress.IPv4Address(ip))
        i = bisect.bisect_right(self._ixp, [x, float("inf")]) - 1
        return self._ixp[i][2] if i >= 0 and self._ixp[i][1] >= x else None


def ixp(ip: str) -> str | None:
    """The exchange point ``ip`` is on, when this process uses a table."""
    if _TABLE is None or not _TABLE._ixp:
        return None
    try:
        return _TABLE.ixp(ip)
    except ValueError:
        return None


def classify(asn: int | None, as_name: str, host: str, chain: list[str]) -> tuple[str, str]:
    """(operator, kind) from the origin AS, with the CNAME chain for Amazon.
    :data:`KNOWN` wins over :func:`roles`; the AS name's words are the last
    resort for universities."""
    if not asn:
        return UNKNOWN, UNKNOWN
    if asn == AMAZON:
        names = " ".join([host, *chain]).lower()
        if any(h in names for h in CLOUDFRONT_HINTS):
            return "Amazon CloudFront", CDN
        return "AWS", CLOUD
    if asn in KNOWN:
        return KNOWN[asn]
    if asn in (table := roles()):
        return table[asn]
    if any(w in as_name.lower() for w in ("univers", "college", "school", "research", "academ")):
        return as_name.split(" - ")[-1].split(",")[0].strip() or f"AS{asn}", ACADEMIC
    return (as_name.split(" - ")[0].strip() or f"AS{asn}"), OTHER
