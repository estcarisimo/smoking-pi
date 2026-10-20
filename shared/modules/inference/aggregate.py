"""Shared persistent congestion: Jitterbug, as published, on an aggregate input.

congestion.py runs Jitterbug on one target's pings. This runs the same
analyzer (congestion.detect, the paper's ``bcp`` + ``ks_test``, no parameter
changed) once, on one stream built across destinations, to ask whether a
queue stayed built up for the whole connection rather than on one route:

1. **Relative pings.** Each voter's pings minus that voter's own floor: the
   5th percentile of its 15-minute minimum RTT over the trailing 7 days
   (the bin itself excluded). Destinations 2 ms and 170 ms away then share
   one scale, and a floor that moved for one route stays that route's.
2. **One sample set per cycle.** For each 5-minute probe cycle, the ten
   deciles (5th to 95th percentile) of every voter's relative pings, when at
   least MIN_REPORTING voters answered in it. Jitterbug reads those ten
   values as the cycle's RTT samples. They are shifted by OFFSET_MS because
   Jitterbug accepts only positive RTTs; a constant shift moves no change
   point, no jump and no KS statistic.
3. **The same voters and the same monitor bins as shared.py.** The voters
   are the shared pass's (a member that loses nothing in at least half its
   bins); a cycle in a bin that was the monitor's -- the radio deaf, or the
   gateway losing with the destinations -- is not part of the stream.

The stream is built **incrementally** and kept in the inference state
volume (``aggregate.csv``): each pass adds only the cycles since the last
one. Re-reading 14 days of every voter's raw pings each hour measured 22 s
and 662 MB on a staging Pi with ~23 ICMP targets (831k rows), so about a
minute and 2 GB with the ~65 of the reference Pi. A cycle's value depends
only on that cycle's pings and on floors from before it, so it never needs
recomputing; the cost is that a cycle keeps the voters of the pass that
wrote it. The first pass fills the degradation window (the span whose
monitor bins the shared pass knows); the stream reaches the congestion
window's full 14 days a week later. Cycles are written once their 15-minute
bin and the next are complete, since the monitor rule looks one bin ahead.

On 52 days of the reference Pi this found no shared congestion: the shared
evening loss raised the median relative floor by 0.25-0.55 ms, below what
Jitterbug calls a jump. That is the expected answer, not a fault to tune
away: loss is shared_degradation's signal, not this one's. Experimental,
never in the alert path.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

import congestion
import deaf
from common.tsdb import flux_str, influx_bucket, query_influx

log = logging.getLogger("inference.aggregate")

CYCLE_S = 300
BIN_S = 900
DAY = 86400
FLOOR_BINS = 7 * 96          # trailing 7 days of 15-minute minimums
FLOOR_MIN_BINS = 96          # a voter needs a day of history for a floor
FLOOR_QUANTILE = 0.05
DECILES = np.linspace(0.05, 0.95, 10)
MIN_REPORTING = 8
OFFSET_MS = 100.0
COLUMNS = ["epoch", "n"] + [f"d{i}" for i in range(1, 11)]


def _state_path() -> Path:
    return Path(os.environ.get("INFERENCE_STATE_DIR", "/var/lib/inference")) / "aggregate.csv"


def _align(epoch: int, step: int) -> int:
    return int(epoch) // step * step


def floor_flux(start: int, stop: int) -> str:
    """Per target, the 15-minute minimum RTT (``ping1``: SmokePing sorts a
    cycle's pings), in the storage engine's shape as in shared.bins_flux."""
    return (f"from(bucket: {flux_str(influx_bucket())}) "
            f"|> range(start: {_align(start, BIN_S)}, stop: {_align(stop, BIN_S)}) "
            '|> filter(fn: (r) => r._measurement == "latency" and r._field == "ping1") '
            "|> aggregateWindow(every: 15m, fn: min, createEmpty: false) "
            '|> keep(columns: ["_time", "_value", "target"])')


def pings_flux(start: int, stop: int) -> str:
    """Every answered ping of every ICMP target in [start, stop)."""
    return (f"from(bucket: {flux_str(influx_bucket())}) "
            f"|> range(start: {int(start)}, stop: {int(stop)}) "
            '|> filter(fn: (r) => r._measurement == "latency" and r._field =~ /^ping[0-9]+$/) '
            '|> keep(columns: ["_time", "_value", "target"])')


def floors(rows: list[dict], start: int, stop: int) -> pd.DataFrame:
    """bins (15-minute START) x targets: each target's trailing floor in ms."""
    recs = [(int(pd.Timestamp(r["_time"]).timestamp()) - BIN_S, r["target"],
             float(r["_value"]) * 1000.0)
            for r in rows if r.get("target") and r.get("_value") is not None
            and r.get("_time") is not None]
    idx = pd.Index(range(_align(start, BIN_S), _align(stop, BIN_S), BIN_S), name="bin")
    if not recs:
        return pd.DataFrame(index=idx)
    df = pd.DataFrame(recs, columns=["bin", "target", "ms"])
    mins = df.pivot_table(index="bin", columns="target", values="ms", aggfunc="min")
    # A regular index, so the window counts 15-minute bins, not rows.
    mins = mins.reindex(idx)
    return mins.rolling(FLOOR_BINS, min_periods=FLOOR_MIN_BINS).quantile(FLOOR_QUANTILE).shift(1)


def cycles(rows: list[dict], floor: pd.DataFrame, voters: list[str],
           monitor: set[int]) -> pd.DataFrame:
    """One row per probe cycle: ``epoch``, ``n`` (voters that answered) and
    ``d1``..``d10``, the deciles of their pings over their own floors (ms)."""
    want = set(voters)
    recs = [(int(pd.Timestamp(r["_time"]).timestamp()), r["target"], float(r["_value"]) * 1000.0)
            for r in rows if r.get("target") in want and r.get("_value") is not None
            and r.get("_time") is not None]
    if not recs or floor.empty:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(recs, columns=["epoch", "target", "ms"])
    df["epoch"] = df["epoch"] // CYCLE_S * CYCLE_S
    df["bin"] = df["epoch"] // BIN_S * BIN_S
    df = df[~df["bin"].isin(list(monitor))]
    mine = floor.reindex(columns=[c for c in floor.columns if c in want])
    mine.columns.name = "target"
    stacked = mine.rename_axis(index="bin").stack().dropna().rename("floor").reset_index()
    df = df.merge(stacked, on=["bin", "target"], how="inner")
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    df["rel"] = df["ms"] - df["floor"]
    groups = df.groupby("epoch")
    n = groups["target"].nunique()
    q = groups["rel"].quantile(DECILES).unstack()
    q.columns = [f"d{i}" for i in range(1, 11)]
    out = q[n >= MIN_REPORTING].copy()
    out.insert(0, "n", n[n >= MIN_REPORTING])
    out = out.reset_index()
    return out[COLUMNS]


def load(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_csv(path)
    except (OSError, ValueError):
        return pd.DataFrame(columns=COLUMNS)
    if list(df.columns) != COLUMNS:
        log.warning("%s has unexpected columns; starting the stream over", path)
        return pd.DataFrame(columns=COLUMNS)
    return df


def save(df: pd.DataFrame, path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        df.to_csv(tmp, index=False, float_format="%.3f")
        os.replace(tmp, path)
    except OSError:
        log.warning("could not save %s", path, exc_info=True)


def extend(stream: pd.DataFrame, voters: list[str], monitor: set[int], begin_floor: int,
           now: int, query=query_influx) -> tuple[pd.DataFrame, int]:
    """Add the cycles since the stream's last one (or since ``begin_floor``,
    the start of the span whose monitor bins are known). Returns the stream
    and how many cycles were added."""
    # The monitor rule looks one bin ahead: write a bin once the next is complete.
    stop = _align(now, BIN_S) - BIN_S
    last = int(stream["epoch"].max()) if len(stream) else None
    begin = max(last + CYCLE_S if last is not None else 0, _align(begin_floor + BIN_S - 1, BIN_S))
    if begin >= stop:
        return stream, 0
    floor = floors(query(floor_flux(begin - (FLOOR_BINS + 1) * BIN_S, stop)),
                   begin - (FLOOR_BINS + 1) * BIN_S, stop)
    added = []
    # A day at a time: the first pass reads a week of raw pings, and one
    # query for all of it would hold every row in memory at once.
    for chunk in range(begin, stop, DAY):
        rows = query(pings_flux(chunk, min(chunk + DAY, stop)))
        added.append(cycles(rows, floor, voters, monitor))
    new = pd.concat([a for a in added if len(a)], ignore_index=True) if any(
        len(a) for a in added) else pd.DataFrame(columns=COLUMNS)
    if not len(new):
        return stream, 0
    out = pd.concat([stream, new], ignore_index=True) if len(stream) else new
    return out, len(new)


def samples(stream: pd.DataFrame) -> pd.DataFrame:
    """The stream as Jitterbug's input: ten rows per cycle, ``epoch`` and
    ``values`` (ms, shifted by OFFSET_MS; a sample still not positive is
    dropped, as Jitterbug would refuse it)."""
    if not len(stream):
        return pd.DataFrame(columns=["epoch", "values"])
    long = stream.melt(id_vars=["epoch"], value_vars=COLUMNS[2:], value_name="values")
    long["values"] = long["values"] + OFFSET_MS
    long = long[long["values"] > 0].dropna(subset=["values"])
    return long.sort_values("epoch", kind="stable")[["epoch", "values"]].reset_index(drop=True)


def run(shared_result: dict, c_start: int, d_start: int, now: int, query=query_influx,
        find=congestion.detect, path: Path | None = None) -> dict:
    """Extend the stream with the shared pass's voters and monitor bins, then
    run Jitterbug on its part inside the congestion window."""
    path = path or _state_path()
    started = time.monotonic()
    stream = load(path)
    stream = stream[stream["epoch"] >= c_start] if len(stream) else stream
    stream, added = extend(stream, shared_result.get("voter_targets") or [],
                           set(shared_result.get("monitor") or ()), d_start, now, query)
    save(stream, path)
    built = time.monotonic() - started
    found = find(samples(stream))
    deaf.coverage(found["periods"], stream["epoch"] if len(stream) else pd.Series(dtype=int))
    return {"periods": found["periods"], "cycles": int(len(stream)), "added": added,
            "change_points": found["change_points"],
            "build_seconds": round(built, 1),
            "seconds": round(time.monotonic() - started, 1)}
