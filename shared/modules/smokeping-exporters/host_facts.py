#!/usr/bin/env python3
"""The host's network as SmokePing sees it, printed as one JSON object.

config-manager runs this with `docker exec` for the web admin's "Your
connection" card: its own namespace is a Docker bridge, so its default
route, its resolvers and its interfaces are Docker's, not the host's. The
SmokePing container shares the host's network (Pro, Basic), so what it
reads here is what the probes cross.

The uplink and the gateways come from wifi_link's route parsing -- the
same code that decides which interface the Wi-Fi verdict is about -- so
the card and the verdict cannot disagree. The CPE is the one cpe_discovery
found and wrote to its state file.

    {"uplink": "wlan0", "wireless": true,
     "gateway4": "192.168.1.1", "gateway6": "fe80::1",
     "resolvers": ["1.1.1.1"],
     "cpe": {"ipv4": "100.64.0.1", "ipv6": null, "updated": 1790210247.9}}

Every field is null (or empty) when it cannot be read; the command never
fails because one of them is missing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import wifi_link

# Written by resolver_identity.py, in this same container.
RESOLVER_SNAPSHOT = Path("/tmp/resolver_identity.json")

RESOLV_CONF = Path("/etc/resolv.conf")
CPE_STATE = Path("/tmp/cpe_state.json")


def resolvers(resolv_conf: Path = RESOLV_CONF) -> list[str]:
    """`nameserver` lines, in order, duplicates dropped.

    Docker writes a container's resolv.conf once, when it is created, from
    the host's (the doctor's container-dns-fresh check exists because of
    it), so this is the host's list as of that moment.
    """
    try:
        lines = resolv_conf.read_text().splitlines()
    except OSError:
        return []
    found: list[str] = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "nameserver" and parts[1] not in found:
            found.append(parts[1])
    return found


def cpe(state_file: Path = CPE_STATE) -> dict[str, Any] | None:
    """What cpe_discovery found: the first responsive hop, per family."""
    try:
        state = json.loads(state_file.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    return {k: state.get(k) for k in ("ipv4", "ipv6", "updated")}


def public_resolver(snapshot: Path = RESOLVER_SNAPSHOT) -> dict[str, Any] | None:
    """What resolver_identity.py last saw per path (router, observer):
    who owns the resolver that answers, its egress addresses, the client
    subnet it passes on. None before its first cycle or without it."""
    try:
        data = json.loads(snapshot.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def facts(proc_route: Path = wifi_link.PROC_ROUTE,
          proc_route6: Path = wifi_link.PROC_IPV6_ROUTE,
          sys_net: Path = wifi_link.SYS_NET,
          resolv_conf: Path = RESOLV_CONF,
          state_file: Path = CPE_STATE,
          resolver_snapshot: Path | None = None) -> dict[str, Any]:
    route4 = wifi_link.default_route4(proc_route)
    route6 = wifi_link.default_route6(proc_route6)
    # v4 first, then v6: wifi_link.uplink_interface's rule.
    uplink = (route4 or route6 or (None, None))[0]
    return {
        "uplink": uplink,
        "wireless": bool(uplink) and uplink in wifi_link.find_interfaces(sys_net),
        "gateway4": route4[1] if route4 else None,
        "gateway6": route6[1] if route6 else None,
        "resolvers": resolvers(resolv_conf),
        "cpe": cpe(state_file),
        "public_resolver": public_resolver(resolver_snapshot or RESOLVER_SNAPSHOT),
    }


if __name__ == "__main__":
    print(json.dumps(facts()))
