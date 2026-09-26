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
import time
from concurrent.futures import ThreadPoolExecutor
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
    "whoami.akamai.net", "myaddr.l.google.com",
)

_psl = PublicSuffixList()
_psl_icann = PublicSuffixList(only_icann=True)
_HEX_OR_LONG = re.compile(r"^(?=.*\d)[a-z0-9-]{20,}$|^[0-9a-f]{12,}$")


def service_of(name: str) -> str:
    return _psl.privatesuffix(name) or name


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

    @classmethod
    def load(cls, path: str) -> State:
        try:
            with open(path) as fh:
                raw = json.load(fh)
            return cls(**{k: raw[k] for k in cls.__dataclass_fields__ if k in raw})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: str) -> None:
        tmp = f"{path}.tmp"
        with open(tmp, "w") as fh:
            json.dump(self.__dict__, fh)
        os.replace(tmp, path)


class Wizard:
    def __init__(self, log_dir: str, state_dir: str, *, canary_domain: str,
                 extra_own: tuple[str, ...] = (), top: int = 25,
                 owners: Owners | None = None, score: str = "auto",
                 coverage: float = 0.8, floor: float = 0.005, max_k: int = 60) -> None:
        self.log_path = os.path.join(log_dir, "querylog.json")
        self.state_path = os.path.join(state_dir, "wizard-state.json")
        self.out_path = os.path.join(state_dir, "wizard.json")
        self.own = OWN_TRAFFIC + ("." + canary_domain,) + extra_own
        self.top = top
        self.score, self.coverage, self.floor, self.max_k = score, coverage, floor, max_k
        self.state = State.load(self.state_path)
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
            if is_own(name, svc, self.own):
                own[0] += 1
                continue
            counts = st.hours.setdefault(hour, {}).setdefault(svc, [0, 0])
            counts[0] += 1
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
        # Network owners: only for the addresses still in use.
        prefixes = {Owners.prefix(m["addr"]) for m in st.meta.values() if m.get("addr")}
        for net in [n for n in self.owners.cache if n not in prefixes]:
            del self.owners.cache[net]

    # -- the snapshot ------------------------------------------------------------

    def snapshot(self, now: float) -> dict:
        st = self.state
        now_h = int(now // 3600)
        presence: dict[str, int] = {}
        q24: dict[str, int] = {}
        q1: dict[str, int] = {}
        q7: dict[str, int] = {}
        for hour, per in st.hours.items():
            age = now_h - int(hour)
            for svc, (queries, _uncached) in per.items():
                presence[svc] = presence.get(svc, 0) + 1
                q7[svc] = q7.get(svc, 0) + queries
                if age < 24:
                    q24[svc] = q24.get(svc, 0) + queries
                if age < 1:
                    q1[svc] = q1.get(svc, 0) + queries
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
        }

    def run_once(self, now: float | None = None) -> dict:
        now = now or time.time()
        kept = self.ingest_new()
        self.prune(now)
        snap = self.snapshot(now)
        self.state.save(self.state_path)
        tmp = f"{self.out_path}.tmp"
        with open(tmp, "w") as fh:
            json.dump(snap, fh, indent=1)
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.out_path)
        log.debug("wizard: %d new queries kept; %d services", kept, len(snap["top"]))
        return snap
