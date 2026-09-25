"""AdGuard Home: the configuration we own, and the query log we read.

AdGuard Home rewrites its own YAML (the web UI saves there), so the file is
merged, not replaced: on every start the keys below are set from the
environment, and everything else is left as the file has it. Keys that are
the *user's* once seeded (filters, protection) are written only when the
file is new -- someone who turns blocking on in the UI keeps it.
"""

from __future__ import annotations

import os
from typing import Any

import bcrypt
import httpx
import yaml

from config import Config

# The config schema AdGuard Home v0.107.79 writes. Without it AdGuard runs
# every migration from schema 0 over our file and fails to parse the result
# (tried: "cannot construct !!seq into string"). Bump with the image pin.
SCHEMA_VERSION = 34


def _password_hash(cfg: Config, current: list[dict]) -> str:
    """Reuse the stored hash when it still matches, so the file is stable."""
    for user in current or []:
        if user.get("name") == cfg.admin_user:
            stored = str(user.get("password", ""))
            try:
                if stored and bcrypt.checkpw(cfg.admin_password.encode(), stored.encode()):
                    return stored
            except ValueError:
                pass
    return bcrypt.hashpw(cfg.admin_password.encode(), bcrypt.gensalt()).decode()


def render(cfg: Config, existing: dict[str, Any] | None) -> dict[str, Any]:
    """The YAML AdGuard Home should start with."""
    new = not existing
    doc: dict[str, Any] = dict(existing or {})
    doc["schema_version"] = max(int(doc.get("schema_version", 0) or 0), SCHEMA_VERSION)

    http = dict(doc.get("http") or {})
    # The admin UI and API listen on loopback only: the supervisor reads the
    # query log there, and the names in it never face the LAN by default.
    http["address"] = cfg.admin_address
    doc["http"] = http
    doc["users"] = [
        {"name": cfg.admin_user, "password": _password_hash(cfg, doc.get("users", []))}
    ]

    dns = dict(doc.get("dns") or {})
    dns.update(
        {
            "bind_hosts": cfg.bind_hosts,
            "port": cfg.port,
            "upstream_dns": cfg.upstreams,
            "fallback_dns": cfg.fallback,
            "bootstrap_dns": cfg.bootstrap,
            # Every query arrives from the router's one address; AdGuard's
            # default of 20 queries/s per /24 would throttle the whole house.
            "ratelimit": 0,
            # The supervisor's self-test is an ANY query, which this makes
            # AdGuard answer locally (NOTIMP) without asking an upstream.
            "refuse_any": True,
            "allowed_clients": cfg.allow_clients,
            "anonymize_client_ip": cfg.anonymize_clients,
            # Fails over to the next upstream (then fallback_dns) in time for
            # the client's own retry; AdGuard's default is 10 s.
            "upstream_timeout": cfg.upstream_timeout,
            # Serve-stale: answer from an expired cache entry while the
            # upstreams are unreachable, instead of SERVFAIL.
            "cache_enabled": True,
            "cache_optimistic": True,
            # Reverse lookups of private addresses would go to the system
            # resolver -- the router -- which forwards them back here.
            "use_private_ptr_resolvers": False,
            "local_ptr_upstreams": [],
        }
    )
    doc["dns"] = dns

    for section in ("querylog", "statistics"):
        block = dict(doc.get(section) or {})
        block.update({"enabled": True, "interval": f"{cfg.retention_hours}h"})
        doc[section] = block

    clients = dict(doc.get("clients") or {})
    runtime = dict(clients.get("runtime_sources") or {})
    # Client names come from rDNS/ARP/whois; rDNS asks the router (loop) and
    # whois sends LAN client addresses nowhere useful. hosts is local.
    runtime.update({"rdns": False, "whois": False, "arp": True, "dhcp": False, "hosts": True})
    clients["runtime_sources"] = runtime
    doc["clients"] = clients

    if new:
        # The observer observes; it does not block. A user may turn
        # filtering on in the UI later and it is not undone on restart.
        doc["filters"] = []
        doc["whitelist_filters"] = []
        doc["user_rules"] = []
        filtering = dict(doc.get("filtering") or {})
        filtering.update({"protection_enabled": False, "filtering_enabled": False})
        doc["filtering"] = filtering
        doc["dhcp"] = {"enabled": False}
    return doc


def write_config(cfg: Config) -> bool:
    """Merge our keys into AdGuard's file; True when the file was new."""
    existing: dict[str, Any] | None = None
    try:
        with open(cfg.adguard_conf) as fh:
            existing = yaml.safe_load(fh) or None
    except FileNotFoundError:
        pass
    doc = render(cfg, existing)
    os.makedirs(os.path.dirname(cfg.adguard_conf), exist_ok=True)
    tmp = f"{cfg.adguard_conf}.tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False)
    os.replace(tmp, cfg.adguard_conf)
    return existing is None


class AdGuardAPI:
    """The two reads the supervisor needs, from AdGuard's loopback API."""

    def __init__(self, cfg: Config, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=cfg.admin_url,
            auth=(cfg.admin_user, cfg.admin_password),
            timeout=5.0,
        )

    async def querylog(self, *, search: str | None = None, limit: int = 100) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if search:
            params["search"] = search
        resp = await self._client.get("/control/querylog", params=params)
        resp.raise_for_status()
        return resp.json().get("data") or []

    async def stats(self) -> dict:
        resp = await self._client.get("/control/stats")
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        await self._client.aclose()
