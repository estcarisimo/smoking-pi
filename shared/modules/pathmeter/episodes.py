"""Traces while a shared loss episode is under way, every five minutes.

The hourly cycle says which way the packets went in a quiet hour; it says
nothing about where they were lost during the nightly episodes in which
every destination lost at once. While one lasts, a round every
``EVERY`` seconds traces the destinations it hit, so the hop where the
paths go silent can be read: inside the access network every destination
shares (the home router and the hops after it), or beyond it.

**The episode is the alerter's.** No detector of its own: the episode is a
widespread run of ``common.loss_events`` -- the alerter's widespread rule
(WIDESPREAD_PCT of the targets reporting in a cycle with a loss event of at
least WIDESPREAD_LOSS_PCT, the breadth-only voters counted), the same runs
the MCP ``get_loss_events`` and the web chat answer with. A run read as
this host's own uplink down or its own DNS (``reading`` uplink_down,
monitor_dns) is not traced: nothing beyond a dead uplink answers, and a
DNS failure is not on the path. It is under way
while its last lossy cycle is at most ``ONGOING_STEPS`` steps old: the
exporter writes a cycle a step after it ends, and a run carries across
``loss_events.GAP_STEPS`` missing steps.

**The cap is per hour, hard.** At most ``PATHMETER_EPISODE_TRACES_PER_HOUR``
traces in any sixty minutes (default 60, so 5 a round at 12 rounds an
hour), counted from a ledger kept in the state volume and written before scamper
runs: a restart, or a crash, in the middle of an episode does not start a
new hour. The ledger counts the traces a round started, answered or not. 0 turns the rounds off.
Destinations take turns (the one traced longest ago first), so a long
episode covers all of them and a short one the most affected.

**The router is spared as in the hourly cycle.** Of each round's traces,
``FULL_PER_ROUND`` (one, within main.FULL_PER_CYCLE) starts at hop 1, in
turn: twelve probes an hour at the home router at most, which is what the
hop-by-hop answer through the access network costs. The others start past
the shared prefix the hourly cycle learned (``-f``), and borrow its
addresses as the hourly traces do. With no prefix learned yet, only the
hop-1 trace runs. Rounds run in the same loop as the hourly cycle, never
beside it, so ``PATHMETER_PPS`` holds over both; while a cycle runs (about
two minutes for 76 destinations) the rounds wait.

**What the check costs.** Every five minutes, the loss-event query of the
last hour (``loss_events.answer``: five Flux queries, a fraction of a
second each on the reference Pi) and the targets' cadence.

**Kept apart.** Round traces are tagged ``trigger=episode`` on
``path_trace`` and ``path_hop`` (the hourly ones carry no such tag, so
their series are unchanged), with the episode's start in the ``episode``
field. They never feed the change tracking (paths.track): a path seen
every five minutes while losing packets is not two hourly traces in a row.

**Cost.** In status.json under ``episodes``: the cap, the mean probes per
round trace over the last day and what the rounds sent. ``smoking-pi
budget`` reserves the worst case (cap x probes per trace) as its own row, so
admission keeps room for it whether an episode is under way or not.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import time
from datetime import datetime

import names as names_mod
import owners as owners_mod
import paths
import status
import store
import targets
import trace
from common import asn as lookup
from common import cadence, loss_events
from common.links import target_base_name
from common.tsdb import query_influx, write_points

log = logging.getLogger("pathmeter.episodes")

EVERY = 300  # seconds between rounds while an episode lasts
DEFAULT_CAP = 60  # traces per hour
MAX_CAP = 1200
HOUR = 3600
# Runs whose loss is this host's own, where a trace shows nothing.
NOT_ON_THE_PATH = ("uplink_down", "monitor_dns")
DAY = 86400
# The traces of a round that start at hop 1. main.FULL_PER_CYCLE is the
# limit per cycle; one is enough to see the access network hop by hop.
FULL_PER_ROUND = 1
# An episode whose last lossy step is no older than this many steps is
# under way (one for the exporter's lag, GAP_STEPS for the gaps a run
# carries across).
ONGOING_STEPS = 1 + loss_events.GAP_STEPS
# The window asked about every round: enough for ONGOING_STEPS of the
# slowest step and a run's start; a longer run keeps the id it was given.
WINDOW_HOURS = 1
# Probes per trace assumed until a round has measured them: an hourly
# trace's median is 13 from hop 1 (docs/path.md), and a trace past the
# prefix stops at the destination or after trace.GAP_LIMIT silent hops.
PROBES_PER_TRACE = 13
TRIGGER = "episode"
STATE = "episodes.json"


def cap() -> int:
    try:
        value = int(os.environ.get("PATHMETER_EPISODE_TRACES_PER_HOUR", "")
                    or DEFAULT_CAP)
    except ValueError:
        return DEFAULT_CAP
    return min(max(value, 0), MAX_CAP)


def per_round(limit: int) -> int:
    """A round's share of the hour's cap."""
    return math.ceil(limit * EVERY / HOUR)


def _epoch(value) -> float | None:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


def under_way(answer: dict, now: float, step_s: int) -> dict | None:
    """The widespread run of ``answer`` (loss_events.answer) still going
    at ``now``: ``{"start", "targets"}``, the targets being
    the base names with a loss event since it began; None when there is
    none."""
    newest = next(iter(answer.get("widespread") or []), None)
    if not newest or newest.get("reading") in NOT_ON_THE_PATH:
        return None
    start, end = _epoch(newest.get("start")), _epoch(newest.get("end"))
    if start is None or end is None or now - end > ONGOING_STEPS * step_s:
        return None
    hit = set()
    for event in answer.get("by_target") or []:
        last = _epoch(event.get("last_time"))
        if event.get("target") and last is not None and last >= start:
            hit.add(target_base_name(str(event["target"])))
    return {"start": newest["start"], "targets": hit}


def detect(now: float, query=query_influx) -> dict | None:
    """The episode under way, if any, by the alerter's widespread rule."""
    try:
        cadences = cadence.by_target(query(cadence.cadence_flux()))
    except Exception:  # noqa: BLE001 -- the cadence refines the rule, never stops it
        cadences = {}
    answer = loss_events.answer(query, WINDOW_HOURS, cadences=cadences)
    return under_way(answer, now, cadence.longest_step(cadences))


