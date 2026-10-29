"""Who announces an address: shared by every module that asks (the
exporter's service labels, inference's operator tags, pathmeter's hops, the
DNS wizard, the resolver and public-address checks).

- :func:`resolve`: a host's first IPv4 address and its CNAME chain (DNS);
- :func:`origin`: an address's origin AS and its name, looked up locally in
  the process's routing table (:func:`use_table`);
- :func:`classify`: the operator and its kind from :data:`KNOWN`, a table of
  well-known networks, then from :func:`roles`, the AS roles derived from
  Linnaeus's hand-labeled ASes (``data/asn_roles.csv``). An AS in neither
  is its own operator, of kind ``other``.

The routing table is originet over CAIDA's daily RouteViews prefix-to-AS
snapshot, CAIDA's IXP dataset and CAIDA's AS2Org names, downloaded by the Pi
and looked up locally; nothing is asked per address. The callers cache what
they learn (``operators.py`` per target, pathmeter's ``owners.py`` per hop).
Needs dnspython for :func:`resolve` and originet for the table, both
imported on first use, so importing this module needs nothing.
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


def origin(ip: str, resolver=None) -> tuple[int, str] | None:
    """(origin ASN, AS name) of an IP address, from the process's routing
    table (:func:`use_table`: originet over CAIDA's RouteViews snapshot, the
    exchange members of CAIDA's IXP dataset, the names of CAIDA's AS2Org),
    looked up locally. None without a loaded table and its names, or with
    no route: a lookup is never asked of anyone else, and an owner without
    its name is not an answer (callers cache an answer for a week, and
    :func:`classify` reads the name). ``resolver`` is unused and kept for
    the callers' signature."""
    if _TABLE is None or not _TABLE.ready or not _TABLE.names:
        return None
    try:
        asn = _TABLE.lookup(ip)
    except ValueError:
        return None
    return (asn, as_name(asn)) if asn else None


def as_name(asn: int, resolver=None) -> str:
    """An AS's name, as ``HANDLE - Organization, CC``, from CAIDA's AS2Org
    in the process's routing table; empty when unknown or not loaded."""
    if _TABLE is None or not _TABLE.names:
        return ""
    return _TABLE.names.get(int(asn), "")


# ───────────────────── the routing table, local ─────────────────────
#
# originet (github.com/estcarisimo/originet, formerly map-ip-to-asn): CAIDA's
# daily RouteViews prefix-to-AS snapshot in a Rust longest-prefix-match table,
# with Python bindings. It reads CAIDA's index, downloads the newest snapshot
# into its cache (about 4 MB) and loads it (1.1 million IPv4 prefixes, two
# seconds on a Pi's half core); a lookup is the most specific announced
# prefix, every hop at once and nothing rate-limited. A prefix several
# networks announce reports the first of them, as originet does. Exchange-point
# LANs are rarely announced, so the routers' replies from them have no origin:
# CAIDA's IXP dataset (monthly; PeeringDB, PCH and Hurricane Electric merged)
# names the exchange and the member AS behind each address. AS names come from
# CAIDA's AS2Org (monthly). All are downloaded by the Pi for its own use and
# never shipped in an image or the repository; CAIDA asks for a citation of
# its datasets. originet is imported only when a table is loaded.

IXPS_ROOT = "https://publicdata.caida.org/datasets/ixps/"
IXP_REFRESH = 7 * 86400     # CAIDA's IXP dataset is monthly; checked weekly
AS2ORG_ROOT = "https://publicdata.caida.org/datasets/as-organizations/"
NAMES_REFRESH = 7 * 86400   # AS2Org is monthly; checked weekly
NAMES_MIN = 50_000          # fewer named ASes is a broken file
TABLE_REFRESH = 86400       # seconds between checks for a newer snapshot
TABLE_MIN_PREFIXES = 500_000  # a snapshot with fewer is broken, not the Internet
SNAPSHOTS_KEPT = 2          # originet's cache keeps every day; this prunes it
FETCH_TIMEOUT = 60
# What the hand-rolled table this replaced left in the same directory.
_LEGACY = ("pfx2as.gz", "pfx2as.name", "pfx2as.ranges", "pfx2as.gz.tmp", "pfx2as.ranges.tmp")

_TABLE = None


