"""Results into InfluxDB, one point per period, replaced on every run.

``persistent_congestion`` (Jitterbug) and ``loss_degradation`` (BCP on
loss), tagged ``target``/``category``, timestamped at the period's start,
with the end in the ``end`` field (epoch seconds) so Grafana can shade the
region. Each run deletes its window's points for the target before writing
the new ones: a period found again is replaced, not duplicated, and one the
detector no longer finds disappears. Points before the window are kept.

A period that begins at the window's first bin was cut by the window: its
real start is earlier and an earlier run recorded it whole. It is not
written, so the earlier, complete record stays.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

CONGESTION = "persistent_congestion"
DEGRADATION = "loss_degradation"


def _client():
    from influxdb_client import InfluxDBClient

    return InfluxDBClient(
        url=os.environ.get("INFLUX_URL", "http://influxdb:8086"),
        token=os.environ.get("INFLUX_TOKEN", ""),
        org=os.environ.get("INFLUX_ORG", "smokeping"),
        timeout=60_000,
    )


def _bucket() -> str:
    return os.environ.get("INFLUX_BUCKET", "smokeping")


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _quoted(value: str) -> str:
    """A tag value inside a delete predicate. A quote or a backslash would
    change what the predicate deletes, so a target named with one is
    refused, never escaped."""
    if '"' in value or "\\" in value:
        raise ValueError("unexpected character in a tag value")
    return f'"{value}"'


def measurement_name(measurement: str) -> str:
    """One of the two measurements, as literals: the doctor reads them from
    here to know what the dashboards may ask this module for."""
    if measurement == CONGESTION:
        return "persistent_congestion"
    if measurement == DEGRADATION:
        return "loss_degradation"
    raise ValueError("unknown measurement")


def points(measurement: str, target: str, category: str, periods: list[dict],
           window_start: int) -> list:
    from influxdb_client import Point, WritePrecision

    name = measurement_name(measurement)
    out = []
    for p in periods:
        if p.get("truncated") or p["start"] <= window_start:
            continue
        pt = (Point(name).tag("target", target).tag("category", category)
              .time(int(p["start"]), WritePrecision.S)
              .field("end", int(p["end"]))
              .field("duration_s", int(p["end"] - p["start"])))
        for key in ("confidence", "jump_ms", "ks_p", "mean_loss_pct"):
            if p.get(key) is not None:
                pt = pt.field(key, float(p[key]))
        out.append(pt)
    return out


def replace(measurement: str, target: str, category: str, periods: list[dict],
            window_start: int, now: int, client=None) -> int:
    """Delete ``measurement``'s points for ``target`` in [window_start, now]
    and write ``periods``. Returns how many points were written."""
    own = client is None
    client = client or _client()
    try:
        client.delete_api().delete(
            _iso(window_start), _iso(now),
            f"_measurement={_quoted(measurement)} AND target={_quoted(target)}",
            bucket=_bucket(), org=os.environ.get("INFLUX_ORG", "smokeping"),
        )
        batch = points(measurement, target, category, periods, window_start)
        if batch:
            from influxdb_client.client.write_api import SYNCHRONOUS

            client.write_api(write_options=SYNCHRONOUS).write(bucket=_bucket(), record=batch)
        return len(batch)
    finally:
        if own:
            client.close()
