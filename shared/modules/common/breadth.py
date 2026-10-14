"""The targets that vote on breadth only, for every reader of breadth.

v2.33.0 let the HTTP and TCP probes and the DNS wizard's services vote on
the alerter's widespread rule and its verdict (common.definitions
.BREADTH_ONLY_MEASUREMENTS, .breadth_voters), but the query and the voting
lived in the alerter. The diagnosis attached to the same alert, the MCP
``diagnose_loss`` and ``get_loss_events``, and the incident history still
counted the ICMP and resolver targets alone, so an alert could read "180 of
243 lost" over a diagnosis that counted 19. Every reader now asks here.

Two shapes, one rule:

- **The alerter's window** (twenty minutes): raw points, because its rules
  bucket every target per cycle (:func:`points_flux`, :func:`window_rows`).
- **A diagnosis window** (hours to a week): only shapes InfluxDB answers
  in its storage engine -- a bare ``count()``, ``first()``, ``last()`` and
  a filter on the value -- and the cycles are built here. The first
  version grouped every point by time and reduced them in Flux: 108 s for
  a week of 63 targets on the staging Pi, and a 30-day one froze the Pi
  until its hardware watchdog reset it (2026-10-14). These take 0.2-0.4 s
  each for a week:

  * points per target, and points at or above the event bar per target
    (one query per distinct bar): who votes (:func:`count_flux`);
  * each voter's first and last point: the cycles it reported in
    (:func:`span_flux`);
  * the voters' lossy points only: which cycles they were lossy or lost
    everything in (:func:`lossy_flux`).

  Breadth-only targets are counts: no caller sees their names.

A target votes when at least half its points in the window were clean
(definitions.breadth_voters), and only on a step no slower than the
breadth's bucket (a 600 s target in 300 s cycles would swing the share).
"""

from __future__ import annotations

from typing import Callable

from common import cadence, definitions, tsdb
from common.tsdb import base_flux, clamp_loss_ratio, flux_str

Query = Callable[[str], list]

MEASUREMENTS = ["latency", *definitions.BREADTH_ONLY_MEASUREMENTS]
# A point lost everything at or above this ratio (the alerter's
# DOWN_LOSS_RATIO, the diagnosis's TOTAL).
LOST_RATIO = 0.999


def _selector() -> str:
    """Flux predicate: the breadth-only targets -- the HTTP and TCP
    measurements, and the DNS wizard's services, whose ICMP targets are in
    ``latency``."""
    only = " or ".join(
        f"r._measurement == {flux_str(m)}"
        for m in definitions.BREADTH_ONLY_MEASUREMENTS
    )
    return f"{only} or r.category == {flux_str(tsdb.WIZARD_CATEGORY)}"


def _base(range_start: str) -> str:
    return (base_flux(MEASUREMENTS, range_start, include_wizard=True)
            + f"|> filter(fn: (r) => {_selector()}) ")


# ---------------------------------------------------------------------------
# The alerter's window: raw points
# ---------------------------------------------------------------------------


def points_flux(window_s: int) -> str:
    """Loss, pings and step of the breadth-only targets over the last
    ``window_s``. The cadence comes from the same points: no 6-hour
    lookback for targets that only ever feed one rule."""
    return (
        _base(f"-{int(window_s)}s")
        + '|> filter(fn: (r) => r._field == "loss" or r._field == "pings" '
        + 'or r._field == "step") '
        + '|> keep(columns: ["_value", "_time", "_field", "target", '
        + '"_measurement", "category"]) '
    )


