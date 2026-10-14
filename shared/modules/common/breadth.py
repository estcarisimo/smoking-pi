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
- **A diagnosis window** (hours to a week): aggregated in Flux, because a
  day of 224 targets is 64 000 points and a Raspberry Pi answers it. One
  row per target says whether it votes (:func:`voters_flux`); one row per
  cycle says how many voters reported, how many were lossy and how many
  lost everything (:func:`steps_flux`). No target name leaves Flux beyond
  the voter list: breadth-only targets are counts, never a per-target view.

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
# A diagnosis window: aggregated in Flux
# ---------------------------------------------------------------------------


def cadence_flux() -> str:
    """The newest ``step`` and ``pings`` per breadth-only target
    (cadence.cadence_flux leaves the wizard out, and reads no HTTP/TCP)."""
    return (
        _base(cadence.LOOKBACK)
        + '|> filter(fn: (r) => r._field == "step" or r._field == "pings") '
        + '|> group(columns: ["target", "_field"]) |> last() '
        + '|> keep(columns: ["target", "_field", "_value"])'
    )


def voters_flux(hours: int, cadences: dict[str, cadence.Cadence]) -> str:
    """Per breadth-only target, the share of its loss points in the last
    ``hours`` that were clean (under the event bar for its pings): one row
    each, whatever the window."""
    prelude, bar = cadence.event_threshold_flux(cadences)
    return (
        prelude + _base(f"-{int(hours)}h")
        + '|> filter(fn: (r) => r._field == "loss") '
        + f"|> map(fn: (r) => ({{r with _value: if r._value >= {bar} "
        + "then 0.0 else 1.0}})) "
        + '|> group(columns: ["target"]) |> mean() '
        + '|> keep(columns: ["target", "_value"])'
    )


def voters(rows: list[dict], cadences: dict[str, cadence.Cadence],
           step_s: int) -> set[str]:
    """The targets of :func:`voters_flux` rows that vote: clean in at least
    definitions.BREADTH_CLEAN_SHARE of their points, on a step no slower
    than ``step_s``."""
    return {
        r["target"] for r in rows
        if r.get("target") is not None and r.get("_value") is not None
        and float(r["_value"]) >= definitions.BREADTH_CLEAN_SHARE
        and cadence.of(cadences, r["target"]).step <= step_s
    }


def steps_flux(hours: int, cadences: dict[str, cadence.Cadence],
               voting: set[str], bar: str | None = None) -> str | None:
    """Per point time, how many voters reported, were lossy (at ``bar``, a
    Flux float expression; default the event bar of each target's pings)
    and lost everything. None when no voter has a name Flux can quote."""
    pairs = []
    for target in sorted(voting):
        try:
            pairs.append(f"{{key: {flux_str(target)}, value: true}}")
        except ValueError:
            continue
    if not pairs:
        return None
    prelude, event_bar = cadence.event_threshold_flux(cadences)
    prelude = (prelude or 'import "dict"\n') + (
        f"voters = dict.fromList(pairs: [{', '.join(pairs)}])\n")
    bar = bar or event_bar
    return (
        prelude + _base(f"-{int(hours)}h")
        + '|> filter(fn: (r) => r._field == "loss" and '
        + "dict.get(dict: voters, key: r.target, default: false)) "
        + f"|> map(fn: (r) => ({{r with lossy: if r._value >= {bar} then 1 "
        + f"else 0, lost: if r._value >= {cadence.flux_float(LOST_RATIO)} "
        + "then 1 else 0}})) "
        + '|> group(columns: ["_time"]) '
        + "|> reduce(identity: {reporting: 0, lossy: 0, lost: 0}, "
        + "fn: (r, accumulator) => ({reporting: accumulator.reporting + 1, "
        + "lossy: accumulator.lossy + r.lossy, lost: accumulator.lost + r.lost})) "
        + "|> group()"
    )


def per_step(rows: list[dict], step_s: int) -> dict[int, dict[str, int]]:
    """:func:`steps_flux` rows bucketed into ``step_s`` cycles:
    ``{step: {"reporting", "lossy", "lost"}}``. Several point times in one
    cycle (jitter): the largest count of each is the cycle's, as the
    reporting counts of the other targets are taken. A voter on a step
    faster than ``step_s`` has several point times in a cycle, and its
    lossy points are counted at the busiest one, not per cycle as the
    alerter buckets them: the shipped probes all share the 300 s step."""
    out: dict[int, dict[str, int]] = {}
    for row in rows:
        ts = cadence.epoch(row.get("_time"))
        if ts is None:
            continue
        step = int(ts // step_s) * step_s
        counts = out.setdefault(step, {"reporting": 0, "lossy": 0, "lost": 0})
        for key in counts:
            counts[key] = max(counts[key], int(row.get(key) or 0))
    return out


def fetch(query: Query, hours: int, step_s: int,
          bar: str | None = None) -> dict[int, dict[str, int]]:
    """The breadth-only voters' counts per cycle over the last ``hours``
    (:func:`per_step`). Three queries, each answered with one row per
    target or per cycle. Raises whatever ``query`` raises: callers decide
    whether the breadth is optional (it is everywhere today)."""
    cadences = cadence.by_target(query(cadence_flux()))
    voting = voters(query(voters_flux(hours, cadences)), cadences, step_s)
    if not voting:
        return {}
    src = steps_flux(hours, cadences, voting, bar)
    return per_step(query(src), step_s) if src else {}
