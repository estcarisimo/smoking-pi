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
its loss fell in deaf bins, so a dashboard can shade it as the monitor's and
a reader does not blame the destination.

The share is of the loss, not of the bins. Since the watchdog reloads the
radio's driver, a hang lasts 11-12 minutes: one deaf bin. Hours later BCP
merges that bin with the clean stretch around it, and a period of 28 bins
with one deaf one -- all of its loss in that bin -- would read 1/28 deaf by
bins. ``deaf_share`` (the bin share) is still written beside the class.

Coverage is the share of a period's 15-minute bins that had data at all.
A congestion period that spans a gap of hours -- a hang, a reboot -- reads
as such with ``coverage`` beside it instead of looking like a solid span.
"""
from __future__ import annotations

import os
from typing import Any

import pandas as pd

import degradation

BIN_S = 900
# The exporter's cadence (WIFI_SAMPLE_INTERVAL, 10 s by default; the compose
# file hands the same value to this service): deaf samples * cadence / BIN_S
# is the share of a bin the radio spent deaf.
DEFAULT_SAMPLE_S = 10
DEAF_BIN_SHARE = 0.5
DEAF_PERIOD_SHARE = 0.5

COUNT_COLUMNS = ["bin", "samples"]

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


def sample_s() -> int:
    try:
        value = int(os.environ.get("WIFI_SAMPLE_INTERVAL", "") or DEFAULT_SAMPLE_S)
    except ValueError:
        return DEFAULT_SAMPLE_S
    return value if value > 0 else DEFAULT_SAMPLE_S


def counts(rows: list[dict]) -> pd.DataFrame:
    """Deaf samples per 15-minute bin (``bin``, its start epoch, and
    ``samples``) from :func:`common.microcuts.uplink_flux` rows."""
    count: dict[int, int] = {}
    for row in rows:
        epoch = _epoch(row.get("_time"))
        if epoch is None:
            continue
        b = int(epoch // BIN_S) * BIN_S
        count[b] = count.get(b, 0) + 1
    return pd.DataFrame({"bin": list(count), "samples": list(count.values())},
                        columns=COUNT_COLUMNS).astype("int64")


def deaf_bins(rows: list[dict]) -> set[int]:
    """The 15-minute bins (by start epoch) in which the radio was deaf for
    at least half the time, from :func:`common.microcuts.uplink_flux` rows
    (deaf samples only)."""
    return bins_of(counts(rows))


def bins_of(table: pd.DataFrame) -> set[int]:
    """The deaf bins of a :func:`counts` table."""
    cadence = sample_s()
    return {int(b) for b, n in zip(table["bin"], table["samples"])
            if n * cadence >= DEAF_BIN_SHARE * BIN_S}


def _span_bins(start: int, end: int) -> list[int]:
    first = int(start // BIN_S) * BIN_S
    return list(range(first, int(end), BIN_S)) or [first]


def label(periods: list[dict], deaf: set[int], loss: pd.DataFrame) -> list[dict]:
    """Set ``class``, ``deaf_loss_share`` and ``deaf_share`` on each period,
    in place.

    ``loss`` is the per-cycle loss the detector read (``epoch``,
    ``loss_pct``). It carries no ping counts, so the loss is measured as the
    detector measures it: each bin's mean loss (``degradation.bins``).
    ``deaf_loss_share`` is the sum of that over the period's deaf bins
    divided by the sum over all its bins, and the period is ``monitor_deaf``
    when it is at least DEAF_PERIOD_SHARE. A period with no loss at all
    (a detected one always has some) has no ``deaf_loss_share`` and is
    classed by ``deaf_share``, the share of its bins that were deaf."""
    per_bin = {int(b): float(v) for b, v in degradation.bins(loss).items()}
    for p in periods:
        span = _span_bins(p["start"], p["end"])
        share = sum(1 for b in span if b in deaf) / len(span)
        total = sum(per_bin.get(b, 0.0) for b in span)
        p["deaf_share"] = share
        if total > 0:
            in_deaf = sum(per_bin.get(b, 0.0) for b in span if b in deaf)
            p["deaf_loss_share"] = decisive = in_deaf / total
        else:
            p.pop("deaf_loss_share", None)
            decisive = share
        p["class"] = MONITOR_DEAF if decisive >= DEAF_PERIOD_SHARE else LOSS
    return periods


def coverage(periods: list[dict], epochs) -> list[dict]:
    """Set ``coverage`` on each period, in place: the share of its bins with
    at least one sample in ``epochs`` (a Series of epoch seconds)."""
    have = {int(e // BIN_S) * BIN_S for e in epochs} if len(epochs) else set()
    for p in periods:
        span = _span_bins(p["start"], p["end"])
        p["coverage"] = sum(1 for b in span if b in have) / len(span)
    return periods
