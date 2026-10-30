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
# The loss a target must have in a cycle to count toward a widespread one,
# besides a loss event (common.cadence.is_event). The alerter's rule always
# had it; the MCP server's ``widespread`` runs did not, so an assistant could
# call a cycle widespread that never could have been an alert.
DEFAULT_WIDESPREAD_LOSS_PCT = 30.0  # WIDESPREAD_LOSS_PCT
DEFAULT_VERDICT_BROAD_PCT = 60.0  # VERDICT_BROAD_PCT
# The mean loss of a target at which it is in trouble: the alerter's
# high_loss rule, the loss chart's line, and the digest's red light (which
# kept a 20 of its own, so a tuned HIGH_LOSS_PCT moved the alert and its
# chart and not the morning summary).
DEFAULT_HIGH_LOSS_PCT = 20.0  # HIGH_LOSS_PCT
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
# The share of its points in the window a breadth-only target must have had
# clean to vote. The queries and the voting over any window are
# common.breadth's, for every reader of breadth.
BREADTH_CLEAN_SHARE = 0.5


def breadth_voters(rows: list[dict], cadences: dict[str, Cadence]) -> set[str]:
    """The breadth-only targets whose loss counts in this window.

    A target votes when most of its points in the window -- at least half
    -- were clean (lost under one and a half pings' worth, as everywhere:
    see common.cadence.is_event). A CDN that never answers ICMP, or a probe
    that is lossy all the time (an HTTP/3 path that is blocked half the
    time), would otherwise sit in every cycle's "lost" or "impaired" share
    and make a cut of a few sites look like everything. A target lost for
    most of the window does not vote either: the targets that always vote
    decide that cycle, as before, so a long outage is judged as it was.

    ``rows``: ``loss`` points (ratio 0-1) with ``target``; ``cadences``:
    their pings per point (unknown: the default ten).
    """
    clean: dict[str, int] = {}
    seen: dict[str, int] = {}
    for row in rows:
        target, value = row.get("target"), row.get("_value")
        if target is None or value is None:
            continue
        ratio = min(1.0, max(0.0, float(value)))
        seen[target] = seen.get(target, 0) + 1
        if not is_event(ratio, of(cadences, target).pings):
            clean[target] = clean.get(target, 0) + 1
    return {t for t, n in seen.items()
            if clean.get(t, 0) >= BREADTH_CLEAN_SHARE * n}


# What makes an episode material: the reason a notification is sent at
# all, and a line in the weekly summary (decision of 2026-10-30, front 8).
# October on the reference Pi sent 72 messages for about 11 real events, two
# of them actionable; these are the bar the rest did not clear. Constants,
# not settings: the alerter, the weekly summary and the MCP tools must
# agree on what was material, and a per-install knob would split them.
MATERIAL_BREADTH_PCT = 30.0  # share of the reporting destinations...
MATERIAL_MIN_CATEGORIES = 2  # ...or destinations in this many categories
# A category counts toward MATERIAL_MIN_CATEGORIES when at least this share
# of its own reporting destinations lost: one site in each of two categories
# (a university's web server and its name server) is still one site.
MATERIAL_CATEGORY_SHARE = 50.0
MATERIAL_MIN_MINUTES = 15  # three 5-minute probe cycles in a row...
MATERIAL_MIN_EPISODES = 2  # ...or this many episodes...
MATERIAL_EPISODES_WINDOW_S = 3600  # ...in this span
MATERIAL_LOSS_PCT = 5.0  # mean loss over the episode's degraded cycles
# Median latency over the episode against the same hours a week before.
MATERIAL_LATENCY_RATIO = 2.0
# Below this share of the week measured, the monitor's own availability is
# news: the weekly goes out to say so.
MATERIAL_AVAILABILITY_PCT = 99.0
# Days of one week on which loss or congestion shared by the destinations
# touched the same hours of the day before those hours are a recurring
# window (the weekly's own rule; the Quality dashboard only draws a band
# someone chose, docs/connectivity-quality.md).
RECURRING_MIN_DAYS = 3
# The monitor blind (a deaf radio, its database unreadable, its exporter
# stalled) for at least this long is told once, silently, when it can
# measure again; shorter is recorded only. healthchecks.io (the dead-man
# check-in) covers the meantime.
MONITOR_BLIND_MIN_MINUTES = 15
# A cut of the line shorter than this is a microcut: recorded for the
# weekly summary, never pushed.
MICROCUT_MAX_S = 60


