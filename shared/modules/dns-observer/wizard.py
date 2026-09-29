"""The DNS wizard: what the house uses, summarised hour by hour.

Reads AdGuard Home's query log from disk *incrementally* (only what was
appended since the last pass), keeps per-hour counts per service for the
last week, and writes ``wizard.json`` next to ``status.json``: the busiest
services by presence, who serves them (CDN, network), how diverse and how
concentrated the house's DNS is, and how much the top of the ranking moves.

Nothing here changes a target. The SmokePing container's ``dns_wizard``
exporter turns the snapshot into InfluxDB points for the Grafana "DNS
wizard" dashboard; the names themselves only leave the Pi's disk when the
owner opts in (DNS_EXPORT_NAMES). Design: Notion, "Selección de dominios:
de observaciones DNS a targets"; the offline counterpart is
tools/dns-explore.

Units, from finest to coarsest:
  service  the registrable domain (eTLD+1, Public Suffix List);
  cdn      the registrable domain at the end of the CNAME chain (ICANN
           suffixes only, so every CloudFront customer is "cloudfront.net");
  asn/org  the origin AS of the answer's first address (Team Cymru, asked
           of 1.1.1.1 directly: through the router the lookups would land
           in the very log being summarised).
Presence (distinct clock hours seen) ranks services: a burst of telemetry
is one hour, a cache hides volume but not presence.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import logging
import math
import os
import re
from functools import lru_cache
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit
from dataclasses import dataclass, field

import dns.exception
import dns.message
import dns.rdatatype
import dns.resolver
from publicsuffixlist import PublicSuffixList

log = logging.getLogger("dns-observer.wizard")

WINDOW_HOURS = 7 * 24
# Below this many hours of data every service ties on presence.
PRESENCE_AFTER_HOURS = 72
KEEP_QTYPES = {"A", "AAAA", "HTTPS", "SVCB"}
KS = (5, 10, 20)

# The Pi's own traffic reaches the observer like the house's: the Pi
# resolves through the router, which forwards here. So do SmokePing's
# lookups of its own targets. Matched against the service, or as a name
# suffix for entries with a leading dot. Same list as tools/dns-explore.
OWN_TRAFFIC = (
    "example.com", "example.net", "example.org", ".test", ".example",
    ".home.arpa", ".arpa", ".local", ".lan", ".invalid", ".localhost",
    "ghcr.io", "github.com", "githubusercontent.com", "docker.io", "docker.com",
    "argotunnel.com", "cloudflared.com", "telegram.org", "cymru.com",
    "debian.org", "raspberrypi.com", "raspberrypi.org", "pypi.org", "pythonhosted.org",
    # Raspberry Pi OS's pip index; Grafana's update check and usage stats.
    "piwheels.org", "grafana.com", "grafana.org",
    "whoami.akamai.net", "myaddr.l.google.com",
)

# How long the house's queries take to resolve (AdGuard's "Elapsed", in
# nanoseconds), kept as a histogram per 5-minute bucket and per path -- the
# cache, or the upstream that answered -- so a busy house costs a few
# kilobytes of state and the percentiles are exact to a bin. Bins are
# log-spaced: 20 % wide from 10 us to 10 s, which is finer than anything a
# person can feel and coarse enough to stay small.
RES_BUCKET = 300
RES_KEEP = 6 * 3600       # buckets kept in the state file
# Complete buckets the snapshot carries: all the state keeps. AdGuard
# writes its log in batches (querylog.size_memory, 1000 queries), so on a
# quiet network a bucket's queries reach this pass hours after it closed;
# the exporter rewrites the bucket's point as they arrive. A two-hour
# window dropped such buckets (seen on a quiet test Pi).
RES_PUBLISH = RES_KEEP
RES_BIN0_MS = 0.01
RES_RATIO = 1.2
RES_BINS = 77             # RES_BIN0_MS * RES_RATIO**76 ~ 10 s; slower goes in the last bin
RES_QUANTILES = (10, 25, 50, 75, 90, 99)

# "The last hour" is the last 12 five-minute buckets (55 to 60 minutes), not
# the clock hour: the clock hour dropped to zero at every hour boundary.
# Per-service counts per bucket are kept for two hours.
RECENT_KEEP = 2 * 3600
LAST_HOUR_BUCKETS = 12
# The queries AdGuard still holds in memory (querylog.size_memory, 1000 by
# default) come from its API; the log file has only what it flushed.
UNFLUSHED_LIMIT = 1000


def last_hour_start(now: float) -> int:
    """The first of the last LAST_HOUR_BUCKETS 5-minute buckets, the current
    (partial) one included."""
    return (int(now // RES_BUCKET) - LAST_HOUR_BUCKETS + 1) * RES_BUCKET


def res_bin(ms: float) -> int:
    if ms <= RES_BIN0_MS:
        return 0
    return min(RES_BINS - 1, int(math.log(ms / RES_BIN0_MS) / math.log(RES_RATIO)))


def res_value(b: int) -> float:
    """A bin's value in ms: its geometric center."""
    return RES_BIN0_MS * RES_RATIO ** (b + 0.5)


