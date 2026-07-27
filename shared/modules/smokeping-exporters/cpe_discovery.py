#!/usr/bin/env python3
"""
CPE Discovery — automatically discovers the ISP CPE (first responsive hop)
via traceroute and injects it as a SmokePing target.

Runs hourly by default. Writes state to /tmp/cpe_state.json for the
microcut detector to consume.
"""

import json
import logging
import os
import re
import subprocess
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# ── config from environment ──────────────────────────────────────
DISCOVERY_INTERVAL = int(os.getenv("CPE_DISCOVERY_INTERVAL", "3600"))
IPV4_TARGET = os.getenv("CPE_IPV4_TARGET", "8.8.8.8")
IPV6_TARGET = os.getenv("CPE_IPV6_TARGET", "dns.google")
MAX_HOPS = int(os.getenv("CPE_MAX_HOPS", "5"))
SMOKEPING_CONFIG_DIR = os.getenv("SMOKEPING_CONFIG_DIR", "/config")
STATE_FILE = "/tmp/cpe_state.json"


# ── traceroute parsing ───────────────────────────────────────────
_HOP_RE = re.compile(
    r"^\s*(\d+)\s+"           # hop number
    r"(?:\S+\s+)?"            # optional hostname
    r"\(?([\d.]+|[\da-fA-F:]+)\)?"  # IP (v4 or v6)
    r"\s+([\d.]+)\s*ms",      # RTT
)

_HOP_STAR_RE = re.compile(r"^\s*(\d+)\s+\*")


def _parse_traceroute(output: str) -> list:
    """Return [(hop_num, ip)] for hops that responded."""
    hops = []
    for line in output.splitlines():
        m = _HOP_RE.search(line)
        if m:
            hops.append((int(m.group(1)), m.group(2)))
    return hops


def _fping_ok(ip: str, ipv6: bool = False) -> bool:
    """Return True if the IP responds to a single fping probe."""
    cmd = ["/usr/sbin/fping"]
    if ipv6:
        cmd.append("-6")
    cmd += ["-c1", "-t500", "-q", ip]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def discover_cpe_ipv4() -> str | None:
    """Traceroute to find the first ICMP-responsive hop after the gateway."""
    log.info("Discovering IPv4 CPE via traceroute to %s (max %d hops)", IPV4_TARGET, MAX_HOPS)
    try:
        result = subprocess.run(
            ["traceroute", "-n", "-q1", "-w1", f"-m{MAX_HOPS}", IPV4_TARGET],
            capture_output=True, text=True, timeout=30,
        )
        hops = _parse_traceroute(result.stdout)
        log.info("IPv4 traceroute hops: %s", hops)

        # Skip hop 1 (home router), try hops 2+ for first responder
        for hop_num, ip in hops:
            if hop_num < 2:
                continue
            if _fping_ok(ip, ipv6=False):
                log.info("IPv4 CPE found: %s (hop %d)", ip, hop_num)
                return ip
            else:
                log.info("Hop %d (%s) does not respond to fping, skipping", hop_num, ip)

        log.warning("No IPv4 CPE found (no responding hop 2-%d)", MAX_HOPS)
        return None

    except subprocess.TimeoutExpired:
        log.error("IPv4 traceroute timed out")
        return None
    except Exception as e:
        log.error("IPv4 discovery failed: %s", e)
        return None


def discover_cpe_ipv6() -> str | None:
    """Traceroute6 to find the first ICMP-responsive hop after the gateway."""
    log.info("Discovering IPv6 CPE via traceroute6 to %s (max %d hops)", IPV6_TARGET, MAX_HOPS)
    try:
        result = subprocess.run(
            ["traceroute6", "-n", "-q1", "-w1", f"-m{MAX_HOPS}", IPV6_TARGET],
            capture_output=True, text=True, timeout=30,
        )
        hops = _parse_traceroute(result.stdout)
        log.info("IPv6 traceroute hops: %s", hops)

        for hop_num, ip in hops:
            if hop_num < 2:
                continue
            if _fping_ok(ip, ipv6=True):
                log.info("IPv6 CPE found: %s (hop %d)", ip, hop_num)
                return ip
            else:
                log.info("Hop %d (%s) does not respond to fping -6, skipping", hop_num, ip)

        log.warning("No IPv6 CPE found (no responding hop 2-%d)", MAX_HOPS)
        return None

    except subprocess.TimeoutExpired:
        log.error("IPv6 traceroute timed out")
        return None
    except Exception as e:
        log.error("IPv6 discovery failed: %s", e)
        return None