def window_rows(
    rows: list[dict], mean_window_s: int, step_s: int = cadence.DEFAULT_STEP
) -> tuple[list[dict], list[dict], dict[str, cadence.Cadence]]:
    """(loss points, mean loss per target, cadences) of the breadth-only
    targets that vote in this window, from :func:`points_flux` rows.

    A target on a slower step than ``step_s`` (the rules' bucket) is left
    out: it would report in one bucket of two, and the share would swing
    between cycles with the denominator (review of #342). The means are
    taken over the newest ``mean_window_s`` of the points already fetched,
    in the shape of the alerter's mean-loss rows, for the verdict: it
    counts the same set the rule does.
    """
    loss = [r for r in rows if r.get("_field") == "loss"]
    cadences = cadence.by_target(
        [r for r in rows if r.get("_field") in ("step", "pings")])
    voters = {t for t in definitions.breadth_voters(loss, cadences)
              if cadence.of(cadences, t).step <= step_s}
    loss = [r for r in loss if r.get("target") in voters]
    times = [cadence.epoch(r.get("_time")) for r in loss]
    newest = max((t for t in times if t is not None), default=None)
    sums: dict[tuple[str, str | None], list[float]] = {}
    for row, at in zip(loss, times):
        if at is None or row.get("_value") is None or newest is None:
            continue
        if at > newest - mean_window_s:
            sums.setdefault((row["target"], row.get("category")), []).append(
                clamp_loss_ratio(row["_value"]))
    means = [{"target": t, "category": c, "_value": sum(v) / len(v)}
             for (t, c), v in sorted(sums.items())]
    return loss, means, {t: c for t, c in cadences.items() if t in voters}


# ---------------------------------------------------------------------------
# A diagnosis window: storage-engine aggregates, cycles built here
# ---------------------------------------------------------------------------

# The longest window the breadth is counted over. Past it the caller counts
# the ICMP and resolver targets alone, and says so: a month of every
# breadth-only target's lossy points is no question to put to a Pi.
MAX_HOURS = 168
# Lossy points of the voters one answer may read; past it (a week-long
# outage) the breadth is left out rather than undercounted.
MAX_LOSSY_ROWS = 20000


class TooMany(RuntimeError):
    """More lossy points than MAX_LOSSY_ROWS: the breadth is left out."""


def cadence_flux() -> str:
    """The newest ``step`` and ``pings`` per breadth-only target
    (cadence.cadence_flux leaves the wizard out, and reads no HTTP/TCP)."""
    return (
        _base(cadence.LOOKBACK)
        + '|> filter(fn: (r) => r._field == "step" or r._field == "pings") '
        + '|> group(columns: ["target", "_field"]) |> last() '
        + '|> keep(columns: ["target", "_field", "_value"])'
    )


def _loss(hours: int) -> str:
    return _base(f"-{int(hours)}h") + '|> filter(fn: (r) => r._field == "loss") '


def count_flux(hours: int, bar: float | None = None) -> str:
    """Loss points per series in the last ``hours``; with ``bar``, only
    those at or above it. A series with none is absent."""
    above = (f"|> filter(fn: (r) => r._value >= {cadence.flux_float(bar)}) "
             if bar is not None else "")
    return _loss(hours) + above + '|> count() |> keep(columns: ["target", "_value"])'


def span_flux(hours: int, end: str) -> str:
    """Each series' ``first`` or ``last`` loss point time."""
    return _loss(hours) + f'|> {end}() |> keep(columns: ["target", "_time"])'


def _targets_predicate(targets: set[str]) -> str:
    return " or ".join(f"r.target == {flux_str(t)}" for t in sorted(targets))


def lossy_flux(hours: int, voting: set[str], bar: float) -> str:
    """The voters' loss points at or above ``bar`` (the lowest of their
    bars), in no particular order, at most one past MAX_LOSSY_ROWS. The
    limit bounds what is sent back, not what InfluxDB reads."""
    return (
        _loss(hours)
        + f"|> filter(fn: (r) => r._value >= {cadence.flux_float(bar)} and "
        + f"({_targets_predicate(voting)})) "
        + '|> keep(columns: ["_time", "_value", "target"]) |> group() '
        + f"|> limit(n: {MAX_LOSSY_ROWS + 1})"
    )


