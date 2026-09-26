"""Aggregation levels: what one "unit" of the house's DNS activity is.

Each level coalesces more than the one before, and each can hide diversity
the finer one shows:

``fqdn``
    The name as asked (``nrdp.logs.netflix.com``).
``service``
    Its registrable domain, eTLD+1 by the Public Suffix List (``netflix.com``).
``cdn``
    The registrable domain at the *end* of the CNAME chain, i.e. who serves
    it (``www.example.com`` -> ``...cloudfront.net`` -> ``cloudfront.net``);
    the service itself when there is no CNAME.
``asn``
    The origin AS of the first address in the answer (``AS2906``).
``org``
    That AS's registered name (``NETFLIX-ASN``), which folds sibling ASes.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache

import dns.exception
import dns.resolver
from publicsuffixlist import PublicSuffixList

from dns_explore.logread import Query

logger = logging.getLogger(__name__)

LEVELS = ("fqdn", "service", "cdn", "asn", "org")

# The Pi's own traffic reaches the observer the same way the house's does
# (the Pi resolves through the router, which forwards here), and so do
# SmokePing's lookups of its own targets. Matched against the service
# (eTLD+1) or, for entries with a dot-prefix, as a name suffix.
DEFAULT_EXCLUDE = (
    # The observer's own canary and self-test names, local names, and the
    # names reserved for documentation and tests (RFC 2606), which only
    # tests ask.
    "example.com",
    "example.net",
    "example.org",
    ".test",
    ".example",
    ".home.arpa",
    ".arpa",
    ".local",
    ".lan",
    ".invalid",
    ".localhost",
    # Smoking Pi itself: images, releases, tunnels, alerts, lookups.
    "ghcr.io",
    "github.com",
    "githubusercontent.com",
    "docker.io",
    "docker.com",
    "argotunnel.com",
    "cloudflared.com",
    "telegram.org",
    "cymru.com",
    "debian.org",
    "raspberrypi.com",
    "raspberrypi.org",
    "pypi.org",
    "pythonhosted.org",
    # resolver_identity.py's probes.
    "whoami.akamai.net",
    "myaddr.l.google.com",
)

_psl = PublicSuffixList()
# ICANN suffixes only: the private section lists CDN and hosting suffixes
# (cloudfront.net, cdn.cloudflare.net), which would make every customer of
# a CDN its own "CDN".
_psl_icann = PublicSuffixList(only_icann=True)
_HEX_OR_LONG = re.compile(r"^(?=.*\d)[a-z0-9-]{20,}$|^[0-9a-f]{12,}$")


def service_of(name: str) -> str:
    """eTLD+1 of ``name`` (``name`` itself for a bare suffix or IP).

    Examples
    --------
    >>> service_of("nrdp.logs.netflix.com")
    'netflix.com'
    >>> service_of("www.bbc.co.uk")
    'bbc.co.uk'
    """
    return _psl.privatesuffix(name) or name


def looks_random(name: str) -> bool:
    """Whether the leftmost label looks generated (a hash or pod id), so the
    name cannot be a stable measurement endpoint."""
    label = name.split(".", 1)[0]
    if _HEX_OR_LONG.match(label):
        return True
    # Mixed ids such as CloudFront distributions (d3p8zr0ffa9t17): long,
    # with several digits and several letters.
    chars = label.replace("-", "")
    digits = sum(c.isdigit() for c in chars)
    return len(chars) >= 12 and digits >= 3 and len(chars) - digits >= 3


def cdn_of(q: Query) -> str:
    """Who serves it: the registrable domain (ICANN suffixes only) at the
    end of the CNAME chain, or of the name itself without one.

    Examples
    --------
    >>> from datetime import datetime, timezone
    >>> q = Query(datetime.now(timezone.utc), "www.example.org", "A", False, "NOERROR",
    ...           cnames=("d3p8zr0ffa9t17.cloudfront.net",))
    >>> cdn_of(q)
    'cloudfront.net'
    """
    name = q.cnames[-1] if q.cnames else q.qname
    return _psl_icann.privatesuffix(name) or name


def excluded(q: Query, patterns: Iterable[str]) -> bool:
    """Whether a query is the Pi's own (or local) traffic, by pattern."""
    svc = service_of(q.qname)
    for p in patterns:
        if p.startswith("."):
            if q.qname.endswith(p) or q.qname == p[1:]:
                return True
        elif svc == p or q.qname == p or q.qname.endswith("." + p):
            return True
    return False


