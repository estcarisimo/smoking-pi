#!/usr/bin/env python3
"""Global IPv6 reachability check.

SmokePing's FPing6 probe only produces meaningful data when the host can
actually reach the global IPv6 internet. Without it every IPv6 target charts a
flat 100% loss, which looks like an outage but only means the host has no IPv6
at all. This module decides whether IPv6 measurements should run.

WHERE THIS RUNS: the check must be executed in the SmokePing container's
network namespace (it uses ``network_mode: host``), NOT in config-manager's.
config-manager sits on a Docker bridge network that has no IPv6 whatsoever, so
a check made locally would report "no IPv6" on a perfectly v6-capable host.
:func:`check` therefore runs its commands through a runner that execs into the
SmokePing container; the parsing and decision logic below is pure and testable
without a network.

Three signals, cheapest first:

1. A global unicast address (2000::/3). Addresses in fd00::/8 (ULA, RFC 4193 —
   including Tailscale's fd7a:115c:a1e0::/48) and fe80::/10 (link-local) are
   NOT globally routable, yet Linux reports them as "scope global", so a naive
   ``ip -6 addr show scope global`` is not a usable test.
2. A default route. Without one the kernel rejects the packet outright
   ("Network is unreachable") and nothing leaves the host.
3. An actual reply from a well-known off-net address, which is the only signal
   that proves end-to-end reachability rather than local configuration.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re

logger = logging.getLogger(__name__)

# Only 2000::/3 is global unicast. Everything else (ULA, link-local,
# multicast, loopback) cannot reach the IPv6 internet.
GLOBAL_UNICAST = ipaddress.ip_network("2000::/3")

# Well-known IPv6 resolvers, used only as ping targets.
DEFAULT_PROBE_HOSTS = ("2001:4860:4860::8888", "2606:4700:4700::1111")

DEFAULT_RECHECK_INTERVAL = 900  # seconds; IPV6_RECHECK_INTERVAL
PROBE_TIMEOUT_S = 5

# Probes whose targets require global IPv6.
IPV6_PROBES = frozenset({"FPing6"})

_INET6_RE = re.compile(r"inet6\s+([0-9a-fA-F:]+)")


def mode() -> str:
    """``auto`` (probe the host), ``force`` (assume IPv6 works), or ``off``."""
    return (os.environ.get("IPV6_MODE") or "auto").strip().lower()


def probe_hosts() -> list[str]:
    raw = os.environ.get("IPV6_PROBE_HOSTS", "")
    hosts = [h.strip() for h in raw.split(",") if h.strip()]
    return hosts or list(DEFAULT_PROBE_HOSTS)


def recheck_interval() -> int:
    try:
        value = int(os.environ.get("IPV6_RECHECK_INTERVAL", "") or
                    DEFAULT_RECHECK_INTERVAL)
    except ValueError:
        return DEFAULT_RECHECK_INTERVAL
    return value if value > 0 else DEFAULT_RECHECK_INTERVAL


# ───────────────────────── parsing (pure) ─────────────────────────
def global_addresses(ip_addr_output: str) -> list[str]:
    """Global-unicast addresses in ``ip -6 addr`` output (ULA excluded)."""
    found = []
    for match in _INET6_RE.finditer(ip_addr_output or ""):
        try:
            addr = ipaddress.IPv6Address(match.group(1))
        except ValueError:
            continue
        if addr in GLOBAL_UNICAST:
            found.append(str(addr))
    return found


def has_default_route(ip_route_output: str) -> bool:
    """True when ``ip -6 route`` output contains a usable default route."""
    for line in (ip_route_output or "").splitlines():
        line = line.strip()
        if line.startswith("default") or line.startswith("::/0"):
            # `default ... unreachable` and blackhole routes do not count.
            if "unreachable" in line or "prohibit" in line or "blackhole" in line:
                continue
            return True
    return False


def ping_succeeded(ping_output: str, returncode: int) -> bool:
    """True when a ping run actually got a reply.

    Exit status alone is not enough: some ping builds exit 0 having sent
    nothing useful, and 'Network is unreachable' is a hard failure.
    """
    text = (ping_output or "").lower()
    if "network is unreachable" in text or "unknown host" in text:
        return False
    if returncode != 0:
        return False
    match = re.search(r"(\d+)\s+received", text)
    if match:
        return int(match.group(1)) > 0
    match = re.search(r"(\d+(?:\.\d+)?)%\s+packet loss", text)
    if match:
        return float(match.group(1)) < 100.0
    return True


# ───────────────────────── decision ─────────────────────────
def evaluate(ip_addr_output: str, ip_route_output: str,
             ping_results: list[tuple[str, int]]) -> dict:
    """Decide availability from already-collected command output.

    ``ping_results`` is a list of (output, returncode), one per probe host.
    Returns ``{"available": bool, "reason": str, "addresses": [...]}``.
    """
    addresses = global_addresses(ip_addr_output)
    if not addresses:
        return {
            "available": False,
            "reason": ("no global IPv6 address on this host (only ULA/"
                       "link-local, which cannot route to the IPv6 internet)"),
            "addresses": [],
        }

    if not has_default_route(ip_route_output):
        return {
            "available": False,
            "reason": "no default IPv6 route; packets cannot leave the host",
            "addresses": addresses,
        }

    if not ping_results:
        return {
            "available": False,
            "reason": "no IPv6 probe hosts were reachable (none attempted)",
            "addresses": addresses,
        }

    if any(ping_succeeded(out, rc) for out, rc in ping_results):
        return {
            "available": True,
            "reason": "global IPv6 address, default route, and probe reply",
            "addresses": addresses,
        }

    return {
        "available": False,
        "reason": ("IPv6 is configured but no probe host replied; the path "
                   "beyond this host looks broken"),
        "addresses": addresses,
    }


def check(runner) -> dict:
    """Run the check using ``runner(argv) -> (output, returncode)``.

    ``runner`` executes in the network namespace that SmokePing probes from
    (see the module docstring). Honours IPV6_MODE; any runner failure is
    reported as unavailable rather than raised.
    """
    configured = mode()
    if configured == "off":
        return {"available": False, "mode": "off", "addresses": [],
                "reason": "IPv6 measurements disabled by IPV6_MODE=off"}
    if configured == "force":
        return {"available": True, "mode": "force", "addresses": [],
                "reason": "IPv6 assumed reachable by IPV6_MODE=force"}

    try:
        addr_out, _ = runner(["ip", "-6", "addr", "show"])
        route_out, _ = runner(["ip", "-6", "route", "show", "default"])
        pings = []
        # Only probe if the structural checks can pass; a ping into a host
        # with no route just burns the timeout.
        if global_addresses(addr_out) and has_default_route(route_out):
            for host in probe_hosts():
                pings.append(runner(
                    ["ping", "-6", "-c", "2", "-W", str(PROBE_TIMEOUT_S), host]
                ))
    except Exception as exc:  # runner/exec failures must not break generation
        # `error` tells the caller this is "we could not tell", not "no IPv6" —
        # a transient docker-exec failure must not silently drop targets.
        logger.warning("IPv6 check could not run: %s", exc)
        return {"available": False, "error": True, "mode": configured,
                "addresses": [],
                "reason": f"IPv6 check could not run: {exc}"}

    result = evaluate(addr_out, route_out, pings)
    result["mode"] = configured
    return result


def is_ipv6_target(target: dict, ipv6_probes=IPV6_PROBES) -> bool:
    """True when a target's probe needs global IPv6."""
    return (target.get("probe") or "") in ipv6_probes


# ───────────────────────── shared status cache ─────────────────────────
# The check needs Docker (to exec in the SmokePing namespace), which the
# config generator deliberately does not use — it never spawns subprocesses.
# The API layer runs the check and publishes the verdict here; the generator
# reads it. Until something publishes a result the status is UNKNOWN, and
# callers must keep IPv6 targets: dropping them because nobody has looked yet
# would turn a missing check into silent data loss.
_status: dict | None = None


def set_status(status: dict) -> None:
    """Publish a check result for the generator to read."""
    global _status
    _status = dict(status)


def get_status() -> dict:
    """Last published result, or an explicit unknown."""
    if _status is None:
        return {"available": None, "reason": "IPv6 reachability not checked yet",
                "addresses": [], "mode": mode()}
    return dict(_status)


def measurements_allowed() -> bool:
    """Whether IPv6 targets should be emitted. Unknown counts as allowed."""
    return get_status().get("available") is not False