def res_quantiles(hist: dict[str, int]) -> dict:
    """``count`` and ``p10`` ... ``p99`` (ms) from a sparse histogram."""
    bins = sorted((int(b), n) for b, n in hist.items() if n > 0)
    total = sum(n for _, n in bins)
    out: dict = {"count": total}
    if not total:
        return out
    for q in RES_QUANTILES:
        rank, seen = q / 100 * total, 0
        for b, n in bins:
            seen += n
            if seen >= rank:
                out[f"p{q}"] = round(res_value(b), 3)
                break
    return out


def res_path(entry: dict) -> str:
    """``cache`` for an answer from AdGuard's cache, else the host of the
    upstream that answered (``local`` when AdGuard answered by itself)."""
    if entry.get("Cached"):
        return "cache"
    upstream = str(entry.get("Upstream") or "")
    # "host:port", "[v6]:port" and a bare v6 address have no scheme; "//"
    # makes urlsplit read them as an authority, so one resolver gets one
    # name whichever way it was written.
    if "://" not in upstream and not upstream.startswith("[") and upstream.count(":") >= 2:
        return upstream.lower()  # a bare IPv6 address: its colons are not a port
    try:
        host = urlsplit(upstream if "://" in upstream else "//" + upstream).hostname
    except ValueError:
        host = None
    return host or "local"


_psl = PublicSuffixList()
_psl_icann = PublicSuffixList(only_icann=True)
# Unknown TLDs are not suffixes here: "config-manager" or "nas.internal" is
# not an Internet service, whatever the default list's "*" rule says.
_psl_known = PublicSuffixList(accept_unknown=False)
_HEX_OR_LONG = re.compile(r"^(?=.*\d)[a-z0-9-]{20,}$|^[0-9a-f]{12,}$")


# Real top-level domains only (com, uk, app), not any unknown label.
_psl_tld = PublicSuffixList(only_icann=True, accept_unknown=False)


# Edge networks whose leftmost labels name a customer, a distribution or a
# load balancer, not a service of its own: every one reaches the same edge,
# so measuring one measures them all. Counted separately they crowd the
# ranking: on the reference Pi, three days of lookups made 84 googleapis.com
# hosts, 52 CloudFront distributions, 38 Fastly customers and 47 Akamai
# edges into "services", and four googleapis.com hosts were adopted as 20
# targets. The value is how many labels in front of the suffix still name
# the service: AWS load balancers keep their region (us-east-1 and
# us-west-2 are different places). Sites under a shared suffix (github.io,
# myshopify.com, a.run.app) stay sites of their own.
EDGE_SUFFIXES = {
    "cloudfront.net": 0, "fastly.net": 0, "fastly-edge.com": 0,
    "akamai.net": 0, "akamaiedge.net": 0, "akamaihd.net": 0, "akamaized.net": 0,
    "edgekey.net": 0, "edgesuite.net": 0, "akadns.net": 0,
    "azureedge.net": 0, "azurefd.net": 0, "trafficmanager.net": 0,
    "awsglobalaccelerator.com": 0, "googleapis.com": 0,
    "elb.amazonaws.com": 1,
}


# The newer AWS load balancer form puts the region after "elb"
# (name.elb.eu-west-2.amazonaws.com): the list alone grouped it by region
# only where the region happens to be a public suffix (us-east-1), and
# into all of amazonaws.com elsewhere.
_ELB_REGIONAL = re.compile(r"(?:^|\.)(elb\.[a-z0-9-]+\.amazonaws\.com)$")


def edge_service(name: str) -> str | None:
    """The service of an edge network's endpoint (``EDGE_SUFFIXES``), or
    None for any other name."""
    m = _ELB_REGIONAL.search(name)
    if m:
        return m.group(1)
    for suffix, keep in EDGE_SUFFIXES.items():
        if name == suffix or name.endswith("." + suffix):
            front = name[: -len(suffix)].split(".")[:-1]
            if keep and len(front) >= keep:
                return ".".join(front[-keep:] + [suffix])
            return suffix
    return None


def service_of(name: str) -> str:
    """The registrable domain, with private suffixes (user.github.io is a
    site of its own), unless it is a CDN endpoint that embeds a customer's
    domain in front of the CDN's private suffix (x.com.cdn.cloudflare.net,
    x.com.akadns.net): the label there is a real TLD with a name before it,
    and the service is the CDN's (cloudflare.net), from the ICANN rules.
    A site whose own name is a TLD (www.io.github.io) is taken for one too.
    An edge network's endpoint is the edge network's (``EDGE_SUFFIXES``)."""
    edge = edge_service(name)
    if edge:
        return edge
    service = _psl.privatesuffix(name) or name
    first = service.split(".", 1)[0]
    if service != name and _psl_tld.publicsuffix(first) == first:
        return _psl_icann.privatesuffix(name) or service
    return service


