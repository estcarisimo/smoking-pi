"""Is the observer getting information? The fallback's detection half.

Three things stop observations, and each looks like silence from inside the
container, so each needs its own evidence:

* **The container (or Docker) is down.** Nothing here runs. Readers detect
  it from outside: ``status.json`` carries a heartbeat and ``stale_after``;
  a heartbeat older than that means *down*, and ``observed_until`` says
  since when there is no data. See :func:`read_status`.
* **Nobody sends queries here: never configured, or the router reverted.**
  A quiet house at 4 a.m. and a router that went back to its ISP DNS look
  identical by query count alone. The canary tells them apart: every few
  minutes the observer asks *the router* for a unique name under the canary
  domain. If the router forwards to the Pi, that name arrives here. Canaries
  arriving + no queries = a quiet house. Canaries missing + no queries = the
  path is broken.
* **AdGuard Home itself stops answering** while the container runs. The
  supervisor's self-test catches it and restarts the process
  (``server_down`` in between).
* **Queries arrive but the upstreams fail.** AdGuard then answers through
  ``fallback_dns`` (plain DNS) or from its stale cache; the state says which.

The state never claims more than DNS shows: *observing* means queries are
arriving, not that every device's traffic is seen (``COVERAGE``).
"""

from __future__ import annotations

import json
import os
import time

# How often the status file is rewritten, and after how long a reader treats
# it as dead. Three missed writes, not one: an SD card stall must not read
# as an outage.
STATUS_INTERVAL = 30
STALE_AFTER = 3 * STATUS_INTERVAL

# A canary still in flight is neither seen nor missed.
CANARY_TIMEOUT = 10

COVERAGE = (
    "Network DNS only: queries that reach the Pi through the router. Apps and "
    "systems with their own encrypted DNS (DoH/DoQ, VPNs, Private Relay) bypass "
    "it, and cached names are not asked again. It shows names resolved, not "
    "traffic transferred."
)

# States in which the observations are current. Everything else means a
# consumer should fall back (to the last known ranking, then to the curated
# default targets).
LIVE_STATES = frozenset(
    {"observing", "quiet", "partial", "upstream_fallback", "upstream_failing"}
)


