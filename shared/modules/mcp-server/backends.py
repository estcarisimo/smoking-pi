"""Backend clients for the SmokePing MCP server.

Two backends are wrapped here:

1. The config-manager REST API (Flask, port 5000) -- target CRUD, config
   generation, SmokePing restart, service status.
2. InfluxDB 2.x -- time-series measurements written by the RRD/CPE exporters
   (``latency``, ``dns_latency``, ``cpe_latency``).

All clients are constructed lazily so that importing this module (or
``server``) never requires environment variables or network access.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_CONFIG_API_URL = "http://config-manager:5000"
DEFAULT_INFLUX_URL = "http://influxdb:8086"
DEFAULT_INFLUX_ORG = "smokeping"
DEFAULT_INFLUX_BUCKET = "smokeping"


class ConfigAPIError(RuntimeError):
    """Raised when the config-manager API returns an error response."""


class ConfigAPI:
    """Thin client for the config-manager REST API.

    Reads ``CONFIG_API_URL`` (default ``http://config-manager:5000``) and, if
    set, sends ``CONFIG_API_TOKEN`` as a Bearer token on every request.
    """

    def __init__(self, base_url: str | None = None, token: str | None = None):
        self.base_url = (
            base_url or os.environ.get("CONFIG_API_URL", DEFAULT_CONFIG_API_URL)
        ).rstrip("/")
        self.token = token if token is not None else os.environ.get("CONFIG_API_TOKEN")
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            headers = {}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            self._client = httpx.Client(
                base_url=self.base_url, headers=headers, timeout=60.0
            )
        return self._client

    def request(self, method: str, path: str, **kwargs: Any) -> dict:
        """Perform a request and return the parsed JSON body.

        Raises ConfigAPIError on HTTP errors or connection failures.
        """
        try:
            resp = self.client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ConfigAPIError(
                f"config-manager API unreachable at {self.base_url}: {exc}"
            ) from exc
        try:
            data = resp.json()
        except ValueError:
            data = {"raw": resp.text}
        if resp.status_code >= 400:
            message = data.get("error") if isinstance(data, dict) else None
            raise ConfigAPIError(
                f"{method} {path} failed with HTTP {resp.status_code}: "
                f"{message or resp.text[:200]}"
            )
        return data


_config_api: ConfigAPI | None = None


def get_config_api() -> ConfigAPI:
    """Return the process-wide ConfigAPI client (created on first use)."""
    global _config_api
    if _config_api is None:
        _config_api = ConfigAPI()
    return _config_api


# ---------------------------------------------------------------------------
# InfluxDB
# ---------------------------------------------------------------------------

_influx_client = None


def influx_bucket() -> str:
    return os.environ.get("INFLUX_BUCKET", DEFAULT_INFLUX_BUCKET)


def _get_query_api():
    """Lazily construct the InfluxDB query API (env is read on first use)."""
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


def query_influx(flux: str) -> list[dict]:
    """Run a Flux query and return a flat list of record dicts.

    Each dict contains the record's columns (``_time``, ``_value``, tags such
    as ``target`` / ``protocol``, etc.).
    """
    tables = _get_query_api().query(flux)
    rows: list[dict] = []
    for table in tables:
        for record in table.records:
            rows.append(dict(record.values))
    return rows


def flux_str(value: str) -> str:
    """Return ``value`` as a quoted Flux string literal, or raise.

    To prevent Flux injection, values containing double quotes, backslashes,
    or control characters are rejected outright (never escaped).
    """
    if not isinstance(value, str):
        raise ValueError("expected a string")
    if '"' in value or "\\" in value or any(ord(c) < 32 for c in value):
        raise ValueError(
            f"invalid characters in value {value!r}: "
            'double quotes, backslashes, and control characters are not allowed'
        )
    return f'"{value}"'
