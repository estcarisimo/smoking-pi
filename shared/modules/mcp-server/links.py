"""Deep links from MCP tool responses back into Grafana and web-admin.

An agent that answers "the link to 8.8.8.8 got worse around 3am" is more
useful if it can also hand over the graph. Grafana accepts
``/d/<uid>?var-target=<name>&from=<t>&to=<t>``, and the dashboard UIDs are
stable and pinned, so the URL can be built without asking Grafana anything.

The one thing that cannot be guessed is the host. This Pi has no canonical
hostname -- it is reachable on a LAN IP, a Tailscale name, and a Cloudflare
tunnel, and which one works depends entirely on where the person reading the
answer is standing. So the base URL is configuration, and when it is absent
this module emits **no links at all** rather than a plausible-looking
``http://localhost:3000`` that fails silently on someone's phone.

Configuration (see ``docs/mcp-server.md``):

- ``PUBLIC_BASE_HOST`` -- host or ``scheme://host`` reachable by whoever reads
  the answers (``192.168.86.27``, ``smokingpi.tailnet.ts.net``). The default
  service ports are appended.
- ``GRAFANA_PUBLIC_URL`` / ``WEB_ADMIN_PUBLIC_URL`` -- full base URLs, for
  reverse proxies and tunnels where the ports are not visible. These win over
  ``PUBLIC_BASE_HOST``.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode

DEFAULT_GRAFANA_PORT = 3000
DEFAULT_WEB_ADMIN_PORT = 8080

# Dashboard UIDs are pinned in the provisioned JSON and are part of this
# contract; a dashboard that changes its UID breaks these links (which is one
# of the things the instrumentation doctor checks).
DASHBOARD_BY_MEASUREMENT = {
    "latency": ("smokeping-lat-pct-v28", "target"),
    "dns_latency": ("smokeping-dns-resolvers-v4", "target"),
    "cpe_latency": ("cpe-microcut-v1", "cpe"),
}

# Per-ping detail, for "show me the actual pings" follow-ups.
DETAIL_BY_MEASUREMENT = {
    "latency": ("individual-pings-v1", "target"),
    "dns_latency": ("dns-resolvers-v1", "target"),
}

# Side-by-side comparison dashboards, keyed by the category vocabulary the
# DATABASE uses. Note this differs from the category tag the exporter writes
# into InfluxDB (top_sites/netflix_oca/dns_resolvers here vs topsites/netflix/
# dns there) -- the two vocabularies drifted apart and both are live.
COMPARE_BY_DB_CATEGORY = {
    "custom": "custom-side-by-side-v1",
    "netflix_oca": "netflix_oca-side-by-side-v1",
    "top_sites": "top_sites-side-by-side-v1",
    "dns_resolvers": "dns-resolvers-v1",
}

# A target's probe decides which measurement it lands in.
MEASUREMENT_BY_PROBE = {
    "FPing": "latency",
    "FPing6": "latency",
    "DNS": "dns_latency",
}


def _normalize_base(value: str | None, default_port: int | None) -> str | None:
    """Turn a configured host or URL into a base URL, or None if unset."""
    if not value:
        return None
    base = value.strip().rstrip("/")
    if not base:
        return None
    if "://" not in base:
        base = f"http://{base}"
        if default_port is not None:
            # Only append the port when the host does not already carry one.
            host_part = base.split("://", 1)[1]
            if ":" not in host_part.split("/", 1)[0]:
                base = f"{base}:{default_port}"
    return base


def grafana_base() -> str | None:
    return _normalize_base(
        os.environ.get("GRAFANA_PUBLIC_URL"), None
    ) or _normalize_base(
        os.environ.get("PUBLIC_BASE_HOST"), DEFAULT_GRAFANA_PORT
    )


def web_admin_base() -> str | None:
    return _normalize_base(
        os.environ.get("WEB_ADMIN_PUBLIC_URL"), None
    ) or _normalize_base(
        os.environ.get("PUBLIC_BASE_HOST"), DEFAULT_WEB_ADMIN_PORT
    )


def links_configured() -> bool:
    return bool(grafana_base() or web_admin_base())


CONFIG_HINT = (
    "Deep links to Grafana panels and the web-admin UI are not configured, so "
    "responses carry numbers only. Set PUBLIC_BASE_HOST (or GRAFANA_PUBLIC_URL "
    "and WEB_ADMIN_PUBLIC_URL) on the mcp-server service to the address this "
    "host is actually reached on -- it cannot be guessed, since the LAN IP, "
    "the Tailscale name and a tunnel hostname all reach it and only one of "
    "them works for a given reader."
)


def _epoch_ms(value: Any) -> int | None:
    """Coerce a datetime or ISO-8601 string to epoch milliseconds."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def _time_range(
    hours: int | None, at: Any = None, pad_minutes: int = 15
) -> dict[str, str]:
    """Grafana from/to params: a window around `at`, else a relative lookback."""
    if at is not None:
        centre = _epoch_ms(at)
        if centre is not None:
            pad = pad_minutes * 60 * 1000
            return {"from": str(centre - pad), "to": str(centre + pad)}
    if hours:
        return {"from": f"now-{int(hours)}h", "to": "now"}
    return {}


def grafana_url(
    uid: str,
    var_name: str | None = None,
    target: str | None = None,
    hours: int | None = None,
    at: Any = None,
) -> str | None:
    base = grafana_base()
    if not base:
        return None
    params: dict[str, str] = {}
    if var_name and target:
        params[f"var-{var_name}"] = target
    params.update(_time_range(hours, at))
    query = urlencode(params) if params else ""
    return f"{base}/d/{quote(uid)}" + (f"?{query}" if query else "")


def web_admin_target_url(name: str | None = None) -> str | None:
    """The target management page, pre-filtered to one target when named."""
    base = web_admin_base()
    if not base:
        return None
    if not name:
        return f"{base}/targets/"
    return f"{base}/targets/?{urlencode({'q': name})}"


def measurement_for_probe(probe: str | None) -> str:
    return MEASUREMENT_BY_PROBE.get(probe or "", "latency")


def target_links(
    name: str | None,
    measurement: str | None = None,
    db_category: str | None = None,
    hours: int | None = None,
    at: Any = None,
) -> dict[str, str]:
    """Links for one target: its graph, the per-ping detail, its peers, its config.

    Returns an empty dict when no base URL is configured, so callers can
    ``if links:`` without special-casing the unconfigured deployment.
    """
    if not name or not links_configured():
        return {}

    out: dict[str, str] = {}
    measurement = measurement or "latency"

    dashboard = DASHBOARD_BY_MEASUREMENT.get(measurement)
    if dashboard:
        uid, var = dashboard
        url = grafana_url(uid, var, name, hours=hours, at=at)
        if url:
            out["graph"] = url

    detail = DETAIL_BY_MEASUREMENT.get(measurement)
    if detail:
        uid, var = detail
        url = grafana_url(uid, var, name, hours=hours, at=at)
        if url:
            out["per_ping_detail"] = url

    compare_uid = COMPARE_BY_DB_CATEGORY.get(db_category or "")
    if compare_uid:
        url = grafana_url(compare_uid, "target", name, hours=hours, at=at)
        if url:
            out["compare_with_peers"] = url

    edit = web_admin_target_url(name)
    if edit:
        out["edit"] = edit
    return out
