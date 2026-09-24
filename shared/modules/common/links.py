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

**Two tiers, because "where the reader is standing" changes mid-conversation.**
The same person asks from the couch and from a train. A LAN address is the
better link at home (no tunnel hop, works when Cloudflare doesn't) and a dead
one on cellular; a tunnel URL works anywhere and is the only one worth pasting
to somebody else. Picking one means being wrong half the time, so every link
can be emitted twice: the configured base, and a ``_tunnel`` twin. Callers that
only understand the original keys keep working unchanged.

Configuration (see ``docs/mcp-server.md``):

- ``PUBLIC_BASE_HOST`` -- host or ``scheme://host`` reachable by whoever reads
  the answers (``192.168.86.27``, ``smokingpi.tailnet.ts.net``). The default
  service ports are appended; an IPv6 literal is bracketed first, and a
  link-local one (``fe80::``) is ignored, since no browser opens it.
- ``GRAFANA_PUBLIC_URL`` / ``WEB_ADMIN_PUBLIC_URL`` -- full base URLs, for
  reverse proxies and tunnels where the ports are not visible. These win over
  ``PUBLIC_BASE_HOST``.
- ``TUNNEL_BASE_HOST``, ``GRAFANA_TUNNEL_URL`` / ``WEB_ADMIN_TUNNEL_URL`` --
  the same three, for the address that works from *outside* the home network
  (``./shared/scripts/create-tunnel.sh`` prints these). Set only these and they
  become the primary links; set both and every link gets a ``_tunnel`` twin.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode

log = logging.getLogger("common.links")

DEFAULT_GRAFANA_PORT = 3000
DEFAULT_WEB_ADMIN_PORT = 8080

# Dashboard UIDs are pinned in the provisioned JSON and are part of this
# contract; a dashboard that changes its UID breaks these links (which is one
# of the things the instrumentation doctor checks).
DASHBOARD_BY_MEASUREMENT = {
    "latency": ("smokeping-lat-pct-v28", "target"),
    "dns_latency": ("smokeping-dns-resolvers-v4", "target"),
    "cpe_latency": ("cpe-microcut-v1", "cpe"),
    # The HTTP dashboard is per site, not per target: its variable is the
    # target name minus the _h1/_h2/_h3 version suffix, so all three versions
    # of that site land on one panel (see _dashboard_value).
    "http_latency": ("http-by-version-v1", "site"),
    # TCP handshakes are the bottom panel of the same dashboard, all targets
    # at once; there is no variable to select one.
    "tcp_latency": ("http-by-version-v1", None),
    # Not a target: the variable is the wireless interface (wlan0), which is
    # why wifi_links() exists instead of routing through target_links().
    "wifi_link": ("wifi-link-v1", "interface"),
}

# Curl targets carry the HTTP version they were probed with as a name suffix
# (Google_h2); the exporters and the dashboard both key on it.
HTTP_VERSION_SUFFIX_RE = re.compile(r"_h[123]$")

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
    "CurlHTTP1": "http_latency",
    "CurlHTTP2": "http_latency",
    "CurlHTTP3": "http_latency",
    "TCPPing": "tcp_latency",
}


# Values already reported as unusable, so a bad setting is logged once per
# process instead of once per link built.
_REPORTED_BASES: set[str] = set()


def _refuse_base(value: str, reason: str) -> None:
    if value not in _REPORTED_BASES:
        _REPORTED_BASES.add(value)
        log.warning("Ignoring base host %r for links: %s", value, reason)


