"""Network-health aggregates from the time-series database.

Reads the same InfluxDB 2.x measurements the exporters write:

- ``latency`` / ``dns_latency``: fields ``median`` (seconds) and ``loss``
  (ratio 0-1), tagged by ``target`` / ``category``.
- ``cpe_latency``: fields ``median``/``min``/``max``/``jitter`` (already in
  milliseconds) and ``loss`` (percent 0-100), tagged by ``target`` and
  ``protocol``.

Everything is aggregated server-side in Flux so the result stays small
whether it is handed to a model or rendered into a digest.

Shared because two images need it: ai-insights renders it into a prompt,
and the alerter renders the same numbers into a scheduled digest. Both get
the window's incidents from the shared diagnosis (common.diagnosis_query),
the one the alerts, the MCP server and the web admin's chat answer from:
before, a day the monitor's radio was deaf for five hours read as two dozen
destinations at 20-70% loss, and a ten-minute cut of the line vanished into
a mean of 0.2%.

A second copy of these queries would drift against the exporters silently --
the loss-unit handling in particular, where latency loss is a 0-1 ratio and
cpe_latency loss is a percent.
"""

from __future__ import annotations

import logging
import time

from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("aggregates")

from . import cadence, definitions, diagnosis_query, microcuts
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

# Keep the payload small: only the worst N targets (by mean loss, then p95
# latency) are included.
MAX_TARGETS = 30

MAX_WORST_WINDOWS = 5

# Incidents handed on, newest first, besides the per-class counts; a probe
# that missed a few pings (probe_miss) is counted, never listed.
MAX_INCIDENTS = 20
# The classes that say the monitor could not measure, not that anything it
# measures failed: its own Wi-Fi, its own name resolution.
MONITOR_CLASSES = ("local_wifi", "monitor")