def use_table(directory, ipv6: bool = False) -> RoutingTable:
    """Use a routing table kept in ``directory`` for :func:`origin` in this
    process (loaded and refreshed by :meth:`RoutingTable.refresh`); with
    ``ipv6``, IPv6 addresses too."""
    global _TABLE
    if _TABLE is None or _TABLE.directory != Path(directory):
        _TABLE = RoutingTable(Path(directory), ipv6=ipv6)
    return _TABLE


def _fetch(url: str) -> bytes:
    from urllib.request import Request, urlopen

    request = Request(url, headers={"User-Agent": "smoking-pi (routing table)"})
    with urlopen(request, timeout=FETCH_TIMEOUT) as response:  # fixed https URLs only
        return response.read()


def _originet(cache_dir: Path, offline: bool, ipv6: bool = False):
    """The newest snapshot(s) as an originet table: IPv4, and IPv6 for a
    caller that sees IPv6 addresses (the DNS observer's answers)."""
    from originet import Table

    return Table.from_caida(cache_dir=str(cache_dir), ipv6=ipv6, offline=offline)


def as2org_names(lines) -> dict[int, str]:
    """ASN -> ``HANDLE - Organization, CC`` from CAIDA's AS2Org JSON lines
    (one object per AS or organization). The shape is the one
    :func:`classify` reads: the handle before " - " names the operator, the
    organization's words tell an academic network. An organization with no
    country ends without ", CC", so a reader that cuts the country at the
    last comma also cuts a comma of its name ("Fastly, Inc." -> "Fastly");
    rare (AS2Org gives nearly every organization a country)."""
    import json

    orgs: dict[str, tuple[str, str]] = {}
    ases: list[tuple[int, str, str]] = []
    for line in lines:
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("type") == "Organization":
            orgs[o.get("organizationId", "")] = (o.get("name") or "", o.get("country") or "")
        elif o.get("type") == "ASN" and str(o.get("asn", "")).isdigit():
            ases.append((int(o["asn"]), o.get("name") or "", o.get("organizationId") or ""))
    out = {}
    for asn, handle, org_id in ases:
        org, country = orgs.get(org_id, ("", ""))
        name = handle or f"AS{asn}"
        if org:
            name += f" - {org}" + (f", {country}" if country else "")
        out[asn] = name
    return out


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


def _ip_int(text: str) -> int | None:
    try:
        return int(ipaddress.ip_address(text))
    except ValueError:
        return None


def ixp_data(ixs_lines, members_lines, ipv6: bool = False) -> dict:
    """From CAIDA's IXP dataset (``ixs_YYYYMM.jsonl``, ``ix-asns_YYYYMM.jsonl``):
    ``ranges``, [start, end, "Name (City)"] per exchange LAN prefix, flattened
    and sorted; ``members``, address -> the member AS that uses it there."""
    import json

    def rows(lines):
        for line in lines:
            if line.startswith("#") or not line.strip():
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue

    families = ("ipv4", "ipv6") if ipv6 else ("ipv4",)
    out = []
    for ix in rows(ixs_lines):
        name = str(ix.get("name") or "").strip()
        if not name:
            continue
        label = f"{name} ({ix['city']})" if ix.get("city") else name
        for family in families:
            for prefix in (ix.get("prefixes") or {}).get(family) or []:
                try:
                    net = ipaddress.ip_network(prefix, strict=False)
                except ValueError:
                    continue
                out.append((int(net.network_address), int(net.broadcast_address), label))
    # Flattened: two exchanges' prefixes may nest, and a bisection needs
    # disjoint ranges.
    flat: list[list] = []
    _nested(sorted(out, key=lambda r: (r[0], -r[1])),
            lambda s, e, v: flat.append([s, e, v]))
    claims: dict[str, set[int]] = {}
    for m in rows(members_lines):
        if not str(m.get("asn", "")).isdigit():
            continue
        for family in families:
            for addr in m.get(family) or []:
                if _ip_int(addr) is not None:
                    claims.setdefault(str(ipaddress.ip_address(addr)), set()).add(int(m["asn"]))
    # An address listed under several ASes (siblings, route servers: about
    # 2 400 of 151 000 in October 2026) names none of them: its hop keeps
    # the announced prefix's origin rather than whichever came last.
    members = {addr: next(iter(asns)) for addr, asns in claims.items() if len(asns) == 1}
    return {"ranges": flat, "members": members}


