"""SmokePing MCP server.

Exposes the Raspberry Pi SmokePing stack (config-manager REST API +
InfluxDB time series) as MCP tools so an AI assistant can inspect and
operate the monitoring system.

Run via ``main.py`` (stdio or streamable-http transport).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

try:
    # mcp 1.x
    from mcp.server.fastmcp import FastMCP
except ImportError:  # mcp >= 2.0 renamed FastMCP to MCPServer (same API)
    from mcp.server.mcpserver import MCPServer as FastMCP

import backends
from backends import ConfigAPIError, flux_str, influx_bucket, query_influx

mcp = FastMCP("smokeping")

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

MAX_HOURS = 24 * 365
MAX_EVENT_ROWS = 500

_TARGET_FIELDS = ("id", "name", "host", "title", "category", "probe", "is_active")


def _validate_name(name: str) -> str | None:
    """Return an error message if the target name is invalid, else None."""
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return (
            f"Invalid target name {name!r}: names must start with a letter and "
            "contain only letters, digits, and underscores (SmokePing section "
            "names cannot contain spaces or punctuation)."
        )
    return None


def _validate_hours(hours: Any) -> tuple[int | None, str | None]:
    try:
        h = int(hours)
    except (TypeError, ValueError):
        return None, f"Invalid hours value {hours!r}: must be an integer."
    if not 1 <= h <= MAX_HOURS:
        return None, f"hours must be between 1 and {MAX_HOURS} (got {h})."
    return h, None


def _slim_target(target: dict) -> dict:
    return {k: target.get(k) for k in _TARGET_FIELDS}


def _fetch_targets(api: backends.ConfigAPI) -> list[dict]:
    return api.request("GET", "/targets").get("targets", [])


def _resolve_target(api: backends.ConfigAPI, name: str) -> tuple[dict | None, dict | None]:
    """Resolve a target name to its DB row. Returns (target, error_dict)."""
    targets = _fetch_targets(api)
    for target in targets:
        if target.get("name") == name:
            return target, None
    return None, {
        "error": f"No monitoring target named '{name}' was found.",
        "available_targets": sorted(
            t.get("name") for t in targets if t.get("name")
        ),
    }


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else None


# ---------------------------------------------------------------------------
# Target management tools (config-manager REST API)
# ---------------------------------------------------------------------------


@mcp.tool()
def list_targets() -> dict:
    """List all SmokePing monitoring targets.

    Returns every configured target with its database id, name, host
    (IP or hostname being pinged), title, category (e.g. dns, web, custom,
    cpe), probe type, and whether it is currently active (inactive targets
    exist in the database but are not monitored).

    Use this to see what is being monitored, or to find a target's exact
    name before calling remove_target / toggle_target.
    """
    try:
        targets = [_slim_target(t) for t in _fetch_targets(backends.get_config_api())]
    except ConfigAPIError as exc:
        return {"error": str(exc)}
    return {"total": len(targets), "targets": targets}


@mcp.tool()
def add_target(
    name: str,
    host: str,
    category: str = "custom",
    title: str | None = None,
    probe: str | None = None,
) -> dict:
    """Add a new host to SmokePing monitoring.

    Args:
        name: Unique identifier for the target. Must start with a letter and
            contain only letters, digits, and underscores (e.g. "quad9_dns").
        host: The IP address or hostname to monitor (e.g. "9.9.9.9").
        category: Target category; must be an existing category name
            (default "custom"). If the category doesn't exist the tool
            returns the list of valid categories.
        title: Human-readable display title (defaults to the name).
        probe: Probe name to use (e.g. "FPing", "DNS"). Defaults to the
            system's default probe.

    On success the SmokePing configuration is regenerated automatically by
    the config-manager; data for the new target starts appearing within a
    few minutes. If SmokePing doesn't pick up the change, call
    apply_config().
    """
    error = _validate_name(name)
    if error:
        return {"error": error}
    if not isinstance(host, str) or not host.strip():
        return {"error": "host must be a non-empty IP address or hostname."}

    api = backends.get_config_api()
    try:
        categories = api.request("GET", "/categories").get("categories", [])
        cat = next((c for c in categories if c.get("name") == category), None)
        if cat is None:
            return {
                "error": f"Unknown category '{category}'.",
                "valid_categories": sorted(
                    c.get("name") for c in categories if c.get("name")
                ),
            }

        probes = api.request("GET", "/probes").get("probes", [])
        if probe is not None:
            probe_row = next((p for p in probes if p.get("name") == probe), None)
            if probe_row is None:
                return {
                    "error": f"Unknown probe '{probe}'.",
                    "valid_probes": sorted(
                        p.get("name") for p in probes if p.get("name")
                    ),
                }
        else:
            probe_row = next((p for p in probes if p.get("is_default")), None)
            if probe_row is None and probes:
                probe_row = probes[0]
            if probe_row is None:
                return {"error": "No probes are configured in the system."}

        result = api.request(
            "POST",
            "/targets",
            json={
                "name": name,
                "host": host.strip(),
                "title": title or name,
                "category_id": cat["id"],
                "probe_id": probe_row["id"],
            },
        )
    except ConfigAPIError as exc:
        return {"error": str(exc)}

    return {
        "success": True,
        "target": _slim_target(result.get("target", {})),
        "note": (
            "SmokePing configuration was regenerated automatically. "
            "Latency data for the new target will appear within a few "
            "minutes. If it doesn't, run apply_config() to regenerate and "
            "restart SmokePing."
        ),
    }


@mcp.tool()
def remove_target(name: str) -> dict:
    """Permanently delete a monitoring target by name.

    Looks up the target by its exact name (as shown by list_targets) and
    deletes it from the database; the SmokePing configuration is regenerated
    automatically. Historical time-series data in InfluxDB is kept.

    If you only want to pause monitoring temporarily, use toggle_target
    instead.
    """
    api = backends.get_config_api()
    try:
        target, err = _resolve_target(api, name)
        if err:
            return err
        result = api.request("DELETE", f"/targets/{target['id']}")
    except ConfigAPIError as exc:
        return {"error": str(exc)}
    return {
        "success": True,
        "message": result.get("message", "Target deleted."),
        "deleted": _slim_target(target),
    }


@mcp.tool()
def toggle_target(name: str) -> dict:
    """Enable or disable monitoring for a target by name (flips its state).

    An active target becomes inactive (monitoring pauses, data retained) and
    an inactive one becomes active again. The SmokePing configuration is
    regenerated automatically. Use list_targets to check the current state.
    """
    api = backends.get_config_api()
    try:
        target, err = _resolve_target(api, name)
        if err:
            return err
        result = api.request("POST", f"/targets/{target['id']}/toggle")
    except ConfigAPIError as exc:
        return {"error": str(exc)}
    return {
        "success": True,
        "message": result.get("message", "Target toggled."),
        "target": _slim_target(result.get("target", {})),
    }


@mcp.tool()
def apply_config() -> dict:
    """Regenerate the SmokePing configuration and restart the service.

    Runs the config-manager's generate step (renders Targets/Probes files
    from the database) and then restarts the SmokePing container so the new
    configuration takes effect. Use this after configuration changes if
    SmokePing hasn't picked them up automatically. Monitoring briefly pauses
    (a few seconds) during the restart.
    """
    api = backends.get_config_api()
    outcome: dict = {}
    try:
        outcome["generate"] = api.request("POST", "/generate")
    except ConfigAPIError as exc:
        outcome["generate"] = {"error": str(exc)}
        outcome["restart"] = {"skipped": "not attempted because generate failed"}
        outcome["success"] = False
        return outcome
    try:
        outcome["restart"] = api.request("POST", "/restart")
        outcome["success"] = True
    except ConfigAPIError as exc:
        outcome["restart"] = {"error": str(exc)}
        outcome["success"] = False
    return outcome


@mcp.tool()
def system_status() -> dict:
    """Get the health and status of the SmokePing monitoring stack.

    Combines the config-manager health check with the detailed service
    status: database availability and target counts, whether the generated
    SmokePing config files exist, and whether the SmokePing container is
    running. Use this first when something seems broken.
    """
    api = backends.get_config_api()
    result: dict = {}
    try:
        result["health"] = api.request("GET", "/health")
    except ConfigAPIError as exc:
        return {
            "summary": "config-manager is unreachable -- the stack is likely down.",
            "error": str(exc),
        }
    try:
        status = api.request("GET", "/status")
    except ConfigAPIError as exc:
        result["error"] = str(exc)
        result["summary"] = "config-manager is up but its status check failed."
        return result

    result["status"] = status
    database = status.get("database", {}) or {}
    smokeping = status.get("smokeping", {}) or {}
    parts = [
        f"overall status: {status.get('status', 'unknown')}",
        f"database available: {database.get('available')}"
        + (
            f" ({database.get('target_count')} targets)"
            if database.get("target_count") is not None
            else ""
        ),
        f"smokeping container running: {smokeping.get('running')}",
    ]
    result["summary"] = "; ".join(parts)
    return result


# ---------------------------------------------------------------------------
# Measurement tools (InfluxDB)
# ---------------------------------------------------------------------------


def _base_flux(measurements: list[str], hours: int) -> str:
    predicate = " or ".join(
        f'r._measurement == {flux_str(m)}' for m in measurements
    )
    return (
        f"from(bucket: {flux_str(influx_bucket())}) "
        f"|> range(start: -{hours}h) "
        f"|> filter(fn: (r) => {predicate}) "
    )


# Old exporter versions wrote loss as a packet count (0..20); current versions
# write a 0..1 ratio. Clamp so legacy points read as (at most) 100% loss.
_CLAMP_LOSS_RATIO = (
    "|> map(fn: (r) => ({r with _value: "
    "if r._value > 1.0 then 1.0 "
    "else if r._value < 0.0 then 0.0 "
    "else r._value})) "
)


@mcp.tool()
def get_latency_stats(target: str | None = None, hours: int = 24) -> dict:
    """Get latency and packet-loss statistics per monitoring target.

    For each target (ICMP `latency` and DNS `dns_latency` measurements),
    computes over the requested time window:
      - median_ms: median round-trip latency in milliseconds
      - p95_ms: 95th-percentile latency in milliseconds
      - avg_loss_pct: mean packet loss as a percentage (0-100)

    Args:
        target: Optional exact target name to filter to a single target
            (see list_targets). Omit for all targets.
        hours: Size of the lookback window in hours (default 24).

    Use this to answer questions like "how is my connection to 8.8.8.8?" or
    "which targets have the worst latency today?".
    """
    hours, err = _validate_hours(hours)
    if err:
        return {"error": err}

    try:
        target_filter = (
            f"|> filter(fn: (r) => r.target == {flux_str(target)}) "
            if target
            else ""
        )
        base = _base_flux(["latency", "dns_latency"], hours)
    except ValueError as exc:
        return {"error": str(exc)}

    group = '|> group(columns: ["target", "_measurement"]) '
    median_flux = (
        base + '|> filter(fn: (r) => r._field == "median") '
        + target_filter + group + "|> median()"
    )
    p95_flux = (
        base + '|> filter(fn: (r) => r._field == "median") '
        + target_filter + group + "|> quantile(q: 0.95)"
    )
    loss_flux = (
        base + '|> filter(fn: (r) => r._field == "loss") '
        + target_filter + _CLAMP_LOSS_RATIO + group + "|> mean()"
    )

    stats: dict[tuple, dict] = {}

    def _merge(rows: list[dict], key: str, scale: float) -> None:
        for row in rows:
            value = row.get("_value")
            if value is None:
                continue
            k = (row.get("target"), row.get("_measurement"))
            entry = stats.setdefault(
                k, {"target": k[0], "measurement": k[1]}
            )
            entry[key] = round(float(value) * scale, 3)

    try:
        _merge(query_influx(median_flux), "median_ms", 1000.0)
        _merge(query_influx(p95_flux), "p95_ms", 1000.0)
        _merge(query_influx(loss_flux), "avg_loss_pct", 100.0)
    except Exception as exc:  # influx client raises many exception types
        return {"error": f"InfluxDB query failed: {exc}"}

    results = sorted(
        stats.values(), key=lambda e: (e.get("target") or "", e.get("measurement") or "")
    )
    if target and not results:
        return {
            "window_hours": hours,
            "stats": [],
            "note": (
                f"No data points found for target '{target}' in the last "
                f"{hours}h. Check the exact name with list_targets."
            ),
        }
    return {"window_hours": hours, "stats": results}


@mcp.tool()
def get_loss_events(hours: int = 24, min_loss_pct: float = 5) -> dict:
    """Find time windows where packet loss exceeded a threshold.

    Scans the `latency` and `dns_latency` measurements and returns every
    data point in the window whose packet loss was at or above
    min_loss_pct, as a list of {time, target, measurement, loss_pct},
    newest first (capped at 500 events).

    Args:
        hours: Lookback window in hours (default 24).
        min_loss_pct: Minimum loss percentage (0-100) for a point to count
            as an event (default 5).

    Use this to answer "did we drop packets last night?" or "when did the
    link to my ISP degrade?". For CPE microcuts, use get_microcut_stats.
    """
    hours, err = _validate_hours(hours)
    if err:
        return {"error": err}
    try:
        threshold = float(min_loss_pct)
    except (TypeError, ValueError):
        return {"error": f"Invalid min_loss_pct value {min_loss_pct!r}."}
    if not 0 <= threshold <= 100:
        return {"error": "min_loss_pct must be between 0 and 100."}

    flux = (
        _base_flux(["latency", "dns_latency"], hours)
        + '|> filter(fn: (r) => r._field == "loss") '
        + _CLAMP_LOSS_RATIO
        + f"|> filter(fn: (r) => r._value >= {threshold / 100.0}) "
        + "|> group() "
        + '|> sort(columns: ["_time"], desc: true) '
        + f"|> limit(n: {MAX_EVENT_ROWS})"
    )
    try:
        rows = query_influx(flux)
    except Exception as exc:
        return {"error": f"InfluxDB query failed: {exc}"}

    events = [
        {
            "time": _iso(row.get("_time")),
            "target": row.get("target"),
            "measurement": row.get("_measurement"),
            "loss_pct": round(float(row.get("_value", 0.0)) * 100.0, 2),
        }
        for row in rows
    ]
    return {
        "window_hours": hours,
        "min_loss_pct": threshold,
        "event_count": len(events),
        "truncated": len(events) >= MAX_EVENT_ROWS,
        "events": events,
    }


@mcp.tool()
def get_microcut_stats(hours: int = 24) -> dict:
    """Summarize CPE microcut activity (brief loss spikes on the local link).

    Reads the high-frequency `cpe_latency` measurement (fields in ms, loss
    as a 0-100 percentage, tagged by target and protocol) and returns, per
    target+protocol:
      - lossy_windows: number of measurement windows with any loss (>0%)
      - max_loss_pct: worst loss percentage seen
      - median_jitter_ms: median jitter in milliseconds
    plus the 5 worst individual windows as {time, target, protocol, loss_pct}.

    Args:
        hours: Lookback window in hours (default 24).

    Use this to answer "were there microcuts last night?" or "is the CPE
    link flapping?".
    """
    hours, err = _validate_hours(hours)
    if err:
        return {"error": err}

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
        + "|> limit(n: 5)"
    )

    stats: dict[tuple, dict] = {}

    def _merge(rows: list[dict], key: str, cast=float) -> None:
        for row in rows:
            value = row.get("_value")
            if value is None:
                continue
            k = (row.get("target"), row.get("protocol"))
            entry = stats.setdefault(k, {"target": k[0], "protocol": k[1]})
            entry[key] = cast(value)

    try:
        _merge(query_influx(lossy_count_flux), "lossy_windows", int)
        _merge(query_influx(max_loss_flux), "max_loss_pct",
               lambda v: round(float(v), 2))
        _merge(query_influx(median_jitter_flux), "median_jitter_ms",
               lambda v: round(float(v), 3))
        worst_rows = query_influx(worst_flux)
    except Exception as exc:
        return {"error": f"InfluxDB query failed: {exc}"}

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
    ]
    return {
        "window_hours": hours,
        "stats": sorted(
            stats.values(),
            key=lambda e: (e.get("target") or "", e.get("protocol") or ""),
        ),
        "worst_windows": worst_windows,
    }
