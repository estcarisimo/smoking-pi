"""Experimental: the path to every measured destination, once an hour.

Every ``PATHMETER_INTERVAL`` (3600 s), a Paris traceroute (trace.py) to
each distinct address the measured targets resolve to (targets.py), paced
at ``PATHMETER_PPS`` probes per second. The hops every path shares (the
home router and the access network) are traced in full for one destination
only and reused for the others (paths.py), relearned when that destination
disagrees and once a day. Each path is written to InfluxDB with its AS path
and whether it changed (store.py); Target Detail shows the latest one.

IPv4 only: the reference Pi has no global IPv6, and the targets gate it out
the same way (``IPV6_MODE``). Reads and writes InfluxDB: with
``TSDB_TYPE=clickhouse`` it idles and says so in its status.

What it costs is in ``status.json`` (``probes_per_hour``), which
``smoking-pi budget`` counts with the rest of the measurements.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import time

import owners as owners_mod
import paths
import status
import store
import targets
import trace
from common import asn as lookup
from common.tsdb import query_influx, write_points

log = logging.getLogger("pathmeter")

DEFAULT_INTERVAL = 3600
DEFAULT_PPS = 20
START_DELAY = 120  # seconds: let InfluxDB and the first measurements come up
STATE = "state.json"


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return min(max(value, low), high)


def interval() -> int:
    return _env_int("PATHMETER_INTERVAL", DEFAULT_INTERVAL, 600, 86400)


def pps() -> int:
    return _env_int("PATHMETER_PPS", DEFAULT_PPS, 1, 100)


def addresses(wanted: dict, resolve=lookup.resolve) -> dict[str, list[str]]:
    """IPv4 address -> the base names behind it."""
    by_host: dict[str, str | None] = {}
    out: dict[str, list[str]] = {}
    for base, (_category, host) in sorted(wanted.items()):
        if host not in by_host:
            try:
                found = resolve(host)
            except Exception:  # noqa: BLE001 -- a name that does not resolve is skipped
                found = None
            by_host[host] = found[0] if found else None
        ip = by_host[host]
        try:
            if ip is None or ipaddress.ip_address(ip).version != 4:
                continue
        except ValueError:
            continue
        out.setdefault(ip, []).append(base)
    return out


def _trace_all(dsts: list[str], rate: int, first_hop: int, run) -> dict[str, trace.Trace]:
    return {t.dst: t for t in run(dsts, rate, first_hop)}


def cycle(now: int, query=query_influx, run=trace.run, resolve=lookup.resolve,
          write=write_points, owners=None, rate: int | None = None,
          every: int | None = None) -> dict:
    """One cycle; returns its report (also written to status.json)."""
    started = time.monotonic()
    rate = rate or pps()
    every = every or interval()
    state = store.load(STATE)
    prefix: list[str] = state.get("prefix") or []
    wanted = targets.wanted(query)
    by_ip = addresses(wanted, resolve)
    inside = {ip for ip in by_ip if ip in prefix}
    rest = sorted(ip for ip in by_ip if ip not in inside)
    report = {"at": now, "interval": every, "destinations": len(by_ip), "targets": len(wanted),
              "traces": 0, "probes": 0, "failed": 0, "full": False, "prefix_moved": False}

    traced: dict[str, trace.Trace] = {}
    joined_hops: dict[str, tuple[list, list]] = {}
    try:
        full = not prefix or now - int(state.get("learned_at") or 0) >= paths.FULL_EVERY
        if not full and rest:
            reference = state.get("reference") if state.get("reference") in rest else rest[0]
            ref = _trace_all([reference], rate, 1, run).get(reference)
            if ref is None or not paths.agrees(ref.hops, prefix) or len(ref.hops) < len(prefix):
                full, report["prefix_moved"] = True, True
            else:
                traced[reference] = ref
                skip = len(prefix) + 1
                # A destination added since the prefix was learned, and
                # nearer than its end, reads as one hop past it until the
                # next full cycle (a day at most).
                others = _trace_all([ip for ip in rest if ip != reference], rate, skip, run)
                traced.update(others)
                for ip, t in others.items():
                    if t.first_hop > 1:
                        joined_hops[ip] = (paths.joined(prefix, t.hops, t.first_hop),
                                           paths.joined(ref.rtts, t.rtts, t.first_hop))
                state["reference"] = reference
        if full:
            # Everything, the destinations inside the old prefix too: the
            # prefix is what this cycle learns.
            inside = set()
            traced = _trace_all(sorted(by_ip), rate, 1, run)
            learned = paths.learn_prefix([t.hops for t in traced.values()])
            if learned:
                prefix = learned
                state["prefix"], state["learned_at"] = prefix, now
            report["full"] = True
            # The reference for the cycles to come: the destination that
            # went furthest in the prefix-sharing set.
            if traced:
                state["reference"] = max(sorted(traced), key=lambda ip: len(traced[ip].hops))
    except Exception:  # noqa: BLE001 -- scamper failing costs this cycle, never the process
        log.exception("trace run failed")
        report["failed"] = 1

    report["traces"] = len(traced)
    report["probes"] = sum(t.probes for t in traced.values())
    report["prefix_hops"] = len(prefix)

    owners = owners or owners_mod.Owners()
    hop_sets: dict[str, tuple[list, list, bool, int | None, int]] = {}
    for ip, t in traced.items():
        hops, rtts = joined_hops.get(ip, (t.hops, t.rtts))
        hop_sets[ip] = (hops, rtts, t.reached, t.distance, t.first_hop)
    for ip in inside:
        at = prefix.index(ip)
        hop_sets[ip] = (prefix[: at + 1], [None] * (at + 1), True, at + 1, 0)
    owners.refresh({a for hops, *_ in hop_sets.values() for a in hops if a})

    memory = state.setdefault("paths", {})
    points, changes = [], 0
    for ip, (hops, rtts, reached, distance, first_hop) in sorted(hop_sets.items()):
        asns = paths.as_path(hops, owners.of)
        for base in by_ip.get(ip, []):
            category = wanted[base][0]
            flags = paths.track(memory.setdefault(base, {}), ip, hops, distance, asns)
            changes += bool(flags["changed"])
            points += store.trace_points(base, category, now, ip, hops, rtts, reached, distance,
                                         first_hop, paths.text(hops), paths.digest(hops), asns,
                                         flags, owners.of)
    for gone in [b for b in memory if b not in wanted]:
        del memory[gone]
    report["changed"] = changes
    report["seconds"] = round(time.monotonic() - started, 1)
    report["probes_per_hour"] = round(report["probes"] * 3600 / every, 1)
    try:
        write(points + [store.pass_point(now, report)])
    except Exception:  # noqa: BLE001 -- the next cycle writes again
        log.exception("writing %d points failed", len(points))
        report["failed"] = 1
    store.save(STATE, state)
    status.write(report)
    log.info("path cycle: %d destinations, %d traces, %d probes in %.0f s, %d changed%s%s",
             report["destinations"], report["traces"], report["probes"], report["seconds"],
             changes, ", full" if report["full"] else "",
             ", prefix moved" if report["prefix_moved"] else "")
    return report


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    every = interval()
    if (os.environ.get("TSDB_TYPE") or "influxdb").lower() != "influxdb":
        status.write({"idle": "pathmeter needs InfluxDB (TSDB_TYPE=influxdb)"})
        log.warning("TSDB_TYPE is not influxdb: idling")
        while True:
            time.sleep(86400)
    status.write({"starting": int(time.time()), "interval": every})
    time.sleep(START_DELAY)
    while True:
        begun = time.time()
        try:
            cycle(int(begun))
        except Exception:  # noqa: BLE001 -- one bad cycle must not stop the next
            log.exception("path cycle failed")
        time.sleep(max(60.0, every - (time.time() - begun)))


if __name__ == "__main__":
    main()