def _clock(ts: float | None) -> str | None:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _mins(seconds: float) -> str:
    m = int(seconds // 60)
    if m < 120:
        return f"{m} min"
    return f"{m // 60} h"


def summarize_upstreams(entries: list[dict], fallback: list[str], now: float,
                        window: int = 600) -> dict:
    """What the recent query log says about the upstreams.

    Cached answers say nothing about the upstreams, so they are skipped. An
    answer whose ``upstream`` is a ``fallback_dns`` entry means the
    encrypted upstreams failed for that query.
    """
    answered = servfail = via_fallback = 0
    for e in entries:
        if e.get("cached") or e.get("_ts", now) < now - window:
            continue
        answered += 1
        if e.get("status") == "SERVFAIL":
            servfail += 1
        up = str(e.get("upstream") or "")
        if any(up == f or up.startswith(f"{f}:") for f in fallback):
            via_fallback += 1
    return {"answered": answered, "servfail": servfail, "via_fallback": via_fallback}


def evaluate(
    *,
    now: float,
    started_at: float,
    server_ok: bool | None,
    port: int = 53,
    last_query_at: float | None,
    canaries: list[tuple[float, float | None]],
    canary_enabled: bool,
    canary_misses: int,
    canary_interval: int,
    quiet_after: int,
    upstreams: dict,
) -> dict:
    """The observer's state, as a dict with ``state``, ``reason`` and ``fix``."""

    def out(state: str, reason: str, fix: str | None = None) -> dict:
        return {"state": state, "reason": reason, "fix": fix}

    if server_ok is None:
        return out("starting", "Started; AdGuard Home is starting.")
    if not server_ok:
        return out(
            "server_down",
            f"AdGuard Home is not answering on port {port}; the supervisor is "
            "restarting it.",
            "If this persists: docker compose logs dns-observer. The router's "
            "secondary DNS keeps the house online meanwhile, if one is set.",
        )

    resolved = [
        (sent, seen) for sent, seen in canaries
        if seen is not None or sent < now - CANARY_TIMEOUT
    ]
    recent = resolved[-canary_misses:]
    missed_all = (
        canary_enabled
        and len(recent) >= canary_misses
        and all(seen is None for _, seen in recent)
    )
    last_seen = max((seen for _, seen in resolved if seen is not None), default=None)
    queried_since_misses = bool(
        recent and last_query_at is not None and last_query_at >= recent[0][0]
    )
    idle_for = now - (last_query_at if last_query_at is not None else started_at)
    starting = not resolved and now - started_at < canary_interval + CANARY_TIMEOUT

    if missed_all and not queried_since_misses:
        return out(
            "not_receiving",
            f"The router has not forwarded the last {len(recent)} canary queries "
            f"(since {_clock(recent[0][0])}) and no DNS query has reached the Pi"
            + (f" since {_clock(last_query_at)}." if last_query_at else "."),
            "Check that the router's DNS setting still points at the Pi: routers "
            "revert it on firmware updates and factory resets.",
        )

    answered = upstreams.get("answered", 0)
    if answered >= 5 and upstreams.get("servfail", 0) >= 0.8 * answered:
        return out(
            "upstream_failing",
            "Queries arrive but the upstream resolvers are failing: names asked "
            "recently are answered from the stale cache, the rest fail.",
            "Check the Pi's internet connection.",
        )
    if missed_all:
        return out(
            "partial",
            f"Queries arrive, but the router did not forward the last {len(recent)} "
            "canaries. It is probably splitting queries between the Pi and a "
            "secondary DNS server, so only part of the house's DNS is observed.",
            "Expected when the router has a secondary DNS for resilience. If it "
            "has none, the router may answer the canary name itself: set "
            "DNS_CANARY_DOMAIN to a name it forwards.",
        )
    if answered >= 5 and upstreams.get("via_fallback", 0) >= 0.5 * answered:
        return out(
            "upstream_fallback",
            "The encrypted upstreams are failing; answering through the plain-DNS "
            "fallback resolvers.",
        )
    if last_query_at is None:
        if starting:
            return out("starting", "Started; no query has arrived yet.")
        if canary_enabled and last_seen is not None:
            return out(
                "quiet",
                "No query from the house yet, but the router forwards to the Pi "
                f"(last canary {_clock(last_seen)}).",
            )
        return out(
            "not_receiving",
            "No DNS query has ever reached the Pi.",
            "Point the router's DNS server setting at the Pi (docs/dns-observer.md).",
        )
    if idle_for >= quiet_after:
        if not canary_enabled:
            return out(
                "idle",
                f"No DNS query for {_mins(idle_for)}. With the canary off this "
                "cannot tell a quiet house from a router that stopped sending here.",
                "Set DNS_CANARY_VIA=auto (or the router's address) to tell them apart.",
            )
        if starting:
            return out("starting", "Started; waiting for the first canary.")
        # Fewer misses than the threshold: the path is presumed working.
        return out(
            "quiet",
            f"No DNS query for {_mins(idle_for)}, but the router still forwards "
            f"to the Pi (last canary {_clock(last_seen) or 'pending'}): the house "
            "is quiet.",
        )
    return out("observing", f"Receiving queries (last at {_clock(last_query_at)}).")


def write_status(path: str, status: dict) -> None:
    """Atomic: a reader never sees half a file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        json.dump(status, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def read_status(path: str, now: float | None = None) -> dict:
    """What a reader outside the container should believe.

    A missing file or an old heartbeat is reported as ``down``, carrying the
    last known ``observed_until`` so the gap in the data has a start.
    """
    now = now or time.time()
    try:
        with open(path) as fh:
            status = json.load(fh)
    except FileNotFoundError:
        return {"state": "down", "reason": "The DNS observer has never run.", "live": False}
    except (OSError, ValueError) as exc:
        return {"state": "down", "reason": f"Unreadable status file: {exc}", "live": False}
    if status.get("state") == "stopped":
        return {**status, "live": False}
    if now > status.get("stale_after", 0):
        last = status.get("heartbeat")
        return {
            **status,
            "state": "down",
            "reason": "The DNS observer is not running"
            + (f" (last heartbeat {_clock(last)})." if last else "."),
            "fix": "Start it (docker compose up -d dns-observer) and read "
            "docker compose logs dns-observer. The router's secondary DNS keeps "
            "the house online meanwhile, if one is set.",
            "live": False,
        }
    return {**status, "live": status.get("state") in LIVE_STATES}
