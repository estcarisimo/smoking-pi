"""SmokePing MCP server.

Exposes the Raspberry Pi SmokePing stack (config-manager REST API +
InfluxDB time series) as MCP tools so an AI assistant can inspect and
operate the monitoring system.

Run via ``main.py`` (stdio or streamable-http transport).
"""

from __future__ import annotations

import difflib
import functools
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any

try:
    # mcp 1.x
    from mcp.server.fastmcp import FastMCP, Image
except ImportError:  # mcp >= 2.0 renamed FastMCP to MCPServer (same API)
    from mcp.server.mcpserver import Image
    from mcp.server.mcpserver import MCPServer as FastMCP

import backends
import links
from backends import ConfigAPIError, flux_str, influx_bucket, query_influx
from common import aggregates, cadence, charts, microcuts, mutes, openclaw

# Framing for the connecting client. Without it an agent that also has shell
# access will answer "how is my internet?" by running ping/curl itself, which
# only describes this instant and throws the answer away. The whole point of
# this host is that the measurement already happened, continuously, and is
# still on disk.
SERVER_INSTRUCTIONS = """\
This server exposes a Raspberry Pi that has been continuously measuring a home
network for as long as it has been running. It is a record of the past, not a
probe you trigger.

Every monitored target is measured on a fixed cycle (300 seconds unless its
probe was configured otherwise) and the CPE/gateway link is sampled every 10
seconds; results are kept for months. So questions about
how the connection *is*, *was*, or *has been behaving* are answered from
recorded history here — including questions about last night, yesterday, or a
moment the user noticed something and you were not watching.

Prefer these tools over running ping, curl, traceroute, or a speed test in a
shell. A live probe describes one instant, cannot see the past, competes with
the very measurement this host is taking, and will disagree with the graphs the
user is looking at. Use `get_latency_stats` for how a target has been
performing (ICMP, DNS, HTTP fetches and TCP connects; take exact names from
`list_targets`), `get_loss_events` for when packets were dropped, and
`get_microcut_stats` for brief local-link dropouts, and `get_wifi_stats` for
the Pi's own wireless uplink (signal, bitrate, disconnects, roams) when the
host is on Wi-Fi -- a microcut that lines up with a signal dip is the router
or the air, not the ISP. Start with `system_status` if something looks wrong
with the monitoring itself.

Two things routinely look like faults and are not: hosts that never answer ICMP
at all chart a permanently flat 100% loss (a dead-flat line with no variance is
a monitoring artifact, not an outage), and the CPE gateway rate-limits ICMP,
which shows as a constant single-digit loss floor on the local link.

Responses may carry a `links` object with URLs into the Grafana panel showing
that target, the per-ping detail, a comparison against its peers, and the page
for editing it. Pass the relevant one through to the user — the graph shows the
shape of a problem far better than a median does, and the links are already
scoped to the target and time window being discussed. Do not build these URLs
yourself; if `links` is absent, deep links are not configured on this
deployment and there is no URL to give.

When the user wants a *picture* -- to look at the shape themselves, or to
send to a friend or an ISP who has no login here -- call `get_chart`. It draws
the target's latency (median with the spread of individual pings) over its
loss as a PNG, on request only; no other tool attaches images. With
`deliver=true` the file is also posted into the chat so it can be forwarded.

Keys ending in `_tunnel` are the same page reached from outside the home
network. When both are present, offer both — label them for where the reader
is standing ("at home" / "from anywhere"), because the plain link is faster
and works when the tunnel is down, and the tunnel one is the only one that
opens on cellular. When a `_tunnel` key is absent there is no such address:
say nothing about it rather than constructing one."""

mcp = FastMCP("smokeping", instructions=SERVER_INSTRUCTIONS)

log = logging.getLogger("mcp.tools")

# Values that should never reach the log even if a tool grows such an argument.
_SECRET_ARG_HINTS = ("token", "password", "secret", "key", "authorization")
_MAX_ARG_CHARS = 60


def _summarize_args(kwargs: dict) -> str:
    parts = []
    for name, value in sorted(kwargs.items()):
        if value is None:
            continue
        if any(hint in name.lower() for hint in _SECRET_ARG_HINTS):
            parts.append(f"{name}=<redacted>")
            continue
        text = str(value)
        if len(text) > _MAX_ARG_CHARS:
            text = text[:_MAX_ARG_CHARS] + "…"
        parts.append(f"{name}={text}")
    return " ".join(parts) or "-"


def _summarize_result(result: Any) -> str:
    """One-word shape of what came back, enough to spot an empty answer."""
    if isinstance(result, dict):
        if "error" in result:
            return f"error:{str(result['error'])[:60]}"
        for key in ("stats", "events", "targets"):
            if isinstance(result.get(key), list):
                return f"{len(result[key])} {key}"
        if "total" in result:
            return f"total={result['total']}"
        return "ok"
    if isinstance(result, list):
        images = sum(1 for item in result if isinstance(item, Image))
        return f"{len(result)} blocks ({images} image)" if images else "ok"
    return "ok"


