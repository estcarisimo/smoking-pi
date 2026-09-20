"""What a microcut is, in one place.

``cpe_latency`` holds one row per 10 s probe window (5 pps, so every loss
value is a multiple of 2%), one window every ~30 s. Home gateways rate-limit
ICMP, so on the reference installation 98% of windows show *some* loss and
the daily 90th percentile sits at 14-22% with nothing wrong. Counting
"windows with any loss" therefore counts nearly everything, and a top-5 of
raw windows on a quiet day is the floor's tail dressed as an event -- which
is exactly how the assistant came to report "strong microcuts, worst 82%"
for weeks (docs/detection-reliability.md).

The definitions every consumer (MCP tool, alerter rule, digest, AI report)
now shares:

- A **cut window** is a window whose loss exceeds ``MICROCUT_LOSS_PCT``
  (default 50). Below that is the floor or its tail, never a microcut.
- A **cut** is a run of cut windows with no more than one missing window
  between them (``GAP_S``). Its duration is first-to-last plus the window.
- A cut is **confirmed** when it spans two or more windows or any window
  lost everything; a single window at 51-99% is a **possible** cut -- five
  seconds of nothing is real, but one in a day on a rate-limited gateway is
  not a pattern.
- The **floor** is the distribution of every window's loss (p50 / p90),
  reported beside the cuts so "the gateway had a bad day" can be said with a
  number instead of a list.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from .tsdb import base_flux

# Loss percent above which a 10 s window is a cut window. Read from the same
# env var the alerter's rule and the compose file declare, so one number
# means one thing everywhere.
DEFAULT_LOSS_PCT = 50.0  # MICROCUT_LOSS_PCT
# The probe's window and cadence (CPE_PROBE_WINDOW + CPE_PROBE_IDLE, 10 + 20
# by default). Two consecutive cut windows are 30 s apart; GAP_S tolerates
# one missing window between them. Rescale if CPE_PROBE_IDLE changes.
WINDOW_S = 10
GAP_S = 70
# Rows fetched for folding. Every window above 50% for a week on a healthy
# gateway is a few dozen; a multi-hour total outage is ~120 per hour.
MAX_ROWS = 5000


def loss_pct() -> float:
    try:
        return float(os.environ.get("MICROCUT_LOSS_PCT", "") or DEFAULT_LOSS_PCT)
    except ValueError:
        return DEFAULT_LOSS_PCT


def cut_windows_flux(range_start: str, threshold_pct: float | None = None) -> str:
    """Every cpe_latency window above the threshold, oldest first, with its
    target and protocol -- the raw material for :func:`fold_cuts`."""
    if threshold_pct is None:
        threshold_pct = loss_pct()
    return (
        base_flux(["cpe_latency"], range_start)
        + '|> filter(fn: (r) => r._field == "loss") '
        + f"|> filter(fn: (r) => r._value > {float(threshold_pct)}) "
        + '|> keep(columns: ["_time", "_value", "target", "protocol"]) '
        + '|> group() |> sort(columns: ["_time"]) '
        + f"|> limit(n: {MAX_ROWS})"
    )


def _epoch(value: Any) -> float | None:
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else None


def fold_cuts(rows: list[dict], gap_s: float = GAP_S) -> list[dict]:
    """Fold cut windows into cuts, per target+protocol. Oldest first.

    Each cut: ``target``, ``protocol``, ``start``, ``end`` (ISO), ``seconds``
    (first-to-last plus one window), ``windows``, ``max_loss_pct``,
    ``total`` (every window lost everything), ``confirmed``.
    """
    by_key: dict[tuple, list[tuple[float, dict]]] = {}
    for row in rows:
        epoch = _epoch(row.get("_time"))
        value = row.get("_value")
        if epoch is None or value is None:
            continue
        key = (row.get("target"), row.get("protocol"))
        by_key.setdefault(key, []).append((epoch, row))

    cuts: list[dict] = []
    for (target, protocol), timed in by_key.items():
        timed.sort(key=lambda item: item[0])
        run: list[tuple[float, dict]] = []
        for item in timed + [None]:
            if item is not None and (not run or item[0] - run[-1][0] <= gap_s):
                run.append(item)
                continue
            if run:
                losses = [float(r["_value"]) for _, r in run]
                cuts.append(
                    {
                        "target": target,
                        "protocol": protocol,
                        "start": _iso(run[0][1].get("_time")),
                        "end": _iso(run[-1][1].get("_time")),
                        "start_epoch": run[0][0],
                        "seconds": int(run[-1][0] - run[0][0]) + WINDOW_S,
                        "windows": len(run),
                        "max_loss_pct": round(max(losses), 2),
                        "total": all(v >= 100.0 for v in losses),
                        "confirmed": len(run) >= 2 or max(losses) >= 100.0,
                    }
                )
            run = [item] if item is not None else []
    cuts.sort(key=lambda c: (c["start_epoch"], c["target"] or ""))
    return cuts


def describe_cuts(cuts: list[dict]) -> str:
    """One clause for a message: '1 cut of 2 min 40 s (6 windows, all at
    100%)' or '3 possible cuts (single windows, 52-78%)'."""
    confirmed = [c for c in cuts if c["confirmed"]]
    possible = [c for c in cuts if not c["confirmed"]]
    parts: list[str] = []
    if confirmed:
        longest = max(confirmed, key=lambda c: c["seconds"])
        detail = f"{longest['windows']} windows"
        if longest["total"]:
            detail += ", all at 100%"
        else:
            detail += f", worst {longest['max_loss_pct']:g}%"
        if len(confirmed) == 1:
            parts.append(f"1 cut of {_duration(longest['seconds'])} ({detail})")
        else:
            parts.append(
                f"{len(confirmed)} cuts, the longest {_duration(longest['seconds'])} "
                f"({detail})"
            )
    if possible:
        lo = min(c["max_loss_pct"] for c in possible)
        hi = max(c["max_loss_pct"] for c in possible)
        span = f"{lo:g}%" if lo == hi else f"{lo:g}-{hi:g}%"
        n = len(possible)
        parts.append(
            f"{n} possible cut{'s' if n != 1 else ''} "
            f"(single window{'s' if n != 1 else ''}, {span})"
        )
    return " and ".join(parts)


def _duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {rest} s" if rest else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


__all__ = [
    "DEFAULT_LOSS_PCT",
    "GAP_S",
    "MAX_ROWS",
    "WINDOW_S",
    "cut_windows_flux",
    "describe_cuts",
    "fold_cuts",
    "loss_pct",
]