@lru_cache(maxsize=65536)
def is_public(name: str) -> bool:
    """Whether ``name`` sits under a suffix the public list knows. A bare
    name (one of the Pi's containers: config-manager) or a private TLD
    (.internal, .lan) is local traffic, never a service to measure."""
    return _psl_known.publicsuffix(name) is not None


_TARGET_HOST = re.compile(r"^[ \t]*host[ \t]*=[ \t]*(\S+)", re.M)


def measured_hosts(path: str | None) -> frozenset[str]:
    """The names SmokePing measures, from the Targets file it reads.

    Each one reaches this log the way the house's names do (the Pi resolves
    through the router), about once per TTL, so a measured name looks
    present every hour: without this the wizard ranks the targets it just
    adopted. And the Pi keeps the router's cache of those names warm, so the
    house's own lookups of them rarely get here at all: leaving them out
    loses little. Addresses and MultiHost paths (/Group/Target) are not
    names. A missing file (no SmokePing yet) is an empty set."""
    if not path:
        return frozenset()
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError:
        return frozenset()
    names = set()
    for m in _TARGET_HOST.finditer(text):
        name = m.group(1).rstrip(".").lower()
        if name.startswith("/"):
            continue
        try:
            ipaddress.ip_address(name)
        except ValueError:
            names.add(name)
    return frozenset(names)


def cdn_of(name: str, cnames: list[str]) -> str:
    end = cnames[-1] if cnames else name
    return _psl_icann.privatesuffix(end) or end


def looks_random(name: str) -> bool:
    """A generated leftmost label (hash, pod id): not a stable endpoint."""
    label = name.split(".", 1)[0]
    if _HEX_OR_LONG.match(label):
        return True
    chars = label.replace("-", "")
    digits = sum(c.isdigit() for c in chars)
    return len(chars) >= 12 and digits >= 3 and len(chars) - digits >= 3


def is_own(name: str, service: str, patterns: tuple[str, ...]) -> bool:
    if not is_public(name):
        return True
    for p in patterns:
        if p.startswith("."):
            if name.endswith(p) or name == p[1:]:
                return True
        elif service == p or name == p or name.endswith("." + p):
            return True
    return False


def parse_time(value: str) -> float:
    from datetime import datetime

    value = re.sub(r"\.(\d{1,6})\d*", r".\1", value.replace("Z", "+00:00"))
    return datetime.fromisoformat(value).timestamp()


def parse_answer(raw: str | None) -> tuple[list[str], str | None]:
    """The CNAME chain and the first address of a stored answer."""
    if not raw:
        return [], None
    try:
        msg = dns.message.from_wire(base64.b64decode(raw))
    except (binascii.Error, dns.exception.DNSException, ValueError):
        return [], None
    cnames: list[str] = []
    addr = None
    for rrset in msg.answer:
        for rd in rrset:
            if rrset.rdtype == dns.rdatatype.CNAME:
                cnames.append(rd.target.to_text().rstrip(".").lower())
            elif addr is None and rrset.rdtype in (dns.rdatatype.A, dns.rdatatype.AAAA):
                addr = rd.address
    return cnames, addr


# -- diversity -------------------------------------------------------------------


def diversity(values: list[float], ks: tuple[int, ...] = KS) -> dict:
    """Richness, HHI and its effective number (1/HHI), Shannon's effective
    number (exp H), and the share covered by the top-K."""
    total = sum(values)
    if total <= 0:
        return {"richness": 0, "hhi": None, "effective_hhi": 0.0,
                "effective_shannon": 0.0, **{f"cov{k}": None for k in ks}}
    shares = sorted((v / total for v in values if v > 0), reverse=True)
    hhi = sum(s * s for s in shares)
    shannon = -sum(s * math.log(s) for s in shares)
    return {
        "richness": len(shares),
        "hhi": hhi,
        "effective_hhi": 1.0 / hhi,
        "effective_shannon": math.exp(shannon),
        **{f"cov{k}": sum(shares[:k]) for k in ks},
    }


