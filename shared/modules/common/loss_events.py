"""What ``get_loss_events`` answers, for every assistant.

The MCP tool folded the loss points into widespread runs (with the reading
of a total loss: this host's uplink, the Internet, its own DNS) and per-
target episodes; the web admin's chat had its own ``get_loss_events``, which
returned the 200 newest raw points and nothing else. Asked about the week of
2026-10-11 on the reference Pi, the chat's answer held no point of the
four-hour outage of that day (the 200 newest were all later), where the MCP
answer led with it. Both now call :func:`answer`: the same events, episodes,
widespread runs and counts whichever assistant asks. Links and uplink
changes stay with each caller.

A widespread cycle is the alerter's rule (alerter/evaluator.py
rule_widespread): WIDESPREAD_PCT of the targets reporting in it had a loss
event of at least WIDESPREAD_LOSS_PCT, counting the breadth-only voters
(common.breadth). Before, these runs left the WIDESPREAD_LOSS_PCT floor out
and could call a cycle widespread that no alert could have fired on.

``query`` is the caller's InfluxDB query function; it raises what the
client raises, and the caller reports it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from common import breadth, cadence, definitions
from common.tsdb import CLAMP_LOSS_RATIO, base_flux, flux_str

log = logging.getLogger(__name__)

Query = Callable[[str], list]

# Events listed in an answer; the roll-ups read up to MAX_ROLLUP_ROWS.
MAX_EVENT_ROWS = 500
# Rows fetched for the roll-ups. The events list stays capped at
# MAX_EVENT_ROWS, but episodes and widespread runs must be computed from
# everything in the window: a three-hour cut across 18 targets is 720
# points, and a roll-up built from the newest 500 of them gets its start
# wrong.
MAX_ROLLUP_ROWS = 5000
# A run of loss points on one target with gaps no longer than this many of
# its probe steps is one episode, and the same for widespread steps: one
# missing point does not split a cut in two (common.diagnosis.GAP_STEPS).
GAP_STEPS = 2
# Loss in percent (0-100) at which a point lost everything.
TOTAL_PCT = definitions.LOST_RATIO * 100.0


def answer(query: Query, hours: int, threshold: float | None = None,
           cadences: dict[str, cadence.Cadence] | None = None) -> dict:
    """The loss events of the last ``hours``, three ways (see the MCP tool
    ``get_loss_events`` for what each field means to a reader).

    ``threshold``: a fixed loss percentage (0-100) instead of each target's
    event bar. ``cadences``: each target's step and pings (empty: the
    defaults). Episodes and ``by_target`` carry ``target`` and
    ``measurement`` for the caller's links; nothing here links.
    """
    cadences = cadences or {}
    step_s = cadence.longest_step(cadences)
    if threshold is None:
        prelude, bar = cadence.event_threshold_flux(cadences)
    else:
        prelude, bar = "", cadence.flux_float(threshold / 100.0)

    base = (
        base_flux(["latency", "dns_latency"], f"-{int(hours)}h")
        + '|> filter(fn: (r) => r._field == "loss") '
        + CLAMP_LOSS_RATIO
    )
    flux = (
        prelude
        + base
        + f"|> filter(fn: (r) => r._value >= {bar}) "
        + "|> group() "
        + '|> sort(columns: ["_time"], desc: true) '
        + f"|> limit(n: {MAX_ROLLUP_ROWS})"
    )
    background_flux = (
        prelude
        + base
        + f"|> filter(fn: (r) => r._value > 0.0 and r._value < {bar}) "
        + "|> group() |> count()"
    )
    # How many targets reported in EACH probe step, so "most targets" has the
    # denominator of that cycle: the catalog would count targets that were
    # paused, and the whole window counts names that rotated through it.
    # distinct() writes into _value; count(column: "target") would write
    # the count into the target column instead, where nothing reads it --
    # verified live: that shape returned 0 targets.
    # Per kind too: the named destinations, the resolvers queried by
    # address and the first hop, which is evidence and not a destination.
    targets_flux = (
        base + '|> group(columns: ["_time", "_measurement", "category"]) '
        + '|> keep(columns: ["_time", "_measurement", "category", "target"]) '
        + '|> distinct(column: "target") |> count()'
    )
    # The first hop's own points, without the event bar: whether it answered
    # must not depend on the threshold a caller chose (review of #337).
    hop_flux = (
        base + f'|> filter(fn: (r) => r.category == '
        f'{flux_str(definitions.FIRST_HOP_CATEGORY)}) '
        + '|> keep(columns: ["_time", "_value"])'
    )
    rows = query(flux)
    background_rows = query(background_flux)
    targets_rows = query(targets_flux)
    hop_rows = query(hop_flux)

    events = [
        {
            "time": _iso(row.get("_time")),
            "target": row.get("target"),
            "measurement": row.get("_measurement"),
            "loss_pct": round(float(row.get("_value", 0.0)) * 100.0, 2),
            "_epoch": cadence.epoch(row.get("_time")),
            "_category": row.get("category"),
        }
        for row in rows
    ]

    # Roll up per target. A hundred loss points on one target is one story,
    # not a hundred.
    rollup: dict[tuple, dict] = {}
    for event in events:
        key = (event["target"], event["measurement"])
        entry = rollup.setdefault(
            key,
            {
                "target": event["target"],
                "measurement": event["measurement"],
                "event_count": 0,
                "max_loss_pct": 0.0,
                "first_time": event["time"],
                "last_time": event["time"],
            },
        )
        if entry["event_count"] == 0:
            own = cadence.of(cadences, event["target"])
            entry["step_s"], entry["pings"] = own.step, own.pings
        entry["event_count"] += 1
        entry["max_loss_pct"] = max(entry["max_loss_pct"], event["loss_pct"])
        # Rows arrive newest-first, so the last one seen is the oldest.
        entry["first_time"] = event["time"]
    by_target = sorted(
        rollup.values(), key=lambda e: (-e["event_count"], e["target"] or "")
    )

    # Summed over the kinds' groups at one _time; max over the _time values
    # of one step: if jitter ever splits a cycle over two, the larger count
    # is the cycle's.
    at_time: dict[tuple, int] = {}
    for row in targets_rows:
        epoch = cadence.epoch(row.get("_time"))
        if epoch is None or row.get("_value") is None:
            continue
        kind = ("hop" if row.get("category") == definitions.FIRST_HOP_CATEGORY
                else "resolver" if row.get("_measurement") == "dns_latency"
                else "named")
        key = (epoch, kind)
        at_time[key] = at_time.get(key, 0) + int(row["_value"])
    counts: dict[str, dict[int, int]] = {"named": {}, "resolver": {}, "hop": {}}
    for (epoch, kind), n in at_time.items():
        step = int(epoch // step_s) * step_s
        counts[kind][step] = max(counts[kind].get(step, 0), n)
    reporting: dict[int, int] = {}
    for kind in ("named", "resolver"):
        for step, n in counts[kind].items():
            reporting[step] = reporting.get(step, 0) + n
    background = 0
    for row in background_rows:
        if row.get("_value") is not None:
            background += int(row["_value"])

    hop_lost: dict[int, bool] = {}
    for row in hop_rows:
        epoch = cadence.epoch(row.get("_time"))
        if epoch is None or row.get("_value") is None:
            continue
        step = int(epoch // step_s) * step_s
        hop_lost[step] = (hop_lost.get(step, True)
                          and float(row["_value"]) >= definitions.LOST_RATIO)
    # The HTTP, TCP and DNS wizard targets that vote on breadth, counted as
    # the alerter counts them (common.breadth), at the caller's bar when one
    # was given and never under WIDESPREAD_LOSS_PCT. Optional: a failed
    # query, or a window past a week (breadth.MAX_HOURS), leaves the ICMP
    # and resolver targets alone, and ``breadth_only`` says so.
    breadth_steps: dict[int, dict[str, int]] = {}
    if hours <= breadth.MAX_HOURS:
        try:
            breadth_steps = breadth.fetch(
                query, hours, step_s,
                bar=None if threshold is None else threshold / 100.0,
                floor=definitions.widespread_loss_pct() / 100.0)
        except Exception:  # influx client raises many exception types
            log.warning("loss events: breadth-only query failed", exc_info=True)
    folded = episodes(events, cadences)
    widespread = widespread_runs(events, reporting, step_s, hop=hop_lost,
                                 resolvers=counts["resolver"],
                                 breadth_steps=breadth_steps)
    for event in events:
        del event["_epoch"]
        del event["_category"]

    return {
        "window_hours": hours,
        "min_loss_pct": threshold,
        "min_lost_pings": cadence.EVENT_LOST_PINGS if threshold is None else None,
        "event_count": len(events),
        "truncated": len(events) >= MAX_EVENT_ROWS,
        "targets_reporting": max(
            (reporting.get(s, 0) + breadth_steps.get(s, {}).get("reporting", 0)
             for s in reporting.keys() | breadth_steps.keys()), default=0),
        "background_points": background,
        "breadth_only": bool(breadth_steps),
        "widespread": widespread,
        "episodes": folded,
        "by_target": by_target,
        "events": events[:MAX_EVENT_ROWS],
    }


def _iso(value) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else None


def episodes(
    events: list[dict], cadences: dict[str, cadence.Cadence] | None = None
) -> list[dict]:
    """Fold per-target events (any order) into runs no more than
    GAP_STEPS of that target's steps apart. Newest episode first."""
    cadences = cadences or {}
    by_key: dict[tuple, list[dict]] = {}
    for event in events:
        if event.get("_epoch") is None:
            continue
        by_key.setdefault((event["target"], event["measurement"]), []).append(event)

    episodes: list[dict] = []
    for (target, measurement), rows in by_key.items():
        rows.sort(key=lambda e: e["_epoch"])
        step_s = cadence.of(cadences, target).step
        gap_s = GAP_STEPS * step_s
        run: list[dict] = []
        for row in rows + [None]:
            if row is not None and (not run or row["_epoch"] - run[-1]["_epoch"] <= gap_s):
                run.append(row)
                continue
            if run:
                episodes.append(
                    {
                        "target": target,
                        "measurement": measurement,
                        "start": run[0]["time"],
                        "end": run[-1]["time"],
                        "minutes": (
                            int((run[-1]["_epoch"] - run[0]["_epoch"]) // 60)
                            + step_s // 60
                        ),
                        "points": len(run),
                        "max_loss_pct": max(e["loss_pct"] for e in run),
                        "all_lost": all(e["loss_pct"] >= TOTAL_PCT for e in run),
                    }
                )
            run = [row] if row is not None else []
    episodes.sort(key=lambda e: (e["start"], e["target"]), reverse=True)
    return episodes


def widespread_runs(
    events: list[dict], reporting: dict[int, int], step_s: int = cadence.DEFAULT_STEP,
    hop: dict[int, bool] | None = None, resolvers: dict[int, int] | None = None,
    breadth_steps: dict[int, dict[str, int]] | None = None,
    loss_pct: float | None = None,
) -> list[dict]:
    """Probe steps in which WIDESPREAD_PCT of the targets reporting IN
    THAT STEP had an event of at least WIDESPREAD_LOSS_PCT (``loss_pct``,
    0-100), folded into runs: the alerter's widespread rule. Newest first.

    Steps are ``step_s`` long -- the slowest target's step, so every target
    has a point in each (``reporting`` must be bucketed the same way). A
    faster target with several points in one step counts as lost in it only
    when all of its events there were.

    The denominator is per step, not per window: the Netflix OCA targets
    rotate, so a day holds more distinct names than any one cycle does, and
    a window-wide count (23 here) put 80% out of reach of the 18 that were
    actually down together -- seen live, the night the field was built for.

    Every target lossy in the same step is one event with one cause: the
    link, or this host. When every one of them lost every packet, the host's
    own uplink was gone (the reference Pi's Wi-Fi radio hung twice in
    September 2026, associated and receiving nothing for 3 and 21 hours) --
    and nothing beyond it could be judged, so the per-target picture for
    those steps is not evidence about any target.

    Total loss is named by what still answered, as the alerter names it
    (common.definitions.total_loss): the first hop (category cpe; ``hop``:
    per step it reported in, whether it lost everything -- read from its own
    points, not from the events) lost too is this host's uplink; answering
    is the Internet beyond the house; answering with the resolvers queried
    by address (``resolvers``) while every named site was lost is this
    host's own DNS. The first hop is never a destination in the share.

    ``breadth_steps``: the HTTP, TCP and DNS wizard targets that vote on
    breadth, as counts per step (common.breadth.per_step) -- in every share
    and count, as in the alerter's rule, and never an event of their own.
    """
    if loss_pct is None:
        loss_pct = definitions.widespread_loss_pct()
    hop = hop or {}
    breadth_steps = breadth_steps or {}
    zero = {"reporting": 0, "lossy": 0, "lost": 0}
    resolvers = resolvers or {}
    resolver_hit: set[int] = set()
    named_hit: set[int] = set()
    by_step: dict[int, dict[str, bool]] = {}
    for event in events:
        epoch = event.get("_epoch")
        if epoch is None or event.get("_category") == definitions.FIRST_HOP_CATEGORY:
            continue
        if event.get("measurement") == "dns_latency":
            resolver_hit.add(int(epoch // step_s) * step_s)
        else:
            named_hit.add(int(epoch // step_s) * step_s)
    for event in events:
        if event.get("_category") == definitions.FIRST_HOP_CATEGORY:
            continue
        epoch = event.get("_epoch")
        if epoch is None or event["loss_pct"] < loss_pct:
            continue
        step = int(epoch // step_s) * step_s
        hits = by_step.setdefault(step, {})
        hits[event["target"]] = hits.get(event["target"], True) and (
            event["loss_pct"] >= TOTAL_PCT
        )

    share = definitions.widespread_pct() / 100.0

    def total(step: int) -> int:
        return reporting.get(step, 0) + breadth_steps.get(step, zero)["reporting"]

    def lossy(step: int) -> int:
        return len(by_step.get(step, {})) + breadth_steps.get(step, zero)["lossy"]

    def lost(step: int) -> int:
        return (sum(by_step.get(step, {}).values())
                + breadth_steps.get(step, zero)["lost"])

    def needed(step: int) -> float | None:
        return (share * total(step)
                if total(step) >= definitions.MIN_BREADTH_TARGETS else None)

    steps = sorted(
        step for step in by_step.keys() | breadth_steps.keys()
        if needed(step) is not None and lossy(step) and lossy(step) >= needed(step)
    )
    runs: list[dict] = []
    run: list[int] = []
    for step in steps + [None]:
        if step is not None and (
            not run or step - run[-1] <= GAP_STEPS * step_s
        ):
            run.append(step)
            continue
        if run:
            affected = max(lossy(s) for s in run)
            # A cut that starts or ends mid-cycle shows partial loss in its
            # edge cycles; the 3 h 20 min hang began with one at 95%. Total
            # loss in every cycle but the two edges is total loss.
            lost_steps = sum(
                1 for s in run if lost(s) >= needed(s)
            )
            all_lost = (
                lost_steps >= len(run) - 2 if len(run) > 2 else lost_steps == len(run)
            )
            uplink = all_lost and lost_steps >= definitions.TOTAL_MIN_CYCLES
            reading = None
            if uplink:
                measured = [s for s in run if s in hop]
                first_hop = (None if not measured else
                             "lost" if all(hop[s] for s in measured)
                             else "answers")
                # Named destinations among the lost: breadth-only targets
                # alone, the pinged ones answering, are not this host's DNS.
                names_only = (any(resolvers.get(s) for s in run)
                              and any(s in named_hit for s in run)
                              and not any(s in resolver_hit for s in run))
                reading, why = definitions.total_loss(first_hop, names_only)
            runs.append(
                {
                    "start": datetime.fromtimestamp(run[0], tz=timezone.utc).isoformat(),
                    "end": datetime.fromtimestamp(run[-1], tz=timezone.utc).isoformat(),
                    "minutes": (run[-1] - run[0]) // 60 + step_s // 60,
                    "targets_affected": affected,
                    "targets_total": max(total(s) for s in run),
                    "all_lost": all_lost,
                    "reading": reading,
                    "cause": (
                        f"every target lost every packet for {lost_steps} cycles "
                        f"— {why}"
                        if uplink
                        else "the link: a brief cut that hit every target at once"
                        + (", every packet lost" if all_lost else "")
                    ),
                }
            )
        run = [step] if step is not None else []
    runs.reverse()
    return runs
