"""What the traffic meter (netmeter) needs to know about the stack.

The meter keys its nftables counters on each container: host-network
ones by the cgroup their sockets live in, bridged ones by address. It runs
with CAP_NET_ADMIN, so it is not also given the Docker socket; it asks
GET /meter/containers here instead, and this builds the answer from the
Docker client config-manager already holds.

Pure: takes container attributes (``docker inspect`` shape) and the
daemon's cgroup driver.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Dict, List, Optional

_ID = re.compile(r"^[0-9a-f]{64}$")


def cgroup_of(container_id: str, driver: str) -> Optional[tuple[str, int]]:
    """The container's cgroup v2 path, relative to /sys/fs/cgroup, and its
    depth: systemd puts it at system.slice/docker-<id>.scope, the cgroupfs
    driver at docker/<id>. Anything else is not counted (None)."""
    if not _ID.match(container_id or ""):
        return None
    if driver == "systemd":
        return f"system.slice/docker-{container_id}.scope", 2
    if driver == "cgroupfs":
        return f"docker/{container_id}", 1
    return None


def _addresses(networks: Dict[str, Any]) -> tuple[list[str], list[str]]:
    v4, v6 = [], []
    for net in (networks or {}).values():
        for key, out in (("IPAddress", v4), ("GlobalIPv6Address", v6)):
            value = (net or {}).get(key) or ""
            try:
                ipaddress.ip_address(value)
            except ValueError:
                continue
            if value not in out:
                out.append(value)
    return sorted(v4), sorted(v6)


def describe(attrs: List[Dict[str, Any]], project: str, driver: str) -> List[Dict[str, Any]]:
    """One entry per running container of this Compose project."""
    out = []
    for a in attrs:
        labels = (a.get("Config") or {}).get("Labels") or {}
        if labels.get("com.docker.compose.project") != project:
            continue
        if not (a.get("State") or {}).get("Running"):
            continue
        service = labels.get("com.docker.compose.service")
        if not service:
            continue
        host = (a.get("HostConfig") or {}).get("NetworkMode") == "host"
        entry: Dict[str, Any] = {"service": service, "host_network": host}
        if host:
            found = cgroup_of(a.get("Id", ""), driver)
            if found:
                entry["cgroup"], entry["cgroup_level"] = found
        else:
            entry["ipv4"], entry["ipv6"] = _addresses(
                (a.get("NetworkSettings") or {}).get("Networks"))
        out.append(entry)
    return sorted(out, key=lambda e: e["service"])
