#!/usr/bin/env python3
"""
SmokePing RRD → InfluxDB exporter

∙ Exports normal ping targets to measurement **latency**
∙ Exports DNS-probe targets (under RRD_DIR/resolvers/ or RRD_DIR/DNS_Resolvers/)
  to measurement **dns_latency**
∙ Uses `rrdtool fetch AVERAGE` from the last exported timestamp per RRD, so
  gaps are backfilled after downtime (capped at 24 h) and points are not
  rewritten every cycle. Last-exported timestamps persist in a JSON state
  file (EXPORTER_STATE_FILE, default /tmp/rrd2influx_state.json).

LOSS SEMANTICS (changed 2026-07): the RRD `loss` data source is the raw COUNT
of lost pings per cycle (0..SMOKEPING_PINGS). This exporter now converts it to
a RATIO 0..1 (count / SMOKEPING_PINGS, default 20) before writing, so the
dashboards' `percentunit` axes are correct. The field name stays `loss`.
Historical points written by older versions keep the old count scale.
"""

import glob
import json
import logging
import math
import os
import pathlib
import subprocess
import time

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

REQUIRED_ENV = ("INFLUX_URL", "INFLUX_TOKEN", "INFLUX_ORG", "INFLUX_BUCKET")
DEFAULT_STATE_FILE = "/tmp/rrd2influx_state.json"
FIRST_RUN_LOOKBACK = 3600       # first run: start from now - 1 h
MAX_BACKFILL = 24 * 3600        # never backfill more than 24 h
WRITE_RETRIES = 3

# Directories that hold DNS-probe RRDs (measurement dns_latency).
DNS_DIRS = ("resolvers", "DNS_Resolvers")

# Directory → category tag. Current names are what smokeping_targets.j2
# generates (websites, Netflix, DNS_Resolvers, Custom); legacy names are kept
# so old RRD trees keep exporting with the same tags.
CATEGORY_MAP = {
    "websites": "topsites",
    "Netflix": "netflix",
    "DNS_Resolvers": "dns",
    "Custom": "custom",
    # legacy directory names
    "TopSites": "topsites",
    "resolvers": "dns",
}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")


# ───────────────────────── classification ─────────────────────────
def measurement_for(rrd_file: str, rrd_dir: str) -> str:
    """RRDs under a DNS directory → dns_latency, everything else → latency."""
    rel = pathlib.Path(rrd_file).relative_to(rrd_dir)
    return "dns_latency" if rel.parts[0] in DNS_DIRS else "latency"


def category_for(rrd_file: str, rrd_dir: str) -> str:
    """Map the first-level directory to a category tag (unknown if unmapped)."""
    rel = pathlib.Path(rrd_file).relative_to(rrd_dir)
    directory = rel.parts[0] if len(rel.parts) > 1 else "unknown"
    return CATEGORY_MAP.get(directory, "unknown")


def probe_type_for(rrd_file: str, rrd_dir: str) -> str:
    """dns for DNS measurements; fping6 for IPv6 targets (name ends in '6',
    e.g. Google6); fping otherwise."""
    if measurement_for(rrd_file, rrd_dir) == "dns_latency":
        return "dns"
    target_name = pathlib.Path(rrd_file).stem
    return "fping6" if target_name.endswith("6") else "fping"


# ───────────────────────── loss conversion ─────────────────────────
def loss_to_ratio(loss_count, pings: int):
    """Convert the RRD loss COUNT (0..pings lost pings per cycle) to a ratio
    0..1. Returns None for unknown values or a nonsensical ping count."""
    if loss_count is None or pings <= 0:
        return None
    return max(0.0, min(1.0, float(loss_count) / float(pings)))


# ───────────────────────── rrdtool fetch ─────────────────────────
def parse_fetch_output(text: str):
    """Parse `rrdtool fetch <rrd> AVERAGE` output.

    Expected shape:

                            median   loss   ping1 ...
        <blank line>
        1690000000: 1.23e-02 0.0e+00 ...
        1690000300: nan nan nan ...

    Returns (ds_names, rows) where rows is a list of
    (timestamp, {ds_name: float | None}); nan/unparseable values become None.
    """
    ds_names = []
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            ds_names = line.split()
            continue
        ts_str, _, values_str = line.partition(":")
        try:
            ts = int(ts_str.strip())
        except ValueError:
            continue
        values = values_str.split()
        row = {}
        for i, name in enumerate(ds_names):
            raw = values[i] if i < len(values) else "nan"
            try:
                val = float(raw)
            except ValueError:
                val = float("nan")
            row[name] = None if math.isnan(val) else val
        rows.append((ts, row))
    return ds_names, rows


def fetch_rows(rrd_path: str, start: int, end: int):
    """Run rrdtool fetch and return parsed rows (see parse_fetch_output)."""
    result = subprocess.run(
        ["rrdtool", "fetch", rrd_path, "AVERAGE",
         "--start", str(start), "--end", str(end)],
        capture_output=True, text=True, check=True,
    )
    return parse_fetch_output(result.stdout)