def _sum_by_target(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        if row.get("target") is not None and row.get("_value") is not None:
            out[row["target"]] = out.get(row["target"], 0) + int(row["_value"])
    return out


def _quotable(target: str) -> bool:
    try:
        flux_str(target)
    except ValueError:
        return False
    return True


def voters(totals: dict[str, int], events: dict[str, int],
           cadences: dict[str, cadence.Cadence], step_s: int) -> set[str]:
    """The targets that vote: clean (under their event bar) in at least
    definitions.BREADTH_CLEAN_SHARE of their points, on a step no slower
    than ``step_s``, with a name Flux can quote."""
    return {
        t for t, n in totals.items()
        if n > 0 and n - events.get(t, 0) >= definitions.BREADTH_CLEAN_SHARE * n
        and cadence.of(cadences, t).step <= step_s and _quotable(t)
    }


def per_step(spans: dict[str, tuple[float, float]], lossy_rows: list[dict],
             bars: dict[str, float], step_s: int) -> dict[int, dict[str, int]]:
    """``{step: {"reporting", "lossy", "lost"}}`` of the voters.

    ``spans``: each voter's first and last point (epochs); it reports in
    every cycle between them -- a gap of a few cycles in a target that
    otherwise reports is counted as reporting, which a probe stall makes
    every target do alike. ``lossy_rows``: the voters' points at or above
    the lowest bar; each counts as lossy at its own target's ``bars``
    entry, as lost at LOST_RATIO, once per target and cycle."""
    out: dict[int, dict[str, int]] = {}
    # A sweep over +1 at each first cycle and -1 after each last one: one
    # pass over the cycles, not one per voter and cycle.
    change: dict[int, int] = {}
    for first, last in spans.values():
        lo = int(first // step_s) * step_s
        hi = int(last // step_s) * step_s + step_s
        change[lo] = change.get(lo, 0) + 1
        change[hi] = change.get(hi, 0) - 1
    if change:
        running = 0
        for step in range(min(change), max(change), step_s):
            running += change.get(step, 0)
            if running:
                out[step] = {"reporting": running, "lossy": 0, "lost": 0}
    seen: dict[tuple[int, str], tuple[bool, bool]] = {}
    for row in lossy_rows:
        target, ts = row.get("target"), cadence.epoch(row.get("_time"))
        if target not in bars or ts is None or row.get("_value") is None:
            continue
        value = float(row["_value"])
        key = (int(ts // step_s) * step_s, target)
        was = seen.get(key, (False, False))
        seen[key] = (was[0] or value >= bars[target], was[1] or value >= LOST_RATIO)
    for (step, _), (lossy, lost) in seen.items():
        counts = out.setdefault(step, {"reporting": 0, "lossy": 0, "lost": 0})
        counts["lossy"] += lossy
        counts["lost"] += lost
    return out


def fetch(query: Query, hours: int, step_s: int,
          bar: float | None = None) -> dict[int, dict[str, int]]:
    """The breadth-only voters' counts per cycle over the last ``hours``
    (:func:`per_step`); ``bar`` (a loss ratio) replaces each target's event
    bar for "lossy", never for who votes. Raises whatever ``query`` raises,
    TooMany past MAX_LOSSY_ROWS, and ValueError past MAX_HOURS: callers
    leave the breadth out on any of them."""
    if hours > MAX_HOURS:
        raise ValueError(f"breadth is counted over at most {MAX_HOURS} h")
    cadences = cadence.by_target(query(cadence_flux()))
    totals = _sum_by_target(query(count_flux(hours)))
    event_bars = {t: cadence.event_ratio(cadence.of(cadences, t).pings)
                  for t in totals}
    events: dict[str, int] = {}
    for level in sorted(set(event_bars.values())):
        above = _sum_by_target(query(count_flux(hours, level)))
        events.update({t: above.get(t, 0) for t, b in event_bars.items()
                       if b == level})
    voting = voters(totals, events, cadences, step_s)
    if not voting:
        return {}
    spans: dict[str, list[float]] = {}
    for end in ("first", "last"):
        for row in query(span_flux(hours, end)):
            ts = cadence.epoch(row.get("_time"))
            if row.get("target") in voting and ts is not None:
                span = spans.setdefault(row["target"], [ts, ts])
                span[0], span[1] = min(span[0], ts), max(span[1], ts)
    bars = {t: (bar if bar is not None else event_bars[t]) for t in voting}
    rows = query(lossy_flux(hours, voting, min(bars.values())))
    if len(rows) > MAX_LOSSY_ROWS:
        raise TooMany(f"more than {MAX_LOSSY_ROWS} lossy breadth-only points")
    return per_step({t: (a, b) for t, (a, b) in spans.items()}, rows, bars, step_s)