def select(candidates: list[dict], *, coverage: float, floor: float, max_k: int) -> list[dict]:
    """Which services to measure: K comes from the data, not a constant.

    ``candidates`` are services with a ``score`` (presence or volume), their
    ``cdn`` and ``asn``, in descending score order, each with a stable
    ``host`` to measure (the caller leaves out services without one).

    1. Coverage: take services in order until they cover ``coverage`` of the
       total score (0.8: the services behind 80% of the house's activity).
    2. Diversity: for every network (AS) and every CDN holding at least
       ``floor`` of the score that step 1 left out, add its best service.
       Grouping and cutting hide small-but-real paths; this puts them back.
    3. Budget: at most ``max_k``. The coverage core is shortened from its
       tail and the diversity picks recomputed against what is left: a cut
       never loses a network or CDN above the floor while room remains.

    Returns the picks in score order, each with ``reason``: "coverage",
    "network <ASN>" or "cdn <domain>".
    """
    total = sum(c["score"] for c in candidates)
    if total <= 0:
        return []
    core: list[dict] = []
    acc = 0.0
    for c in candidates:
        if acc >= coverage * total:
            break
        core.append({**c, "reason": "coverage"})
        acc += c["score"]

    shares: dict[str, dict[str, float]] = {"asn": {}, "cdn": {}}
    for c in candidates:
        for level in shares:
            if c.get(level):
                shares[level][c[level]] = shares[level].get(c[level], 0.0) + c["score"] / total

    def diversity_for(kept: list[dict]) -> list[dict]:
        extra: list[dict] = []
        for level in ("asn", "cdn"):
            picked = {p["service"] for p in kept + extra}
            have = {c.get(level) for c in kept + extra}
            for unit in sorted(u for u, sh in shares[level].items() if sh >= floor and u not in have):
                best = next(c for c in candidates if c.get(level) == unit)
                if best["service"] not in picked:
                    label = "network" if level == "asn" else "cdn"
                    extra.append({**best, "reason": f"{label} {unit}"})
                    picked.add(best["service"])
        return extra

    # Over budget: shorten the coverage core from its tail and recompute the
    # diversity picks against what is left, so a rare network that entered by
    # coverage and was cut comes back as a diversity pick.
    kept = core
    extra = diversity_for(kept)
    while kept and len(kept) + len(extra) > max_k:
        kept = kept[:-1]
        extra = diversity_for(kept)
    chosen = (kept + extra)[:max_k]
    order = {c["service"]: i for i, c in enumerate(candidates)}
    return sorted(chosen, key=lambda c: order[c["service"]])


def jaccard(a: set[str], b: set[str]) -> float | None:
    union = a | b
    return len(a & b) / len(union) if union else None


# -- origin AS ---------------------------------------------------------------------


class Owners:
    """Origin AS per /24 (IPv4) or /48 (IPv6), from Team Cymru, cached."""

    def __init__(self, cache: dict | None = None, nameserver: str = "1.1.1.1") -> None:
        self.cache: dict[str, list[str]] = cache or {}
        self._res = dns.resolver.Resolver(configure=False)
        self._res.nameservers = [nameserver]
        self._res.lifetime = 3.0
        self._names: dict[str, str] = {}

    @staticmethod
    def prefix(addr: str) -> str:
        ip = ipaddress.ip_address(addr)
        return str(ipaddress.ip_network(f"{addr}/{24 if ip.version == 4 else 48}", strict=False))

    def _txt(self, qname: str) -> str | None:
        try:
            ans = self._res.resolve(qname, "TXT")
        except (dns.exception.DNSException, OSError):
            return None
        return b"".join(ans[0].strings).decode(errors="replace")

    def _lookup(self, net: str) -> list[str] | None:
        n = ipaddress.ip_network(net)
        a = n.network_address
        if n.version == 4:
            qname = ".".join(reversed(str(a).split(".")[:3])) + ".origin.asn.cymru.com"
        else:
            qname = ".".join(reversed(a.exploded.replace(":", "")[:12])) + ".origin6.asn.cymru.com"
        txt = self._txt(qname)
        if not txt:
            return None  # not cached: tried again next pass
        asn = "AS" + txt.split("|")[0].strip().split()[0]
        if asn not in self._names:
            parts = [p.strip() for p in (self._txt(f"{asn}.asn.cymru.com") or "").split("|")]
            self._names[asn] = parts[-1].rsplit(",", 1)[0].strip() if parts[-1] else asn
        return [asn, self._names[asn]]

    def resolve(self, addrs: set[str], budget: int = 200) -> None:
        todo = sorted({self.prefix(a) for a in addrs} - self.cache.keys())[:budget]
        if not todo:
            return
        with ThreadPoolExecutor(8) as pool:
            for net, owner in zip(todo, pool.map(self._lookup, todo), strict=True):
                if owner:
                    self.cache[net] = owner

    def owner(self, addr: str | None) -> list[str] | None:
        return self.cache.get(self.prefix(addr)) if addr else None


# -- the incremental summary ---------------------------------------------------------


