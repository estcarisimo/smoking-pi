"""Fetch what common.diagnosis needs, for any window, and run it.

The MCP tool ``diagnose_loss`` asked these questions of InfluxDB on its own,
so only an assistant could say what an episode was. The alerter answered
the same question with a cruder rule set of its own (alerter/verdict.py),
and the two could disagree about the same minutes. Both now fetch through
here: the queries, the context and the result are one code path.

``query`` is the caller's InfluxDB query function (each container has its
own client, and its tests mock it). Nothing here holds a connection.
"""

from __future__ import annotations

import logging
from typing import Callable

from common import cadence, diagnosis, microcuts
from common.tsdb import base_flux, flux_str

log = logging.getLogger("common.diagnosis_query")

Query = Callable[[str], list]

MAX_HOURS = 168
# Rows any one query may return; past it the answer says it is truncated.
MAX_ROWS = 5000

# Old exporter versions wrote loss as a packet count (0..20); current ones a
# 0..1 ratio. Clamp so legacy points read as (at most) 100% loss.
CLAMP_LOSS_RATIO = (
    "|> map(fn: (r) => ({r with _value: "
    "if r._value > 1.0 then 1.0 "
    "else if r._value < 0.0 then 0.0 "
    "else r._value})) "
)


def is_ipv6(target: str, category: str | None) -> bool:
    """The alerter's rule (evaluator._is_ipv6_target), kept in step by hand."""
    return (target or "").endswith("6") or any(
        k in (category or "").lower() for k in ("fping6", "ipv6"))


def wifi_width(hours: int) -> int:
    """Seconds per wifi_link row in a diagnosis: a minute up to two days."""
    return 60 if hours <= 48 else 300


def _base(measurements: list[str], hours: int) -> str:
    return base_flux(measurements, f"-{hours}h")


def wifi_minute_fluxes(hours: int) -> list[str]:
    """wifi_link per minute (5 min past two days): min signal, weak-sample
    count, min association, carrier drops, and which interface carried the
    default route -- renamed fields, one row shape for diagnosis.wifi_minutes."""
    every = f"{wifi_width(hours)}s"
    base = _base(["wifi_link"], hours)
    agg = f'|> aggregateWindow(every: {every}, fn: {{fn}}, createEmpty: false) '
    return [
        base + '|> filter(fn: (r) => r._field == "signal_dbm") '
        + agg.format(fn="min"),
        base + '|> filter(fn: (r) => r._field == "signal_dbm") '
        + f'|> map(fn: (r) => ({{r with _value: if r._value < {cadence.flux_float(diagnosis.WEAK_DBM)} then 1 else 0}})) '
        + agg.format(fn="sum") + '|> set(key: "_field", value: "weak") ',
        base + '|> filter(fn: (r) => r._field == "associated") '
        + '|> toFloat() ' + agg.format(fn="min"),
        base + '|> filter(fn: (r) => r._field == "carrier_down_count") '
        + '|> difference(nonNegative: true) ' + agg.format(fn="sum")
        + '|> set(key: "_field", value: "drops") ',
        base + '|> filter(fn: (r) => r._field == "uplink") '
        + '|> toFloat() |> group(columns: ["interface", "_field"]) |> max() ',
    ]


