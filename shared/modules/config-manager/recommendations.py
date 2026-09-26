"""What to measure, from where this host actually is: the "Your connection" card.

Stage B of the first-run flow (docs/getting-started.md). The seeded targets
are the same on every install; the router, the resolvers the host was given
and the ISP's first hop are not, and they are what separates "my Wi-Fi",
"my ISP" and "the Internet" when something is slow. This module turns the
host's facts (host_facts.py, run in SmokePing's network namespace) and what
SmokePing already measures into one list, each entry either already
measured -- named -- or a suggestion the web admin can pre-fill into its add
form. Nothing is added here: a suggestion is accepted by a person, through
the same form and validation as any other target.

Pure, so it is tested without Docker; api.py supplies the inputs.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Dict, List, Optional

_SECTION = re.compile(r"^\+{1,2}\s*(\S+)\s*$")
_HOST = re.compile(r"^host\s*=\s*(\S+)\s*$")

# Tailscale's MagicDNS: a resolver that answers on this host, like 127.0.0.53.
_ON_HOST_RESOLVERS = {"100.100.100.100", "fd7a:115c:a1e0::53"}


def measured_hosts(*texts: str) -> Dict[str, str]:
    """host (lowercased) -> target name, from generated Targets-format files.

    The generated file is what SmokePing measures: active targets only, the
    same set the Measurements card reads, in database and YAML mode alike.
    """
    found: Dict[str, str] = {}
    for text in texts:
        name: Optional[str] = None
        for raw in text.splitlines():
            line = raw.strip()
            section = _SECTION.match(line)
            if section:
                name = section.group(1)
                continue
            host = _HOST.match(line)
            if host and name:
                found.setdefault(host.group(1).lower(), name)
    return found


def _ip(host: str) -> Optional[ipaddress._BaseAddress]:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _name_for(prefix: str, host: str) -> str:
    """A target name the add form accepts: letter first, [A-Za-z0-9_], <= 30."""
    return (prefix + "_" + re.sub(r"[^A-Za-z0-9]+", "_", host).strip("_"))[:30]


def _item(kind: str, host: str, measured: Dict[str, str], why: str,
          suggest: Optional[Dict[str, str]]) -> Dict[str, Any]:
    name = measured.get(host.lower())
    return {
        "kind": kind,
        "host": host,
        "why": why,
        "status": "measured" if name else "suggested",
        "measured_as": name,
        "suggest": None if name else suggest,
    }


def recommend(facts: Dict[str, Any], measured: Dict[str, str]) -> Dict[str, Any]:
    """The card's content: the uplink, then one entry per thing worth measuring."""
    items: List[Dict[str, Any]] = []

    gateway4 = facts.get("gateway4")
    if gateway4:
        items.append(_item(
            "gateway", gateway4, measured,
            "Your router. Loss or delay here is your Wi-Fi or your LAN; "
            "anything that is fine here but bad further out is past your home.",
            {"target_type": "icmp", "name": "Router", "hostname": gateway4,
             "title": f"Router ({gateway4})"}))

    gateway6 = facts.get("gateway6")
    if gateway6:
        addr = _ip(gateway6)
        if addr is not None and addr.is_link_local:
            items.append({
                "kind": "gateway6", "host": gateway6, "status": "not_measurable",
                "measured_as": None, "suggest": None,
                "why": "Your router's IPv6 address is link-local. SmokePing "
                       "cannot ping it without naming the interface, so the "
                       "IPv4 router above stands in for it.",
            })
        else:
            items.append(_item(
                "gateway6", gateway6, measured,
                "Your router, over IPv6.",
                {"target_type": "icmp", "name": "Router6", "hostname": gateway6,
                 "title": f"Router, IPv6 ({gateway6})"}))

    cpe = facts.get("cpe") or {}
    for family in ("ipv4", "ipv6"):
        host = cpe.get(family)
        if host:
            items.append({
                "kind": "cpe", "host": host, "status": "automatic",
                "measured_as": measured.get(host.lower()),
                "suggest": None,
                "why": "Your ISP's first hop, found by traceroute every hour and "
                       "measured automatically. It is not in the target list "
                       "because it changes when the ISP renumbers.",
            })

    for host in facts.get("resolvers") or []:
        addr = _ip(host)
        if (addr is not None and addr.is_loopback) or host in _ON_HOST_RESOLVERS:
            items.append({
                "kind": "resolver", "host": host, "status": "local",
                "measured_as": None, "suggest": None,
                "why": "A resolver on this host (a local cache or VPN stub). "
                       "Measuring it would say nothing about your network.",
            })
            continue
        items.append(_item(
            "resolver", host, measured,
            "A DNS resolver this host was given. Every page load starts with "
            "a lookup, so a slow resolver feels like a slow Internet.",
            {"target_type": "dns", "name": _name_for("Resolver", host),
             "hostname": host, "title": f"System resolver ({host})",
             "dns_query": "google.com"}))

    return {
        "available": True,
        "uplink": {"interface": facts.get("uplink"),
                   "wireless": bool(facts.get("wireless"))},
        "items": items,
        "suggested": sum(1 for i in items if i["status"] == "suggested"),
        # Who answers the house's DNS on the Internet side (resolver_identity);
        # {} before the collector's first cycle.
        "public_resolver": facts.get("public_resolver") or {},
    }


def unavailable(reason: str) -> Dict[str, Any]:
    return {"available": False, "reason": reason}

