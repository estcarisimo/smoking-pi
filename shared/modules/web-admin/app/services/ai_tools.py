"""Tool registry for the in-UI AI assistant.

Mirrors the SmokePing MCP server's tool surface (shared/modules/mcp-server)
so the web assistant and Claude Desktop behave the same way, but implemented
against web-admin's own backends:

- config-manager REST via the existing ConfigAPIGateway (target CRUD,
  system status)
- InfluxDB 2.x via influxdb-client (latency / loss / microcut queries)

``TOOLS`` is the Anthropic ``tools`` schema list; ``execute_tool`` is the
dispatcher. Mutating tools are listed in ``MUTATING_TOOLS`` and are executed
only after explicit user confirmation (enforced by the /ai routes, which
never call execute_tool for them until the user approves).

There is deliberately no apply_config tool: the config-manager regenerates
the SmokePing configuration automatically on every target change.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

from app.services.config_api import ConfigAPIGateway

# One shared gateway (module-level, like app.routes.api.config_api)
gateway = ConfigAPIGateway()

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

MAX_HOURS = 24 * 365
MAX_EVENT_ROWS = 200

_TARGET_FIELDS = ("id", "name", "host", "title", "category", "probe", "is_active")

MUTATING_TOOLS = {"add_target", "remove_target", "toggle_target"}


# ---------------------------------------------------------------------------
# InfluxDB (lazy, mirrors mcp-server/backends.py)
# ---------------------------------------------------------------------------

DEFAULT_INFLUX_URL = "http://influxdb:8086"
DEFAULT_INFLUX_ORG = "smokeping"
DEFAULT_INFLUX_BUCKET = "smokeping"

_influx_client = None


def influx_bucket() -> str:
    return os.environ.get("INFLUX_BUCKET", DEFAULT_INFLUX_BUCKET)


def _get_query_api():
    global _influx_client
    if _influx_client is None:
        from influxdb_client import InfluxDBClient

        _influx_client = InfluxDBClient(
            url=os.environ.get("INFLUX_URL", DEFAULT_INFLUX_URL),
            token=os.environ.get("INFLUX_TOKEN", ""),
            org=os.environ.get("INFLUX_ORG", DEFAULT_INFLUX_ORG),
            timeout=60_000,
        )
    return _influx_client.query_api()


def query_influx(flux: str) -> list:
    """Run a Flux query and return a flat list of record dicts."""
    tables = _get_query_api().query(flux)
    rows = []
    for table in tables:
        for record in table.records:
            rows.append(dict(record.values))
    return rows


def flux_str(value: str) -> str:
    """Quote a Flux string literal; reject injection-capable characters."""
    if not isinstance(value, str):
        raise ValueError("expected a string")
    if '"' in value or "\\" in value or any(ord(c) < 32 for c in value):
        raise ValueError(
            f"invalid characters in value {value!r}: double quotes, "
            "backslashes, and control characters are not allowed"
        )
    return f'"{value}"'


def _base_flux(measurements: list, hours: int) -> str:
    predicate = " or ".join(f"r._measurement == {flux_str(m)}" for m in measurements)
    return (
        f"from(bucket: {flux_str(influx_bucket())}) "
        f"|> range(start: -{int(hours)}h) "
        f"|> filter(fn: (r) => {predicate}) "
    )


# loss in latency/dns_latency is a 0..1 ratio (legacy points may exceed 1)
_CLAMP_LOSS_RATIO = (
    "|> map(fn: (r) => ({r with _value: "
    "if r._value > 1.0 then 1.0 "
    "else if r._value < 0.0 then 0.0 "
    "else r._value})) "
)


# ---------------------------------------------------------------------------
# Anthropic tool schemas
# ---------------------------------------------------------------------------

_HOURS_PROP = {
    "type": "integer",
    "description": "Lookback window in hours (default 24).",
}

TOOLS = [
    {
        "name": "list_targets",
        "description": (
            "List all SmokePing monitoring targets with id, name, host, "
            "title, category, probe, and active flag. Use this to see what "
            "is monitored or to find a target's exact name."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "add_target",
        "description": (
            "Add a new host to SmokePing monitoring. Requires user "
            "confirmation. The SmokePing configuration is regenerated "
            "automatically afterwards."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Unique identifier; must start with a letter and "
                        "contain only letters, digits, and underscores."
                    ),
                },
                "host": {
                    "type": "string",
                    "description": "IP address or hostname to monitor.",
                },
                "category": {
                    "type": "string",
                    "description": "Existing category name (default 'custom').",
                },
                "title": {
                    "type": "string",
                    "description": "Display title (defaults to the name).",
                },
                "probe": {
                    "type": "string",
                    "description": "Probe name (defaults to the default probe).",
                },
            },
            "required": ["name", "host"],
        },
    },
    {
        "name": "remove_target",
        "description": (
            "Permanently delete a monitoring target by exact name. Requires "
            "user confirmation. Historical time-series data is kept. To "
            "pause monitoring instead, use toggle_target."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Exact target name."}
            },
            "required": ["name"],
        },
    },
    {
        "name": "toggle_target",
        "description": (
            "Enable or disable monitoring for a target by exact name (flips "
            "its active state). Requires user confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Exact target name."}
            },
            "required": ["name"],
        },
    },
    {
        "name": "system_status",
        "description": (
            "Get health/status of the monitoring stack: config-manager "
            "health, database availability, target counts, and whether the "
            "SmokePing container is running."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_latency_stats",
        "description": (
            "Per-target latency and loss statistics over a time window: "
            "median_ms, p95_ms, avg_loss_pct (from the latency and "
            "dns_latency measurements)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Optional exact target name filter.",
                },
                "hours": _HOURS_PROP,
            },
        },
    },
    {
        "name": "get_loss_events",
        "description": (
            "Find data points where packet loss met or exceeded a threshold "
            "percentage, newest first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": _HOURS_PROP,
                "min_loss_pct": {
                    "type": "number",
                    "description": "Loss threshold percentage 0-100 (default 5).",
                },
            },
        },
    },
    {
        "name": "get_microcut_stats",
        "description": (
            "Summarize CPE microcut activity (brief loss spikes on the "
            "local link) from the high-frequency cpe_latency measurement."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"hours": _HOURS_PROP},
        },
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_hours(hours: Any) -> tuple:
    if hours is None:
        return 24, None
    try:
        h = int(hours)
    except (TypeError, ValueError):
        return None, f"Invalid hours value {hours!r}: must be an integer."
    if not 1 <= h <= MAX_HOURS:
        return None, f"hours must be between 1 and {MAX_HOURS} (got {h})."
    return h, None


def _slim_target(target: dict) -> dict:
    return {k: target.get(k) for k in _TARGET_FIELDS}


def _fetch_targets() -> list:
    return gateway.get_all_targets_from_db().get("targets", [])


def _resolve_target(name: str) -> tuple:
    targets = _fetch_targets()
    for target in targets:
        if target.get("name") == name:
            return target, None
    return None, {
        "error": f"No monitoring target named '{name}' was found.",
        "available_targets": sorted(t.get("name") for t in targets if t.get("name")),
    }


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else None


# ---------------------------------------------------------------------------
# Tool implementations (mirror mcp-server/server.py behavior)
# ---------------------------------------------------------------------------


def _list_targets(_input: dict) -> dict:
    targets = [_slim_target(t) for t in _fetch_targets()]
    return {"total": len(targets), "targets": targets}


def _add_target(tool_input: dict) -> dict:
    name = tool_input.get("name")
    host = tool_input.get("host")
    category = tool_input.get("category") or "custom"
    if not isinstance(name, str) or not _NAME_RE.match(name or ""):
        return {
            "error": (
                f"Invalid target name {name!r}: names must start with a letter "
                "and contain only letters, digits, and underscores."
            )
        }
    if not isinstance(host, str) or not host.strip():
        return {"error": "host must be a non-empty IP address or hostname."}

    categories = gateway.get_categories_from_db().get("categories", [])
    cat = next((c for c in categories if c.get("name") == category), None)
    if cat is None:
        return {
            "error": f"Unknown category '{category}'.",
            "valid_categories": sorted(
                c.get("name") for c in categories if c.get("name")
            ),
        }

    probes = gateway.get_probes_from_db().get("probes", [])
    probe_name = tool_input.get("probe")
    if probe_name is not None:
        probe_row = next((p for p in probes if p.get("name") == probe_name), None)
        if probe_row is None:
            return {
                "error": f"Unknown probe '{probe_name}'.",
                "valid_probes": sorted(p.get("name") for p in probes if p.get("name")),
            }
    else:
        probe_row = next((p for p in probes if p.get("is_default")), None)
        if probe_row is None and probes:
            probe_row = probes[0]
        if probe_row is None:
            return {"error": "No probes are configured in the system."}

    result = gateway.create_target_in_db(
        {
            "name": name,
            "host": host.strip(),
            "title": tool_input.get("title") or name,
            "category_id": cat["id"],
            "probe_id": probe_row["id"],
        }
    )
    return {
        "success": True,
        "target": _slim_target(result.get("target", {})),
        "note": (
            "SmokePing configuration was regenerated automatically; data "
            "for the new target appears within a few minutes."
        ),
    }


def _remove_target(tool_input: dict) -> dict:
    target, err = _resolve_target(tool_input.get("name"))
    if err:
        return err
    result = gateway.delete_target_from_db(target["id"])
    return {
        "success": True,
        "message": result.get("message", "Target deleted."),
        "deleted": _slim_target(target),
    }


def _toggle_target(tool_input: dict) -> dict:
    target, err = _resolve_target(tool_input.get("name"))
    if err:
        return err
    result = gateway.toggle_target_in_db(target["id"])
    return {
        "success": True,
        "message": result.get("message", "Target toggled."),
        "target": _slim_target(result.get("target", {})),
    }


def _system_status(_input: dict) -> dict:
    status = gateway.get_service_status()
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
    return {"status": status, "summary": "; ".join(parts)}


def _get_latency_stats(tool_input: dict) -> dict:
    hours, err = _validate_hours(tool_input.get("hours"))
    if err:
        return {"error": err}
    target = tool_input.get("target")

    try:
        target_filter = (
            f"|> filter(fn: (r) => r.target == {flux_str(target)}) " if target else ""
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

    stats: dict = {}

    def _merge(rows: list, key: str, scale: float) -> None:
        for row in rows:
            value = row.get("_value")
            if value is None:
                continue
            k = (row.get("target"), row.get("_measurement"))
            entry = stats.setdefault(k, {"target": k[0], "measurement": k[1]})
            entry[key] = round(float(value) * scale, 3)

    _merge(query_influx(median_flux), "median_ms", 1000.0)
    _merge(query_influx(p95_flux), "p95_ms", 1000.0)
    _merge(query_influx(loss_flux), "avg_loss_pct", 100.0)

    results = sorted(
        stats.values(),
        key=lambda e: (e.get("target") or "", e.get("measurement") or ""),
    )
    out = {"window_hours": hours, "stats": results}
    if target and not results:
        out["note"] = (
            f"No data points found for target '{target}' in the last {hours}h. "
            "Check the exact name with list_targets."
        )
    return out


def _get_loss_events(tool_input: dict) -> dict:
    hours, err = _validate_hours(tool_input.get("hours"))
    if err:
        return {"error": err}
    raw = tool_input.get("min_loss_pct", 5)
    try:
        threshold = float(raw)
    except (TypeError, ValueError):
        return {"error": f"Invalid min_loss_pct value {raw!r}."}
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
    rows = query_influx(flux)
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


def _get_microcut_stats(tool_input: dict) -> dict:
    hours, err = _validate_hours(tool_input.get("hours"))
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

    stats: dict = {}

    def _merge(rows: list, key: str, cast) -> None:
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

    return {
        "window_hours": hours,
        "stats": sorted(
            stats.values(),
            key=lambda e: (e.get("target") or "", e.get("protocol") or ""),
        ),
        "worst_windows": [
            {
                "time": _iso(row.get("_time")),
                "target": row.get("target"),
                "protocol": row.get("protocol"),
                "loss_pct": round(float(row.get("_value", 0.0)), 2),
            }
            for row in worst_rows
        ],
    }


_DISPATCH = {
    "list_targets": _list_targets,
    "add_target": _add_target,
    "remove_target": _remove_target,
    "toggle_target": _toggle_target,
    "system_status": _system_status,
    "get_latency_stats": _get_latency_stats,
    "get_loss_events": _get_loss_events,
    "get_microcut_stats": _get_microcut_stats,
}


def execute_tool(name: str, tool_input: dict | None) -> dict:
    """Run a tool and return its JSON-serializable result.

    Never raises: backend failures come back as {"error": ...} so the model
    can relay them to the user.
    """
    handler = _DISPATCH.get(name)
    if handler is None:
        return {"error": f"Unknown tool '{name}'."}
    try:
        return handler(tool_input or {})
    except Exception as exc:  # backend/network errors become tool errors
        return {"error": f"{type(exc).__name__}: {exc}"}