def run(query: Query, hours: int, cadences: dict | None = None) -> dict:
    """Diagnose the last ``hours``. Returns diagnosis.diagnose's result
    (incidents newest first, with ``start_epoch``/``end_epoch``) plus
    ``coverage``, ``targets_reporting`` and ``rows_truncated``.

    The four core queries raise: without them there is no answer. The rest
    refine it; a failure there costs confidence, and ``coverage`` says so."""
    cadences = cadences or {}
    step_s = cadence.longest_step(cadences)
    prelude, bar = cadence.event_threshold_flux(cadences)
    base = (_base(["latency", "dns_latency"], hours)
            + '|> filter(fn: (r) => r._field == "loss") ' + CLAMP_LOSS_RATIO)
    events_flux = (prelude + base + f"|> filter(fn: (r) => r._value >= {bar}) "
                   + '|> group() |> sort(columns: ["_time"], desc: true) '
                   + f"|> limit(n: {MAX_ROWS})")
    # Destinations only: the ISP first hop's ping target (category cpe) is
    # not one (diagnosis.FIRST_HOP_CATEGORY).
    targets_flux = (base + f'|> filter(fn: (r) => not exists r.category or '
                    f'r.category != {flux_str(diagnosis.FIRST_HOP_CATEGORY)}) '
                    + '|> group(columns: ["_time"]) '
                    + '|> keep(columns: ["_time", "target"]) '
                    + '|> distinct(column: "target") |> count()')
    app_base = (_base(["http_latency", "tcp_latency"], hours)
                + '|> filter(fn: (r) => r._field == "loss") ' + CLAMP_LOSS_RATIO)
    app_flux = (app_base + f"|> filter(fn: (r) => r._value >= "
                f"{cadence.flux_float(diagnosis.APP_LOSS)}) |> group() "
                f"|> limit(n: {MAX_ROWS})")
    app_sites_flux = (app_base + '|> group() |> keep(columns: ["target"]) '
                      + '|> distinct(column: "target")')
    cpe_count_flux = (_base(["cpe_latency"], hours)
                      + '|> filter(fn: (r) => r._field == "loss") |> group() |> count()')

    rows = query(events_flux)
    targets_rows = query(targets_flux)
    cut_rows = query(microcuts.cut_windows_flux(f"-{hours}h", microcuts.loss_pct()))
    cpe_rows = query(cpe_count_flux)

    def _optional(flux: str, what: str) -> list[dict]:
        try:
            return query(flux)
        except Exception:  # influx client raises many exception types
            log.warning("diagnosis: %s query failed", what, exc_info=True)
            return []

    epoch = cadence.epoch
    deaf_rows = _optional(microcuts.uplink_flux(f"-{hours}h"), "deaf-radio")
    wifi_rows = [row for flux in wifi_minute_fluxes(hours)
                 for row in _optional(flux, "wifi_link")]
    app_rows = _optional(app_flux, "app-layer")
    floor_rows = _optional(
        _base(["cpe_latency"], hours) + '|> filter(fn: (r) => r._field == "loss") '
        + f"|> aggregateWindow(every: {int(step_s)}s, fn: mean, createEmpty: false) ",
        "first-hop floor")
    for r in floor_rows:
        r["_epoch"] = epoch(r.get("_time"))
    app_site_rows = _optional(app_sites_flux, "app-layer targets")

    events = [{"target": r.get("target"), "category": r.get("category"),
               "loss_pct": round(float(r.get("_value", 0.0)) * 100.0, 2),
               "_epoch": epoch(r.get("_time"))} for r in rows]
    reporting: dict[int, int] = {}
    for row in targets_rows:
        ts = epoch(row.get("_time"))
        if ts is None or row.get("_value") is None:
            continue
        step = int(ts // step_s) * step_s
        reporting[step] = max(reporting.get(step, 0), int(row["_value"]))

    attributed = microcuts.attribute(microcuts.fold_cuts(cut_rows), deaf_rows)
    app: dict[str, list] = {}
    for r in app_rows:
        ts = epoch(r.get("_time"))
        if ts is not None and r.get("_value") is not None:
            app.setdefault(diagnosis.site_key(r.get("target")), []).append(
                (ts, float(r["_value"])))
    for r in wifi_rows:
        r["_epoch"] = epoch(r.get("_time"))
    wifi = diagnosis.wifi_minutes(wifi_rows)
    cpe = any(int(r.get("_value") or 0) > 0 for r in cpe_rows)
    ctx = {
        "step_s": step_s,
        "reporting": reporting,
        "cuts": microcuts.link_cuts(attributed),
        "deaf": diagnosis.merge_spans(microcuts.host_cuts(attributed)),
        "cpe": cpe,
        "wifi": wifi,
        "app": app,
        "app_sites": {diagnosis.site_key(r.get("target")) for r in app_site_rows},
        "ipv6": is_ipv6,
        "floor": diagnosis.first_hop_floor(floor_rows, step_s),
        "wifi_width": float(wifi_width(hours)),
    }
    result = diagnosis.diagnose(events, ctx, window_steps=hours * 3600 // step_s)
    result["coverage"] = {"wifi": wifi is not None, "cpe": cpe,
                          "app_layer": bool(app_site_rows)}
    result["targets_reporting"] = max(reporting.values(), default=0)
    result["rows_truncated"] = len(rows) >= MAX_ROWS
    return result
