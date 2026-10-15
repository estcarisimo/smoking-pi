"""What a period carries besides its detection: whether the monitor could
hear, and how much of the span had data.

The detectors run on the raw series, as published. What this adds is read
off the same evidence the alerter and ``common/microcuts.py`` use for the
host's own radio: a ``wifi_link`` sample is deaf when the interface carries
the default route and either is not associated or its received-packet
counter did not move. On the reference Pi a radio hang records 100 % loss
on every destination for hours; BCP on loss then finds a "degradation" of
every target that is really the monitor being deaf. The period stays (the
method is untouched) and is labeled ``monitor_deaf`` when at least half of
its bins were deaf, so a dashboard can shade it as the monitor's and a
reader does not blame the destination.

Coverage is the share of a period's 15-minute bins that had data at all.
A congestion period that spans a gap of hours -- a hang, a reboot -- reads
as such with ``coverage`` beside it instead of looking like a solid span.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

BIN_S = 900
# A wifi_link sample every ~10 s: deaf samples * SAMPLE_S / BIN_S is the
# share of a bin the radio spent deaf.
SAMPLE_S = 10
DEAF_BIN_SHARE = 0.5
DEAF_PERIOD_SHARE = 0.5

MONITOR_DEAF = "monitor_deaf"
LOSS = "loss"


def _epoch(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return pd.Timestamp(value).timestamp()
    except (TypeError, ValueError):
        return None


def deaf_bins(rows: list[dict]) -> set[int]:
    """The 15-minute bins (by start epoch) in which the radio was deaf for
    at least half the time, from :func:`common.microcuts.uplink_flux` rows
    (deaf samples only)."""
    count: dict[int, int] = {}
    for row in rows:
        epoch = _epoch(row.get("_time"))
        if epoch is None:
            continue
        b = int(epoch // BIN_S) * BIN_S
        count[b] = count.get(b, 0) + 1
    return {b for b, n in count.items() if n * SAMPLE_S >= DEAF_BIN_SHARE * BIN_S}


def _span_bins(start: int, end: int) -> list[int]:
    first = int(start // BIN_S) * BIN_S
    return list(range(first, int(end), BIN_S)) or [first]


def label(periods: list[dict], deaf: set[int]) -> list[dict]:
    """Set ``class`` and ``deaf_share`` on each period, in place."""
    for p in periods:
        span = _span_bins(p["start"], p["end"])
        share = sum(1 for b in span if b in deaf) / len(span)
        p["deaf_share"] = share
        p["class"] = MONITOR_DEAF if share >= DEAF_PERIOD_SHARE else LOSS
    return periods


def coverage(periods: list[dict], epochs) -> list[dict]:
    """Set ``coverage`` on each period, in place: the share of its bins with
    at least one sample in ``epochs`` (a Series of epoch seconds)."""
    have = {int(e // BIN_S) * BIN_S for e in epochs} if len(epochs) else set()
    for p in periods:
        span = _span_bins(p["start"], p["end"])
        p["coverage"] = sum(1 for b in span if b in have) / len(span)
    return periods