def prune_snapshots(cache_dir: Path, keep: int = SNAPSHOTS_KEPT) -> int:
    """Delete all but the ``keep`` newest snapshots in originet's cache (it
    keeps every day, about 4 MB each); returns how many went. IPv4 only, as
    the table is loaded: the names (routeviews-rv2-YYYYMMDD-HHMM) then sort
    by date across months and years."""
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

    def __init__(self, directory: Path, fetch=_fetch, clock=None, loader=_originet,
                 ipv6: bool = False) -> None:
        import time

        self.directory = Path(directory)
        self._fetch = fetch
        self._clock = clock or time.time
        self._load = loader
        self._ipv6 = ipv6
        self._table = None
        self.names: dict[int, str] = {}
        self._names_mtime = 0.0
        # When each download last failed: retried an hour later, not every call.
        self._failed_at: dict[str, float] = {}
        self._ixp: list[list] = []
        self._members: dict[str, int] = {}
        self.checked_at = 0.0
        self.snapshot = ""
        self._cleaned = False
        self._reached = False

    @property
    def ready(self) -> bool:
        return self._table is not None

    @property
    def cache_dir(self) -> Path:
        return self.directory / "originet"

    def _due(self, what: str) -> bool:
        """A download that failed is tried again an hour later, not on every
        call (the wizard calls once per pass)."""
        return self._clock() - self._failed_at.get(what, float("-inf")) >= 3600

    def _stale(self, path: Path, ttl: float = TABLE_REFRESH) -> bool:
        try:
            return self._clock() - path.stat().st_mtime >= ttl
        except OSError:
            return True

    def _download_ixp(self, path: Path) -> None:
        """The newest month of CAIDA's IXP dataset, when it is not the one
        there is (this month's, else up to three months back)."""
        import datetime
        import json

        source = self.directory / "ixp.source"
        today = datetime.datetime.fromtimestamp(self._clock(), datetime.UTC).date()
        year, month = today.year, today.month
        for _ in range(4):
            stamp = f"{year:04d}{month:02d}"
            base = f"{IXPS_ROOT}{year:04d}/{month:02d}/"
            if path.exists() and source.exists() and source.read_text().strip() == stamp:
                source.touch()
                return
            try:
                ixs = self._fetch(f"{base}ixs_{stamp}.jsonl").decode(errors="replace")
                members = self._fetch(f"{base}ix-asns_{stamp}.jsonl").decode(errors="replace")
            except Exception:  # noqa: BLE001 -- not (fully) published: the month before
                year, month = (year, month - 1) if month > 1 else (year - 1, 12)
                continue
            data = ixp_data(ixs.splitlines(), members.splitlines(), self._ipv6)
            if not data["ranges"]:
                raise ValueError("no IXP prefix")
            tmp = self.directory / "ixp.json.tmp"
            tmp.write_text(json.dumps(data, separators=(",", ":")))
            tmp.replace(path)
            source.write_text(stamp)
            return
        raise ValueError("no IXP dataset in the last four months")

    def _load_table(self) -> bool:
        """A newer snapshot when there is one, else the cached one. Whether
        CAIDA was reached: a cached snapshot loaded offline may be weeks old,
        so the next cycle tries again rather than waiting a day."""
        import logging

        log = logging.getLogger("common.asn")
        online = True
        try:
            table = self._load(self.cache_dir, False, self._ipv6)
        except Exception:  # noqa: BLE001 -- offline, CAIDA down: what is cached will do
            log.warning("could not fetch a routing snapshot; trying the cached one",
                        exc_info=True)
            online = False
            if self._table is not None:
                return False
            try:
                table = self._load(self.cache_dir, True, self._ipv6)
            except Exception:  # noqa: BLE001 -- nothing cached either
                log.warning("no routing table yet; AS lookups answer nothing", exc_info=True)
                return False
        if table.len_v4 < TABLE_MIN_PREFIXES:
            log.warning("routing snapshot of %d prefixes refused", table.len_v4)
            return online
        # The new table is built before the old one goes: for a moment two
        # are alive (about 75 MB each), once a day. No memory limit is set.
        self._table = table
        self.snapshot = str((table.snapshots or [{}])[0].get("file") or "")
        prune_snapshots(self.cache_dir)
        return online

    def _download_names(self, path: Path) -> None:
        """The newest AS2Org file, when it is not the one there is."""
        import gzip
        import re

        listing = self._fetch(AS2ORG_ROOT).decode(errors="replace")
        files = sorted(set(re.findall(r"\b(\d{8}\.as-org2info\.jsonl\.gz)\b", listing)))
        if not files:
            raise ValueError("no AS2Org file listed")
        source = self.directory / "as2org.source"
        if path.exists() and source.exists() and source.read_text().strip() == files[-1]:
            source.touch()  # nothing newer
            return
        import io

        data = self._fetch(AS2ORG_ROOT + files[-1])
        # Line by line: decompressed it is about 30 MB, and three services
        # do this on a Pi.
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as raw:
            names = as2org_names(line.decode(errors="replace") for line in raw)
        if len(names) < NAMES_MIN:
            raise ValueError("AS2Org file too small")
        import json

        tmp = self.directory / "as2org.json.tmp"
        tmp.write_text(json.dumps(names, separators=(",", ":")))
        tmp.replace(path)
        source.write_text(files[-1])

    def _refresh_names(self) -> None:
        import json
        import logging

        path = self.directory / "as2org.json"
        if (not path.exists() or self._stale(self.directory / "as2org.source", NAMES_REFRESH)) \
                and self._due("as2org"):
            try:
                self._download_names(path)
            except Exception:  # noqa: BLE001 -- the previous file stays
                self._failed_at["as2org"] = self._clock()
                logging.getLogger("common.asn").warning(
                    "could not refresh AS2Org; keeping the names there are", exc_info=True)
        try:
            mtime = path.stat().st_mtime
            if mtime > self._names_mtime:
                self.names = {int(k): v for k, v in json.loads(path.read_text()).items()}
                self._names_mtime = mtime
        except (OSError, ValueError):
            pass

    def refresh(self) -> bool:
        """Check for a newer snapshot once a day, refresh the IXPs when a day
        old and the AS names when a week old; never raises. Whether a table
        is ready afterwards."""
        import json
        import logging

        log = logging.getLogger("common.asn")
        try:
            if not self._cleaned:
                self.directory.mkdir(parents=True, exist_ok=True)
                for name in _LEGACY:
                    (self.directory / name).unlink(missing_ok=True)
                self._cleaned = True
            # Once a day once CAIDA was reached; every cycle while the table
            # in use is only the cached one (or none).
            if (self._table is None or not self._reached
                    or self._clock() - self.checked_at >= TABLE_REFRESH):
                self._reached = self._load_table()
                if self._reached:
                    self.checked_at = self._clock()
            ixp = self.directory / "ixp.json"
            if (not ixp.exists() or self._stale(self.directory / "ixp.source", IXP_REFRESH)) \
                    and self._due("ixp"):
                try:
                    self._download_ixp(ixp)
                except Exception:  # noqa: BLE001 -- the previous file stays
                    self._failed_at["ixp"] = self._clock()
                    log.warning("could not refresh ixp.json; keeping the one there is",
                                exc_info=True)
            try:
                data = json.loads(ixp.read_text())
                if isinstance(data, dict):  # a PeeringDB-era list is replaced next check
                    self._ixp, self._members = data["ranges"], data["members"]
            except (OSError, ValueError, KeyError):
                pass
            self._refresh_names()
        except Exception:  # noqa: BLE001 -- a table is an aid, never a reason to stop
            log.warning("routing table refresh failed", exc_info=True)
        return self.ready

    def lookup(self, ip: str) -> int | None:
        """The origin AS of the longest announced prefix covering ``ip``
        (IPv6 only when the table was loaded with it)."""
        if self._table is None:
            return None
        # An address on an exchange LAN is a member's port: the router that
        # answers is the member's, whoever announces the LAN (reference Pi:
        # 206.223.118.40 is Transtelco's port on Equinix Dallas, AS32098, in
        # a /23 that AS18734 announces). CAIDA's IXP dataset names it.
        member = self.ixp_member(ip)
        if member:
            return member
        found = self._table.lookup(ip)
        return found.asn if found else None

    def ixp(self, ip: str) -> str | None:
        """The exchange point whose LAN ``ip`` is on ("Name (City)")."""
        import bisect

        x = _ip_int(ip)
        if x is None:
            return None
        i = bisect.bisect_right(self._ixp, [x, float("inf")]) - 1
        return self._ixp[i][2] if i >= 0 and self._ixp[i][1] >= x else None

    def ixp_member(self, ip: str) -> int | None:
        """The AS that uses ``ip`` on an exchange LAN (CAIDA's ix-asns)."""
        try:
            return self._members.get(str(ipaddress.ip_address(ip)))
        except ValueError:
            return None


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