# ───────────────────────── state file ─────────────────────────
def load_state(path: str) -> dict:
    """Load {rrd_path: last_exported_ts}. Missing/corrupt file → empty dict."""
    try:
        with open(path) as fh:
            state = json.load(fh)
        if isinstance(state, dict):
            return {str(k): int(v) for k, v in state.items()}
        logging.warning("State file %s has unexpected shape, ignoring", path)
    except FileNotFoundError:
        pass
    except (ValueError, TypeError, OSError) as exc:
        logging.warning("Could not read state file %s: %s", path, exc)
    return {}


def save_state(path: str, state: dict) -> None:
    """Atomically persist the state file (write temp file, then rename)."""
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w") as fh:
            json.dump(state, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        logging.error("Could not write state file %s: %s", path, exc)


# ───────────────────────── influx write ─────────────────────────
def write_with_retry(write_api, bucket: str, points, retries: int = WRITE_RETRIES) -> bool:
    """Write points with exponential backoff (1 s, 2 s, 4 s). Returns False
    after the final attempt; the caller then leaves the state untouched so the
    same window is re-fetched next cycle (bounded by MAX_BACKFILL)."""
    delay = 1.0
    for attempt in range(1, retries + 1):
        try:
            write_api.write(bucket=bucket, record=points)
            return True
        except Exception as exc:
            if attempt < retries:
                logging.warning("Influx write failed (attempt %d/%d): %s — retrying in %.0fs",
                                attempt, retries, exc, delay)
                time.sleep(delay)
                delay *= 2
            else:
                logging.error("Influx write failed after %d attempts, dropping batch of %d points: %s",
                              retries, len(points), exc)
    return False


# ───────────────────────── export ─────────────────────────
def build_points(rrd_file: str, rows, rrd_dir: str, pings: int):
    """Turn fetch rows into Influx points, timestamped from the RRD rows.
    Returns (points, last_ts) where last_ts is the newest row with data."""
    measurement = measurement_for(rrd_file, rrd_dir)
    target_name = pathlib.Path(rrd_file).stem
    category = category_for(rrd_file, rrd_dir)
    probe_type = probe_type_for(rrd_file, rrd_dir)

    points = []
    last_ts = None
    for ts, data in rows:
        fields = {}
        for name, val in data.items():
            if val is None:
                continue
            if name == "loss":
                val = loss_to_ratio(val, pings)
                if val is None:
                    continue
            fields[name] = float(val)   # latency stays raw seconds (×1000 in dashboards)
        if not fields:
            continue                    # skip all-NaN rows (e.g. in-progress bucket)
        pt = (Point(measurement)
              .tag("target", target_name)
              .tag("category", category)
              .tag("probe_type", probe_type)
              .time(ts, WritePrecision.S))
        for name, val in fields.items():
            pt.field(name, val)
        points.append(pt)
        last_ts = ts
    return points, last_ts


def run_cycle(write_api, bucket: str, rrd_dir: str, state: dict, pings: int) -> None:
    now = int(time.time())
    rrd_files = glob.glob(os.path.join(rrd_dir, "**", "*.rrd"), recursive=True)
    logging.info("Processing %d RRD files...", len(rrd_files))

    # Drop state entries for RRDs that no longer exist.
    current = set(rrd_files)
    for stale in [k for k in state if k not in current]:
        del state[stale]

    exported = 0
    for rrd in rrd_files:
        try:
            start = state.get(rrd, now - FIRST_RUN_LOOKBACK)
            start = max(start, now - MAX_BACKFILL)
            _, rows = fetch_rows(rrd, start, now)
            rows = [(ts, data) for ts, data in rows if start < ts <= now]
            points, last_ts = build_points(rrd, rows, rrd_dir, pings)
            if not points:
                continue
            if write_with_retry(write_api, bucket, points):
                state[rrd] = last_ts
                exported += len(points)
        except subprocess.CalledProcessError as exc:
            logging.warning("%s → rrdtool fetch failed: %s", rrd, exc.stderr)
        except Exception as exc:
            logging.warning("%s → %s", rrd, exc)
    logging.info("Cycle complete: %d points exported", exported)


def main() -> int:
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        logging.error("Missing required environment variables: %s",
                      ", ".join(missing))
        return 1

    influx_url = os.getenv("INFLUX_URL")
    influx_token = os.getenv("INFLUX_TOKEN")
    influx_org = os.getenv("INFLUX_ORG")
    influx_bucket = os.getenv("INFLUX_BUCKET")
    rrd_dir = os.getenv("RRD_DIR", "/var/lib/smokeping")
    interval = int(os.getenv("EXPORT_INTERVAL", "60"))
    pings = int(os.getenv("SMOKEPING_PINGS", "20"))
    state_file = os.getenv("EXPORTER_STATE_FILE", DEFAULT_STATE_FILE)

    client = InfluxDBClient(url=influx_url, token=influx_token,
                            org=influx_org, timeout=10000)
    write_api = client.write_api(write_options=SYNCHRONOUS)
    state = load_state(state_file)

    logging.info("RRD exporter started → %s (bucket: %s, pings/cycle: %d, state: %s)",
                 influx_url, influx_bucket, pings, state_file)
    try:
        while True:
            try:
                run_cycle(write_api, influx_bucket, rrd_dir, state, pings)
                save_state(state_file, state)
            except Exception as exc:
                logging.error("Main loop error: %s", exc)
            time.sleep(interval)
    except KeyboardInterrupt:
        logging.info("Received interrupt signal, shutting down")
        save_state(state_file, state)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