def logged_tool(func):
    """Log every invocation so tool use is provable from the server side.

    Without this the access log shows only `POST /mcp 200`, which cannot
    distinguish an agent calling a tool from an agent merely connecting — a
    gap that let a broken integration look healthy for days while an agent
    answered from its shell instead.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        started = time.monotonic()
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            elapsed_ms = (time.monotonic() - started) * 1000
            log.warning("tool=%s args=%s -> raised:%s in %.0fms",
                        func.__name__, _summarize_args(kwargs),
                        type(exc).__name__, elapsed_ms)
            raise
        elapsed_ms = (time.monotonic() - started) * 1000
        log.info("tool=%s args=%s -> %s in %.0fms",
                 func.__name__, _summarize_args(kwargs),
                 _summarize_result(result), elapsed_ms)
        return result

    return wrapper


def _tool_error(message: str, exc: BaseException | None = None, **extra) -> dict:
    """An error result whose text is chosen here, never derived from ``exc``.

    A tool result goes to the model and from there into a chat, so it gets
    the same discipline as an HTTP error body: the InfluxDB client's
    exception, for one, carries the full response headers and the Flux
    query. The detail goes to the log under a short id that is also
    returned, so "see the mcp-server log" is a real instruction.
    """
    error_id = secrets.token_hex(4)
    log.error("%s [%s]", message, error_id, exc_info=exc)
    return {"error": f"{message} (log id {error_id})", "error_id": error_id,
            **extra}


_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
# Linux interface names: up to 15 chars, letters/digits and the punctuation
# udev actually produces (wlan0, wlp3s0, wlan0.1, wlan-ap). Not _NAME_RE:
# that one is for SmokePing section names and rejects the dot and the dash.
_IFACE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")

# Below this the Wi-Fi link is called weak; shared with the alerter's verdict
# through the same env var (docs/wifi.md).
DEFAULT_WIFI_WEAK_DBM = -75.0

MAX_HOURS = 24 * 365
MAX_EVENT_ROWS = 500
# Rows fetched for the roll-ups in get_loss_events. The events list stays
# capped at MAX_EVENT_ROWS, but episodes and widespread runs must be computed
# from everything in the window: a three-hour cut across 18 targets is 720
# points, and a roll-up built from the newest 500 of them gets its start wrong.
MAX_ROLLUP_ROWS = 5000

# get_loss_events: by default a point is an event when it lost more than one
# ping's worth, of however many its probe sends (common.cadence
# .EVENT_LOST_PINGS): one lost ping is the Wi-Fi hop's background on this
# host, 60-300 such points a day spread over every target. That is the old
# 15% on a 10-ping probe; a fixed percent meant 3 of 20 and 1 of 5 elsewhere.
# A run of loss points on one target with gaps no longer than this many of
# its probe steps is one episode: one missing point does not split a cut in
# two. Each target's own step (common.cadence), 300 s when unknown.
EPISODE_GAP_STEPS = 2
# Share of the reporting targets that must have an event in the same probe
# step for that step to count as widespread -- the same bar the alerter's
# rule_widespread applies (its own copy; the alerter is not importable here).
WIDESPREAD_SHARE = 0.8
STEP_S = cadence.DEFAULT_STEP
# Cycles of total loss before a widespread run is attributed to this host's
# uplink rather than to a brief cut -- the alerter's DOWN_MIN_POINTS.
UPLINK_MIN_STEPS = 3

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


def _validate_interface(name: Any) -> str | None:
    if not isinstance(name, str) or not _IFACE_RE.match(name):
        return (
            f"Invalid interface name {name!r}: a Linux interface name is 1-15 "
            "characters of letters, digits, '.', '-' or '_' (for example wlan0)."
        )
    return None


def _wifi_weak_dbm() -> float:
    raw = (os.environ.get("WIFI_WEAK_DBM") or "").strip()
    try:
        return float(raw) if raw else DEFAULT_WIFI_WEAK_DBM
    except ValueError:
        return DEFAULT_WIFI_WEAK_DBM


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


def _target_catalog() -> dict[str, dict]:
    """name -> DB row, for attaching links to measurement results.

    Measurement tools query InfluxDB, which knows a target's name but not the
    category the database filed it under, and the two vocabularies differ. This
    bridges them, solely so a target can be linked to the side-by-side
    dashboard for its peers.

    Returns {} when the config API is unreachable, which costs only that one
    link — the numbers are the answer and links are a garnish, so no failure
    here may propagate.
    """
    if not links.links_configured():
        return {}
    try:
        return {
            t["name"]: t
            for t in _fetch_targets(backends.get_config_api())
            if t.get("name")
        }
    except Exception as exc:  # deliberately broad -- see below
        # Anything at all: the measurement numbers are the answer and the links
        # are decoration, so no failure here may propagate. Catching
        # ConfigAPIError alone is not enough — the config API can fail in ways
        # that surface as httpx or parsing errors.
        log.warning("links: target catalog unavailable, omitting links: %s", exc)
        return {}


def _links_for(
    catalog: dict[str, dict],
    name: str | None,
    measurement: str | None,
    hours: int | None = None,
    at: Any = None,
) -> dict[str, str]:
    row = catalog.get(name or "") or {}
    return links.target_links(
        name,
        measurement=measurement,
        db_category=row.get("category"),
        hours=hours,
        at=at,
    )


# ---------------------------------------------------------------------------
# Target management tools (config-manager REST API)
# ---------------------------------------------------------------------------


@mcp.tool()
@logged_tool
def list_targets() -> dict:
    """List all SmokePing monitoring targets.

    Returns every configured target with its database id, name, host
    (IP or hostname being pinged), title, category (e.g. dns, web, custom,
    cpe), probe type, and whether it is currently active (inactive targets
    exist in the database but are not monitored).

    Use this to see what is being monitored, or to find a target's exact
    name before calling remove_target / toggle_target.

    Each target carries a `links` object (graph, per-ping detail, comparison
    against its peers, edit page) when deep links are configured, each with a
    `_tunnel` twin reachable from outside the home network when one exists.
    """
    try:
        rows = _fetch_targets(backends.get_config_api())
    except ConfigAPIError as exc:
        return {"error": str(exc)}

    targets = []
    for row in rows:
        slim = _slim_target(row)
        target_links = links.target_links(
            row.get("name"),
            measurement=links.measurement_for_probe(row.get("probe")),
            db_category=row.get("category"),
        )
        if target_links:
            slim["links"] = target_links
        targets.append(slim)
    return {"total": len(targets), "targets": targets}


@mcp.tool()
@logged_tool
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
@logged_tool
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
@logged_tool
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
@logged_tool
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


def _uplink_changes(hours: float) -> list[dict]:
    """host_uplink change points in the window, oldest first, each with a
    ``said`` sentence (the alerter's and the digest's wording); [] on failure."""
    try:
        rows = query_influx(aggregates.uplink_changes_flux(f"-{int(hours)}h"))
    except Exception as exc:  # noqa: BLE001 - optional measurement, never fatal
        # The type only: an influx error message can carry the token.
        log.warning("host_uplink change lookup failed: %s", type(exc).__name__)
        return []
    return [{**c, "said": aggregates.describe_uplink_change(c)}
            for c in aggregates.parse_uplink_changes(rows)]


UPLINK_STATUS_HOURS = 24 * 7


def _uplink_now() -> dict:
    """The current uplink and its last change within a week; {} without
    host_uplink data or on failure."""
    try:
        rows = query_influx(
            f'from(bucket: {flux_str(influx_bucket())}) |> range(start: -10m) '
            '|> filter(fn: (r) => r._measurement == "host_uplink" and '
            '(r._field == "interface" or r._field == "kind" or r._field == "family")) '
            "|> last()"
        )
        fields = {r.get("_field"): r.get("_value") for r in rows if r.get("_field")}
        if "interface" not in fields:
            return {}
        out: dict = {
            "interface": str(fields.get("interface") or ""),
            "kind": str(fields.get("kind") or ""),
            "family": int(fields.get("family") or 0),
        }
    # The casts are inside too, as in _wifi_now: one malformed row must not
    # fail system_status, which reports the whole stack's health.
    except Exception as exc:  # noqa: BLE001
        log.warning("host_uplink status lookup failed: %s", type(exc).__name__)
        return {}
    changes = _uplink_changes(UPLINK_STATUS_HOURS)
    out["last_change"] = changes[-1] if changes else None
    out["changes_7d"] = len(changes)
    return out


@mcp.tool()
@logged_tool
def system_status() -> dict:
    """Get the health and status of the SmokePing monitoring stack.

    Combines the config-manager health check with the detailed service
    status: database availability and target counts, whether the generated
    SmokePing config files exist, and whether the SmokePing container is
    running.

    This reports the health of the MONITORING SYSTEM, not of the network it
    measures. For "how is my internet?" use get_latency_stats. Use this one
    when the monitoring itself looks wrong — no recent data, a target that
    never appears, graphs that stopped updating.

    `uplink` names the interface every measurement crosses (`kind`:
    wireless, wired, virtual or none) and its `last_change` within a week:
    numbers from before a change crossed a different link.
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

    # Whether this host measures through Wi-Fi changes how every other number
    # should be read, so it belongs on the status page. Absent on a wired
    # host (no wifi_link data) and on any failure -- the stack's health must
    # not depend on one optional measurement.
    wifi = _wifi_now()
    if wifi:
        result["wifi"] = wifi

    # Which interface every measurement crosses, and when that last changed:
    # a latency step at a cable being plugged in is a path change, not the
    # ISP. Absent without host_uplink data (older exporter) or on failure.
    uplink = _uplink_now()
    if uplink:
        result["uplink"] = uplink

    # The one place that reports on deep-link configuration. Repeating the
    # hint on every measurement response would be noise; saying it nowhere
    # would make an unconfigured deployment indistinguishable from a bug.
    if links.links_configured():
        result["links"] = links.entry_point_links(hours=24)
    elif not links.dashboards_match_backend():
        # A different reason from "unconfigured", and conflating them would
        # send someone to set PUBLIC_BASE_HOST when it is already set.
        result["deep_links"] = links.BACKEND_HINT
    else:
        result["deep_links"] = links.CONFIG_HINT
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


