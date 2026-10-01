#!/usr/bin/env python3
"""The measurement budget over time, for Grafana's Overview.

config-manager's ``GET /budget`` (config-manager/budget.py) says what the
configured SmokePing measurements cost against two ceilings, now. This
writes that report to InfluxDB every five minutes, so the Overview can
chart it: per probe, stacked, against the ceiling as a dashed line, and
show when a change (the DNS wizard adopting services, a shorter step)
moved it.

It is the *configured* cost, not metered traffic: samples per hour and
approximate decimal megabytes (1e6 bytes) a day, from the per-sample
costs budget.py measured.

Asks the API rather than computing the report here: config-manager's
report is the one source of truth (the CLI, the web admin and this read
the same thing), and its ceilings live in config-manager's environment.
The smokeping container runs on the host network and config-manager
publishes 127.0.0.1:5000, as for the MCP server.

Writes:

* ``measurement_budget`` (no tags): ``targets``, ``samples_per_hour``,
  ``mb_per_day``, ``ceiling_mb_per_day``, ``ceiling_samples_per_hour``,
  ``bandwidth_pct``, ``samples_pct`` (percent, 0-100), ``over``,
  ``complete`` (0/1) and ``unpriced`` (a count of probes).
* ``measurement_budget_probe`` (tags ``probe``, the SmokePing probe name,
  and ``probe_class``, its SmokePing class): ``targets``,
  ``samples_per_hour``, ``step``, ``pings``, and ``bytes_per_sample`` and
  ``mb_per_day`` only for a probe whose class has a measured cost.

Points are stamped at the five-minute boundary, so a restart within one
rewrites the same point. Pro edition only (requires InfluxDB).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

log = logging.getLogger("measurement_budget")

REQUIRED_ENV = ("INFLUX_URL", "INFLUX_TOKEN", "INFLUX_ORG", "INFLUX_BUCKET")
INTERVAL = 300  # SmokePing's step: the report cannot change faster than a reload
DEFAULT_URL = "http://127.0.0.1:5000"


def fetch_budget(base_url: str, token: str = "",
                 opener=urllib.request.urlopen) -> dict | None:
    """config-manager's /budget body, or None when it cannot be had.

    Only the exception's type is logged: a URL error can carry the
    address, and nothing here should ever echo the token.
    """
    req = urllib.request.Request(base_url.rstrip("/") + "/budget")
    if token:
        req.add_header("X-API-Token", token)
    try:
        with opener(req, timeout=60) as resp:
            body = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("budget not read this cycle: %s", type(exc).__name__)
        return None
    if not isinstance(body, dict):
        log.warning("budget not read this cycle: the answer is not an object")
        return None
    return body


def _num(value) -> float:
    return float(value or 0)


def points_for(body: dict, ts: int) -> list[Point]:
    """The report as points; none when config-manager has no budget yet."""
    if not body.get("available"):
        return []
    ceiling = body.get("ceiling") or {}
    used = body.get("used") or {}
    total = (Point("measurement_budget")
             .field("targets", int(body.get("targets") or 0))
             .field("samples_per_hour", _num(body.get("samples_per_hour")))
             .field("mb_per_day", _num(body.get("mb_per_day")))
             .field("ceiling_mb_per_day", _num(ceiling.get("mb_per_day")))
             .field("ceiling_samples_per_hour", _num(ceiling.get("samples_per_hour")))
             .field("bandwidth_pct", _num(used.get("bandwidth_pct")))
             .field("samples_pct", _num(used.get("samples_pct")))
             .field("over", 1 if body.get("over") else 0)
             .field("complete", 0 if body.get("complete") is False else 1)
             .field("unpriced", len(body.get("unpriced") or []))
             .time(ts, WritePrecision.S))
    points = [total]
    for row in body.get("by_probe") or []:
        probe = row.get("probe")
        if not probe:
            continue
        pt = (Point("measurement_budget_probe")
              .tag("probe", probe)
              .tag("probe_class", row.get("class") or probe)
              .field("targets", int(row.get("targets") or 0))
              .field("samples_per_hour", _num(row.get("samples_per_hour")))
              .field("step", int(row.get("step") or 0))
              .field("pings", int(row.get("pings") or 0)))
        # Unpriced: no byte figure at all, rather than a 0 that would chart
        # as free.
        if row.get("mb_per_day") is not None:
            pt = (pt.field("bytes_per_sample", int(row.get("bytes_per_sample") or 0))
                  .field("mb_per_day", _num(row.get("mb_per_day"))))
        points.append(pt.time(ts, WritePrecision.S))
    return points


def boundary(now: float, interval: int = INTERVAL) -> int:
    return int(now) // interval * interval


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        log.error("Missing required environment variables: %s", ", ".join(missing))
        return 1
    base_url = os.environ.get("CONFIG_API_URL") or DEFAULT_URL
    token = os.environ.get("CONFIG_API_TOKEN", "")
    bucket = os.environ["INFLUX_BUCKET"]
    client = InfluxDBClient(url=os.environ["INFLUX_URL"], token=os.environ["INFLUX_TOKEN"],
                            org=os.environ["INFLUX_ORG"], timeout=10_000)
    write_api = client.write_api(write_options=SYNCHRONOUS)
    log.info("measurement budget exporter started (every %ds)", INTERVAL)
    while True:
        started = time.time()
        body = fetch_budget(base_url, token)
        if body is not None:
            try:
                points = points_for(body, boundary(started))
                if points:
                    write_api.write(bucket=bucket, record=points)
            except Exception as exc:
                # The type only: an InfluxDB error's message can carry the token.
                log.error("budget not written this cycle: %s", type(exc).__name__)
        time.sleep(max(1.0, INTERVAL - (time.time() - started)))


if __name__ == "__main__":
    sys.exit(main())