__all__ = [
    "EVENT_LOST_PINGS",
    "MAX_TARGETS",
    "MAX_WORST_WINDOWS",
    "collect",
    "target_stats",
    "describe_uplink_change",
    "describe_resolver_change",
    "resolver_changes_flux",
    "parse_resolver_changes",
    "collect_resolver",
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


def _base_flux(measurements: list[str], hours: int) -> str:
    """Range + measurement filter for a whole-hours window."""
    return base_flux(measurements, f"-{int(hours)}h")


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


def _collect_target_stats(hours: int,
                          monitor_spans: list[tuple[float, float]] | None = None,
                          window: tuple[float, float] | None = None,
                          ) -> list[dict]:
    """Per-target aggregates from the ``latency``/``dns_latency`` series.

    Latency fields are stored in seconds (converted here to ms); loss is a
    0-1 ratio (converted here to a 0-100 percentage).

    ``monitor_spans``: epoch ``(lo, hi)`` spans in which the monitor itself
    could not measure (MONITOR_CLASSES incidents). Each target also gets
    ``avg_loss_pct_measurable`` and ``loss_events_measurable``: its loss
    with the points inside them left out. The first is None when every
    point the target has falls inside them (one renamed into a deaf
    spell): heard only through a deaf radio, it has no loss of its own to
    report. Without spans both equal the plain figures.

    ``window``: epoch ``(start, stop)`` instead of the last ``hours`` (the
    weekly summary's calendar week, see :func:`target_stats`).
    """
    # The window's start, taken before the first query: a span clipped to a
    # later one would leave out edge points the whole-window counts hold.
    if window is not None:
        start, stop = window
        base = _destinations(_rfc3339(start), _rfc3339(stop))
    else:
        start, stop = time.time() - int(hours) * 3600, None
        base = _destinations(f"-{int(hours)}h")
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
    spans = merge_spans([(max(lo, start), hi if stop is None else min(hi, stop))
                         for lo, hi in monitor_spans or ()
                         if hi > start and (stop is None or lo < stop)])
    if spans:
        try:
            _subtract_spans(stats, mean_loss, base, loss, events, group, prelude,
                            spans)
        except Exception:  # influx client raises many exception types
            # The plain figures stand: a digest with them beats no digest.
            log.warning("in-span loss query failed; measurable loss = plain loss",
                        exc_info=True)

    # Worst first: by the loss that is the target's own, then by mean loss,
    # then p95 latency -- so the MAX_TARGETS cap keeps the targets with a
    # problem, not the ones heard only through a deaf radio.
    results.sort(
        key=lambda e: (e["avg_loss_pct_measurable"] or 0.0,
                       e.get("avg_loss_pct", 0.0), e.get("p95_ms", 0.0)),
        reverse=True,
    )
    return results


def target_stats(start: float, stop: float,
                 monitor_spans: list[tuple[float, float]] | None = None
                 ) -> list[dict]:
    """The per-target figures of :func:`collect` over ``[start, stop)``
    (epoch seconds), worst first, uncapped: ``avg_loss_pct_measurable`` is
    the loss outside ``monitor_spans``. Raises whatever the queries raise
    (the span subtraction alone degrades to the plain figures)."""
    return _collect_target_stats(0, monitor_spans, window=(start, stop))


def _collect_cpe_stats(hours: int) -> dict:
    """CPE microcut summary from ``cpe_latency`` (loss already 0-100 %):
    the floor per target+protocol, and the cuts above MICROCUT_LOSS_PCT
    folded into runs (common.microcuts) -- the same reading the MCP tool
    and the alerter give, so the AI report cannot call the floor's tail a
    microcut when the alert did not."""
    base = _base_flux(["cpe_latency"], hours)
    group = '|> group(columns: ["target", "protocol"]) '
    loss = '|> filter(fn: (r) => r._field == "loss") '

    windows_flux = base + loss + group + "|> count()"
    p50_flux = base + loss + group + "|> quantile(q: 0.5)"
    p90_flux = base + loss + group + "|> quantile(q: 0.9)"
    max_loss_flux = base + loss + group + "|> max()"
    median_jitter_flux = (
        base + '|> filter(fn: (r) => r._field == "jitter") ' + group + "|> median()"
    )
    threshold = microcuts.loss_pct()

    stats: dict[tuple, dict] = {}

    def _merge(rows: list[dict], key: str, cast) -> None:
        for row in rows:
            value = row.get("_value")
            if value is None:
                continue
            k = (row.get("target"), row.get("protocol"))
            entry = stats.setdefault(k, {"target": k[0], "protocol": k[1]})
            entry[key] = cast(value)

    pct = lambda v: round(float(v), 2)  # noqa: E731
    _merge(query_influx(windows_flux), "windows", int)
    _merge(query_influx(p50_flux), "p50_loss_pct", pct)
    _merge(query_influx(p90_flux), "p90_loss_pct", pct)
    _merge(query_influx(max_loss_flux), "max_loss_pct", pct)
    _merge(
        query_influx(median_jitter_flux),
        "median_jitter_ms",
        lambda v: round(float(v), 3),
    )
    cut_rows = query_influx(microcuts.cut_windows_flux(f"-{int(hours)}h", threshold))
    # This host's deaf radio makes cuts the link did not: reported apart, as
    # ``deaf``, never as microcuts. Optional, like the Wi-Fi stats.
    try:
        deaf_rows = query_influx(microcuts.uplink_flux(f"-{int(hours)}h"))
    except Exception:  # influx client raises many exception types
        log.warning("deaf-radio evidence query failed; every microcut counted as the link's", exc_info=True)
        deaf_rows = []
    attributed = microcuts.attribute(microcuts.fold_cuts(cut_rows), deaf_rows)
    cut_rows = microcuts.link_windows(cut_rows, attributed)
    deaf = microcuts.deaf_spans(attributed)
    for cut in attributed:
        cut.pop("start_epoch", None)
    attributed.reverse()  # newest first, as the tool reports them
    cuts = microcuts.link_cuts(attributed)

    for entry in stats.values():
        entry.setdefault("windows", 0)
        own = [c for c in cuts
               if c["target"] == entry["target"] and c["protocol"] == entry["protocol"]]
        entry["cut_windows"] = sum(c["windows"] for c in own)
        entry["confirmed_cuts"] = sum(1 for c in own if c["confirmed"])
        entry["possible_cuts"] = sum(1 for c in own if not c["confirmed"])

    worst_windows = [
        {
            "time": _iso(row.get("_time")),
            "target": row.get("target"),
            "protocol": row.get("protocol"),
            "loss_pct": round(float(row.get("_value", 0.0)), 2),
        }
        for row in sorted(cut_rows, key=lambda r: float(r.get("_value") or 0.0),
                          reverse=True)[:MAX_WORST_WINDOWS]
    ]
    return {
        "cut_loss_pct": threshold,
        "stats": sorted(
            stats.values(),
            key=lambda e: (e.get("target") or "", e.get("protocol") or ""),
        ),
        "cuts": cuts,
        "deaf": deaf,
        "deaf_note": microcuts.describe_deaf(deaf),
        "worst_windows": worst_windows,
    }


def _collect_wifi_stats(hours: int) -> dict:
    """The host's Wi-Fi uplink over the window, from ``wifi_link``; ``{}`` on
    a wired host -- and on any failure, because a digest or a report must not
    go unsent over one optional measurement (the caller sends nothing at all
    when collect() raises)."""
    try:
        uplink_rows = query_influx(
            _base_flux(["wifi_link"], hours)
            + '|> filter(fn: (r) => r._field == "uplink") '
            # Sorted: a disconnected sample is a series of its own (no
            # ssid/bssid), and after group() last() takes the last row.
            '|> group(columns: ["interface"]) |> sort(columns: ["_time"]) |> last()'
        )
        by_iface = {r.get("interface"): r.get("_value") for r in uplink_rows if r.get("interface")}
        if not by_iface:
            return {}
        # The interface carrying the default route, else the first: two
        # radios pooled into one line would blend their signals.
        uplink = sorted(i for i, v in by_iface.items() if int(v or 0) == 1)
        interface = uplink[0] if uplink else sorted(by_iface)[0]
        base = (_base_flux(["wifi_link"], hours)
                + f"|> filter(fn: (r) => r.interface == {flux_str(interface)}) ")
        signal = '|> filter(fn: (r) => r._field == "signal_dbm") |> group() '
        summary_rows = query_influx(
            base + signal
            + "|> reduce(identity: {n: 0, weak: 0, min: 0.0, max: -999.0}, "
            "fn: (r, accumulator) => ({n: accumulator.n + 1, "
            # Readings under WIFI_WEAK_DBM: weak means WIFI_WEAK_SAMPLES of
            # them (common.definitions), as the verdict and the diagnosis read it.
            "weak: accumulator.weak + (if r._value < "
            + cadence.flux_float(definitions.wifi_weak_dbm()) + " then 1 else 0), "
            "min: if accumulator.n == 0 or r._value < accumulator.min "
            "then r._value else accumulator.min, "
            "max: if accumulator.n == 0 or r._value > accumulator.max "
            "then r._value else accumulator.max}))"
        )
        if not summary_rows or not int(summary_rows[0].get("n") or 0):
            return {}
        median_rows = query_influx(base + signal + "|> median()")
        last_rows = query_influx(
            base + '|> group(columns: ["_field"]) |> sort(columns: ["_time"]) |> last()')
        drop_rows = query_influx(
            base + '|> filter(fn: (r) => r._field == "carrier_down_count") '
            '|> group() |> sort(columns: ["_time"]) |> increase() |> last()'
        )
        roam_rows = query_influx(
            base + '|> filter(fn: (r) => r._field == "associated" and r._value == 1) '
            '|> group() |> distinct(column: "bssid") |> count()'
        )
    except Exception:  # noqa: BLE001 - optional measurement, never fatal
        log.warning("wifi_link aggregate failed; digest goes out without it",
                    exc_info=True)
        return {}

    fields = {r.get("_field"): r for r in last_rows if r.get("_field")}
    sig_row = fields.get("signal_dbm") or {}
    out = {
        "interface": interface,
        "uplink_is_wifi": bool((fields.get("uplink") or {}).get("_value")),
        "ssid": sig_row.get("ssid"),
        "channel": int(fields["channel"]["_value"]) if fields.get("channel") else None,
        "band_ghz": float(fields["band_ghz"]["_value"]) if fields.get("band_ghz") else None,
        "tx_bitrate_mbps": (round(float(fields["tx_bitrate_mbps"]["_value"]), 1)
                            if fields.get("tx_bitrate_mbps") else None),
        "samples": int(summary_rows[0]["n"]),
        "weak_samples": int(summary_rows[0].get("weak") or 0),
        "weak_below_dbm": definitions.wifi_weak_dbm(),
        "min_dbm": float(summary_rows[0]["min"]),
        "max_dbm": float(summary_rows[0]["max"]),
        "median_dbm": (round(float(median_rows[0]["_value"]), 1)
                       if median_rows and median_rows[0].get("_value") is not None else None),
        "disconnects": int(drop_rows[0]["_value"]) if drop_rows and drop_rows[0].get("_value") is not None else 0,
        "roams": (max(0, int(roam_rows[0]["_value"]) - 1)
                  if roam_rows and roam_rows[0].get("_value") is not None else 0),
    }
    return out


# ── the uplink ───────────────────────────────────────────────────────────
# host_uplink (written by smokeping-exporters/wifi_link.py): which interface
# the default route is on, and -- only on the point where it changed --
# `previous`. A change is a change of path for every series, so the
# verdict, the digest, the AI report and the MCP server all say so, in the
# same words (describe_uplink_change).


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


def collect_resolver(hours: int) -> dict:
    """``{current: {path: {owner, ecs}}, changes: [...]}`` over the window;
    ``{}`` without dns_resolver data (an older exporter, no InfluxDB) or on
    failure -- optional, like the uplink block."""
    try:
        changes = parse_resolver_changes(query_influx(resolver_changes_flux(f"-{int(hours)}h")))
        current = parse_resolver_current(query_influx(resolver_current_flux()))
    except Exception:  # noqa: BLE001 - optional measurement, never fatal
        log.warning("dns_resolver aggregate failed; going out without it", exc_info=True)
        return {}
    if not current and not changes:
        return {}
    return {"current": current, "changes": changes}


def _collect_uplink(hours: int) -> dict:
    """``{current: {interface, kind, family} | None, changes: [...]}`` over the
    window; ``{}`` when host_uplink has nothing (Standard, an older
    exporter) or the query fails -- optional, like the Wi-Fi block."""
    try:
        changes = parse_uplink_changes(query_influx(uplink_changes_flux(f"-{int(hours)}h")))
        last_rows = query_influx(
            base_flux(["host_uplink"], "-10m")
            + '|> filter(fn: (r) => r._field == "interface" or r._field == "kind" '
            'or r._field == "family") |> last()'
        )
        fields = {r.get("_field"): r.get("_value") for r in last_rows if r.get("_field")}
        current = None
        if "interface" in fields:
            current = {
                "interface": str(fields.get("interface") or ""),
                "kind": str(fields.get("kind") or ""),
                "family": int(fields.get("family") or 0),
            }
    # The casts are inside too: a malformed row must cost this block, not
    # the digest or the report it sits in.
    except Exception:  # noqa: BLE001 - optional measurement, never fatal
        log.warning("host_uplink aggregate failed; going out without it", exc_info=True)
        return {}
    if current is None and not changes:
        return {}
    return {"current": current, "changes": changes}


def _collect_incidents(hours: int) -> dict | None:
    """The window's incidents, classified by the shared diagnosis: what the
    alerts, ``diagnose_loss`` and the web admin's chat say about it. None
    when they could not be read (a failed query, a window longer than the
    diagnosis reads): a reader must not take that for "no incidents"."""
    if hours > diagnosis_query.MAX_HOURS:
        return None
    try:
        cadences = cadence.by_target(query_influx(cadence.cadence_flux()))
    except Exception:  # a missing cadence means the default, not no answer
        cadences = {}
    try:
        result = diagnosis_query.run(query_influx, hours, cadences)
    except Exception:  # influx client raises many exception types
        log.warning("incident diagnosis failed; leaving incidents out",
                    exc_info=True)
        return None
    listed = [i for i in result["incidents"] if i["class"] != "probe_miss"]
    monitor = [i for i in listed if i["class"] in MONITOR_CLASSES]
    monitor_minutes = sum(i.get("minutes") or 0 for i in monitor)
    # The points those incidents cover, for the per-target figures: a point
    # stamped t measured the step before it, so it heard the monitor's
    # trouble when (t - step, t) overlaps the incident's (start, end).
    # SmokePing's RRD points sit on multiples of their step, as do the
    # incidents' bounds, so start + 1 leaves out the clean point at start.
    # The longest step is the tail for every target: one on a shorter step
    # loses the few points it took in that tail after the spell -- clean
    # ones, which only lowers its measurable loss by their share.
    step_s = cadence.longest_step(cadences)
    return {
        "by_class": result["by_class"],
        "monitor_minutes": monitor_minutes,
        "monitor_spans": [(i["start_epoch"] + 1, i["end_epoch"] + step_s)
                          for i in monitor],
        "truncated": len(listed) > MAX_INCIDENTS or result["rows_truncated"],
        "incidents": [
            {
                "class": i["class"],
                "confidence": i.get("confidence"),
                "start": _iso(datetime.fromtimestamp(i["start_epoch"]).astimezone()),
                "end": _iso(datetime.fromtimestamp(i["end_epoch"]).astimezone()),
                "minutes": i.get("minutes"),
                "targets": len(i.get("targets") or ()),
                "summary": i.get("summary"),
            }
            for i in listed[:MAX_INCIDENTS]
        ],
    }


def collect(hours: int = 24) -> dict:
    """Return the compact aggregate dict handed to the reporter.

    The target list is capped at MAX_TARGETS (worst-by-loss first) so the
    rendered prompt stays small even on installs with hundreds of targets.
    """
    incidents = _collect_incidents(hours)
    # Not handed on: they only serve the per-target measurable figures.
    spans = incidents.pop("monitor_spans") if incidents else []
    targets = _collect_target_stats(hours, spans)
    total = len(targets)
    truncated = total > MAX_TARGETS
    cpe = _collect_cpe_stats(hours)
    wifi = _collect_wifi_stats(hours)
    uplink = _collect_uplink(hours)
    resolver = collect_resolver(hours)
    return {
        "window_hours": hours,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "target_total": total,
        "targets_truncated": truncated,
        "targets": targets[:MAX_TARGETS],
        "cpe": cpe,
        "wifi": wifi,
        "uplink": uplink,
        "resolver": resolver,
        "incidents": incidents,
    }
