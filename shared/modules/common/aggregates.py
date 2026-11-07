"""Network-health aggregates from the time-series database.

Reads the same InfluxDB 2.x measurements the exporters write:

- ``latency`` / ``dns_latency``: fields ``median`` (seconds) and ``loss``
  (ratio 0-1), tagged by ``target`` / ``category``.
- ``cpe_latency``: fields ``median``/``min``/``max``/``jitter`` (already in
  milliseconds) and ``loss`` (percent 0-100), tagged by ``target`` and
  ``protocol``.

Everything is aggregated server-side in Flux so the result stays small.

Shared because two images need it: the alerter's weekly summary reads the
per-target figures, the MCP server the uplink and resolver changes.

A second copy of these queries would drift against the exporters silently --
the loss-unit handling in particular, where latency loss is a 0-1 ratio and
cpe_latency loss is a percent.
"""

from __future__ import annotations

import logging

from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("aggregates")

from . import cadence, definitions
from .tsdb import (
    CLAMP_LOSS_RATIO as _CLAMP_LOSS_RATIO,
)
from .tsdb import (
    base_flux,
    flux_str,
    influx_bucket,
    query_influx,
)

# A data point is a "loss event" when it lost more than one ping's worth, of
# however many its probe sends (15% of ten, 7.5% of twenty, 30% of five). A
# single lost ping happens 60-300 times a day across every target on a host
# that measures through Wi-Fi -- background, not events. Same bar as the
# MCP tool get_loss_events' default; see common.cadence.EVENT_LOST_PINGS.
EVENT_LOST_PINGS = cadence.EVENT_LOST_PINGS

# The classes that say the monitor could not measure, not that anything it
# measures failed: its own Wi-Fi, its own name resolution.
MONITOR_CLASSES = ("local_wifi", "monitor")

__all__ = [
    "EVENT_LOST_PINGS",
    "target_stats",
    "describe_uplink_change",
    "describe_resolver_change",
    "resolver_changes_flux",
    "parse_resolver_changes",
    "resolver_current_flux",
    "parse_resolver_current",
    "flux_str",
    "influx_bucket",
    "query_influx",
    "uplink_changes_flux",
    "parse_uplink_changes",
]


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else None


