"""Experimental: the path to every measured destination, once an hour.

Every ``PATHMETER_INTERVAL`` (3600 s), a Paris traceroute (trace.py) to
each distinct address the measured targets resolve to (targets.py), paced
at ``PATHMETER_PPS`` probes per second. The hops every path shares (the
home router and the access network) are probed by ``FULL_PER_CYCLE``
destinations a cycle only, in turn, and reused for the others (paths.py):
the router never sees a burst, however many destinations there are. Each
path is written to InfluxDB with its AS path and whether it changed
(store.py); Target Detail shows the latest one.

IPv4 only: the reference Pi has no global IPv6, and the targets gate it out
the same way (``IPV6_MODE``). Reads and writes InfluxDB: with
``TSDB_TYPE=clickhouse`` it idles and says so in its status.

What it costs is in ``status.json`` (``probes_per_hour``), which
``smoking-pi budget`` counts with the rest of the measurements.
"""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import logging
import os
import time

import owners as owners_mod
import names as names_mod
import paths
import status
import store
import targets
import trace
from common import asn as lookup
from common.tsdb import query_influx, write_points

log = logging.getLogger("pathmeter")

DEFAULT_INTERVAL = 3600
DEFAULT_PPS = 5
START_DELAY = 120  # seconds: let InfluxDB and the first measurements come up
# Destinations traced from hop 1 per cycle, the ones whose last full trace
# is the oldest. What the shared hops (the home router, the access network)
# receive per cycle, whatever the number of destinations; a full sweep of
# every destination takes destinations / FULL_PER_CYCLE cycles.
FULL_PER_CYCLE = 4
# Destinations traced from hop 1 per cycle until that many have been: the
# prefix is learned from them, and four alike could share more than the rest.
WARMUP_PATHS = 8
DAY = 86400
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


def _ipv4(values) -> list[str]:
    out = []
    for value in values or []:
        try:
            if ipaddress.ip_address(value).version == 4:
                out.append(value)
        except ValueError:
            continue
    return out


def addresses(wanted: dict, previous: dict | None = None,
              resolve=lookup.addresses) -> dict[str, list[str]]:
    """IPv4 address -> the base names behind it.

    A name with several addresses (round-robin, CDN names) keeps the one it
    was traced at last time while the answer still includes it: picking
    whichever came first would trace another address most hours, and every
    one would start a new path. A new address only when the old one left.
    """
    previous = previous or {}
    answers: dict[str, list[str]] = {}
    out: dict[str, list[str]] = {}
    for base, (_category, host) in sorted(wanted.items()):
        if host not in answers:
            try:
                answers[host] = _ipv4(resolve(host))
            except Exception:  # noqa: BLE001 -- a name that does not resolve is skipped
                answers[host] = []
        found = answers[host]
        if not found:
            continue
        ip = previous.get(base) if previous.get(base) in found else found[0]
        out.setdefault(ip, []).append(base)
    return out


def _trace_all(dsts: list[str], rate: int, first_hop: int, run) -> dict[str, trace.Trace]:
    return {t.dst: t for t in run(dsts, rate, first_hop)}