def _split_authority(value: str, authority: str) -> tuple[str, bool] | None:
    """``(host, has_port)`` for a bare authority, or None if it is unusable.

    The host comes back ready for a URL: an IPv6 literal is bracketed. An
    unbracketed IPv6 literal cannot carry a port (``2001:db8::5:3000`` is
    itself an address), so it is taken as an address without one.
    """
    if authority.startswith("["):
        address, closed, after = authority[1:].partition("]")
        if not closed:
            _refuse_base(value, "unbalanced '[' in an IPv6 literal")
            return None
        if after and not (after[0] == ":" and after[1:].isdigit()):
            _refuse_base(value, "expected [address] or [address]:port")
            return None
        has_port = bool(after)
    elif authority.count(":") >= 2:
        address, has_port = authority, False
    else:
        # A hostname or an IPv4 address, with or without :port.
        return authority, ":" in authority
    try:
        ip = ipaddress.IPv6Address(address)
    except ValueError:
        _refuse_base(value, "not an IPv6 address")
        return None
    # A link-local address only routes with a zone id (fe80::1%wlan0), and
    # browsers refuse zone ids in URLs: every such link would be dead.
    if ip.is_link_local or ip.scope_id:
        _refuse_base(value, "link-local IPv6 needs a zone id, which browsers reject")
        return None
    return f"[{address}]{after if has_port else ''}", has_port


def _normalize_base(value: str | None, default_port: int | None) -> str | None:
    """Turn a configured host or URL into a base URL, or None if unset.

    A value with a scheme is taken as complete. A bare host gets ``http://``
    and, unless it already carries a port, ``default_port``; an IPv6 literal
    is bracketed (``2001:db8::5`` -> ``http://[2001:db8::5]:3000``).
    """
    if not value:
        return None
    base = value.strip().rstrip("/")
    if not base:
        return None
    if "://" in base:
        return base
    authority, slash, path = base.partition("/")
    split = _split_authority(base, authority)
    if split is None:
        return None
    host, has_port = split
    if default_port is not None and not has_port:
        host = f"{host}:{default_port}"
    return f"http://{host}{slash}{path}"


def _tier(url_var: str, host_var: str, default_port: int) -> str | None:
    """One configured tier: the explicit service URL, else host + port."""
    return _normalize_base(os.environ.get(url_var), None) or _normalize_base(
        os.environ.get(host_var), default_port
    )


def grafana_tunnel_base() -> str | None:
    """The from-anywhere Grafana base, or None when no tunnel is configured."""
    return _tier("GRAFANA_TUNNEL_URL", "TUNNEL_BASE_HOST", DEFAULT_GRAFANA_PORT)


def web_admin_tunnel_base() -> str | None:
    return _tier("WEB_ADMIN_TUNNEL_URL", "TUNNEL_BASE_HOST", DEFAULT_WEB_ADMIN_PORT)


def grafana_base() -> str | None:
    """The primary Grafana base: the LAN/tailnet address, else the tunnel.

    Falling back to the tunnel matters for the deployment that has *only* a
    tunnel: without it ``links_configured()`` would be false and a perfectly
    reachable Grafana would produce no links at all.
    """
    return (
        _tier("GRAFANA_PUBLIC_URL", "PUBLIC_BASE_HOST", DEFAULT_GRAFANA_PORT)
        or grafana_tunnel_base()
    )


def web_admin_base() -> str | None:
    return (
        _tier("WEB_ADMIN_PUBLIC_URL", "PUBLIC_BASE_HOST", DEFAULT_WEB_ADMIN_PORT)
        or web_admin_tunnel_base()
    )


def has_tunnel_links() -> bool:
    """True when a tunnel is configured *and* differs from the primary base.

    Equal bases mean the tunnel IS the primary link, and emitting the same
    URL twice under two labels reads as two different places to look.
    """
    return (grafana_tunnel_base() or web_admin_tunnel_base()) is not None and (
        grafana_tunnel_base() != grafana_base()
        or web_admin_tunnel_base() != web_admin_base()
    )


def dashboards_match_backend() -> bool:
    """True when the pinned dashboard UIDs belong to the active backend.

    Every UID in the maps above is from the InfluxDB provisioning tree. The
    ClickHouse tree is a parallel set with DIFFERENT uids (and no CPE
    dashboard at all), so under TSDB_TYPE=clickhouse every link built here
    resolves to a Grafana 404. Silently, and looking perfectly valid in the
    transcript -- which is the failure this module already refuses to risk
    by guessing a base URL.
    """
    backend = (os.environ.get("TSDB_TYPE") or "influxdb").strip().lower()
    return backend in ("", "influxdb")


