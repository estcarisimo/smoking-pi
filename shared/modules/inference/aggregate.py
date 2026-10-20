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
   values as the cycle's RTT samples, **at the distance of a typical
   voter**: the median of the cycles' median voter floor over the window,
   one constant per pass (``base``). Jitterbug accepts only positive RTTs,
   and its BCP is not indifferent to where the series sits: its Student-t
   likelihood's prior is centered on 0, so the same 3 ms step measured 2
   change points at +10 ms and none at +100 ms or +1000 ms. Any offset is
   then a scale choice; this one is read off the data, not tuned, and
   makes the stream look like what Jitterbug would see on this connection's
   typical destination. It is constant within a pass, so a voter joining
   or leaving moves no step into the stream. Kept on purpose (decided
   2026-10-20, docs/inference.md): experimental, like the whole stream.
3. **The same voters and the same monitor bins as shared.py.** The voters
   are the shared pass's (a member that loses nothing in at least half its
   bins); a cycle in a bin that was the monitor's -- the radio deaf, an
   upgrade, or the gateway losing with the destinations -- is not part of
   the stream.

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
bin and the two after it are complete: the monitor rule pads one bin on each
side of a gateway rule that itself needs the next bin, so it looks two bins
ahead.

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
LOOKAHEAD_BINS = 2           # shared.monitor_bins reads up to two bins ahead
COLUMNS = ["epoch", "n", "base"] + [f"d{i}" for i in range(1, 11)]
DECILE_COLUMNS = COLUMNS[3:]


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
    """One row per probe cycle: ``epoch``, ``n`` (voters that answered),
    ``base`` (the median of their floors, ms) and ``d1``..``d10``, the
    deciles of their pings over their own floors (ms)."""
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
    base = df.drop_duplicates(["epoch", "target"]).groupby("epoch")["floor"].median()
    q = groups["rel"].quantile(DECILES).unstack()
    q.columns = DECILE_COLUMNS
    keep = n >= MIN_REPORTING
    out = q[keep].copy()
    out.insert(0, "base", base[keep])
    out.insert(0, "n", n[keep])
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
    # A cell that does not read as a number (a hand edit, a bad sector)
    # would fail every pass from now on: drop its row instead.
    df = df.apply(pd.to_numeric, errors="coerce")
    bad = df.isna().any(axis=1)
    if bad.any():
        log.warning("%s: dropped %d unreadable rows", path, int(bad.sum()))
        df = df[~bad]
    return df.astype({"epoch": "int64", "n": "int64"}).reset_index(drop=True)


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
    # The monitor rule looks two bins ahead: write a bin once they are complete.
    stop = _align(now, BIN_S) - LOOKAHEAD_BINS * BIN_S
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


def samples(stream: pd.DataFrame) -> tuple[pd.DataFrame, float, int]:
    """The stream as Jitterbug's input: ten rows per cycle, ``epoch`` and
    ``values`` (ms, at the typical voter's distance ``base``). Returns the
    input, the base and how many samples were dropped for not being
    positive (Jitterbug would refuse them): a decile more than ``base``
    below the floors, which takes a route that got much shorter."""
    if not len(stream):
        return pd.DataFrame(columns=["epoch", "values"]), 0.0, 0
    base = float(stream["base"].median())
    long = stream.melt(id_vars=["epoch"], value_vars=DECILE_COLUMNS, value_name="values")
    long["values"] = long["values"] + base
    ok = long["values"] > 0
    dropped = int((~ok).sum())
    if dropped:
        log.warning("aggregate stream: %d samples below zero at a %.1f ms base were dropped",
                    dropped, base)
    long = long[ok]
    out = long.sort_values("epoch", kind="stable")[["epoch", "values"]].reset_index(drop=True)
    return out, base, dropped


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
    inp, base, dropped = samples(stream)
    found = find(inp)
    deaf.coverage(found["periods"], stream["epoch"] if len(stream) else pd.Series(dtype=int))
    # Where the stream starts: the caller replaces periods from here on only,
    # so a stream that restarted (a lost volume) keeps the older periods.
    since = int(stream["epoch"].min()) if len(stream) else None
    return {"periods": found["periods"], "cycles": int(len(stream)), "added": added,
            "since": since, "base_ms": round(base, 2), "dropped": dropped,
            "change_points": found["change_points"],
            "build_seconds": round(built, 1),
            "seconds": round(time.monotonic() - started, 1)}