def cycle(now: int, query=query_influx, run=trace.run, resolve=lookup.addresses,
          write=write_points, owners=None, rate: int | None = None,
          every: int | None = None, names=None) -> dict:
    """One cycle; returns its report (also written to status.json)."""
    started = time.monotonic()
    rate = rate or pps()
    every = every or interval()
    state = store.load(STATE)
    prefix: list[str] = state.get("prefix") or []
    wanted = targets.wanted(query)
    last_dst = {base: (mem.get("stable") or {}).get("dst")
                for base, mem in (state.get("paths") or {}).items()}
    by_ip = addresses(wanted, last_dst, resolve)
    report = {"at": now, "interval": every, "destinations": len(by_ip), "targets": len(wanted),
              "traces": 0, "probes": 0, "failed": 0, "full_traces": 0, "waiting": 0,
              "prefix_moved": False, "access_moved": False}

    traced: dict[str, trace.Trace] = {}
    joined_hops: dict[str, tuple[list, list]] = {}
    inside: set[str] = set()
    try:
        # From hop 1, only a few destinations a cycle: the shared hops never
        # see more than that many probes, however many destinations there
        # are. First those with no full trace kept (new, or dropped because
        # the access path moved since), then the oldest; ties broken by a
        # hash, not the address, since neighboring addresses share long
        # paths and a prefix learned from a few of them would stretch.
        full_at = state.get("full_at") or {}
        kept = state.get("full_paths") or {}
        candidates = sorted((ip for ip in by_ip if ip not in prefix),
                            key=lambda ip: (ip in kept, full_at.get(ip, 0),
                                            hashlib.sha256(ip.encode()).hexdigest()))
        known = len([ip for ip in kept if ip in by_ip])
        # Until a few destinations have a full trace, WARMUP_PATHS a cycle:
        # still a handful of probes at the router, and a prefix that rests on
        # more than four paths.
        count = FULL_PER_CYCLE if known >= WARMUP_PATHS else max(FULL_PER_CYCLE, WARMUP_PATHS)
        fulls = _trace_all(candidates[:count], rate, 1, run)
        traced.update(fulls)
        report["full_traces"] = len(fulls)
        for ip in fulls:
            full_at[ip] = now
        fresh = {ip: t.hops for ip, t in fulls.items()}
        old = {ip: hops for ip, hops in kept.items() if ip not in fresh}
        # The access path moved when this cycle's full traces and the kept
        # ones each agree among themselves and part on a hop that answered
        # (a new gateway, a failover): the kept ones describe a path that is
        # gone. They are dropped, and those destinations go first next.
        if paths.parted(paths.learn_prefix(list(fresh.values())),
                        paths.learn_prefix(list(old.values()))):
            old = {}
            report["access_moved"] = True
        kept = {**old, **fresh}
        learned = paths.learn_prefix(list(kept.values()))
        if not learned and prefix:
            # Mixed answers learn nothing; going blind would cost every
            # destination its path. The previous prefix, cut where this
            # cycle's traces contradict it, still keeps the router out of it.
            learned = paths.cut(prefix, list(fresh.values()))
        # A destination list that shrank by half or more in one cycle is a
        # DNS or query blip, not a new configuration: keep what was learned.
        if len(by_ip) * 2 >= len(state.get("full_paths") or {}):
            kept = {ip: hops for ip, hops in kept.items() if ip in by_ip}
            full_at = {ip: t for ip, t in full_at.items() if ip in by_ip}
        state["full_paths"], state["full_at"] = kept, full_at
        report["prefix_moved"] = bool(prefix) and learned != prefix
        prefix = learned
        state["prefix"] = prefix
        inside = {ip for ip in by_ip if ip in prefix and ip not in traced}
        others = [ip for ip in sorted(by_ip) if ip not in traced and ip not in inside]
        if prefix:
            # A destination whose path left the shared hops before their end
            # reads as sharing them until its own turn from hop 1 comes.
            skip = len(prefix) + 1
            past = _trace_all(others, rate, skip, run)
            traced.update(past)
            for ip, t in past.items():
                if t.first_hop > 1:
                    # The prefix's addresses are those the full traces
                    # learned; reply times are not this destination's.
                    joined_hops[ip] = (paths.joined(prefix, t.hops, t.first_hop),
                                       paths.joined([], t.rtts, t.first_hop))
        else:
            # No prefix to skip (not learned yet, or it could not be): the
            # rest wait for their turn from hop 1 rather than all of them
            # crossing the shared hops at once.
            report["waiting"] = len(others)
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
    # Destinations inside the prefix were not traced: their path is the
    # prefix's, which only a cycle whose traces ran has just confirmed.
    for ip in (inside if not report["failed"] else ()):
        at = prefix.index(ip)
        hop_sets[ip] = (prefix[: at + 1], [None] * (at + 1), True, at + 1, 0)
    on_paths = {a for hops, *_ in hop_sets.values() for a in hops if a}
    owners.refresh(on_paths)
    # The routers' names (PTR): shown with their hop, never part of a path.
    names = names or names_mod.Names()
    names.refresh(on_paths)
    names.prune(on_paths | set(by_ip))

    memory = state.setdefault("paths", {})
    # If the points do not reach InfluxDB, the changes this cycle confirmed
    # must not be forgotten: the tracking goes back to where it was.
    before = copy.deepcopy(memory)
    points, changes = [], 0
    for ip, (hops, rtts, reached, distance, first_hop) in sorted(hop_sets.items()):
        asns = paths.as_path(hops, owners.of)
        for base in by_ip.get(ip, []):
            category = wanted[base][0]
            # A destination inside the prefix (first_hop 0) measured nothing
            # itself this cycle: its path is the prefix's, never a change.
            measured = first_hop >= 1
            flags = paths.track(memory.setdefault(base, {}), ip, hops,
                                distance if measured else None, owners.of,
                                own_from=first_hop if measured else len(hops) + 1)
            changes += bool(flags["changed"])
            points += store.trace_points(base, category, now, ip, hops, rtts, reached, distance,
                                         first_hop, paths.text(hops), paths.digest(hops), asns,
                                         flags, owners.of, lookup.ixp, names.of)
    for gone in [b for b in memory if b not in wanted]:
        del memory[gone]
    report["changed"] = changes
    report["seconds"] = round(time.monotonic() - started, 1)
    # What it costs, for the budget: the mean over the last day's cycles, not
    # the last one, which is a cheap prefix cycle or the daily full one.
    recent = [r for r in state.get("recent") or [] if now - r[0] < DAY]
    if traced:
        recent.append([now, report["probes"]])
    state["recent"] = recent
    per_cycle = sum(r[1] for r in recent) / len(recent) if recent else report["probes"]
    report["probes_per_hour"] = round(per_cycle * 3600 / every, 1)
    try:
        write(points + [store.pass_point(now, report)])
    except Exception:  # noqa: BLE001 -- the next cycle writes again
        log.exception("writing %d points failed", len(points))
        report["failed"] = 1
        state["paths"] = before
    report["failed_cycles"] = (int(status.read().get("failed_cycles") or 0) + 1
                               if report["failed"] else 0)
    store.save(STATE, state)
    status.write(report)
    log.info("path cycle: %d destinations, %d traces, %d probes in %.0f s, %d changed%s%s%s",
             report["destinations"], report["traces"], report["probes"], report["seconds"],
             changes, f", {report['full_traces']} from hop 1",
             f", {report['waiting']} waiting" if report["waiting"] else "",
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
    # The routing table (CAIDA RouteViews prefix-to-AS, PeeringDB IXPs):
    # downloaded once a day into the state volume, looked up locally.
    table = lookup.use_table(store.state_dir() / "routing")
    while True:
        begun = time.time()
        try:
            table.refresh()
        except Exception:  # noqa: BLE001 -- the table is an aid; the paths go on without it
            log.exception("routing table refresh failed")
        try:
            cycle(int(begun))
        except Exception:  # noqa: BLE001 -- one bad cycle must not stop the next
            log.exception("path cycle failed")
        time.sleep(max(60.0, every - (time.time() - begun)))


if __name__ == "__main__":
    main()
