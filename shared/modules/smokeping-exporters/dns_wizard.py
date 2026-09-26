#!/usr/bin/env python3
"""The DNS wizard's snapshot, into InfluxDB, for the "DNS wizard" dashboard.

The DNS observer (dns-observer/wizard.py) summarises what the house
resolves into ``wizard.json`` on its state volume, mounted here read-only
at /dns-observer. Each new snapshot becomes:

* ``dns_wizard``: the house as a whole, no names. Queries, the Pi's own
  share, and how diverse and concentrated the services, CDNs and networks
  (ASes) are: richness, the effective number (1/HHI), Shannon's effective
  number, the share covered by the top-5/10/20, what sits behind the top 10,
  and how much the top 10 moved since yesterday.
* ``dns_wizard_service``: the top services, one point each, tagged with
  their name, CDN and network. **Only with DNS_EXPORT_NAMES=1.** The names
  of what a house uses are private. They stay on the Pi's disk unless the
  owner chooses to put them in InfluxDB, and from there in Grafana, which
  may be reachable from outside (a tunnel).

Every point of one snapshot carries the snapshot's own timestamp, so a
panel can show *the latest snapshot only* and not every service that ever
ranked (the "series that no longer exist keep drawing" problem).

Pro edition, InfluxDB only. Nothing to do, and nothing written, while the
observer is off or has not produced a snapshot.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

log = logging.getLogger("dns_wizard")

REQUIRED_ENV = ("INFLUX_URL", "INFLUX_TOKEN", "INFLUX_ORG", "INFLUX_BUCKET")
SNAPSHOT = Path(os.environ.get("DNS_WIZARD_FILE", "/dns-observer/wizard.json"))
CHECK_EVERY = 60


def names_enabled(env: dict[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return env.get("DNS_EXPORT_NAMES", "").strip().lower() in ("1", "true", "yes", "on")


def read_snapshot(path: Path = SNAPSHOT) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and value == value else None


def house_point(snap: dict, ts: int) -> Point:
    """One ``dns_wizard`` point: the whole house, no names."""
    p = Point("dns_wizard").time(ts, WritePrecision.S)
    fields = {
        "queries_24h": snap.get("queries_24h"),
        "queries_1h": snap.get("queries_1h"),
        "own_share_24h": snap.get("own_share_24h"),
        "hours_with_data": snap.get("hours_with_data"),
        "top10_jaccard": (snap.get("churn") or {}).get("top10_jaccard_vs_yesterday"),
    }
    for unit, d in (snap.get("diversity") or {}).items():
        for key in ("richness", "effective_hhi", "effective_shannon", "cov5", "cov10", "cov20"):
            fields[f"{unit}_{key}"] = d.get(key)
    for unit, n in (snap.get("behind_top10") or {}).items():
        fields[f"top10_distinct_{unit}"] = n
    for key, value in fields.items():
        num = _num(value)
        if num is not None:
            p = p.field(key, num)
    return p


def service_points(snap: dict, ts: int) -> list[Point]:
    """One ``dns_wizard_service`` point per top service (names: opt-in)."""
    points = []
    for s in snap.get("top") or []:
        p = (
            Point("dns_wizard_service")
            .tag("service", s.get("service") or "?")
            .tag("cdn", s.get("cdn") or "")
            .tag("asn", s.get("asn") or "")
            .tag("org", s.get("org") or "")
            .field("rank", int(s.get("rank", 0)))
            .field("presence_h", int(s.get("presence_h", 0)))
            .field("queries_24h", int(s.get("queries_24h", 0)))
            .field("queries_1h", int(s.get("queries_1h", 0)))
            .field("host", s.get("host") or "")
            .time(ts, WritePrecision.S)
        )
        points.append(p)
    return points


def points_for(snap: dict, names: bool) -> list[Point]:
    ts = int(snap["generated"])
    points = [house_point(snap, ts)]
    if names:
        points += service_points(snap, ts)
    return points


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        log.error("Missing required environment variables: %s", ", ".join(missing))
        return 1
    bucket = os.environ["INFLUX_BUCKET"]
    client = InfluxDBClient(url=os.environ["INFLUX_URL"], token=os.environ["INFLUX_TOKEN"],
                            org=os.environ["INFLUX_ORG"], timeout=10_000)
    write_api = client.write_api(write_options=SYNCHRONOUS)
    names = names_enabled()
    log.info("DNS wizard exporter started (service names %s)",
             "exported" if names else "kept on the Pi; DNS_EXPORT_NAMES=1 exports them")
    last = None
    while True:
        snap = read_snapshot()
        generated = snap.get("generated") if snap else None
        if generated and generated != last:
            try:
                write_api.write(bucket=bucket, record=points_for(snap, names))
                last = generated
            except Exception as exc:
                # The type only: an InfluxDB error's message can carry the token.
                log.error("writing the DNS wizard snapshot failed: %s", type(exc).__name__)
        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    sys.exit(main())