def _rfc3339(epoch: float) -> str:
    return datetime.fromtimestamp(int(epoch), timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def merge_spans(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Overlapping or touching ``(lo, hi)`` spans merged, oldest first."""
    merged: list[list[float]] = []
    for lo, hi in sorted(spans):
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return [(lo, hi) for lo, hi in merged]


def _destinations(range_start: str, range_stop: str | None = None) -> str:
    """latency/dns_latency over a range, destinations only: the ISP first
    hop's ping target (category cpe) is evidence about the line, not a
    destination (common.definitions); its rate-limit floor would read as a
    lossy target every day."""
    return (base_flux(["latency", "dns_latency"], range_start,
                      range_stop=range_stop)
            + '|> filter(fn: (r) => not exists r.category or r.category != '
            + flux_str(definitions.FIRST_HOP_CATEGORY) + ") ")


def _in_spans_flux(spans: list[tuple[float, float]], tail: str) -> str:
    """The destinations' points inside ``spans`` (epoch ``[lo, hi)``), one
    pushed-down range per span, unioned. Not one range with a ``_time``
    filter: that reads every point of the window through the Flux
    evaluator, these read only the minutes the monitor could not measure."""
    tables = [_destinations(_rfc3339(lo), _rfc3339(hi)) + tail
              for lo, hi in spans]
    if len(tables) == 1:
        return tables[0]
    return "union(tables: [" + ", ".join(tables) + "]) "


def _subtract_spans(stats: dict, mean_loss: dict[tuple, float], base: str,
                    loss: str, events: str, group: str, prelude: str,
                    spans: list[tuple[float, float]]) -> None:
    """Each target's measurable figures: its whole-window sum and count less
    those of its points inside ``spans``. The in-span queries read only
    those minutes; the whole-window count is one pass like the mean's
    (``mean_loss``, unrounded ratios). All four queries run before any entry changes, so a failure leaves the
    plain figures whole."""

    def _rows(rows: list[dict]) -> dict[tuple, float]:
        return {(r.get("target"), r.get("_measurement")): float(r["_value"])
                for r in rows if r.get("_value") is not None}

    count_all = _rows(query_influx(base + loss + group + "|> count()"))
    span_sum = _rows(query_influx(_in_spans_flux(spans, loss) + group + "|> sum()"))
    span_count = _rows(query_influx(_in_spans_flux(spans, loss) + group + "|> count()"))
    span_events = _rows(query_influx(
        prelude + _in_spans_flux(spans, loss + events) + group + "|> count()"))
    for k, entry in stats.items():
        n_in = span_count.get(k, 0.0)
        if not n_in:
            continue
        entry["loss_events_measurable"] = max(
            0, entry["loss_events"] - int(span_events.get(k, 0.0)))
        n_out = count_all.get(k, 0.0) - n_in
        if n_out <= 0:
            entry["avg_loss_pct_measurable"] = None
            continue
        own = (mean_loss.get(k, 0.0) * count_all[k] - span_sum.get(k, 0.0)) / n_out
        entry["avg_loss_pct_measurable"] = round(min(1.0, max(0.0, own)) * 100.0, 2)


def target_stats(start: float, stop: float,
                 monitor_spans: list[tuple[float, float]] | None = None
                 ) -> list[dict]:
    """Per-target aggregates from the ``latency``/``dns_latency`` series over
    ``[start, stop)`` (epoch seconds), worst first, uncapped.

    Latency fields are stored in seconds (converted here to ms); loss is a
    0-1 ratio (converted here to a 0-100 percentage).

    ``monitor_spans``: epoch ``(lo, hi)`` spans in which the monitor itself
    could not measure (MONITOR_CLASSES incidents). Each target also gets
    ``avg_loss_pct_measurable`` and ``loss_events_measurable``: its loss
    with the points inside them left out. The first is None when every
    point the target has falls inside them (one renamed into a deaf
    spell): heard only through a deaf radio, it has no loss of its own to
    report. Without spans both equal the plain figures. Raises whatever the
    queries raise (the span subtraction alone degrades to the plain figures).
    """
    base = _destinations(_rfc3339(start), _rfc3339(stop))
    group = '|> group(columns: ["target", "_measurement"]) '
    median_filter = '|> filter(fn: (r) => r._field == "median") '
    loss = '|> filter(fn: (r) => r._field == "loss") ' + _CLAMP_LOSS_RATIO

    median_flux = base + median_filter + group + "|> median()"
    p95_flux = base + median_filter + group + "|> quantile(q: 0.95)"
    mean_loss_flux = base + loss + group + "|> mean()"
    max_loss_flux = base + loss + group + "|> max()"
    try:
        cadences = cadence.by_target(query_influx(cadence.cadence_flux()))
    except Exception:  # a missing cadence means the default, not no report
        log.warning("cadence query failed; counting loss events on 10 pings")
        cadences = {}
    prelude, bar = cadence.event_threshold_flux(cadences)
    events = f"|> filter(fn: (r) => r._value >= {bar}) "
    loss_events_flux = prelude + base + loss + events + group + "|> count()"

    stats: dict[tuple, dict] = {}

    def _rows(rows: list[dict]) -> dict[tuple, float]:
        return {(r.get("target"), r.get("_measurement")): float(r["_value"])
                for r in rows if r.get("_value") is not None}

    def _merge(values: dict[tuple, float], key: str, scale: float,
               digits: int = 3) -> None:
        for k, value in values.items():
            entry = stats.setdefault(k, {"target": k[0], "measurement": k[1]})
            entry[key] = round(value * scale, digits)

    mean_loss = _rows(query_influx(mean_loss_flux))
    _merge(_rows(query_influx(median_flux)), "median_ms", 1000.0)
    _merge(_rows(query_influx(p95_flux)), "p95_ms", 1000.0)
    _merge(mean_loss, "avg_loss_pct", 100.0, 2)
    _merge(_rows(query_influx(max_loss_flux)), "max_loss_pct", 100.0, 2)
    for k, value in _rows(query_influx(loss_events_flux)).items():
        entry = stats.setdefault(k, {"target": k[0], "measurement": k[1]})
        entry["loss_events"] = int(value)

    results = list(stats.values())
    for entry in results:
        entry.setdefault("avg_loss_pct", 0.0)
        entry.setdefault("loss_events", 0)
        entry["avg_loss_pct_measurable"] = entry["avg_loss_pct"]
        entry["loss_events_measurable"] = entry["loss_events"]

    # Clipped to the window: a span reaching back past its start would
    # subtract points the whole-window figures never counted.
    spans = merge_spans([(max(lo, start), min(hi, stop))
                         for lo, hi in monitor_spans or ()
                         if hi > start and lo < stop])
    if spans:
        try:
            _subtract_spans(stats, mean_loss, base, loss, events, group, prelude,
                            spans)
        except Exception:  # influx client raises many exception types
            # The plain figures stand: a summary with them beats none.
            log.warning("in-span loss query failed; measurable loss = plain loss",
                        exc_info=True)

    # Worst first: by the loss that is the target's own, then by mean loss,
    # then p95 latency -- the targets with a problem, not the ones heard
    # only through a deaf radio.
    results.sort(
        key=lambda e: (e["avg_loss_pct_measurable"] or 0.0,
                       e.get("avg_loss_pct", 0.0), e.get("p95_ms", 0.0)),
        reverse=True,
    )
    return results


def uplink_changes_flux(range_start: str) -> str:
    """The change points since ``range_start`` (e.g. ``-60m``), oldest first,
    one row each with previous / interface / kind."""
    return (
        base_flux(["host_uplink"], range_start)
        + '|> filter(fn: (r) => r._field == "previous" or r._field == "interface" '
        'or r._field == "kind") '
        '|> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value") '
        "|> filter(fn: (r) => exists r.previous) "
        '|> group() |> sort(columns: ["_time"])'
    )


def parse_uplink_changes(rows: list[dict]) -> list[dict]:
    """Rows of uplink_changes_flux as ``[{time, previous, interface, kind}]``.
    ``interface`` is ``""`` when the change was losing the default route;
    ``previous`` is ``"none"`` when it was getting one back."""
    out = []
    for r in rows:
        if r.get("previous") is None:
            continue
        out.append({
            "time": _iso(r.get("_time")),
            "previous": str(r.get("previous")),
            "interface": str(r.get("interface") or ""),
            "kind": str(r.get("kind") or ""),
        })
    return out


def _clock(iso: str | None, fmt=None) -> str:
    """HH:MM in the host's local time (the containers mount /etc/localtime),
    or ``fmt(when)`` for a caller with a zone of its own (the alerter prints
    every time in the digest's zone: alerter/clock.py). A naive ISO is UTC."""
    if not iso:
        return "?"
    try:
        when = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if fmt is None:
        return when.astimezone().strftime("%H:%M")
    return fmt(when if when.tzinfo else when.replace(tzinfo=timezone.utc))


def describe_uplink_change(change: dict, with_time: bool = True, fmt=None) -> str:
    """One change in words, e.g. "this host's uplink moved from wlan0 to
    eth0 (wired) at 14:02". Shared so every surface says it the same way;
    ``fmt`` formats the time (see :func:`_clock`)."""
    at = f" at {_clock(change.get('time'), fmt)}" if with_time else ""
    prev, new, kind = change.get("previous"), change.get("interface"), change.get("kind")
    if not new:
        return f"this host lost its default route (it was on {prev}){at}"
    if prev in (None, "", "none"):
        return f"this host got a default route back, on {new} ({kind}){at}"
    return f"this host's uplink moved from {prev} to {new} ({kind}){at}"


# --- dns_resolver: who answers the house's DNS on the Internet side ------------
#
# Written by smokeping-exporters/resolver_identity.py, per path (router,
# observer). A change point carries ``previous``, the owners before; the
# dashboards' "Resolver changed" annotation reads the same rows.

RESOLVER_PATHS = {"router": "through the router", "observer": "through the DNS observer"}


def resolver_changes_flux(range_start: str) -> str:
    """The change points since ``range_start``, oldest first, one row each
    with path / previous / owner."""
    return (
        base_flux(["dns_resolver"], range_start)
        + '|> filter(fn: (r) => r._field == "previous" or r._field == "owner") '
        '|> pivot(rowKey: ["_time", "path"], columnKey: ["_field"], valueColumn: "_value") '
        "|> filter(fn: (r) => exists r.previous) "
        '|> group() |> sort(columns: ["_time"])'
    )


def parse_resolver_changes(rows: list[dict]) -> list[dict]:
    """Rows of resolver_changes_flux as ``[{time, path, previous, owner}]``."""
    out = []
    for r in rows:
        if r.get("previous") is None:
            continue
        out.append({
            "time": _iso(r.get("_time")),
            "path": str(r.get("path") or ""),
            "previous": str(r.get("previous")),
            "owner": str(r.get("owner") or ""),
        })
    return out


def describe_resolver_change(change: dict, with_time: bool = True, fmt=None) -> str:
    """One change in words, e.g. "the resolver answering through the router
    changed from AS15169 GOOGLE - Google LLC, US to AS19281 QUAD9-AS-1, US at
    14:02". Shared so every surface says it the same way; ``fmt`` formats the
    time (see :func:`_clock`)."""
    at = f" at {_clock(change.get('time'), fmt)}" if with_time else ""
    where = RESOLVER_PATHS.get(change.get("path") or "", f"via {change.get('path')}")
    return (f"the resolver answering {where} changed from {change.get('previous')} "
            f"to {change.get('owner') or 'unknown'}{at}")


def resolver_current_flux() -> str:
    """The newest owner and client subnet per path, within the hour."""
    return (
        base_flux(["dns_resolver"], "-1h")
        + '|> filter(fn: (r) => r._field == "owner" or r._field == "ecs") |> last()'
    )


def parse_resolver_current(rows: list[dict]) -> dict[str, dict]:
    """Rows of resolver_current_flux as ``{path: {owner, ecs}}``; a path
    whose last probe got no answer (owner "") is left out."""
    current: dict[str, dict] = {}
    for r in rows:
        path, fld = r.get("path"), r.get("_field")
        if path and fld:
            current.setdefault(str(path), {})[str(fld)] = str(r.get("_value") or "")
    return {p: v for p, v in current.items() if v.get("owner")}
