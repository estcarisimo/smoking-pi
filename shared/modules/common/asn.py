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
CAIDA's daily RouteViews prefix-to-AS snapshot, looked up locally, and
PeeringDB's exchange-point LANs. The callers cache what they learn
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
# The data behind map-ip-to-asn (github.com/estcarisimo/map-ip-to-asn):
# CAIDA's daily RouteViews prefix-to-AS snapshot, one line per announced
# prefix ("1.0.0.0<TAB>24<TAB>13335", a multi-origin prefix as "a_b", an AS
# set as "a,b"; the last AS is taken, as that tool takes it). About 4 MB
# compressed and 1.1 million IPv4 prefixes, refreshed once a day, looked up
# locally: no query per address, the announced prefix exactly, and nothing
# rate-limited. Nested prefixes are flattened into non-overlapping ranges
# (the longest prefix wins) kept in three arrays, a few MB, searched by
# bisection. Exchange-point LANs are not announced, so the routers' replies
# from them have no origin: PeeringDB's IXP prefixes name them instead.

PFX2AS_ROOT = "https://publicdata.caida.org/datasets/routing/routeviews-prefix2as/"
PFX2AS_LOG = PFX2AS_ROOT + "pfx2as-creation.log"
PEERINGDB = "https://www.peeringdb.com/api/"
TABLE_REFRESH = 86400       # seconds between downloads
TABLE_MIN_PREFIXES = 500_000  # a snapshot with fewer is broken, not the Internet
FETCH_TIMEOUT = 60

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


def _last_as(field: str) -> int:
    return int(field.replace(",", "_").split("_")[-1])


def flatten(lines) -> tuple:
    """(starts, ends, asns) arrays of non-overlapping ranges from prefix2as
    lines sorted by address, then by length (as CAIDA's are): inside a
    prefix, a more specific one wins for its own addresses."""
    from array import array

    starts, ends, asns = array("I"), array("I"), array("I")

    def emit(start: int, end: int, asn: int) -> None:
        if start > end:
            return
        if asns and asns[-1] == asn and ends[-1] + 1 == start:
            ends[-1] = end
        else:
            starts.append(start)
            ends.append(end)
            asns.append(asn)

    stack: list[tuple[int, int, int]] = []
    cursor = 0
    for line in lines:
        parts = line.split()
        if len(parts) != 3 or ":" in parts[0]:
            continue
        try:
            # By hand, not ipaddress: a million lines, on a Pi, once a day.
            a, b, c, d = (int(x) for x in parts[0].split("."))
            length = int(parts[1])
            if not (0 <= length <= 32 and max(a, b, c, d) <= 255):
                continue
            start = (a << 24) | (b << 16) | (c << 8) | d
            end = start + (1 << (32 - length)) - 1
            asn = _last_as(parts[2])
        except ValueError:
            continue
        while stack and stack[-1][1] < start:
            s, e, a = stack.pop()
            emit(max(cursor, s), e, a)
            cursor = max(cursor, e + 1)
        if stack:
            emit(max(cursor, stack[-1][0]), start - 1, stack[-1][2])
        cursor = max(cursor, start)
        stack.append((start, end, asn))
    while stack:
        s, e, a = stack.pop()
        emit(max(cursor, s), e, a)
        cursor = max(cursor, e + 1)
    return starts, ends, asns


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
            out.append([int(net.network_address), int(net.broadcast_address), str(name)])
    return sorted(out)