def materiality(
    *,
    measurable: bool,
    breadth_pct: float,
    categories: int,
    minutes: float,
    episodes_in_window: int,
    loss_pct: float | None,
    latency_ratio: float | None = None,
    novel: bool = True,
) -> list[str]:
    """The conditions an episode misses to be material; empty when it is.

    All five must hold (decision of 2026-10-30):

    - ``measurable``: judged on minutes the monitor could hear -- outside
      its own deaf spans, the ``upgrade`` windows and the watchdog's
      actions, with one probe cycle of margin. The caller subtracts them.
    - ``breadth``: ``breadth_pct`` (of the reporting destinations) at least
      MATERIAL_BREADTH_PCT, or ``categories`` (each with
      MATERIAL_CATEGORY_SHARE of its destinations) at least
      MATERIAL_MIN_CATEGORIES.
    - ``persistence``: ``minutes`` covered by consecutive degraded probe
      cycles at least MATERIAL_MIN_MINUTES (three 5-minute cycles), or MATERIAL_MIN_EPISODES episodes within
      MATERIAL_EPISODES_WINDOW_S (``episodes_in_window``, this one
      included).
    - ``magnitude``: mean loss ``loss_pct`` at least MATERIAL_LOSS_PCT, or
      median latency at least MATERIAL_LATENCY_RATIO times the same hours
      a week before (``latency_ratio``; None when not measured).
    - ``novelty``: not explained by an open episode or a known pattern.

    The names in the list are the conditions, for the record and the MCP.
    """
    unmet = []
    if not measurable:
        unmet.append("measurable")
    if breadth_pct < MATERIAL_BREADTH_PCT and categories < MATERIAL_MIN_CATEGORIES:
        unmet.append("breadth")
    if (minutes < MATERIAL_MIN_MINUTES
            and episodes_in_window < MATERIAL_MIN_EPISODES):
        unmet.append("persistence")
    if ((loss_pct is None or loss_pct < MATERIAL_LOSS_PCT)
            and (latency_ratio is None or latency_ratio < MATERIAL_LATENCY_RATIO)):
        unmet.append("magnitude")
    if not novel:
        unmet.append("novelty")
    return unmet


def material(**facts) -> bool:
    """Whether an episode is material: the single gate, shared by the
    alerter's push policy, the weekly summary and the MCP tools. See
    :func:`materiality` for the conditions and their arguments."""
    return not materiality(**facts)


# Total loss, by what still answers: one incident at a time.
TOTAL_RULES = ("uplink_down", "internet_down", "monitor_dns")
# Below this many reporting targets in a cycle, breadth means nothing.
MIN_BREADTH_TARGETS = 3
# A point lost everything at or above this ratio.
LOST_RATIO = 0.999
# Cycles of total loss before it is this host's uplink (or the Internet, or
# its DNS) rather than a brief cut: the alerter's DOWN_MIN_POINTS.
TOTAL_MIN_CYCLES = 3


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


def widespread_loss_pct() -> float:
    """Loss, in percent, a target must have in one cycle to count toward a
    widespread one (with a loss event)."""
    return _env_float("WIDESPREAD_LOSS_PCT", DEFAULT_WIDESPREAD_LOSS_PCT)


def broad_pct() -> float:
    """Share of destinations, in percent, at which damage is broad rather
    than one site's (the verdict and the diagnosis)."""
    return _env_float("VERDICT_BROAD_PCT", DEFAULT_VERDICT_BROAD_PCT)


def high_loss_pct() -> float:
    """Mean loss, in percent, at which a target is in trouble."""
    return _env_float("HIGH_LOSS_PCT", DEFAULT_HIGH_LOSS_PCT)


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