@dataclass
class State:
    """What survives between passes (``wizard-state.json``)."""

    inode: int | None = None
    offset: int = 0
    # hour (epoch // 3600) -> service -> [queries, uncached]
    hours: dict[str, dict[str, list[int]]] = field(default_factory=dict)
    # hour -> [own queries, all kept-type queries]
    own: dict[str, list[int]] = field(default_factory=dict)
    # service -> {"cdn", "host", "addr", "hosts": {fqdn: count}}
    meta: dict[str, dict] = field(default_factory=dict)
    owners: dict[str, list[str]] = field(default_factory=dict)
    # day (YYYY-MM-DD, local) -> the top-10 services at that day's last pass
    top_by_day: dict[str, list[str]] = field(default_factory=dict)
    # 5-minute bucket start (epoch) -> path -> {bin: count}: resolution times
    res: dict[str, dict[str, dict[str, int]]] = field(default_factory=dict)
    # 5-minute bucket start (epoch) -> service -> queries, for the last hour
    recent: dict[str, dict[str, int]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str) -> State:
        try:
            with open(path) as fh:
                raw = json.load(fh)
            state = cls(**{k: raw[k] for k in cls.__dataclass_fields__ if k in raw})
        except (OSError, ValueError, TypeError):
            return cls()
        # A rewrite of persisted data must never make a state worse than it
        # was: on a shape it does not expect, the state stays as saved.
        try:
            renamed = cls(**json.loads(json.dumps(state.__dict__)))
            renamed.rename_services()
        except (AttributeError, TypeError, ValueError):
            return state
        return renamed

    def rename_services(self) -> None:
        """Merge every service counted under a name ``service_of`` no longer
        gives into today's name.

        A state saved before 2.15.5 counted dynamic.x.com.cdn.cloudflare.net
        as "com.cdn.cloudflare.net"; its hours kept ranking under that name,
        beside the new "cloudflare.net", until they aged out of the window
        three days later. The key alone cannot say which it was
        (play.googleapis.com is a name of its own when looked up directly),
        so today's name comes from the hosts it kept (its 20 busiest,
        random-looking labels never stored): renamed only when they all
        agree on one other name. A key with none kept, or none in ``meta``,
        stays until it ages out. Counts add up, hosts merge, a day's top
        ten keeps its order without the duplicate (so the first churn
        after an upgrade compares like names)."""
        renames = {}
        for svc, meta in self.meta.items():
            now = {service_of(h) for h in meta.get("hosts", {})}
            if len(now) == 1 and svc not in now:
                renames[svc] = now.pop()
        # A -> B where B is renamed too ends where B does, whatever the
        # order the tables are walked in; a cycle renames nothing.
        for svc in list(renames):
            seen, new = {svc}, renames[svc]
            while new in renames and new not in seen:
                seen.add(new)
                new = renames[new]
            if new in seen:
                del renames[svc]
            else:
                renames[svc] = new
        if not renames:
            return
        for table in (*self.hours.values(), *self.recent.values()):
            for old in [s for s in table if s in renames]:
                count, new = table.pop(old), renames[old]
                if isinstance(count, list):
                    have = table.get(new, [0] * len(count))
                    table[new] = [a + b for a, b in zip(have, count)]
                else:
                    table[new] = table.get(new, 0) + count
        for old, new in renames.items():
            meta = self.meta.pop(old)
            into = self.meta.setdefault(new, {"hosts": {}})
            hosts = into.setdefault("hosts", {})
            for host, n in meta.get("hosts", {}).items():
                hosts[host] = hosts.get(host, 0) + n
            for key, value in meta.items():
                into.setdefault(key, value)
        for day, top in self.top_by_day.items():
            names = [renames.get(s, s) for s in top]
            self.top_by_day[day] = list(dict.fromkeys(names))

    def save(self, path: str) -> None:
        tmp = f"{path}.tmp"
        with open(tmp, "w") as fh:
            json.dump(self.__dict__, fh)
        os.replace(tmp, path)