def _turn(ip: str, at: dict) -> tuple:
    return (at.get(ip, 0), hashlib.sha256(ip.encode()).hexdigest())


def pick(affected: list[str], others: list[str], n: int, traced_at: dict,
         full_at: dict, full: int) -> tuple[list[str], list[str]]:
    """``n`` destinations, the affected ones first and the one traced
    longest ago first within each; of them, ``full`` from hop 1 (the one
    whose last round trace from hop 1 is the oldest). Returns (from hop 1,
    past the prefix)."""
    order = (sorted(affected, key=lambda ip: _turn(ip, traced_at))
             + sorted(others, key=lambda ip: _turn(ip, traced_at)))[:n]
    first = sorted(order, key=lambda ip: _turn(ip, full_at))[:full]
    return first, [ip for ip in order if ip not in first]


def _ledger(rounds: list, now: float) -> list:
    return [r for r in rounds if now - r[0] < DAY]


def round_(now: int, query=query_influx, run=trace.run, write=write_points,
           detect_fn=None, owners=None, names=None, rate: int = 5,
           limit: int | None = None, hourly_state: str = "state.json",
           full_per_cycle: int = 4) -> dict:
    """One check, and one round of traces when an episode is under way;
    returns its report (also written to status.json, under ``episodes``).
    ``rate``, ``hourly_state`` and ``full_per_cycle`` are main's
    PATHMETER_PPS, STATE and FULL_PER_CYCLE, which main passes."""
    started = time.monotonic()
    limit = cap() if limit is None else limit
    mine = store.load(STATE)
    rounds = _ledger(mine.get("rounds") or [], now)
    report = {"at": now, "cap_per_hour": limit, "every": EVERY, "episode": None,
              "traces": 0, "probes": 0, "full_traces": 0, "capped": 0, "failed": 0}
    episode = None
    if limit > 0:
        try:
            episode = (detect_fn or detect)(now, query)
        except Exception:  # noqa: BLE001 -- no reading this time; the next check asks again
            log.exception("episode check failed")
            report["failed"] = 1

    points: list = []
    if episode:
        # One id per episode: a run longer than the window it is read
        # from keeps the start it had when its rounds began.
        last = mine.get("episode") or {}
        if last.get("id") and now - float(last.get("at") or 0) <= ONGOING_STEPS * EVERY:
            episode["start"] = last["id"]
        mine["episode"] = {"id": episode["start"], "at": now}
        report["episode"] = episode["start"]

        hourly = store.load(hourly_state)
        prefix: list = hourly.get("prefix") or []
        wanted = targets.wanted(query)
        # The address each destination was traced at last: no name is
        # resolved while the network is losing packets.
        dst = {base: (mem.get("stable") or {}).get("dst")
               for base, mem in (hourly.get("paths") or {}).items() if base in wanted}
        by_ip: dict[str, list[str]] = {}
        for base, ip in sorted(dst.items()):
            if ip and ip not in prefix:
                by_ip.setdefault(ip, []).append(base)
        affected = sorted(ip for ip, bases in by_ip.items()
                          if set(bases) & episode["targets"])
        others = sorted(ip for ip in by_ip if ip not in affected)
        used = sum(r[1] for r in rounds if now - r[0] < HOUR)
        room = max(0, min(per_round(limit), limit - used))
        traced_at, full_at = mine.get("traced_at") or {}, mine.get("full_at") or {}
        fulls, past = pick(affected, others, room, traced_at, full_at,
                           min(FULL_PER_ROUND, full_per_cycle))
        if not prefix:
            past = []  # nothing to start past: only the hop-1 trace runs
        report["capped"] = max(0, len(affected) - room)
        # Counted before they are sent: a round that crashes or fails
        # halfway still used its share of the hour.
        if fulls or past:
            rounds.append([now, len(fulls) + len(past), 0])
            mine["rounds"] = rounds
            store.save(STATE, mine)
        traced: dict[str, trace.Trace] = {}
        try:
            traced.update({t.dst: t for t in run(fulls, rate, 1)})
            if past:
                traced.update({t.dst: t for t in run(past, rate, len(prefix) + 1)})
        except Exception:  # noqa: BLE001 -- scamper failing costs this round only
            log.exception("episode trace run failed")
            report["failed"] = 1
        for ip in traced:
            traced_at[ip] = now
        for ip in fulls:
            if ip in traced:
                full_at[ip] = now
        mine["traced_at"] = {ip: t for ip, t in traced_at.items() if ip in by_ip}
        mine["full_at"] = {ip: t for ip, t in full_at.items() if ip in by_ip}
        report["traces"] = len(traced)
        report["full_traces"] = len([ip for ip in fulls if ip in traced])
        report["probes"] = sum(t.probes for t in traced.values())

        owners = owners or owners_mod.Owners()
        names = names or names_mod.Names()
        for ip, t in sorted(traced.items()):
            hops, rtts = t.hops, t.rtts
            if t.first_hop > 1:
                hops = paths.joined(prefix, t.hops, t.first_hop)
                rtts = paths.joined([], t.rtts, t.first_hop)
            asns = paths.as_path(hops, owners.of)
            for base in by_ip[ip]:
                points += store.trace_points(
                    base, wanted[base][0], now, ip, hops, rtts, t.reached, t.distance,
                    t.first_hop, paths.text(hops), paths.digest(hops), asns, {},
                    owners.of, lookup.ixp, names.of, trigger=TRIGGER,
                    episode=episode["start"])
    else:
        mine.pop("episode", None)

    report["seconds"] = round(time.monotonic() - started, 1)
    if rounds and rounds[-1][0] == now:
        rounds[-1][2] = report["probes"]
    if report["traces"]:
        try:
            write(points + [store.pass_point(now, report, trigger=TRIGGER)])
        except Exception:  # noqa: BLE001 -- the traces were sent; the cap counts them
            log.exception("writing %d episode points failed", len(points))
            report["failed"] = 1
        log.info("episode %s: %d traces (%d from hop 1), %d probes, %d left for later",
                 report["episode"], report["traces"], report["full_traces"],
                 report["probes"], report["capped"])
    mine["rounds"] = rounds
    store.save(STATE, mine)

    measured = [r for r in rounds if r[2]]
    traces_day = sum(r[1] for r in measured)
    probes_day = sum(r[2] for r in measured)
    report["traces_last_hour"] = sum(r[1] for r in rounds if now - r[0] < HOUR)
    report["probes_last_day"] = probes_day
    report["probes_per_trace"] = (round(probes_day / traces_day, 1) if traces_day
                                  else PROBES_PER_TRACE)
    current = status.read()
    current["episodes"] = report
    status.write(current)
    return report
