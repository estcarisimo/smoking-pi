"""The thresholds every reader of loss shares, and what total loss means.

The alerter's rules, its verdict, the diagnosis (alert lines, MCP
``diagnose_loss``, the incident history) and the MCP ``get_loss_events``
used to each keep their own copy: 80% "widespread" here, 60% "broad" there,
-75 dBm and 6 samples in three places, a bare 0.8 in a fourth. Comments
said "the same as the alerter's", but only the alerter read the settings,
so tuning ``WIDESPREAD_PCT`` changed the alert and not the diagnosis that
led it. Every reader now asks here, and here reads the settings.

The settings are the alerter's (editions/pro/.env.template); the MCP server
gets the same ones in docker-compose.yml.
"""

from __future__ import annotations

import os

from .cadence import Cadence, is_event, of

DEFAULT_WIDESPREAD_PCT = 80.0  # WIDESPREAD_PCT
DEFAULT_VERDICT_BROAD_PCT = 60.0  # VERDICT_BROAD_PCT
DEFAULT_WIFI_WEAK_DBM = -75.0  # WIFI_WEAK_DBM
DEFAULT_WIFI_WEAK_SAMPLES = 6  # WIFI_WEAK_SAMPLES

# The ISP gateway past the home router (CPE_IPv4 / CPE_IPv6 in ``latency``):
# evidence about the line, never a destination, never in a breadth count.
FIRST_HOP_CATEGORY = "cpe"
# The DNS resolvers, queried by address (``dns_latency``).
RESOLVER_CATEGORY = "dns"

# Targets that vote on breadth and nothing else (decision of 2026-10-14):
# the HTTP and TCP probes, and the DNS wizard's adopted services (every
# protocol). Before, the breadth was the ICMP and resolver targets alone --
# 19 of 247 on the reference Pi -- so "most destinations" meant a handful.
# None of them raises a per-target alert: many CDNs drop ICMP, and the
# wizard's targets are exploratory (common.tsdb.WIZARD_CATEGORY).
BREADTH_ONLY_MEASUREMENTS = ("http_latency", "tcp_latency")


def breadth_voters(rows: list[dict], cadences: dict[str, Cadence]) -> set[str]:
    """The breadth-only targets whose loss counts in this window.

    A target votes when at least one of its points in the window was
    clean (lost under one and a half pings' worth, as everywhere: see
    common.cadence.is_event). A CDN that never answers ICMP, or a probe
    that is always lossy, would otherwise sit in every cycle's "lost"
    share and make a cut of a few sites look like everything. A target
    lost for the whole window does not vote either; the targets that
    always do decide that cycle, as before.

    ``rows``: ``loss`` points (ratio 0-1) with ``target``; ``cadences``:
    their pings per point (unknown: the default ten).
    """
    voters = set()
    for row in rows:
        target, value = row.get("target"), row.get("_value")
        if target is None or value is None or target in voters:
            continue
        ratio = min(1.0, max(0.0, float(value)))
        if not is_event(ratio, of(cadences, target).pings):
            voters.add(target)
    return voters


# Total loss, by what still answers: one incident at a time.
TOTAL_RULES = ("uplink_down", "internet_down", "monitor_dns")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def widespread_pct() -> float:
    """Share of reporting destinations, in percent, that makes one cycle's
    loss one widespread incident."""
    return _env_float("WIDESPREAD_PCT", DEFAULT_WIDESPREAD_PCT)


def broad_pct() -> float:
    """Share of destinations, in percent, at which damage is broad rather
    than one site's (the verdict and the diagnosis)."""
    return _env_float("VERDICT_BROAD_PCT", DEFAULT_VERDICT_BROAD_PCT)


def wifi_weak_dbm() -> float:
    return _env_float("WIFI_WEAK_DBM", DEFAULT_WIFI_WEAK_DBM)


def wifi_weak_samples() -> int:
    return _env_int("WIFI_WEAK_SAMPLES", DEFAULT_WIFI_WEAK_SAMPLES)


def total_loss(first_hop: str | None, names_only: bool) -> tuple[str, str]:
    """(rule, cause) for nearly every destination lost for several cycles.

    ``first_hop``: "answers", "lost", or None (no first-hop measurement).
    ``names_only``: every named destination lost while the DNS resolvers,
    queried by address, answered.
    """
    if first_hop == "answers" and names_only:
        return "monitor_dns", ("this host cannot resolve names (its own DNS), "
                               "not the network")
    if first_hop == "answers":
        return "internet_down", ("the Internet beyond the house is down; this "
                                 "host's link and the line to the ISP are fine")
    if first_hop == "lost":
        return "uplink_down", ("this host's uplink is down; nothing beyond it "
                               "can be judged")
    return "uplink_down", ("this host's uplink, or everything beyond it, is "
                           "down: with no first-hop measurement the two cannot "
                           "be told apart")