class Wizard:
    def __init__(self, log_dir: str, state_dir: str, *, canary_domain: str,
                 extra_own: tuple[str, ...] = (), top: int = 25,
                 owners: Owners | None = None, score: str = "auto",
                 coverage: float = 0.8, floor: float = 0.005, max_k: int = 60,
                 targets_path: str | None = None) -> None:
        self.log_path = os.path.join(log_dir, "querylog.json")
        self.state_path = os.path.join(state_dir, "wizard-state.json")
        self.out_path = os.path.join(state_dir, "wizard.json")
        self.own = OWN_TRAFFIC + ("." + canary_domain,) + extra_own
        self.targets_path = targets_path
        self.measured: frozenset[str] = frozenset()
        self.top = top
        self.score, self.coverage, self.floor, self.max_k = score, coverage, floor, max_k
        self.state = State.load(self.state_path)
        self.res_too_late = 0
        self.now = time.time()
        self.owners = owners or Owners(self.state.owners)

    # -- reading ---------------------------------------------------------------

    CHUNK = 8 << 20  # a week of log can be hundreds of MB: never all at once

    def _read_from(self, path: str, offset: int):
        """Batches of complete lines from ``offset``, each with the bytes it
        spans (never past a partial last line AdGuard may still be writing)."""
        with open(path, "rb") as fh:
            fh.seek(offset)
            rest = b""
            while True:
                data = fh.read(self.CHUNK)
                if not data:
                    return
                data = rest + data
                end = data.rfind(b"\n") + 1
                rest = data[end:]
                yield data[:end].decode(errors="replace").splitlines(), end

    def ingest_new(self) -> int:
        """Count what was appended since the last pass, across AdGuard's
        rotation (querylog.json -> querylog.json.1). Returns queries kept."""
        try:
            inode = os.stat(self.log_path).st_ino
        except OSError:
            return 0
        st = self.state
        kept = 0
        if st.inode is not None and inode != st.inode:
            rotated = self.log_path + ".1"
            try:
                if os.stat(rotated).st_ino == st.inode:
                    for lines, _ in self._read_from(rotated, st.offset):
                        kept += self.ingest(lines)
                else:
                    log.info("DNS wizard: the log rotated more than once since the last "
                             "pass; what was appended to the older file is not counted")
            except OSError:
                log.info("DNS wizard: the rotated log is gone; its unread tail is not counted")
            st.offset = 0
        if st.inode == inode and os.path.getsize(self.log_path) < st.offset:
            st.offset = 0  # truncated
        st.inode = inode
        for lines, consumed in self._read_from(self.log_path, st.offset):
            kept += self.ingest(lines)
            st.offset += consumed
        return kept

    def ingest(self, lines: list[str]) -> int:
        """Count lines into their hour; returns how many were kept."""
        kept = 0
        st = self.state
        for line in lines:
            try:
                e = json.loads(line)
                ts = parse_time(e["T"])
                name = str(e["QH"]).rstrip(".").lower()
            except (ValueError, KeyError, TypeError):
                continue
            if str(e.get("QT", "")) not in KEEP_QTYPES:
                continue
            hour = str(int(ts // 3600))
            svc = service_of(name)
            own = st.own.setdefault(hour, [0, 0])
            own[1] += 1
            if name in self.measured or is_own(name, svc, self.own):
                own[0] += 1
                continue
            counts = st.hours.setdefault(hour, {}).setdefault(svc, [0, 0])
            counts[0] += 1
            if ts >= self.now - RECENT_KEEP:
                per = st.recent.setdefault(str(int(ts // RES_BUCKET) * RES_BUCKET), {})
                per[svc] = per.get(svc, 0) + 1
            elapsed = e.get("Elapsed")
            if (isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool)
                    and math.isfinite(elapsed) and elapsed > 0
                    and ts < self.now - RES_KEEP):
                # Older than the buckets kept: prune would drop it at once.
                self.res_too_late += 1
            elif (isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool)
                    and math.isfinite(elapsed) and elapsed > 0):
                bucket = str(int(ts // RES_BUCKET) * RES_BUCKET)
                hist = st.res.setdefault(bucket, {}).setdefault(res_path(e), {})
                b = str(res_bin(elapsed / 1e6))
                hist[b] = hist.get(b, 0) + 1
            if not e.get("Cached", False):
                counts[1] += 1
            kept += 1
            meta = st.meta.setdefault(svc, {"hosts": {}})
            if not looks_random(name):
                meta["hosts"][name] = meta["hosts"].get(name, 0) + 1
            # One answer parse per service and hour is enough to know who
            # serves it; parsing every answer would dominate the pass.
            if meta.get("seen_hour") != hour:
                cnames, addr = parse_answer(e.get("Answer"))
                if addr:
                    meta["cdn"] = cdn_of(name, cnames)
                    meta["addr"] = addr
                    meta["seen_hour"] = hour
        return kept

    def prune(self, now: float) -> None:
        oldest = int(now // 3600) - WINDOW_HOURS
        st = self.state
        for table in (st.hours, st.own):
            for hour in [h for h in table if int(h) < oldest]:
                del table[hour]
        live = {svc for per in st.hours.values() for svc in per}
        for svc in [s for s in st.meta if s not in live]:
            del st.meta[svc]
        for svc, meta in st.meta.items():
            hosts = meta.get("hosts", {})
            if len(hosts) > 20:  # keep the busiest names only
                meta["hosts"] = dict(sorted(hosts.items(), key=lambda kv: -kv[1])[:20])
        for day in sorted(st.top_by_day)[:-8]:
            del st.top_by_day[day]
        for bucket in [b for b in st.res if int(b) < now - RES_KEEP]:
            del st.res[bucket]
        for bucket in [b for b in st.recent if int(b) < now - RECENT_KEEP]:
            del st.recent[bucket]
        # Network owners: only for the addresses still in use.
        prefixes = {Owners.prefix(m["addr"]) for m in st.meta.values() if m.get("addr")}
        for net in [n for n in self.owners.cache if n not in prefixes]:
            del self.owners.cache[net]

    def last_logged(self) -> float | None:
        """The time of the log's last complete line: AdGuard flushed every
        query up to it. The rotated file's, when the current one is empty."""
        for path in (self.log_path, self.log_path + ".1"):
            try:
                with open(path, "rb") as fh:
                    size = fh.seek(0, os.SEEK_END)
                    fh.seek(max(0, size - 65536))
                    lines = fh.read().splitlines()
            except OSError:
                continue
            for raw in reversed(lines):
                try:
                    return parse_time(json.loads(raw)["T"])
                except (ValueError, KeyError, TypeError):
                    continue  # a line AdGuard is still writing
        return None

    def unflushed(self, entries: list[dict] | None, now: float) -> dict[str, int]:
        """Per service, the house's queries of the last hour that AdGuard
        holds in memory and has not written to the log yet: its API's
        entries newer than the log's last line. Counted in the snapshot
        only, never in the state, so once written they are not counted twice.
        """
        if not entries:
            return {}
        last = self.last_logged()
        since = last_hour_start(now)
        out: dict[str, int] = {}
        for e in entries:
            try:
                ts = parse_time(e["time"])
                q = e["question"]
                name = str(q["name"]).rstrip(".").lower()
            except (ValueError, KeyError, TypeError):
                continue
            if (last is not None and ts <= last) or ts < since:
                continue
            if str(q.get("type", "")) not in KEEP_QTYPES:
                continue
            svc = service_of(name)
            if name in self.measured or is_own(name, svc, self.own):
                continue
            out[svc] = out.get(svc, 0) + 1
        return out

    # -- the snapshot ------------------------------------------------------------

    def snapshot(self, now: float, unflushed: dict[str, int] | None = None) -> dict:
        st = self.state
        now_h = int(now // 3600)
        presence: dict[str, int] = {}
        q24: dict[str, int] = {}
        q1: dict[str, int] = {}
        q7: dict[str, int] = {}
        for hour, per in st.hours.items():
            age = now_h - int(hour)
            for svc, (queries, _uncached) in per.items():
                if not is_public(svc):  # counted before is_own knew
                    continue
                presence[svc] = presence.get(svc, 0) + 1
                q7[svc] = q7.get(svc, 0) + queries
                if age < 24:
                    q24[svc] = q24.get(svc, 0) + queries
        since = last_hour_start(now)
        for bucket, per in st.recent.items():
            if int(bucket) >= since:
                for svc, queries in per.items():
                    if is_public(svc):
                        q1[svc] = q1.get(svc, 0) + queries
        # Not in the 7-day volume or presence: those select targets, and a
        # service seen only in memory has no host to measure yet.
        for svc, queries in (unflushed or {}).items():
            if is_public(svc):
                for table in (q1, q24):
                    table[svc] = table.get(svc, 0) + queries
        ranked = sorted(presence, key=lambda s: (-presence[s], -q24.get(s, 0), s))

        self.owners.resolve({m["addr"] for m in st.meta.values() if m.get("addr")})
        st.owners = self.owners.cache

        def owner(svc: str) -> list[str]:
            return self.owners.owner(st.meta.get(svc, {}).get("addr")) or ["", ""]

        def roll(key) -> dict[str, float]:
            out: dict[str, float] = {}
            for svc, p in presence.items():
                k = key(svc)
                if k:
                    out[k] = out.get(k, 0) + p
            return out

        cdn = roll(lambda s: st.meta.get(s, {}).get("cdn"))
        asn = roll(lambda s: owner(s)[0])
        top10 = ranked[:10]
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        st.top_by_day[today] = top10
        days = sorted(st.top_by_day)
        yesterday = st.top_by_day[days[-2]] if len(days) >= 2 else None

        own24 = [v for h, v in st.own.items() if now_h - int(h) < 24]
        own_q, all_q = sum(v[0] for v in own24), sum(v[1] for v in own24)
        hours_with_data = len(st.hours)

        top = []
        for rank, svc in enumerate(ranked[: self.top], start=1):
            meta = st.meta.get(svc, {})
            hosts = meta.get("hosts") or {}
            asn_, org = owner(svc)
            top.append({
                "rank": rank, "service": svc, "presence_h": presence[svc],
                "queries_24h": q24.get(svc, 0), "queries_1h": q1.get(svc, 0),
                "host": max(hosts, key=hosts.get) if hosts else "",
                "cdn": meta.get("cdn", ""), "asn": asn_, "org": org,
            })
        behind = {
            "cdn": len({st.meta.get(s, {}).get("cdn") for s in top10} - {None}),
            "asn": len({owner(s)[0] for s in top10} - {""}),
        }
        # What to measure. Presence ranks best once there is enough of it;
        # with a few hours every service ties, so volume ranks until then.
        score_name = self.score
        if score_name == "auto":
            score_name = "presence" if hours_with_data >= PRESENCE_AFTER_HOURS else "queries"
        weight = presence if score_name == "presence" else q7
        candidates = []
        skipped_no_host = 0
        for svc in sorted(weight, key=lambda s: (-weight[s], -q7.get(s, 0), s)):
            hosts = st.meta.get(svc, {}).get("hosts") or {}
            if not hosts:
                skipped_no_host += 1  # only generated names: nothing stable to measure
                continue
            candidates.append({
                "service": svc, "score": float(weight[svc]),
                "host": max(hosts, key=hosts.get),
                "cdn": st.meta.get(svc, {}).get("cdn", ""), "asn": owner(svc)[0],
            })
        picks = select(candidates, coverage=self.coverage, floor=self.floor, max_k=self.max_k)
        total_w = sum(c["score"] for c in candidates) or 1.0
        selection = {
            "score": score_name,
            "coverage_target": self.coverage,
            "diversity_floor": self.floor,
            "max": self.max_k,
            "k": len(picks),
            "covered": sum(p["score"] for p in picks) / total_w,
            "networks": len({p["asn"] for p in picks} - {""}),
            "networks_total": len({c["asn"] for c in candidates} - {""}),
            "skipped_no_host": skipped_no_host,
            "services": [{k: p[k] for k in ("service", "host", "cdn", "asn", "reason")} for p in picks],
        }

        return {
            "generated": now,
            "window_hours": WINDOW_HOURS,
            "hours_with_data": hours_with_data,
            "queries_24h": sum(q24.values()),
            "queries_1h": sum(q1.values()),
            "own_share_24h": own_q / all_q if all_q else None,
            "diversity": {
                "service": diversity(list(map(float, presence.values()))),
                "service_volume": diversity(list(map(float, q24.values()))),
                "cdn": diversity(list(cdn.values())),
                "asn": diversity(list(asn.values())),
            },
            "behind_top10": behind,
            "churn": {"top10_jaccard_vs_yesterday":
                      jaccard(set(top10), set(yesterday)) if yesterday is not None else None},
            "top": top,
            "selection": selection,
            "resolution": self.resolution(now),
            # Today's service of every name SmokePing measures: adoption
            # matches its targets by it, so a target adopted under a name
            # the rules have since changed (or one of several an edge
            # network's endpoints were split into) is still recognized.
            "measured_services": {h: service_of(h) for h in sorted(self.measured)},
        }

    def resolution(self, now: float) -> list[dict]:
        """The complete 5-minute buckets the state keeps (six hours): per path
        (``cache``, each upstream, ``local`` for AdGuard's own answers) and
        for every upstream together (``upstreams``, neither cache nor local),
        how many queries and their p10 ... p99 in ms. The bucket still
        filling is left out: its percentiles would move under the reader."""
        current = int(now // RES_BUCKET) * RES_BUCKET
        out = []
        for bucket in sorted(self.state.res, key=int):
            start = int(bucket)
            if start >= current or start < now - RES_PUBLISH:
                continue
            per = self.state.res[bucket]
            upstream_all: dict[str, int] = {}
            for path, hist in sorted(per.items()):
                out.append({"t": start, "path": path, **res_quantiles(hist)})
                # AdGuard's own answers (blocked, rewritten: "local") take
                # microseconds; with the upstreams they would pass for fast
                # resolvers.
                if path not in ("cache", "local"):
                    for b, n in hist.items():
                        upstream_all[b] = upstream_all.get(b, 0) + n
            if upstream_all:
                out.append({"t": start, "path": "upstreams", **res_quantiles(upstream_all)})
        return out

    def run_once(self, now: float | None = None,
                 unflushed: list[dict] | None = None) -> dict:
        """One pass. ``unflushed`` is AdGuard's API query log (newest
        first), for the queries it has not written to the file yet."""
        now = now or time.time()
        self.now = now
        self.measured = measured_hosts(self.targets_path)  # adoption adds to it
        self.res_too_late = 0
        kept = self.ingest_new()
        if self.res_too_late:
            log.info("DNS wizard: %d query times reached the log more than %d h late; "
                     "not in the resolution times", self.res_too_late, RES_KEEP // 3600)
        self.prune(now)
        snap = self.snapshot(now, self.unflushed(unflushed, now))
        self.state.save(self.state_path)
        tmp = f"{self.out_path}.tmp"
        with open(tmp, "w") as fh:
            json.dump(snap, fh, indent=1)
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.out_path)
        log.debug("wizard: %d new queries kept; %d services", kept, len(snap["top"]))
        return snap
