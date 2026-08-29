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
and the alerter renders the same numbers into a scheduled digest. A second
copy of these queries would drift against the exporters silently -- the
loss-unit handling in particular, where latency loss is a 0-1 ratio and
cpe_latency loss is a percent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .tsdb import (
    CLAMP_LOSS_RATIO as _CLAMP_LOSS_RATIO,
)
from .tsdb import (
    base_flux,
    flux_str,
    influx_bucket,
    query_influx,
)

# Loss threshold (percent) above which a data point counts as a "loss event".
LOSS_EVENT_PCT = 5.0

# Keep the payload small: only the worst N targets (by mean loss, then p95
# latency) are included.
MAX_TARGETS = 30

MAX_WORST_WINDOWS = 5

__all__ = [
    "LOSS_EVENT_PCT",
    "MAX_TARGETS",
    "MAX_WORST_WINDOWS",
    "collect",
    "flux_str",
    "influx_bucket",
    "query_influx",
]


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else None


def _base_flux(measurements: list[str], hours: int) -> str:
    """Range + measurement filter for a whole-hours window."""
    return base_flux(measurements, f"-{int(hours)}h")


def _collect_target_stats(hours: int) -> list[dict]:
    """Per-target aggregates from the ``latency``/``dns_latency`` series.

    Latency fields are stored in seconds (converted here to ms); loss is a
    0-1 ratio (converted here to a 0-100 percentage).
    """
    base = _base_flux(["latency", "dns_latency"], hours)
    group = '|> group(columns: ["target", "_measurement"]) '
    median_filter = '|> filter(fn: (r) => r._field == "median") '
    loss_filter = '|> filter(fn: (r) => r._field == "loss") '

    median_flux = base + median_filter + group + "|> median()"
    p95_flux = base + median_filter + group + "|> quantile(q: 0.95)"
    mean_loss_flux = base + loss_filter + _CLAMP_LOSS_RATIO + group + "|> mean()"
    max_loss_flux = base + loss_filter + _CLAMP_LOSS_RATIO + group + "|> max()"
    loss_events_flux = (
        base + loss_filter + _CLAMP_LOSS_RATIO
        + f"|> filter(fn: (r) => r._value >= {LOSS_EVENT_PCT / 100.0}) "
        + group + "|> count()"
    )

    stats: dict[tuple, dict] = {}

    def _merge(rows: list[dict], key: str, scale: float, digits: int = 3) -> None:
        for row in rows:
            value = row.get("_value")
            if value is None:
                continue
            k = (row.get("target"), row.get("_measurement"))
            entry = stats.setdefault(k, {"target": k[0], "measurement": k[1]})
            entry[key] = round(float(value) * scale, digits)

    _merge(query_influx(median_flux), "median_ms", 1000.0)
    _merge(query_influx(p95_flux), "p95_ms", 1000.0)
    _merge(query_influx(mean_loss_flux), "avg_loss_pct", 100.0, 2)
    _merge(query_influx(max_loss_flux), "max_loss_pct", 100.0, 2)
    for row in query_influx(loss_events_flux):
        value = row.get("_value")
        if value is None:
            continue
        k = (row.get("target"), row.get("_measurement"))
        entry = stats.setdefault(k, {"target": k[0], "measurement": k[1]})
        entry["loss_events"] = int(value)

    results = list(stats.values())
    for entry in results:
        entry.setdefault("avg_loss_pct", 0.0)
        entry.setdefault("loss_events", 0)
    # Worst first: by mean loss, then p95 latency.
    results.sort(
        key=lambda e: (e.get("avg_loss_pct", 0.0), e.get("p95_ms", 0.0)),
        reverse=True,
    )
    return results


def _collect_cpe_stats(hours: int) -> dict:
    """CPE microcut summary from ``cpe_latency`` (loss already 0-100 %)."""
    base = _base_flux(["cpe_latency"], hours)
    group = '|> group(columns: ["target", "protocol"]) '
    loss = '|> filter(fn: (r) => r._field == "loss") '

    lossy_count_flux = (
        base + loss + "|> filter(fn: (r) => r._value > 0.0) " + group + "|> count()"
    )
    max_loss_flux = base + loss + group + "|> max()"
    median_jitter_flux = (
        base + '|> filter(fn: (r) => r._field == "jitter") ' + group + "|> median()"
    )
    worst_flux = (
        base + loss
        + "|> group() "
        + '|> sort(columns: ["_value"], desc: true) '
        + f"|> limit(n: {MAX_WORST_WINDOWS})"
    )

    stats: dict[tuple, dict] = {}

    def _merge(rows: list[dict], key: str, cast) -> None:
        for row in rows:
            value = row.get("_value")
            if value is None:
                continue
            k = (row.get("target"), row.get("protocol"))
            entry = stats.setdefault(k, {"target": k[0], "protocol": k[1]})
            entry[key] = cast(value)

    _merge(query_influx(lossy_count_flux), "lossy_windows", int)
    _merge(query_influx(max_loss_flux), "max_loss_pct", lambda v: round(float(v), 2))
    _merge(
        query_influx(median_jitter_flux),
        "median_jitter_ms",
        lambda v: round(float(v), 3),
    )
    worst_rows = query_influx(worst_flux)

    for entry in stats.values():
        entry.setdefault("lossy_windows", 0)

    worst_windows = [
        {
            "time": _iso(row.get("_time")),
            "target": row.get("target"),
            "protocol": row.get("protocol"),
            "loss_pct": round(float(row.get("_value", 0.0)), 2),
        }
        for row in worst_rows
        if row.get("_value") is not None and float(row["_value"]) > 0.0
    ]
    return {
        "stats": sorted(
            stats.values(),
            key=lambda e: (e.get("target") or "", e.get("protocol") or ""),
        ),
        "worst_windows": worst_windows,
    }


def collect(hours: int = 24) -> dict:
    """Return the compact aggregate dict handed to the reporter.

    The target list is capped at MAX_TARGETS (worst-by-loss first) so the
    rendered prompt stays small even on installs with hundreds of targets.
    """
    targets = _collect_target_stats(hours)
    total = len(targets)
    truncated = total > MAX_TARGETS
    cpe = _collect_cpe_stats(hours)
    return {
        "window_hours": hours,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "target_total": total,
        "targets_truncated": truncated,
        "targets": targets[:MAX_TARGETS],
        "cpe": cpe,
    }