# ── state persistence ────────────────────────────────────────────
def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(ipv4: str | None, ipv6: str | None):
    # Atomic write: the microcut detector reads this file concurrently.
    state = {"ipv4": ipv4, "ipv6": ipv6, "updated": time.time()}
    tmp_path = STATE_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(state, f)
    os.replace(tmp_path, STATE_FILE)
    log.info("State saved: %s", state)


# ── SmokePing target injection ───────────────────────────────────
# We write a separate file /config/CPE_Targets (the /config volume is
# writable) and append an @include to the main Targets file on first run.
# This avoids modifying the read-only bind-mounted Targets file directly.

CPE_TARGETS_FILE = "CPE_Targets"


def build_cpe_targets(ipv4: str | None, ipv6: str | None) -> str:
    """Build SmokePing Targets config fragment for CPE."""
    lines = [
        "# Auto-generated by cpe_discovery.py — do not edit",
        "+ CPE",
        "menu = CPE",
        "title = ISP CPE Monitoring (auto-discovered)",
        "",
    ]
    if ipv4:
        lines += [
            "++ CPE_IPv4",
            "menu = CPE IPv4",
            f"title = CPE IPv4 ({ipv4})",
            f"host = {ipv4}",
            "",
        ]
    if ipv6:
        lines += [
            "++ CPE_IPv6",
            "menu = CPE IPv6",
            f"title = CPE IPv6 ({ipv6})",
            "probe = FPing6",
            f"host = {ipv6}",
            "",
        ]
    return "\n".join(lines)


def update_smokeping_targets(ipv4: str | None, ipv6: str | None):
    """Write CPE targets and ensure SmokePing includes them."""
    if not ipv4 and not ipv6:
        log.warning("No CPE IPs discovered, skipping target injection")
        return

    cpe_path = os.path.join(SMOKEPING_CONFIG_DIR, CPE_TARGETS_FILE)

    # Write the CPE targets file; the generated Targets file @includes it
    # (see smokeping_targets.j2 — Targets itself is a read-only bind mount).
    content = build_cpe_targets(ipv4, ipv6)
    tmp_path = cpe_path + ".tmp"
    with open(tmp_path, "w") as f:
        f.write(content)
    os.replace(tmp_path, cpe_path)
    log.info("Wrote CPE targets to %s", cpe_path)

    # Signal SmokePing to reload config
    try:
        subprocess.run(["killall", "-HUP", "smokeping"], capture_output=True, timeout=5)
        log.info("SmokePing reload signal sent")
    except Exception as e:
        log.warning("Failed to signal SmokePing: %s", e)


# ── main loop ────────────────────────────────────────────────────
def ensure_cpe_file_exists():
    """Create empty CPE_Targets file if it doesn't exist so SmokePing @include doesn't fail."""
    cpe_path = os.path.join(SMOKEPING_CONFIG_DIR, CPE_TARGETS_FILE)
    if not os.path.exists(cpe_path):
        with open(cpe_path, "w") as f:
            f.write("# CPE targets — will be populated by cpe_discovery.py\n")
        log.info("Created empty %s", cpe_path)


def main():
    log.info(
        "CPE discovery started (interval=%ds, max_hops=%d, ipv4_target=%s, ipv6_target=%s)",
        DISCOVERY_INTERVAL, MAX_HOPS, IPV4_TARGET, IPV6_TARGET,
    )
    ensure_cpe_file_exists()

    while True:
        old_state = load_state()

        ipv4 = discover_cpe_ipv4() or old_state.get("ipv4")
        ipv6 = discover_cpe_ipv6() or old_state.get("ipv6")

        save_state(ipv4, ipv6)
        update_smokeping_targets(ipv4, ipv6)

        log.info(
            "Discovery cycle complete. IPv4=%s, IPv6=%s. Sleeping %ds.",
            ipv4 or "none", ipv6 or "none", DISCOVERY_INTERVAL,
        )
        time.sleep(DISCOVERY_INTERVAL)


if __name__ == "__main__":
    main()