@dataclass(frozen=True)
class Owner:
    asn: str
    name: str


class AsnLookup:
    """Origin AS of an address, from Team Cymru's DNS service.

    Asked of ``1.1.1.1`` directly, never the router: through the router the
    lookups would reach the observer and show up in the very log being
    analysed. One lookup per /24 (IPv4) or /48 (IPv6), cached.
    """

    def __init__(self, nameserver: str = "1.1.1.1", timeout: float = 3.0) -> None:
        self._res = dns.resolver.Resolver(configure=False)
        self._res.nameservers = [nameserver]
        self._res.lifetime = timeout
        self._prefix: dict[str, Owner] = {}
        self._names: dict[str, str] = {}

    @staticmethod
    def prefix(addr: str) -> str:
        ip = ipaddress.ip_address(addr)
        bits = 24 if ip.version == 4 else 48
        return str(ipaddress.ip_network(f"{addr}/{bits}", strict=False))

    def _txt(self, qname: str) -> str | None:
        try:
            ans = self._res.resolve(qname, "TXT")
        except (dns.exception.DNSException, OSError):
            return None
        return b"".join(ans[0].strings).decode(errors="replace")

    def _origin(self, net: str) -> Owner:
        n = ipaddress.ip_network(net)
        addr = n.network_address
        if n.version == 4:
            qname = ".".join(reversed(str(addr).split(".")[:3])) + ".origin.asn.cymru.com"
        else:
            nibbles = addr.exploded.replace(":", "")[:12]
            qname = ".".join(reversed(nibbles)) + ".origin6.asn.cymru.com"
        txt = self._txt(qname)
        if not txt:
            return Owner("AS?", "unknown")
        asn = "AS" + txt.split("|")[0].strip().split()[0]
        if asn not in self._names:
            name_txt = self._txt(f"{asn}.asn.cymru.com") or ""
            parts = [p.strip() for p in name_txt.split("|")]
            self._names[asn] = parts[-1].split(",")[0].split()[0] if parts[-1] else asn
        return Owner(asn, self._names[asn])

    def resolve_all(self, addrs: Iterable[str], workers: int = 16) -> None:
        """Look up every prefix not yet known, in parallel."""
        todo = sorted({self.prefix(a) for a in addrs} - self._prefix.keys())
        if not todo:
            return
        logger.info("looking up the origin AS of %d prefixes", len(todo))
        with ThreadPoolExecutor(workers) as pool:
            for net, owner in zip(todo, pool.map(self._origin, todo), strict=True):
                self._prefix[net] = owner

    def owner(self, addr: str) -> Owner:
        net = self.prefix(addr)
        if net not in self._prefix:
            self._prefix[net] = self._origin(net)
        return self._prefix[net]


def unit_fn(level: str, asn: AsnLookup | None = None) -> Callable[[Query], str | None]:
    """The function mapping a query to its unit at ``level``.

    At ``asn`` and ``org``, a query whose answer has no address (an HTTPS
    record, an empty AAAA) has no unit: ``None``, which the scores skip.
    """
    if level == "fqdn":
        return lambda q: q.qname
    if level == "service":
        return lambda q: _service(q.qname)
    if level == "cdn":
        return cdn_of
    if level in ("asn", "org"):
        if asn is None:
            raise ValueError(f"level {level!r} needs an AsnLookup")
        attr = "asn" if level == "asn" else "name"
        return lambda q: getattr(asn.owner(q.addrs[0]), attr) if q.addrs else None
    raise ValueError(f"unknown level {level!r}; choose from {', '.join(LEVELS)}")


@lru_cache(maxsize=65536)
def _service(name: str) -> str:
    return service_of(name)
