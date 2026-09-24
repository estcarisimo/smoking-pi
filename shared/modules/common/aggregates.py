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

import logging

from datetime import datetime
from typing import Any

log = logging.getLogger("aggregates")

from . import cadence, microcuts
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

__all__ = [
    "EVENT_LOST_PINGS",
    "MAX_TARGETS",
    "MAX_WORST_WINDOWS",
    "collect",
    "describe_uplink_change",
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
    try:
        cadences = cadence.by_target(query_influx(cadence.cadence_flux()))
    except Exception:  # a missing cadence means the default, not no report
        log.warning("cadence query failed; counting loss events on 10 pings")
        cadences = {}
    prelude, bar = cadence.event_threshold_flux(cadences)
    loss_events_flux = (
        prelude + base + loss_filter + _CLAMP_LOSS_RATIO
        + f"|> filter(fn: (r) => r._value >= {bar}) "
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
    cuts = microcuts.fold_cuts(cut_rows)
    for cut in cuts:
        cut.pop("start_epoch", None)
    cuts.reverse()  # newest first, as the tool reports them

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
            '|> group(columns: ["interface"]) |> last()'
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
            + "|> reduce(identity: {n: 0, min: 0.0, max: -999.0}, "
            "fn: (r, accumulator) => ({n: accumulator.n + 1, "
            "min: if accumulator.n == 0 or r._value < accumulator.min "
            "then r._value else accumulator.min, "
            "max: if accumulator.n == 0 or r._value > accumulator.max "
            "then r._value else accumulator.max}))"
        )
        if not summary_rows or not int(summary_rows[0].get("n") or 0):
            return {}
        median_rows = query_influx(base + signal + "|> median()")
        last_rows = query_influx(base + '|> group(columns: ["_field"]) |> last()')
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


def _clock(iso: str | None) -> str:
    """HH:MM in the host's local time (the containers mount /etc/localtime)."""
    if not iso:
        return "?"
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%H:%M")
    except ValueError:
        return iso


def describe_uplink_change(change: dict, with_time: bool = True) -> str:
    """One change in words, e.g. "this host's uplink moved from wlan0 to
    eth0 (wired) at 14:02". Shared so every surface says it the same way."""
    at = f" at {_clock(change.get('time'))}" if with_time else ""
    prev, new, kind = change.get("previous"), change.get("interface"), change.get("kind")
    if not new:
        return f"this host lost its default route (it was on {prev}){at}"
    if prev in (None, "", "none"):
        return f"this host got a default route back, on {new} ({kind}){at}"
    return f"this host's uplink moved from {prev} to {new} ({kind}){at}"


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


def collect(hours: int = 24) -> dict:
    """Return the compact aggregate dict handed to the reporter.

    The target list is capped at MAX_TARGETS (worst-by-loss first) so the
    rendered prompt stays small even on installs with hundreds of targets.
    """
    targets = _collect_target_stats(hours)
    total = len(targets)
    truncated = total > MAX_TARGETS
    cpe = _collect_cpe_stats(hours)
    wifi = _collect_wifi_stats(hours)
    uplink = _collect_uplink(hours)
    return {
        "window_hours": hours,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "target_total": total,
        "targets_truncated": truncated,
        "targets": targets[:MAX_TARGETS],
        "cpe": cpe,
        "wifi": wifi,
        "uplink": uplink,
    }