# Every per-target measurement with a `median` and a `loss` field in seconds
# and ratio. `cpe_latency` has its own tool (get_microcut_stats).
_STATS_MEASUREMENTS = ["latency", "dns_latency", "http_latency", "tcp_latency"]


def _unknown_target_error(name: str) -> dict | None:
    """An error naming the real targets, when `name` is not one of them.

    An agent guesses names ("Cloudflare" for "cloudflare", "CPE_Gateway"),
    and "no data points" reads like an outage rather than a typo. Returns
    None when the name exists (its data is simply missing) or when the
    config API cannot say -- then the caller keeps its plain note.
    """
    try:
        names = sorted(
            t["name"] for t in _fetch_targets(backends.get_config_api())
            if t.get("name")
        )
    except Exception as exc:  # deliberately broad, as in _target_catalog
        log.warning("target lookup unavailable: %s", exc)
        return None
    if not names or name in names:
        return None
    result: dict[str, Any] = {
        "error": f"No monitoring target named '{name}' was found.",
        "available_targets": names,
    }
    if any(w in name.lower() for w in ("cpe", "gateway", "router")):
        result["hint"] = ("The CPE gateway is not a target: its latency and "
                          "microcuts come from get_microcut_stats.")
    close = [n for n in names if n.lower() == name.lower()] or (
        difflib.get_close_matches(name, names, n=3, cutoff=0.6)
    )
    if close:
        result["did_you_mean"] = close
    return result


