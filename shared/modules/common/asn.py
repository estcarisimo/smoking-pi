"""Who announces an address, from public DNS data: shared by the modules
that ask (inference's operator tags, pathmeter's hops).

- :func:`resolve`: a host's first IPv4 address and its CNAME chain;
- :func:`origin`: an IPv4 address's origin AS and its name, from Team
  Cymru's DNS service (``<reversed /24>.origin.asn.cymru.com``), as the DNS
  observer and the resolver probe already do;
- :func:`classify`: the operator and its kind from :data:`KNOWN`, a table of
  well-known networks. An AS not in it is its own operator, of kind
  ``other``.

DNS queries only; the callers cache what they learn. Needs dnspython,
imported on first use, so importing this module needs nothing.
"""

from __future__ import annotations

import ipaddress

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
