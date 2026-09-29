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
    # Raspberry Pi OS's pip index; Grafana's update check and usage stats.
    "piwheels.org",
    "grafana.com",
    "grafana.org",
    # resolver_identity.py's probes.
    "whoami.akamai.net",
    "myaddr.l.google.com",
)

_psl = PublicSuffixList()
# ICANN suffixes only: the private section lists CDN and hosting suffixes
# (cloudfront.net, cdn.cloudflare.net), which would make every customer of
# a CDN its own "CDN".
_psl_icann = PublicSuffixList(only_icann=True)
# Unknown TLDs are not suffixes here: "config-manager" or "nas.internal" is
# not an Internet service, whatever the default list's "*" rule says.
_psl_known = PublicSuffixList(accept_unknown=False)
# Real top-level domains only (com, uk, app), not any unknown label.
_psl_tld = PublicSuffixList(only_icann=True, accept_unknown=False)
_HEX_OR_LONG = re.compile(r"^(?=.*\d)[a-z0-9-]{20,}$|^[0-9a-f]{12,}$")


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


def edge_service(name: str) -> str | None:
    """The service of an edge network's endpoint (``EDGE_SUFFIXES``), or
    None for any other name."""
    for suffix, keep in EDGE_SUFFIXES.items():
        if name == suffix or name.endswith("." + suffix):
            front = name[: -len(suffix)].split(".")[:-1]
            if keep and len(front) >= keep:
                return ".".join(front[-keep:] + [suffix])
            return suffix
    return None


def service_of(name: str) -> str:
    """eTLD+1 of ``name`` (``name`` itself for a bare suffix or IP).

    Examples
    --------
    >>> service_of("nrdp.logs.netflix.com")
    'netflix.com'
    >>> service_of("www.bbc.co.uk")
    'bbc.co.uk'
    >>> service_of("www.x.com.cdn.cloudflare.net")
    'cloudflare.net'

    A private suffix is a site of its own (user.github.io), unless a CDN
    endpoint embeds a customer's domain in front of it: then the service is
    the CDN. An edge network's endpoint (EDGE_SUFFIXES) is the edge
    network's. Same rule as the observer (dns-observer/wizard.py).
    """
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
    """Whether ``name`` sits under a suffix the public list knows.

    Examples
    --------
    >>> is_public("www.bbc.co.uk")
    True
    >>> is_public("github.io")  # a suffix itself is still public
    True
    >>> is_public("config-manager"), is_public("nas.internal")
    (False, False)
    """
    return _psl_known.publicsuffix(name) is not None


_TARGET_HOST = re.compile(r"^[ \t]*host[ \t]*=[ \t]*(\S+)", re.M)


def measured_hosts(text: str) -> frozenset[str]:
    r"""The names a SmokePing ``Targets`` file measures.

    SmokePing looks each of them up through the router about once per TTL,
    so they reach the observer's log all day and all night. They are the
    Pi's own lookups, whatever the house does (the DNS observer's wizard
    leaves them out the same way). Addresses and MultiHost paths are not
    names.

    Examples
    --------
    >>> sorted(measured_hosts("host = www.bbc.co.uk.\nhost = 8.8.8.8\nhost = /A/b /A/c"))
    ['www.bbc.co.uk']
    """
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
    """Whether a query is the Pi's own (or local) traffic: a name outside
    the public suffixes, or one matching a pattern."""
    if not is_public(q.qname):
        return True
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
            # "NETFLIX-ASN, US": the name is everything before the country.
            self._names[asn] = parts[-1].rsplit(",", 1)[0].strip() if parts[-1] else asn
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