@mcp.tool()
@logged_tool
def get_latency_stats(target: str | None = None, hours: int = 24) -> dict:
    """Get latency and packet-loss statistics per monitoring target.

    For each target -- ICMP (`latency`), DNS (`dns_latency`), HTTP fetches
    (`http_latency`, the *_h1/_h2/_h3 targets) and TCP connects
    (`tcp_latency`, the *_tcp443 targets) -- computes over the requested
    time window:
      - median_ms: median round-trip latency in milliseconds
      - p95_ms: 95th-percentile latency in milliseconds
      - avg_loss_pct: mean packet loss as a percentage (0-100)

    For HTTP targets the median is the whole fetch (DNS, connect, TLS and
    the response), so it is not comparable with an ICMP round trip.

    Args:
        target: Optional exact target name to filter to a single target
            (see list_targets). Omit for all targets. A name that is not a
            target returns an error listing the real ones.
        hours: Size of the lookback window in hours (default 24).

    The data is already recorded — this host measures every target on a
    fixed cycle (300 seconds by default) and has done so continuously. Use this instead of
    running ping yourself: it covers the whole window rather than this
    instant, it matches the graphs the user sees, and it does not add probe
    traffic. This is the tool for "how is my internet / my connection?",
    "how is the link to 8.8.8.8?", and "which targets are worst today?".
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
        base = _base_flux(_STATS_MEASUREMENTS, hours)
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
        return _tool_error("InfluxDB query failed", exc)

    results = sorted(
        stats.values(), key=lambda e: (e.get("target") or "", e.get("measurement") or "")
    )
    catalog = _target_catalog()
    for entry in results:
        entry_links = _links_for(
            catalog, entry.get("target"), entry.get("measurement"), hours=hours
        )
        if entry_links:
            entry["links"] = entry_links
    if target and not results:
        unknown = _unknown_target_error(target)
        if unknown:
            return unknown
        return {
            "window_hours": hours,
            "stats": [],
            "note": (
                f"No data points found for target '{target}' in the last "
                f"{hours}h. Check the exact name with list_targets."
            ),
        }
    return {"window_hours": hours, "stats": results}


_epoch = cadence.epoch


def _cadences() -> dict[str, cadence.Cadence]:
    """Each target's step and pings; empty (every target on the default)
    when the query fails -- the cadence refines the answer, it is never a
    reason to refuse one."""
    try:
        return cadence.by_target(query_influx(cadence.cadence_flux()))
    except Exception:  # influx client raises many exception types
        return {}


def _loss_episodes(
    events: list[dict], cadences: dict[str, cadence.Cadence] | None = None
) -> list[dict]:
    """Fold per-target events (any order) into runs no more than
    EPISODE_GAP_STEPS of that target's steps apart. Newest episode first."""
    cadences = cadences or {}
    by_key: dict[tuple, list[dict]] = {}
    for event in events:
        if event.get("_epoch") is None:
            continue
        by_key.setdefault((event["target"], event["measurement"]), []).append(event)

    episodes: list[dict] = []
    for (target, measurement), rows in by_key.items():
        rows.sort(key=lambda e: e["_epoch"])
        step_s = cadence.of(cadences, target).step
        gap_s = EPISODE_GAP_STEPS * step_s
        run: list[dict] = []
        for row in rows + [None]:
            if row is not None and (not run or row["_epoch"] - run[-1]["_epoch"] <= gap_s):
                run.append(row)
                continue
            if run:
                episodes.append(
                    {
                        "target": target,
                        "measurement": measurement,
                        "start": run[0]["time"],
                        "end": run[-1]["time"],
                        "minutes": (
                            int((run[-1]["_epoch"] - run[0]["_epoch"]) // 60)
                            + step_s // 60
                        ),
                        "points": len(run),
                        "max_loss_pct": max(e["loss_pct"] for e in run),
                        "all_lost": all(e["loss_pct"] >= 99.9 for e in run),
                    }
                )
            run = [row] if row is not None else []
    episodes.sort(key=lambda e: (e["start"], e["target"]), reverse=True)
    return episodes


def _widespread_runs(
    events: list[dict], reporting: dict[int, int], step_s: int = STEP_S
) -> list[dict]:
    """Probe steps in which WIDESPREAD_SHARE of the targets reporting IN
    THAT STEP had an event, folded into runs. Newest first.

    Steps are ``step_s`` long -- the slowest target's step, so every target
    has a point in each (``reporting`` must be bucketed the same way). A
    faster target with several points in one step counts as lost in it only
    when all of its events there were.

    The denominator is per step, not per window: the Netflix OCA targets
    rotate, so a day holds more distinct names than any one cycle does, and
    a window-wide count (23 here) put 80% out of reach of the 18 that were
    actually down together -- seen live, the night the field was built for.

    Every target lossy in the same step is one event with one cause: the
    link, or this host. When every one of them lost every packet, the host's
    own uplink was gone (the reference Pi's Wi-Fi radio hung twice in
    September 2026, associated and receiving nothing for 3 and 21 hours) --
    and nothing beyond it could be judged, so the per-target picture for
    those steps is not evidence about any target.
    """
    by_step: dict[int, dict[str, bool]] = {}
    for event in events:
        epoch = event.get("_epoch")
        if epoch is None:
            continue
        step = int(epoch // step_s) * step_s
        hits = by_step.setdefault(step, {})
        hits[event["target"]] = hits.get(event["target"], True) and (
            event["loss_pct"] >= 99.9
        )

    def needed(step: int) -> float | None:
        total = reporting.get(step, 0)
        return WIDESPREAD_SHARE * total if total >= 3 else None

    steps = sorted(
        step for step, hits in by_step.items()
        if needed(step) is not None and len(hits) >= needed(step)
    )
    runs: list[dict] = []
    run: list[int] = []
    for step in steps + [None]:
        if step is not None and (
            not run or step - run[-1] <= EPISODE_GAP_STEPS * step_s
        ):
            run.append(step)
            continue
        if run:
            affected = max(len(by_step[s]) for s in run)
            # A cut that starts or ends mid-cycle shows partial loss in its
            # edge cycles; the 3 h 20 min hang began with one at 95%. Total
            # loss in every cycle but the two edges is total loss.
            lost_steps = sum(
                1 for s in run if sum(by_step[s].values()) >= needed(s)
            )
            all_lost = (
                lost_steps >= len(run) - 2 if len(run) > 2 else lost_steps == len(run)
            )
            uplink = all_lost and lost_steps >= UPLINK_MIN_STEPS
            runs.append(
                {
                    "start": datetime.fromtimestamp(run[0], tz=timezone.utc).isoformat(),
                    "end": datetime.fromtimestamp(run[-1], tz=timezone.utc).isoformat(),
                    "minutes": (run[-1] - run[0]) // 60 + step_s // 60,
                    "targets_affected": affected,
                    "targets_total": max(reporting[s] for s in run),
                    "all_lost": all_lost,
                    "cause": (
                        "this host's uplink: every target lost every packet for "
                        f"{lost_steps} cycles, so nothing beyond it could be judged "
                        "(a hung Wi-Fi radio, a dropped association, a cable) — "
                        "not the ISP"
                        if uplink
                        else "the link: a brief cut that hit every target at once"
                        + (", every packet lost" if all_lost else "")
                    ),
                }
            )
        run = [step] if step is not None else []
    runs.reverse()
    return runs


@mcp.tool()
@logged_tool
def get_loss_events(hours: int = 24, min_loss_pct: float | None = None) -> dict:
    """Find packet loss in the window and say what shape it had.

    Scans the `latency` and `dns_latency` measurements for loss EVENTS --
    points that lost two or more pings (more than 1.5 pings' worth, of
    however many that target's probe sends) -- and returns them three ways:

      - `widespread`: runs of probe steps in which most targets (80%) had
        loss at once, with a `cause` line. `all_lost: true` for three or
        more cycles means every target lost every packet from this host:
        its own uplink was down, and the per-target numbers for that span
        say nothing about any target. Shorter runs are a brief cut of the
        link. Read this first; when it is non-empty it is usually the whole
        story.
      - `episodes`: per target, consecutive loss points folded into one run
        with its start, duration in minutes, point count, worst loss and
        whether it was total. One 25-minute cut is one episode, not five
        events.
      - `by_target` and `events`: the counts and the raw points (newest
        first, `events` capped at 500 with `truncated` set when it was).

    `uplink_changes`, only when this host's uplink changed in the window
    (Wi-Fi to Ethernet, a lost route): points on either side of a change
    crossed different links, so a step there is the path, not the network.

    `background_points` counts the points with some loss that were left
    out: on a host measuring across Wi-Fi that is one lost ping, a few dozen
    to a few hundred a day, and not an event.

    Each `by_target` entry carries `step_s` and `pings`: that target's cycle
    and pings per point, so a loss percentage can be read as pings lost (10%
    of 10 pings is one; 20% of 5 DNS queries is one). Loss values are not
    whole pings: the RRD spreads each cycle over two aligned steps.

    Args:
        hours: Lookback window in hours (default 24).
        min_loss_pct: Optional. A fixed loss percentage (0-100) instead of
            the pings-lost rule, applied to every target alike. Lower it to
            see the single-ping background; it is noise, not events.

    Because the measurement is continuous, this answers questions about
    moments nobody was watching: "did we drop packets last night?", "when did
    the link to my ISP degrade?", "was it bad while I was on that call?". A
    live probe cannot answer any of those. For brief local-link dropouts, use
    get_microcut_stats.

    Note a permanently flat 100%-loss target is usually a host that does not
    answer ICMP at all, not an outage — real loss varies.
    """
    hours, err = _validate_hours(hours)
    if err:
        return {"error": err}
    threshold: float | None = None
    if min_loss_pct is not None:
        try:
            threshold = float(min_loss_pct)
        except (TypeError, ValueError):
            return {"error": f"Invalid min_loss_pct value {min_loss_pct!r}."}
        if not 0 <= threshold <= 100:
            return {"error": "min_loss_pct must be between 0 and 100."}

    cadences = _cadences()
    step_s = cadence.longest_step(cadences)
    if threshold is None:
        prelude, bar = cadence.event_threshold_flux(cadences)
    else:
        prelude, bar = "", cadence.flux_float(threshold / 100.0)

    base = (
        _base_flux(["latency", "dns_latency"], hours)
        + '|> filter(fn: (r) => r._field == "loss") '
        + _CLAMP_LOSS_RATIO
    )
    flux = (
        prelude
        + base
        + f"|> filter(fn: (r) => r._value >= {bar}) "
        + "|> group() "
        + '|> sort(columns: ["_time"], desc: true) '
        + f"|> limit(n: {MAX_ROLLUP_ROWS})"
    )
    background_flux = (
        prelude
        + base
        + f"|> filter(fn: (r) => r._value > 0.0 and r._value < {bar}) "
        + "|> group() |> count()"
    )
    # How many targets reported in EACH probe step, so "most targets" has the
    # denominator of that cycle: the catalog would count targets that were
    # paused, and the whole window counts names that rotated through it.
    # distinct() writes into _value; count(column: "target") would write
    # the count into the target column instead, where nothing reads it --
    # verified live: that shape returned 0 targets.
    targets_flux = (
        base + '|> group(columns: ["_time"]) |> keep(columns: ["_time", "target"]) '
        + '|> distinct(column: "target") |> count()'
    )
    try:
        rows = query_influx(flux)
        background_rows = query_influx(background_flux)
        targets_rows = query_influx(targets_flux)
    except Exception as exc:
        return _tool_error("InfluxDB query failed", exc)

    events = [
        {
            "time": _iso(row.get("_time")),
            "target": row.get("target"),
            "measurement": row.get("_measurement"),
            "loss_pct": round(float(row.get("_value", 0.0)) * 100.0, 2),
            "_epoch": _epoch(row.get("_time")),
        }
        for row in rows
    ]

    # Roll up per target. A hundred loss points on one target is one story, not
    # a hundred, and this is where a link belongs -- attaching a URL to every
    # individual event would bury the numbers under boilerplate.
    rollup: dict[tuple, dict] = {}
    for event in events:
        key = (event["target"], event["measurement"])
        entry = rollup.setdefault(
            key,
            {
                "target": event["target"],
                "measurement": event["measurement"],
                "event_count": 0,
                "max_loss_pct": 0.0,
                "first_time": event["time"],
                "last_time": event["time"],
            },
        )
        if entry["event_count"] == 0:
            own = cadence.of(cadences, event["target"])
            entry["step_s"], entry["pings"] = own.step, own.pings
        entry["event_count"] += 1
        entry["max_loss_pct"] = max(entry["max_loss_pct"], event["loss_pct"])
        # Rows arrive newest-first, so the last one seen is the oldest.
        entry["first_time"] = event["time"]

    catalog = _target_catalog()
    by_target = sorted(
        rollup.values(), key=lambda e: (-e["event_count"], e["target"] or "")
    )
    for entry in by_target:
        entry_links = _links_for(
            catalog, entry["target"], entry["measurement"], hours=hours
        )
        if entry_links:
            entry["links"] = entry_links

    reporting: dict[int, int] = {}
    for row in targets_rows:
        epoch = _epoch(row.get("_time"))
        if epoch is None or row.get("_value") is None:
            continue
        # max, not overwrite: if jitter ever splits one cycle over two
        # _time values, the larger count is the cycle's.
        step = int(epoch // step_s) * step_s
        reporting[step] = max(reporting.get(step, 0), int(row["_value"]))
    background = 0
    for row in background_rows:
        if row.get("_value") is not None:
            background += int(row["_value"])

    episodes = _loss_episodes(events, cadences)
    for episode in episodes:
        episode_links = _links_for(
            catalog, episode["target"], episode["measurement"], hours=hours
        )
        if episode_links.get("graph"):
            episode["graph"] = episode_links["graph"]
    widespread = _widespread_runs(events, reporting, step_s)
    for event in events:
        del event["_epoch"]

    result = {
        "window_hours": hours,
        "min_loss_pct": threshold,
        "min_lost_pings": cadence.EVENT_LOST_PINGS if threshold is None else None,
        "event_count": len(events),
        "truncated": len(events) >= MAX_EVENT_ROWS,
        "targets_reporting": max(reporting.values(), default=0),
        "background_points": background,
        "widespread": widespread,
        "episodes": episodes,
        "by_target": by_target,
        "events": events[:MAX_EVENT_ROWS],
    }
    # Only when the uplink changed in the window: loss or latency on either
    # side of a change crossed different links, so compare them with care.
    changes = _uplink_changes(hours)
    if changes:
        result["uplink_changes"] = changes
    return result


@mcp.tool()
@logged_tool
def get_microcut_stats(hours: int = 24) -> dict:
    """Summarize CPE microcuts: brief cuts on the local link, and the floor
    they stand on.

    Reads the high-frequency `cpe_latency` measurement (10 s windows at 5
    pps, one every ~30 s; loss as a 0-100 percentage, tagged by target and
    protocol). A window counts as a CUT WINDOW only above `cut_loss_pct`
    (MICROCUT_LOSS_PCT, default 50): home gateways rate-limit ICMP, so
    nearly every window shows some loss and the floor is not a fault.

    Returns:
      - `cuts`: runs of cut windows folded into one each, newest first, with
        `start`, `seconds`, `windows`, `max_loss_pct`, `total` (every window
        lost everything) and `confirmed` — two or more windows, or a 100%
        window. A single window at 51-99% is `confirmed: false`: a possible
        cut, five seconds of nothing, not a pattern on its own. Each carries a
        `graph` link zoomed to its moment when links are configured.
      - `stats` per target+protocol: `windows` sampled, `cut_windows`,
        `confirmed_cuts`, `possible_cuts`, the floor as `p50_loss_pct` /
        `p90_loss_pct`, `max_loss_pct`, `median_jitter_ms`.
      - `worst_windows`: the five worst CUT windows; empty when there were
        none, and then `note` states the floor instead.

    Args:
        hours: Lookback window in hours (default 24).

    Use it for "were there microcuts last night?", "is the CPE link
    flapping?", and for explaining call/game stutters that leave no trace in
    the target data (one point per probe cycle, 300 s by default). Report
    the floor as the floor ("the gateway sat at p90 18%"), confirmed cuts
    with their duration, and possible cuts as possible; never a top-5 as if it were five events.
    """
    hours, err = _validate_hours(hours)
    if err:
        return {"error": err}

    threshold = microcuts.loss_pct()
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
    cut_windows_flux = microcuts.cut_windows_flux(f"-{hours}h", threshold)

    stats: dict[tuple, dict] = {}

    def _merge(rows: list[dict], key: str, cast=float) -> None:
        for row in rows:
            value = row.get("_value")
            if value is None:
                continue
            k = (row.get("target"), row.get("protocol"))
            entry = stats.setdefault(k, {"target": k[0], "protocol": k[1]})
            entry[key] = cast(value)

    def _pct(v: Any) -> float:
        return round(float(v), 2)

    try:
        _merge(query_influx(windows_flux), "windows", int)
        _merge(query_influx(p50_flux), "p50_loss_pct", _pct)
        _merge(query_influx(p90_flux), "p90_loss_pct", _pct)
        _merge(query_influx(max_loss_flux), "max_loss_pct", _pct)
        _merge(query_influx(median_jitter_flux), "median_jitter_ms",
               lambda v: round(float(v), 3))
        cut_rows = query_influx(cut_windows_flux)
    except Exception as exc:
        return _tool_error("InfluxDB query failed", exc)

    cuts = microcuts.fold_cuts(cut_rows)
    for entry in stats.values():
        entry.setdefault("windows", 0)
        own = [c for c in cuts
               if c["target"] == entry["target"] and c["protocol"] == entry["protocol"]]
        entry["cut_windows"] = sum(c["windows"] for c in own)
        entry["confirmed_cuts"] = sum(1 for c in own if c["confirmed"])
        entry["possible_cuts"] = sum(1 for c in own if not c["confirmed"])
        entry_links = links.target_links(
            entry.get("target"), measurement="cpe_latency", hours=hours
        )
        if entry_links:
            entry["links"] = entry_links

    for cut in cuts:
        # Each cut gets a link zoomed to its own moment -- the whole value of
        # a microcut report is being able to look at the one that mattered.
        cut_links = links.target_links(
            cut["target"], measurement="cpe_latency",
            at=datetime.fromtimestamp(cut.pop("start_epoch"), tz=timezone.utc),
        )
        if cut_links.get("graph"):
            cut["graph"] = cut_links["graph"]
    cuts.reverse()

    worst_windows = []
    for row in sorted(cut_rows, key=lambda r: float(r.get("_value") or 0.0),
                      reverse=True)[:5]:
        window = {
            "time": _iso(row.get("_time")),
            "target": row.get("target"),
            "protocol": row.get("protocol"),
            "loss_pct": round(float(row.get("_value", 0.0)), 2),
        }
        window_links = links.target_links(
            window["target"], measurement="cpe_latency", at=row.get("_time")
        )
        if window_links.get("graph"):
            window["graph"] = window_links["graph"]
        worst_windows.append(window)

    result = {
        "window_hours": hours,
        "cut_loss_pct": threshold,
        "cuts": cuts,
        "truncated": len(cut_rows) >= microcuts.MAX_ROWS,
        "stats": sorted(
            stats.values(),
            key=lambda e: (e.get("target") or "", e.get("protocol") or ""),
        ),
        "worst_windows": worst_windows,
    }
    if not cuts and stats:
        floor = ", ".join(
            f"{e['target']}/{e['protocol']} p50 {e.get('p50_loss_pct', 0):g}% / "
            f"p90 {e.get('p90_loss_pct', 0):g}%"
            for e in result["stats"]
        )
        result["note"] = (
            f"No window exceeded {threshold:g}% loss in the last {hours}h: no "
            f"microcuts. The gateway's ICMP floor sat at {floor}; that is "
            "rate limiting, not a fault."
        )
    return result


def _wifi_last_flux(interface: str | None, minutes: int = 10) -> str:
    """The latest value of every wifi_link field, whichever series holds it.

    ``group(columns: ["_field"]) |> last()`` on purpose: a plain ``last()``
    answers per series, and after a roam the stale BSSID's series is exactly
    as "last" as the live one.
    """
    where = f" and r.interface == {flux_str(interface)}" if interface else ""
    return (
        f"from(bucket: {flux_str(influx_bucket())}) "
        f"|> range(start: -{int(minutes)}m) "
        f'|> filter(fn: (r) => r._measurement == "wifi_link"{where}) '
        '|> group(columns: ["_field"]) |> last()'
    )


def _wifi_uplink_flux(hours: int) -> str:
    """The last uplink flag per wireless interface within the window."""
    return (
        _base_flux(["wifi_link"], hours)
        + '|> filter(fn: (r) => r._field == "uplink") '
        '|> group(columns: ["interface"]) |> last()'
    )


def _wifi_uplink_interface(hours: int = 24) -> str | None:
    """Which wireless interface to talk about when the caller named none:
    the one carrying the default route, else the first seen. Pooling two
    radios into one answer would blend their signals and counters -- the
    verdict picks the uplink the same way (verdict._wifi_state)."""
    rows = query_influx(_wifi_uplink_flux(hours))
    by_iface = {r.get("interface"): r.get("_value") for r in rows if r.get("interface")}
    if not by_iface:
        return None
    uplink = sorted(i for i, v in by_iface.items() if int(v or 0) == 1)
    return uplink[0] if uplink else sorted(by_iface)[0]


def _wifi_now(interface: str | None = None) -> dict | None:
    """The link as it is right now, or None when there is no recent data or
    anything at all goes wrong (logged under the exception type, never
    surfaced -- system_status must not depend on one optional measurement).
    """
    try:
        interface = interface or _wifi_uplink_interface(hours=1)
        if interface is None:
            return None
        rows = query_influx(_wifi_last_flux(interface))
        if not rows:
            return None
        fields = {r.get("_field"): r.get("_value") for r in rows if r.get("_field")}
        latest = max(rows, key=lambda r: r.get("_time") or 0)
        now: dict[str, Any] = {
            "interface": interface,
            "sampled_at": _iso(latest.get("_time")),
            "associated": bool(fields.get("associated")),
            "uplink_is_wifi": bool(fields.get("uplink")),
        }
        if now["associated"]:
            # Tags ride on the row that carried the field; signal_dbm exists
            # only while associated, so its row names the live AP.
            sig = next((r for r in rows if r.get("_field") == "signal_dbm"), latest)
            now["ssid"] = sig.get("ssid")
            now["bssid"] = sig.get("bssid")
        for src, dst, cast in (
            ("signal_dbm", "signal_dbm", float), ("tx_bitrate_mbps", "tx_bitrate_mbps", float),
            ("rx_bitrate_mbps", "rx_bitrate_mbps", float), ("channel", "channel", int),
            ("band_ghz", "band_ghz", float), ("width_mhz", "width_mhz", int),
            ("connected_seconds", "connected_seconds", int), ("noise_dbm", "noise_dbm", float),
            ("snr_db", "snr_db", float),
        ):
            if fields.get(src) is not None:
                now[dst] = cast(fields[src])
        return now
    except Exception as exc:
        log.warning("wifi_link status lookup failed: %s", type(exc).__name__)
        return None


@mcp.tool()
@logged_tool
def get_wifi_stats(hours: int = 24, interface: str | None = None) -> dict:
    """Summarize the Pi's own Wi-Fi uplink: how the wireless hop every other
    measurement crosses has been behaving.

    Reads the `wifi_link` measurement (the host's wireless interface, sampled
    every 10 seconds) and returns:
      - now: the current association (ssid, bssid, band/channel/width, signal
        in dBm, negotiated tx/rx bitrate in Mbit/s, time associated) and
        whether this interface carries the default route (uplink_is_wifi)
      - window: over the lookback -- signal min/p10/median/max, the share of
        samples below the weak threshold, disconnects, roams (distinct access
        points), transmit failures, and peak throughput
      - worst_windows: the 5 weakest samples, each with a graph link zoomed
        to that moment
    Empty (`present: false`) on a host that is wired.

    Args:
        hours: Lookback window in hours (default 24).
        interface: Wireless interface name (default: whichever the collector
            chose -- the one carrying the default route).

    Reading the numbers: above -60 dBm is excellent, to -67 comfortable, to
    -75 marginal, below that weak enough to expect retries and rate drops.
    The bitrate is the negotiated PHY rate, not throughput. A steady failure
    rate with a good signal is interference or a busy channel. Use this when
    a microcut or a latency spike might be the Wi-Fi rather than the ISP, or
    when the user asks about their Wi-Fi at all.
    """
    hours, err = _validate_hours(hours)
    if err:
        return {"error": err}
    if interface is not None:
        err = _validate_interface(interface)
        if err:
            return {"error": err}
    weak_dbm = _wifi_weak_dbm()

    try:
        interface = interface or _wifi_uplink_interface(hours)
    except Exception as exc:
        return _tool_error("InfluxDB query failed", exc)
    result: dict[str, Any] = {"window_hours": hours, "weak_below_dbm": weak_dbm}
    if interface is None:
        result["present"] = False
        result["note"] = ("No wifi_link data: this host measures through a wired "
                          "interface, or the collector is not running.")
        return result

    base = (_base_flux(["wifi_link"], hours)
            + f"|> filter(fn: (r) => r.interface == {flux_str(interface)}) ")
    signal = '|> filter(fn: (r) => r._field == "signal_dbm") |> group() '
    summary_flux = (
        base + signal
        + "|> reduce(identity: {n: 0, weak: 0, min: 0.0, max: -999.0}, "
        "fn: (r, accumulator) => ({"
        "n: accumulator.n + 1, "
        f"weak: accumulator.weak + (if r._value < {weak_dbm:.1f} then 1 else 0), "
        "min: if accumulator.n == 0 or r._value < accumulator.min then r._value else accumulator.min, "
        "max: if accumulator.n == 0 or r._value > accumulator.max then r._value else accumulator.max}))"
    )
    median_flux = base + signal + "|> median()"
    p10_flux = base + signal + "|> quantile(q: 0.1)"
    counters_flux = (
        base
        + '|> filter(fn: (r) => r._field == "carrier_down_count" or r._field == "tx_failed" '
        'or r._field == "tx_retries" or r._field == "beacon_loss") '
        '|> group(columns: ["_field"]) |> sort(columns: ["_time"]) |> increase() |> last()'
    )
    roams_flux = (
        base + '|> filter(fn: (r) => r._field == "associated" and r._value == 1) '
        '|> group() |> distinct(column: "bssid") |> count()'
    )
    throughput_flux = (
        base + '|> filter(fn: (r) => r._field == "rx_bytes" or r._field == "tx_bytes") '
        '|> group(columns: ["_field"]) |> sort(columns: ["_time"]) '
        "|> derivative(unit: 1s, nonNegative: true) |> max()"
    )
    worst_flux = base + signal + '|> sort(columns: ["_value"]) |> limit(n: 5)'

    try:
        now = _wifi_now(interface)
        summary_rows = query_influx(summary_flux)
        median_rows = query_influx(median_flux)
        p10_rows = query_influx(p10_flux)
        counter_rows = query_influx(counters_flux)
        roam_rows = query_influx(roams_flux)
        throughput_rows = query_influx(throughput_flux)
        worst_rows = query_influx(worst_flux)
    except Exception as exc:
        return _tool_error("InfluxDB query failed", exc)

    if now is None and not summary_rows:
        result["present"] = False
        result["note"] = (f"No wifi_link data for {interface} in the last {hours}h: "
                          "the collector is not running, or the interface is gone.")
        return result
    result["present"] = True
    result["interface"] = interface
    result["uplink_is_wifi"] = bool((now or {}).get("uplink_is_wifi"))
    if now:
        result["now"] = {k: v for k, v in now.items() if k not in ("interface", "uplink_is_wifi")}

    window: dict[str, Any] = {}
    if summary_rows:
        row = summary_rows[0]
        n = int(row.get("n") or 0)
        window["samples"] = n
        window["signal_dbm"] = {
            "min": float(row.get("min")) if n else None,
            "max": float(row.get("max")) if n else None,
        }
        window["weak_share_pct"] = round(100.0 * int(row.get("weak") or 0) / n, 1) if n else 0.0
        if median_rows and median_rows[0].get("_value") is not None:
            window["signal_dbm"]["median"] = round(float(median_rows[0]["_value"]), 1)
        if p10_rows and p10_rows[0].get("_value") is not None:
            window["signal_dbm"]["p10"] = round(float(p10_rows[0]["_value"]), 1)
    counters = {r.get("_field"): r.get("_value") for r in counter_rows if r.get("_value") is not None}
    window["disconnects"] = int(counters.get("carrier_down_count") or 0)
    for name in ("tx_failed", "tx_retries", "beacon_loss"):
        if name in counters:
            window[name] = int(counters[name])
    if roam_rows and roam_rows[0].get("_value") is not None:
        window["roams"] = max(0, int(roam_rows[0]["_value"]) - 1)
    peaks = {r.get("_field"): r.get("_value") for r in throughput_rows if r.get("_value") is not None}
    if peaks:
        window["throughput_mbps"] = {
            f"max_{k.split('_')[0]}": round(float(v) * 8 / 1e6, 2) for k, v in peaks.items()
        }
    result["window"] = window

    worst = []
    for row in worst_rows:
        w = {"time": _iso(row.get("_time")), "signal_dbm": float(row.get("_value", 0.0)),
             "bssid": row.get("bssid")}
        w_links = links.wifi_links(row.get("interface") or result["interface"], at=row.get("_time"))
        if w_links.get("graph"):
            w["graph"] = w_links["graph"]
        worst.append(w)
    result["worst_windows"] = worst

    link_set = links.wifi_links(result["interface"], hours=hours)
    if link_set:
        result["links"] = link_set
    return result


# ---------------------------------------------------------------------------
# Alert mute control
#
# Buttons in a Telegram message cannot call back without an OpenClaw channel
# plugin, so control is natural language onto these tools -- which is the
# better interface anyway, because "mute amazon for two hours because I'm
# rebooting the router" carries an argument, a duration and a reason that no
# button could.
#
# This server is the ONLY writer of the mutes file; the alerter mounts it
# read-only and only reads. See common/mutes.py for why that removes the race
# rather than managing it.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# On-request chart
# ---------------------------------------------------------------------------

# A year of 300 s samples aggregates to ~120 points either way, but the
# spread query pivots every ping field per window and a very long window
# stops being a picture of anything -- the dashboard is the place for that.
MAX_CHART_HOURS = 24 * 30
_IP_RE = re.compile(r"[0-9a-fA-F.:]{2,45}")


def _cpe_target_exists(name: str, hours: int) -> bool:
    """The CPE gateway is discovered, not configured: it lives only in the
    `cpe_latency` measurement under its IP, so the DB catalog never knows
    it. One cheap probe decides whether a name the DB rejects is that."""
    flux = (
        _base_flux(["cpe_latency"], hours)
        + f"|> filter(fn: (r) => r.target == {flux_str(name)}) "
        + '|> filter(fn: (r) => r._field == "median") '
        + "|> limit(n: 1)"
    )
    return bool(query_influx(flux))


def _chart_peers(catalog: list[dict], target: dict) -> list[str]:
    """Active siblings sharing the category *and* the measurement. A DNS
    probe's latency is a different quantity from an ICMP one; drawing them
    on one axis would compare the incomparable."""
    return sorted(
        t["name"] for t in catalog
        if t.get("name") and t["name"] != target["name"]
        and t.get("is_active")
        and t.get("category") == target.get("category")
        and links.measurement_for_probe(t.get("probe"))
        == links.measurement_for_probe(target.get("probe"))
    )


@mcp.tool()
@logged_tool
def get_chart(
    target: str,
    hours: int = 24,
    with_peers: bool = False,
    deliver: bool = False,
):
    """Draw one target's latency and packet loss over a window, as a PNG.

    Use this when the user wants to SEE the connection rather than read a
    number -- and especially when they want something to send to someone
    who has no login here (a friend, a housemate, the ISP). The picture is
    self-contained: median latency with the spread of the individual pings
    shaded around it, loss underneath on a fixed 0-100 axis, major and minor
    gridlines, local-time axis, and a footer naming the source and when it
    was drawn.

    This is on request only. No other tool attaches images, so do not call
    it as part of answering an ordinary "how is my internet?" -- call it
    when a picture was asked for or is clearly what would help.

    Args:
        target: Exact target name (see list_targets), or the CPE gateway's
            IP as shown by get_microcut_stats.
        hours: Window to draw, 1-720 (default 24). Alerts use 6; a day
            shows the daily rhythm; a week shows whether a problem is new.
        with_peers: Also draw the target's same-category peers as faint
            lines, so "is it this host or everything?" is visible.
        deliver: Also post the PNG into the OpenClaw chat as a file, so the
            user can forward it from there. Requires the gateway to be
            configured on this server (OPENCLAW_GATEWAY_TOKEN and
            OPENCLAW_TO); the result says if it was not delivered and why.

    Returns the image plus a JSON block with what was drawn, the Grafana
    links for the same view, and the delivery outcome. Returns an error
    object instead when the target is unknown or has no data in the window.
    """
    # The CPE gateway is addressed by IP, which the section-name rule rejects;
    # flux_str() still refuses anything that could break out of a literal.
    looks_like_ip = isinstance(target, str) and bool(_IP_RE.fullmatch(target))
    err = None if looks_like_ip else _validate_name(target)
    if err:
        return {"error": err}
    hours, err = _validate_hours(hours)
    if err:
        return {"error": err}
    if hours > MAX_CHART_HOURS:
        return {"error": f"hours must be at most {MAX_CHART_HOURS} for a chart "
                         f"(got {hours}); use the Grafana links for longer views."}

    peers: list[str] = []
    try:
        api = backends.get_config_api()
        catalog = _fetch_targets(api)
        row = next((t for t in catalog if t.get("name") == target), None)
    except ConfigAPIError as exc:
        return {"error": str(exc)}

    if row is not None:
        measurement = links.measurement_for_probe(row.get("probe"))
        if with_peers:
            peers = _chart_peers(catalog, row)
    else:
        try:
            is_cpe = _cpe_target_exists(target, hours)
        except Exception as exc:  # the Influx client raises many types
            return _tool_error("InfluxDB query failed", exc)
        if not is_cpe:
            return {
                "error": f"No monitoring target named '{target}' was found.",
                "available_targets": sorted(
                    t.get("name") for t in catalog if t.get("name")
                ),
            }
        measurement = "cpe_latency"

    png = charts.render_target_chart(
        target, measurement=measurement, hours=hours, peers=peers,
    )
    if png is None:
        return {
            "error": (f"No chart for '{target}': no data points in the last "
                      f"{hours}h, or the render failed (see the server log)."),
            "target": target,
            "measurement": measurement,
        }

    summary: dict[str, Any] = {
        "target": target,
        "measurement": measurement,
        "hours": hours,
        "peers_drawn": peers[:charts.MAX_PEERS],
        "png_bytes": len(png),
        "note": ("The image is a static PNG: it can be forwarded to anyone "
                 "and needs no login to view."),
    }
    the_links = _links_for(_target_catalog(), target, measurement, hours=hours)
    if the_links:
        summary["links"] = the_links

    if deliver:
        caption = f"{target} — last {hours}h · median latency and loss"
        problem = openclaw.send(
            caption, png, charts.chart_filename(target, hours), silent=True,
        )
        summary["delivered"] = problem is None
        if problem:
            summary["delivery_error"] = problem

    return [summary, Image(data=png, format="png")]


def _read_alerter_state() -> dict:
    """Read the alerter's state file. Read-only: this container never writes it.

    Returns an empty shape rather than raising when the alerter has not run
    yet, so "what's muted?" still answers on a fresh install.
    """
    path = os.environ.get("ALERT_STATE_FILE") or "/var/lib/alerter/state.json"
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"incidents": {}}
    if not isinstance(data, dict):
        return {"incidents": {}}
    data.setdefault("incidents", {})
    return data


@mcp.tool()
@logged_tool
def mute_alerts(target: str | None = None, rule: str | None = None,
                hours: float = 2, reason: str = "") -> dict:
    """Stop alert notifications for a target and/or rule for a while.

    Use when the user already knows about something and does not want to be
    told again — "I'm rebooting the router, mute for an hour", "amazon is
    always lossy, quiet it until tonight".

    At least one of target or rule is required. Pass target="*" to mute
    everything, which must be typed deliberately. Duration is capped at 24
    hours; a longer request is clamped and the response says so. Muting
    suppresses only the *sending* — incidents are still tracked, still counted,
    and still appear in the daily digest and in list_alert_state.
    """
    if not target and not rule:
        return {
            "error": "Specify at least one of target or rule. To mute every "
                     "alert, pass target='*' explicitly.",
        }

    try:
        requested = float(hours)
    except (TypeError, ValueError):
        return {"error": f"Invalid hours value {hours!r}: must be a number."}
    if requested <= 0:
        return {"error": f"hours must be positive (got {requested})."}
    granted = min(requested, mutes.MAX_HOURS)

    # Resolve a named target so a typo is rejected loudly rather than creating
    # a mute that silently matches nothing and leaves the user believing they
    # are covered.
    if target and target != mutes.WILDCARD:
        api = backends.get_config_api()
        try:
            _resolved, err = _resolve_target(api, target)
        except ConfigAPIError as exc:
            return {"error": str(exc)}
        if err:
            return err

    now = time.time()
    entry = {
        "target": target,
        "rule": rule,
        "reason": reason,
        "until": now + granted * 3600,
        "created_at": now,
    }
    entries = [
        e for e in mutes.load()
        if not (e.get("target") == target and e.get("rule") == rule
                and e.get("key") is None)
    ]
    entries.append(entry)
    try:
        mutes.save(entries, now=now)
    except OSError as exc:
        return _tool_error("Could not write the mutes file", exc)

    result = {
        "success": True,
        "muted": mutes.describe(entry, now),
        "message": f"Muted for {granted:g}h.",
    }
    if granted < requested:
        result["clamped"] = (
            f"Requested {requested:g}h; capped at {mutes.MAX_HOURS}h. An "
            f"open-ended mute is how a real outage gets missed overnight — "
            f"re-mute if you still need it."
        )
    return result


@mcp.tool()
@logged_tool
def unmute_alerts(target: str | None = None, rule: str | None = None,
                  all: bool = False) -> dict:
    """Lift a mute early, restoring alert notifications.

    Pass the same target/rule that was muted, or all=True to clear every mute.
    A still-active incident re-alerts on its normal cooldown; nothing is
    replayed, so unmuting never produces a burst of catch-up messages.
    """
    entries = mutes.load()
    now = time.time()
    before = len(mutes.active(entries, now))

    if all:
        remaining: list[dict] = []
    elif not target and not rule:
        return {
            "error": "Specify target and/or rule, or pass all=True to clear "
                     "every mute.",
        }
    else:
        remaining = [
            e for e in entries
            if not (
                (target is None or e.get("target") == target)
                and (rule is None or e.get("rule") == rule)
            )
        ]

    try:
        mutes.save(remaining, now=now)
    except OSError as exc:
        return _tool_error("Could not write the mutes file", exc)

    after = len(mutes.active(remaining, now))
    return {
        "success": True,
        "removed": before - after,
        "still_muted": [mutes.describe(e, now) for e in mutes.active(remaining, now)],
    }


@mcp.tool()
@logged_tool
def ack_incident(key: str, hours: float = 24) -> dict:
    """Acknowledge one specific incident: stop re-notifying until it recovers.

    Narrower than mute_alerts — this silences exactly one active incident and
    nothing else, so a new problem on the same target still alerts. Get the key
    from list_alert_state. The recovery notice is NOT suppressed: you will
    still be told when it clears.
    """
    state = _read_alerter_state()
    records = state.get("incidents", {})
    if key not in records:
        return {
            "error": f"No active incident with key '{key}'.",
            "active_keys": sorted(records),
        }

    now = time.time()
    granted = min(max(float(hours), 0.1), mutes.MAX_HOURS)
    entry = {
        "key": key,
        "clear_on_recovery": True,
        "reason": "acknowledged",
        "until": now + granted * 3600,
        "created_at": now,
    }
    entries = [e for e in mutes.load() if e.get("key") != key]
    entries.append(entry)
    try:
        mutes.save(entries, now=now)
    except OSError as exc:
        return _tool_error("Could not write the mutes file", exc)

    return {
        "success": True,
        "acknowledged": mutes.describe(entry, now),
        "message": f"{key} acknowledged; no further alerts for {granted:g}h "
                   f"or until it recovers.",
    }


@mcp.tool()
@logged_tool
def list_alert_state() -> dict:
    """Show active incidents and active mutes — what is wrong and what is quiet.

    Answers "is anything muted?" and "why haven't I heard about X?". Reads both
    files read-only. `muted_suppressed_count` on an incident is how many alerts
    a mute has actually swallowed, which is the number that matters when
    deciding whether a mute is still a good idea.
    """
    now = time.time()
    state = _read_alerter_state()
    entries = mutes.load()
    live = mutes.active(entries, now)

    incidents = []
    for key, record in sorted(state.get("incidents", {}).items()):
        incident_view = {
            "key": key,
            "rule": record.get("rule"),
            "severity": record.get("severity"),
            "target": record.get("target"),
            "message": record.get("message"),
            "first_seen": record.get("first_seen"),
            "notified_count": record.get("notified_count", 0),
            "muted_suppressed_count": record.get("muted_suppressed_count", 0),
        }
        # Recompute rather than trusting the alerter's cached muted_until: the
        # mute may have been lifted since it last ran.
        covering = mutes.find(live, {"key": key,
                                     "target": record.get("target"),
                                     "rule": record.get("rule")}, now)
        incident_view["muted"] = covering is not None
        incidents.append(incident_view)

    return {
        "incidents": incidents,
        "active_mutes": [mutes.describe(e, now) for e in live],
        "mutes_file_readable": True,
        "note": (
            "No active incidents does not mean no data — it means every rule "
            "is currently satisfied."
            if not incidents else None
        ),
    }