def links_configured() -> bool:
    return bool(grafana_base() or web_admin_base()) and dashboards_match_backend()


CONFIG_HINT = (
    "Deep links to Grafana panels and the web-admin UI are not configured, so "
    "responses carry numbers only. Set PUBLIC_BASE_HOST (or GRAFANA_PUBLIC_URL "
    "and WEB_ADMIN_PUBLIC_URL) on the mcp-server service to the address this "
    "host is actually reached on -- it cannot be guessed, since the LAN IP, "
    "the Tailscale name and a tunnel hostname all reach it and only one of "
    "them works for a given reader. Setting TUNNEL_BASE_HOST as well adds a "
    "second, from-anywhere link beside each of the first."
)

BACKEND_HINT = (
    "Deep links are disabled because TSDB_TYPE is not influxdb. The pinned "
    "dashboard UIDs belong to the InfluxDB provisioning tree; the ClickHouse "
    "tree uses different uids, so every link would 404. No link beats a "
    "broken one."
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
        center = _epoch_ms(at)
        if center is not None:
            pad = pad_minutes * 60 * 1000
            return {"from": str(center - pad), "to": str(center + pad)}
    if hours:
        return {"from": f"now-{int(hours)}h", "to": "now"}
    return {}


def grafana_url(
    uid: str,
    var_name: str | None = None,
    target: str | None = None,
    hours: int | None = None,
    at: Any = None,
    base: str | None = None,
) -> str | None:
    base = base or grafana_base()
    if not base:
        return None
    params: dict[str, str] = {}
    if var_name and target:
        params[f"var-{var_name}"] = target
    params.update(_time_range(hours, at))
    query = urlencode(params) if params else ""
    return f"{base}/d/{quote(uid)}" + (f"?{query}" if query else "")


def web_admin_target_url(
    name: str | None = None, base: str | None = None
) -> str | None:
    """The target management page, pre-filtered to one target when named."""
    base = base or web_admin_base()
    if not base:
        return None
    if not name:
        return f"{base}/targets/"
    return f"{base}/targets/?{urlencode({'q': name})}"


def measurement_for_probe(probe: str | None) -> str:
    return MEASUREMENT_BY_PROBE.get(probe or "", "latency")


def _dashboard_value(measurement: str, name: str) -> str:
    """The dashboard-variable value for a target: the site for HTTP targets
    (Google_h2 -> Google), the target name itself everywhere else."""
    if measurement == "http_latency":
        return HTTP_VERSION_SUFFIX_RE.sub("", name)
    return name


def _target_links_for(
    name: str,
    measurement: str,
    db_category: str | None,
    hours: int | None,
    at: Any,
    grafana: str | None,
    web_admin: str | None,
) -> dict[str, str]:
    """One tier's worth of target links, against explicitly given bases."""
    out: dict[str, str] = {}

    dashboard = DASHBOARD_BY_MEASUREMENT.get(measurement)
    if dashboard and grafana:
        uid, var = dashboard
        url = grafana_url(uid, var, _dashboard_value(measurement, name),
                          hours=hours, at=at, base=grafana)
        if url:
            out["graph"] = url

    detail = DETAIL_BY_MEASUREMENT.get(measurement)
    if detail and grafana:
        uid, var = detail
        url = grafana_url(uid, var, name, hours=hours, at=at, base=grafana)
        if url:
            out["per_ping_detail"] = url

    compare_uid = COMPARE_BY_DB_CATEGORY.get(db_category or "")
    if compare_uid and grafana:
        url = grafana_url(compare_uid, "target", name, hours=hours, at=at, base=grafana)
        if url:
            out["compare_with_peers"] = url

    if web_admin:
        edit = web_admin_target_url(name, base=web_admin)
        if edit:
            out["edit"] = edit
    return out


def _with_tunnel_twins(
    primary: dict[str, str], tunnel: dict[str, str]
) -> dict[str, str]:
    """Merge a tunnel tier into a primary one as ``<key>_tunnel`` entries.

    A twin identical to its primary is dropped rather than emitted: two labels
    on one URL invite a reader to try "the other one" when there isn't one.
    """
    out = dict(primary)
    for key, url in tunnel.items():
        if url and url != primary.get(key):
            out[f"{key}_tunnel"] = url
    return out


def target_links(
    name: str | None,
    measurement: str | None = None,
    db_category: str | None = None,
    hours: int | None = None,
    at: Any = None,
) -> dict[str, str]:
    """Links for one target: its graph, the per-ping detail, its peers, its config.

    Each key gains a ``<key>_tunnel`` twin when a tunnel base is configured
    alongside a different primary one -- the same panel, reachable from
    outside the home network.

    Returns an empty dict when no base URL is configured, so callers can
    ``if links:`` without special-casing the unconfigured deployment.
    """
    if not name or not links_configured():
        return {}

    measurement = measurement or "latency"
    primary = _target_links_for(
        name, measurement, db_category, hours, at, grafana_base(), web_admin_base()
    )
    if not has_tunnel_links():
        return primary
    tunnel = _target_links_for(
        name,
        measurement,
        db_category,
        hours,
        at,
        grafana_tunnel_base(),
        web_admin_tunnel_base(),
    )
    return _with_tunnel_twins(primary, tunnel)


def wifi_links(
    interface: str | None, hours: int | None = None, at: Any = None
) -> dict[str, str]:
    """Links for the Wi-Fi uplink: its dashboard, in both tiers.

    Deliberately not target_links(): that always adds an ``edit`` link into
    the web-admin target search and, given a category, a peers comparison,
    and neither means anything for ``wlan0``. Only ``graph`` (and its tunnel
    twin) is right here.
    """
    if not interface or not links_configured():
        return {}
    uid, var = DASHBOARD_BY_MEASUREMENT["wifi_link"]

    def tier(grafana: str | None) -> dict[str, str]:
        if not grafana:
            return {}
        url = grafana_url(uid, var, interface, hours=hours, at=at, base=grafana)
        return {"graph": url} if url else {}

    primary = tier(grafana_base())
    if not has_tunnel_links():
        return primary
    return _with_tunnel_twins(primary, tier(grafana_tunnel_base()))


def entry_point_links(hours: int = 24) -> dict[str, str]:
    """The front doors: latency overview, CPE microcuts, Wi-Fi link, targets.

    Lives here rather than in the MCP server so both tiers are assembled in
    one place -- a second hand-rolled copy is how the tunnel twin would end up
    on target links but not on the ones an agent reaches for first.
    """
    if not links_configured():
        return {}

    def tier(grafana: str | None, web_admin: str | None) -> dict[str, str]:
        out: dict[str, str] = {}
        if grafana:
            overview = grafana_url("smokeping-lat-pct-v28", hours=hours, base=grafana)
            if overview:
                out["grafana_overview"] = overview
            cpe = grafana_url("cpe-microcut-v1", hours=hours, base=grafana)
            if cpe:
                out["grafana_cpe_microcuts"] = cpe
            wifi = grafana_url("wifi-link-v1", hours=hours, base=grafana)
            if wifi:
                out["grafana_wifi_link"] = wifi
        if web_admin:
            admin = web_admin_target_url(base=web_admin)
            if admin:
                out["web_admin_targets"] = admin
        return out

    primary = tier(grafana_base(), web_admin_base())
    if not has_tunnel_links():
        return primary
    return _with_tunnel_twins(
        primary, tier(grafana_tunnel_base(), web_admin_tunnel_base())
    )
