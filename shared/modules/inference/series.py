"""What the detectors read: each ICMP target's pings and loss from InfluxDB.

``latency`` points carry one field per ping of a probe cycle (``ping1`` ..
``pingN``, seconds, SORTED by SmokePing: the order the pings were sent is
lost) and ``loss`` as a 0-1 ratio. A lost ping writes no ``pingK`` field, so
the pings of a cycle are the answered ones, exactly what Jitterbug expects
as input (one row per RTT sample, lost ones absent).
"""

from __future__ import annotations

import logging
import os
import re

import pandas as pd

import bins
import deaf
from common import microcuts
from common.microcuts import uplink_flux
from common.tsdb import flux_str, influx_bucket, query_influx

# Influx tag vocabulary (not the database's: top_sites is topsites here).
DEFAULT_CATEGORIES = "topsites,custom,cpe"
PING_FIELD = re.compile(r"^ping\d+$")
DEAF_LOOKBACK_S = 3600
log = logging.getLogger("inference.series")


def categories() -> list[str]:
    raw = os.environ.get("INFERENCE_CATEGORIES", "") or DEFAULT_CATEGORIES
    return [c.strip() for c in raw.split(",") if c.strip()]


def explicit_targets() -> list[str]:
    raw = os.environ.get("INFERENCE_TARGETS", "")
    return [t.strip() for t in raw.split(",") if t.strip()]


def targets(start: int, query=query_influx) -> list[tuple[str, str]]:
    """(target, category) of every ICMP target that answered since ``start``:
    INFERENCE_TARGETS when set, else the targets in INFERENCE_CATEGORIES."""
    wanted = set(explicit_targets())
    cats = set(categories())
    flux = (
        f"from(bucket: {flux_str(influx_bucket())}) "
        f"|> range(start: {int(start)}) "
        '|> filter(fn: (r) => r._measurement == "latency" and r._field == "loss") '
        '|> keep(columns: ["target", "category"]) '
        '|> distinct(column: "target")'
    )
    found = set()
    for row in query(flux):
        target, category = row.get("target") or row.get("_value"), row.get("category") or ""
        if not target:
            continue
        if wanted and target not in wanted:
            continue
        if not wanted and category not in cats:
            continue
        found.add((target, category))
    return sorted(found)


def fetch(target: str, start: int, query=query_influx) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(pings, loss) of ``target`` since ``start`` (epoch seconds).

    pings: one row per answered ping, columns ``epoch`` (s) and ``values``
    (ms), the input format of Jitterbug. loss: one row per probe cycle,
    ``epoch`` and ``loss_pct`` (0-100)."""
    flux = (
        f"from(bucket: {flux_str(influx_bucket())}) "
        f"|> range(start: {int(start)}) "
        f'|> filter(fn: (r) => r._measurement == "latency" and r.target == {flux_str(target)}) '
        '|> filter(fn: (r) => r._field == "loss" or r._field =~ /^ping[0-9]+$/) '
        '|> keep(columns: ["_time", "_field", "_value"])'
    )
    pings, loss = [], []
    for row in query(flux):
        field, value, when = row.get("_field"), row.get("_value"), row.get("_time")
        if value is None or when is None:
            continue
        epoch = int(pd.Timestamp(when).timestamp())
        if field == "loss":
            loss.append((epoch, min(1.0, max(0.0, float(value))) * 100.0))
        elif PING_FIELD.match(str(field)):
            pings.append((epoch, float(value) * 1000.0))
    p = pd.DataFrame(pings, columns=["epoch", "values"]).sort_values("epoch", kind="stable")
    lo = pd.DataFrame(loss, columns=["epoch", "loss_pct"]).sort_values("epoch", kind="stable")
    return p.reset_index(drop=True), lo.reset_index(drop=True)


def deaf_rows(start: int, query=query_influx) -> list[dict]:
    """The host's deaf Wi-Fi samples since ``start``: the same query the
    alerter and the MCP server use to tell a radio hang from a cut
    (``common.microcuts.uplink_flux``). Empty on a wired host; raises when
    the database cannot be read (deaf_bins then labels nothing deaf)."""
    rows = list(query(uplink_flux(str(int(start)))))
    # The shared query stops at MAX_ROWS * 4 samples, oldest first: about
    # 55 h of deaf time at a 10 s cadence. Past that the newest hangs would
    # read as loss again, so say so rather than let it pass in silence.
    if len(rows) >= microcuts.MAX_ROWS * 4:
        log.warning("deaf samples truncated at %d rows: the newest deaf periods may "
                    "be labeled loss; a %d-row week means the radio was deaf most of it",
                    len(rows), len(rows))
    return rows


def deaf_bins(start: int, now: int, query=query_influx, directory=None) -> set[int]:
    """The deaf bins since ``start``, closed ones from the state volume
    (bins.py): the full query measured 4.5 s a pass on the staging Pi. Each
    read starts DEAF_LOOKBACK_S early, because uplink_flux's ``difference``
    and ``fill`` need the samples before a bin to judge its first ones. An
    unreadable database labels nothing deaf and caches nothing."""
    def fetch(since: int, stop: int) -> pd.DataFrame:
        table = deaf.counts(deaf_rows(since - DEAF_LOOKBACK_S, query))
        return table[(table["bin"] >= since) & (table["bin"] < stop)]

    try:
        return deaf.bins_of(bins.read("deaf", start, now, fetch, deaf.COUNT_COLUMNS,
                                      directory))
    except Exception:  # noqa: BLE001 -- a label is optional; the detectors are not
        log.warning("could not read the deaf samples", exc_info=True)
        return set()
