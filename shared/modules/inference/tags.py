"""Who runs the infrastructure behind each target: operator and kind.

The shared detector (shared.py) asks whether loss reached *many kinds of
infrastructure at once*, not many target names: three names on one CDN are
one piece of infrastructure, not three. So each target is tagged by what it
reaches, not by a list of targets: its network from public data, and that
network's kind from a short curated table of well-known networks:

1. its host, from the ``target_host`` measurement the exporter writes;
2. the host's address and CNAME chain, from DNS (the chain is what tells
   CloudFront from the rest of Amazon);
3. the address's origin AS and its name, from Team Cymru's DNS service
   (``<reversed /24>.origin.asn.cymru.com``), as the DNS observer and the
   resolver probe already do;
4. the operator and its kind from :data:`KNOWN`, a table of well-known
   networks. An AS not in it is its own operator, of kind ``other``.

Kinds: a multi-tenant CDN (many sites on one edge), a content provider's own
network, a cache embedded near the access network, a cloud or origin server,
an academic network. PeeringDB's network type was tried first and is too
coarse: it calls nearly all of these "Content".

Lookups are DNS queries only, a few per target and cached in the state
volume (addresses for a day, owners for a week). A failed lookup is cached
for an hour, so a host that never resolves cannot hold the budget forever;
until a lookup succeeds the target is ``unknown`` and counts as no operator.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

log = logging.getLogger("inference.tags")

CDN = "cdn"                  # multi-tenant CDN
CONTENT = "content_network"  # a content provider's own network
CACHE = "embedded_cache"     # caches placed near the access network (Netflix OCA)
CLOUD = "cloud_origin"       # cloud, hosting, or the service's own servers
ACADEMIC = "academic"
OTHER = "other"
UNKNOWN = "unknown"

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

ADDRESS_TTL = 86400
FAILED_TTL = 3600
OWNER_TTL = 7 * 86400
BUDGET = 64  # new lookups per pass; the rest wait for the next one


def _state_path() -> Path:
    return Path(os.environ.get("INFERENCE_STATE_DIR", "/var/lib/inference")) / "tags.json"


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


def _txt(qname: str, resolver=None) -> str | None:
    try:
        answer = (resolver or _resolver()).resolve(qname, "TXT")
    except Exception:  # noqa: BLE001
        return None
    return b"".join(answer[0].strings).decode(errors="replace")


def origin(ip: str, resolver=None) -> tuple[int, str] | None:
    """(origin ASN, AS name) of an IPv4 address, from Team Cymru."""
    if ipaddress.ip_address(ip).version != 4:
        return None
    reply = _txt(".".join(reversed(ip.split(".")[:3])) + ".origin.asn.cymru.com", resolver)
    if not reply:
        return None
    head = reply.split("|", 1)[0].split()
    if not head or not head[0].isdigit():
        return None
    asn = int(head[0])
    parts = [p.strip() for p in (_txt(f"AS{asn}.asn.cymru.com", resolver) or "").split("|")]
    return asn, (parts[4] if len(parts) >= 5 else "")


def classify(asn: int | None, as_name: str, host: str, chain: list[str]) -> tuple[str, str]:
    """(operator, kind) from the origin AS, with the CNAME chain for Amazon."""
    if not asn:
        return UNKNOWN, UNKNOWN
    if asn == AMAZON:
        names = " ".join([host, *chain]).lower()
        if any(h in names for h in CLOUDFRONT_HINTS):
            return "Amazon CloudFront", CDN
        return "AWS", CLOUD
    if asn in KNOWN:
        return KNOWN[asn]
    if any(w in as_name.lower() for w in ("univers", "college", "school", "research", "academ")):
        return as_name.split(" - ")[-1].split(",")[0].strip() or f"AS{asn}", ACADEMIC
    return (as_name.split(" - ")[0].strip() or f"AS{asn}"), OTHER


class Tags:
    """Operator and kind per target, cached in ``tags.json``."""

    def __init__(self, path: Path | None = None, clock=time.time,
                 resolve_fn=resolve, origin_fn=origin) -> None:
        self.path = path or _state_path()
        self.clock = clock
        self._resolve, self._origin = resolve_fn, origin_fn
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            raw = {}
        self.addresses: dict[str, dict] = raw.get("addresses", {})
        self.owners: dict[str, dict] = raw.get("owners", {})

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"addresses": self.addresses, "owners": self.owners}))
            os.replace(tmp, self.path)
        except OSError:
            log.warning("could not save %s", self.path, exc_info=True)

    @staticmethod
    def _prefix(ip: str) -> str:
        return ".".join(ip.split(".")[:3])

    def refresh(self, hosts: dict[str, str]) -> None:
        """Look up what is missing or stale, at most BUDGET of each kind."""
        now = self.clock()
        def old(entry: dict | None, ttl: int) -> bool:
            if entry is None:
                return True
            return now - entry.get("at", now) > (FAILED_TTL if entry.get("failed") else ttl)

        def safe(fn):
            def call(arg):
                try:
                    return fn(arg)
                except Exception:  # noqa: BLE001 -- one bad lookup must not lose the others
                    log.debug("lookup failed for %s", arg, exc_info=True)
                    return None
            return call

        wanted = set(hosts.values())
        stale = [h for h in sorted(wanted, key=lambda h: self.addresses.get(h, {}).get("at", 0))
                 if old(self.addresses.get(h), ADDRESS_TTL)][:BUDGET]
        if stale:
            with ThreadPoolExecutor(8) as pool:
                for host, found in zip(stale, pool.map(safe(self._resolve), stale)):
                    self.addresses[host] = ({"ip": found[0], "chain": found[1], "at": now}
                                            if found else {"failed": True, "at": now})
        ips = {a["ip"] for h, a in self.addresses.items()
               if h in wanted and a.get("ip") and ipaddress.ip_address(a["ip"]).version == 4}
        todo = sorted({self._prefix(ip): ip for ip in ips
                       if old(self.owners.get(self._prefix(ip)), OWNER_TTL)}.items(),
                      key=lambda kv: self.owners.get(kv[0], {}).get("at", 0))[:BUDGET]
        if todo:
            with ThreadPoolExecutor(8) as pool:
                for (prefix, _ip), found in zip(todo, pool.map(safe(self._origin),
                                                               [ip for _, ip in todo])):
                    self.owners[prefix] = ({"asn": found[0], "name": found[1], "at": now}
                                           if found else {"failed": True, "at": now})
        if stale or todo:
            self.save()

    def of(self, target: str, hosts: dict[str, str]) -> tuple[str, str]:
        """(operator, kind) of a target; ``unknown`` until its lookups succeed."""
        host = hosts.get(target)
        address = self.addresses.get(host or "")
        if not address or not address.get("ip"):
            return UNKNOWN, UNKNOWN
        owner = self.owners.get(self._prefix(address["ip"])) if "." in address["ip"] else None
        if not owner or not owner.get("asn"):
            return UNKNOWN, UNKNOWN
        return classify(owner["asn"], owner.get("name", ""), host, address.get("chain", []))
