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
  * per cycle, how many voters were lossy (one query per distinct event
    bar) and how many lost everything (:func:`cycle_count_flux`).

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
LOST_RATIO = definitions.LOST_RATIO


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
    """A Flux predicate true for the named targets: their equalities as a
    balanced ``or`` tree, so the storage engine applies it.

    Not a chain: Flux parses ``a or b or c ...`` as nested binary
    expressions and refuses about 200 of them ("Program is nested too
    deep") -- the reference Pi's 224 voters did (v2.33.1). A balanced tree
    nests as deep as log2 of the names: 8 for 224, 11 for 2 000. Not
    ``contains(set: [...])`` either, which v2.33.1 used: no engine pushes it
    down, so Flux scanned the whole list for every point above the bar --
    5.2 of the 5.4 s the reference Pi took to count a week of lost cycles
    (2026-10-15), 0.4 s as a tree."""
    if not targets:
        raise ValueError("no targets to select")
    names = sorted(targets)

    def tree(lo: int, hi: int) -> str:
        if hi - lo == 1:
            return f"r.target == {flux_str(names[lo])}"
        mid = (lo + hi) // 2
        return f"({tree(lo, mid)} or {tree(mid, hi)})"

    return tree(0, len(names))


def cycle_count_flux(hours: int, targets: set[str], bar: float,
                     step_s: int) -> str:
    """Per ``step_s`` cycle, how many of ``targets`` had a loss point at or
    above ``bar``: one row per cycle with any, at most one per cycle of the
    window. The value filter and the targets are both predicates the
    storage engine applies (:func:`_targets_predicate`), so Flux sees only
    the voters' points above the bar. Two stages, so a target counts once
    per cycle however many points it has there -- one on a faster step
    than the cycle, or split over several series (review of #349): per
    target, its points per cycle; then, per cycle, the targets."""
    window = (f"|> aggregateWindow(every: {int(step_s)}s, fn: count, "
              'createEmpty: false, timeSrc: "_start") ')
    return (
        _loss(hours)
        + f"|> filter(fn: (r) => r._value >= {cadence.flux_float(bar)}) "
        + f"|> filter(fn: (r) => {_targets_predicate(targets)}) "
        + '|> group(columns: ["target"]) ' + window
        + "|> group() " + window
        + '|> keep(columns: ["_time", "_value"])'
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
             lost_rows: list[dict], step_s: int) -> dict[int, dict[str, int]]:
    """``{step: {"reporting", "lossy", "lost"}}`` of the voters.

    ``spans``: each voter's first and last point (epochs); it reports in
    every cycle between them -- a gap of a few cycles in a target that
    otherwise reports is counted as reporting, which a probe stall makes
    every target do alike. ``lossy_rows`` and ``lost_rows``: per-cycle
    target counts (:func:`cycle_count_flux`), summed per cycle (one query
    per distinct bar: each target has one, so the sets are disjoint), and
    clamped to the cycle's reporting count -- never the reverse, which
    would turn a miscount into a larger denominator (review of #349)."""
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
    for key, rows in (("lossy", lossy_rows), ("lost", lost_rows)):
        for row in rows:
            ts = cadence.epoch(row.get("_time"))
            if ts is None or row.get("_value") is None:
                continue
            counts = out.setdefault(int(ts // step_s) * step_s,
                                    {"reporting": 0, "lossy": 0, "lost": 0})
            counts[key] += int(row["_value"])
    for counts in out.values():
        counts["lossy"] = min(counts["lossy"], counts["reporting"])
        counts["lost"] = min(counts["lost"], counts["reporting"])
    return {s: c for s, c in out.items() if c["reporting"]}


def fetch(query: Query, hours: int, step_s: int,
          bar: float | None = None,
          floor: float = 0.0) -> dict[int, dict[str, int]]:
    """The breadth-only voters' counts per cycle over the last ``hours``
    (:func:`per_step`); ``bar`` (a loss ratio) replaces each target's event
    bar for "lossy", never for who votes; ``floor`` (a ratio) is the least
    loss "lossy" needs whatever the bar (the widespread rule's
    WIDESPREAD_LOSS_PCT). Raises whatever ``query`` raises,
    and ValueError past MAX_HOURS: callers leave the breadth out on either.
    Every query answers one row per target, or one per cycle with any
    (2 016 for a week), however long the outage; what InfluxDB reads still
    grows with the points above the bar: v2.33.1 fetched the voters' lossy points themselves, and a
    week of the reference Pi's 224 had more than its 20 000-row bound."""
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
    bars = {t: max(floor, bar if bar is not None else event_bars[t])
            for t in voting}
    lossy_rows: list[dict] = []
    for level in sorted(set(bars.values())):
        lossy_rows += query(cycle_count_flux(
            hours, {t for t, b in bars.items() if b == level}, level, step_s))
    lost_rows = query(cycle_count_flux(hours, voting, LOST_RATIO, step_s))
    return per_step({t: (a, b) for t, (a, b) in spans.items()}, lossy_rows,
                    lost_rows, step_s)