class RoutingTable:
    """The prefix-to-AS table and the IXP LANs, kept in ``directory``
    (``pfx2as.gz``, ``ixp.json``) and refreshed once a day. A download that
    fails, or a snapshot that looks broken, keeps the previous one."""

    def __init__(self, directory: Path, fetch=_fetch, clock=None) -> None:
        import time

        self.directory = Path(directory)
        self._fetch = fetch
        self._clock = clock or time.time
        self._ranges: tuple | None = None
        self._ixp: list[list] = []
        self.loaded_at = 0.0
        self.snapshot = ""

    @property
    def ready(self) -> bool:
        return self._ranges is not None

    def _stale(self, path: Path) -> bool:
        try:
            return self._clock() - path.stat().st_mtime >= TABLE_REFRESH
        except OSError:
            return True

    def _download_pfx2as(self, path: Path) -> None:
        import gzip

        log = self._fetch(PFX2AS_LOG).decode(errors="replace").strip().splitlines()
        name = log[-1].split()[-1]
        if not name.endswith(".pfx2as.gz") or ".." in name or name.startswith("/"):
            raise ValueError("unexpected snapshot name")
        data = self._fetch(PFX2AS_ROOT + name)
        import io

        with gzip.GzipFile(fileobj=io.BytesIO(data)) as lines:  # counted, never held
            if sum(1 for _ in lines) < TABLE_MIN_PREFIXES:
                raise ValueError("snapshot too small")
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        (self.directory / "pfx2as.name").write_text(name)

    def _download_ixp(self, path: Path) -> None:
        import json

        def get(what: str, fields: str) -> list[dict]:
            return json.loads(self._fetch(f"{PEERINGDB}{what}?fields={fields}"))["data"]

        ranges = ixp_ranges(get("ixpfx", "prefix,ixlan_id"), get("ixlan", "id,ix_id"),
                            get("ix", "id,name"))
        if not ranges:
            raise ValueError("no IXP prefix")
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(ranges))
        tmp.replace(path)

    def refresh(self) -> bool:
        """Download what is a day old, load what changed; never raises.
        Whether a table is ready afterwards."""
        import gzip
        import json
        import logging

        log = logging.getLogger("common.asn")
        self.directory.mkdir(parents=True, exist_ok=True)
        pfx2as, ixp = self.directory / "pfx2as.gz", self.directory / "ixp.json"
        for path, download in ((pfx2as, self._download_pfx2as), (ixp, self._download_ixp)):
            if self._stale(path):
                try:
                    download(path)
                except Exception:  # noqa: BLE001 -- the previous file stays
                    log.warning("could not refresh %s; keeping the one there is", path.name,
                                exc_info=True)
        try:
            mtime = pfx2as.stat().st_mtime
            if self._ranges is None or mtime > self.loaded_at:
                self._ranges = self._load_built(pfx2as)
                if self._ranges is None:
                    with gzip.open(pfx2as, "rt") as lines:
                        self._ranges = flatten(lines)
                    self._save_built(pfx2as, self._ranges)
                self.loaded_at = mtime
                name = self.directory / "pfx2as.name"
                self.snapshot = name.read_text().strip() if name.exists() else ""
        except (OSError, EOFError, ValueError):
            log.warning("no routing table loaded; AS lookups go to Team Cymru", exc_info=True)
        try:
            self._ixp = json.loads(ixp.read_text())
        except (OSError, ValueError):
            pass
        return self.ready

    # The flattened ranges, saved next to the snapshot they came from: a
    # restart reads them in milliseconds instead of flattening a million
    # lines again (half a minute on a Pi's half core).
    def _built(self) -> Path:
        return self.directory / "pfx2as.ranges"

    def _load_built(self, source: Path) -> tuple | None:
        from array import array

        built = self._built()
        try:
            if built.stat().st_mtime < source.stat().st_mtime:
                return None
            with built.open("rb") as f:
                count = array("I")
                count.fromfile(f, 1)
                out = tuple(array("I") for _ in range(3))
                for arr in out:
                    arr.fromfile(f, count[0])
            return out
        except (OSError, EOFError, ValueError):
            return None

    def _save_built(self, source: Path, ranges: tuple) -> None:
        from array import array

        try:
            tmp = self._built().with_suffix(".tmp")
            with tmp.open("wb") as f:
                array("I", [len(ranges[0])]).tofile(f)
                for arr in ranges:
                    arr.tofile(f)
            tmp.replace(self._built())
        except OSError:
            pass

    def lookup(self, ip: str) -> int | None:
        """The origin AS of the longest announced prefix covering ``ip``."""
        import bisect

        if self._ranges is None:
            return None
        starts, ends, asns = self._ranges
        x = int(ipaddress.IPv4Address(ip))
        i = bisect.bisect_right(starts, x) - 1
        return asns[i] if i >= 0 and ends[i] >= x else None

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
