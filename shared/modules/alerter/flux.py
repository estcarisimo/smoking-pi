"""InfluxDB 2.x query helpers for the alerting engine.

Reads the same measurements the exporters write (see
``shared/modules/ai-insights/collector.py`` for the authoritative notes):

- ``latency`` / ``dns_latency``: fields ``median`` (seconds) and ``loss``
  (ratio 0-1; legacy points may be packet counts 0..20 — clamp!), tagged by
  ``target`` / ``category``. Written roughly every 60s per target.
- ``cpe_latency``: ``median``/``min``/``max``/``jitter`` in milliseconds and
  ``loss`` as a PERCENT 0-100, tagged by ``target`` / ``protocol``. Written
  roughly every 10s per target+protocol.

The client is constructed lazily so importing this module never needs
environment variables or network access.
"""

from __future__ import annotations

import os

# The alerter runs with network_mode: host, so InfluxDB is on localhost.
DEFAULT_INFLUX_URL = "http://localhost:8086"
DEFAULT_INFLUX_ORG = "smokeping"
DEFAULT_INFLUX_BUCKET = "smokeping"

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
    """Run a Flux query and return a flat list of record dicts."""
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
            f"invalid characters in value {value!r}: double quotes, "
            "backslashes, and control characters are not allowed"
        )
    return f'"{value}"'


# Old exporter versions wrote loss as a packet count (0..20); current versions
# write a 0..1 ratio. Clamp so legacy points read as (at most) 100% loss.
# NOTE: apply only to latency/dns_latency loss — cpe_latency loss is a
# percent 0-100 and must NOT be clamped this way.
CLAMP_LOSS_RATIO = (
    "|> map(fn: (r) => ({r with _value: "
    "if r._value > 1.0 then 1.0 "
    "else if r._value < 0.0 then 0.0 "
    "else r._value})) "
)


def clamp_loss_ratio(value: float) -> float:
    """Python-side twin of CLAMP_LOSS_RATIO for raw-point rules."""
    return min(1.0, max(0.0, float(value)))


def base_flux(measurements: list[str], range_start: str) -> str:
    """Range + measurement filter prefix. ``range_start`` e.g. ``-300s``."""
    predicate = " or ".join(
        f"r._measurement == {flux_str(m)}" for m in measurements
    )
    return (
        f"from(bucket: {flux_str(influx_bucket())}) "
        f"|> range(start: {range_start}) "
        f"|> filter(fn: (r) => {predicate}) "
    )
