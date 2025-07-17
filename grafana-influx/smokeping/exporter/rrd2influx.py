#!/usr/bin/env python3
"""
SmokePing RRD → InfluxDB exporter

∙ Exports normal ping targets to measurement **latency**
∙ Exports DNS-probe targets (under RRD_DIR/resolvers/…) to measurement **dns_latency**
∙ Handles both 2-tuple and 3-tuple return formats of rrdtool.lastupdate
"""

import os, time, glob, logging, rrdtool, pathlib
from influxdb_client import InfluxDBClient, Point, WriteOptions, WritePrecision

# ────────────────────────────── env ──────────────────────────────
INFLUX_URL    = os.getenv("INFLUX_URL")
INFLUX_TOKEN  = os.getenv("INFLUX_TOKEN")
INFLUX_ORG    = os.getenv("INFLUX_ORG")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET")
RRD_DIR       = os.getenv("RRD_DIR", "/var/lib/smokeping")
INTERVAL      = int(os.getenv("EXPORT_INTERVAL", "60"))

# ─────────────────────────── logging ─────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
write  = client.write_api(
            write_options=WriteOptions(batch_size=1000, flush_interval=10_000))

# ───────────────────────── helpers ───────────────────────────────
def latest(rrd_path: str):
    """Return (timestamp, dict{name:value}) from rrdtool.lastupdate output."""
    ret = rrdtool.lastupdate(rrd_path)

    if isinstance(ret, tuple):
        if len(ret) == 2:                        # (ts, {name:value})
            return ret
        if len(ret) == 3:                        # (ts, [names], (values))
            ts, names, vals = ret
            return ts, dict(zip(names, vals))

    if isinstance(ret, dict):                    # new dict style
        if "date" in ret and "ds" in ret:
            return int(ret["date"].timestamp()), ret["ds"]

    raise TypeError(f"Unrecognised rrdtool.lastupdate output: {type(ret)}")

def measurement_for(rrd_file: str) -> str:
    """
    Any RRD under ‹…/resolvers/...› is considered DNS latency.
    Everything else → latency.
    """
    rel = pathlib.Path(rrd_file).relative_to(RRD_DIR)
    return "dns_latency" if rel.parts[0] == "resolvers" else "latency"

def push(rrd_file: str):
    ts, data = latest(rrd_file)
    measurement = measurement_for(rrd_file)
    target_name = pathlib.Path(rrd_file).stem

    pt = (Point(measurement)
          .tag("target", target_name)
          .time(ts, WritePrecision.S))

    for name, val in data.items():
        if val is not None:
            pt.field(name, float(val))           # keep raw seconds (×1000 in dashboard)

    write.write(bucket=INFLUX_BUCKET, record=pt)

# ───────────────────────── main loop ─────────────────────────────
logging.info("RRD exporter started → %s (bucket: %s)", INFLUX_URL, INFLUX_BUCKET)
while True:
    for rrd in glob.glob(os.path.join(RRD_DIR, "**", "*.rrd"), recursive=True):
        try:
            push(rrd)
        except Exception as exc:
            logging.warning("%s → %s", rrd, exc)
    time.sleep(INTERVAL)