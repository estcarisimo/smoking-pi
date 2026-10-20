"""Closed 15-minute bins kept in the state volume, so a pass re-reads only the tail.

Every hourly pass used to re-read its whole 7-day window: the radio's deaf
samples (``common.microcuts.uplink_flux``, a pivot InfluxDB cannot push
down) measured 4.5 s on the staging Pi, and the loss bins of the shared pass
another second, to learn about one new hour. A bin that closed hours ago does
not change, so it is read once and kept here, like the aggregate stream
(aggregate.py); each pass reads from ``TAIL_S`` before the last pass's stop,
which also picks up points that arrived a little late.

Points that arrive much later (an exporter catching up after an outage)
would be missed by the tail, so the whole window is read again once a day
(``REFRESH_S``), and whenever the cache does not cover the window asked for
(a first pass, a lost volume, a longer window). A cache that cannot be read
is a cache miss, never an error: the pass then costs what it used to.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path

import pandas as pd

log = logging.getLogger("inference.bins")

BIN_S = 900
TAIL_S = 3600
REFRESH_S = 86400


def state_dir() -> Path:
    return Path(os.environ.get("INFERENCE_STATE_DIR", "/var/lib/inference"))


def _align(epoch: int) -> int:
    return int(epoch) // BIN_S * BIN_S


def _load(name: str, columns: list[str], directory: Path) -> tuple[pd.DataFrame, dict] | None:
    try:
        meta = json.loads((directory / f"{name}.json").read_text())
        df = pd.read_csv(directory / f"{name}.csv")
        if list(df.columns) != columns or not {"from", "through", "full_at"} <= set(meta):
            return None
        meta = {k: int(meta[k]) for k in ("from", "through", "full_at")}
        df = df.astype({"bin": "int64"})
    except (OSError, ValueError, TypeError, KeyError):
        return None
    return df, meta


def _save(name: str, df: pd.DataFrame, meta: dict, directory: Path) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        for suffix, write in ((".csv", lambda p: df.to_csv(p, index=False)),
                              (".json", lambda p: p.write_text(json.dumps(meta)))):
            path = directory / f"{name}{suffix}"
            tmp = path.with_suffix(suffix + ".tmp")
            write(tmp)
            os.replace(tmp, path)
    except OSError:
        log.warning("could not save the %s cache", name, exc_info=True)


def read(name: str, start: int, now: int, fetch: Callable[[int, int], pd.DataFrame],
         columns: list[str], directory: Path | None = None) -> pd.DataFrame:
    """The bins of [start, now), ``columns`` with ``bin`` (the bin's start
    epoch) first. ``fetch(since, stop)`` reads the bins of [since, stop) from
    InfluxDB, ``since`` on a bin boundary; this calls it for the whole window
    or only for its tail, and keeps what it returns."""
    directory = directory or state_dir()
    first, stop = _align(start), int(now)
    cached = _load(name, columns, directory)
    full = (cached is None or cached[1]["from"] > first
            or cached[1]["through"] - TAIL_S < first
            or stop - cached[1]["full_at"] >= REFRESH_S)
    since = first if full else _align(cached[1]["through"] - TAIL_S)
    new = fetch(since, stop).reindex(columns=columns)
    if full:
        out, full_at = new, stop
    else:
        old = cached[0]
        out = pd.concat([old[(old["bin"] >= first) & (old["bin"] < since)], new],
                        ignore_index=True)
        full_at = cached[1]["full_at"]
    out = out[out["bin"] >= first].sort_values("bin", kind="stable").reset_index(drop=True)
    _save(name, out, {"from": first, "through": stop, "full_at": full_at}, directory)
    return out

