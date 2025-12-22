#!/usr/bin/env python3
"""
SmokePing RRD → InfluxDB exporter

∙ Exports normal ping targets to measurement **latency**
∙ Exports DNS-probe targets (under RRD_DIR/resolvers/…) to measurement **dns_latency**
∙ Handles both 2-tuple and 3-tuple return formats of rrdtool.lastupdate
"""

import os, time, glob, logging, pathlib, subprocess, json
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

client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG, timeout=10000)
write  = client.write_api(
            write_options=WriteOptions(batch_size=100, flush_interval=5_000))

# ───────────────────────── helpers ───────────────────────────────
def latest(rrd_path: str):
    """Return (timestamp, dict{name:value}) from rrdtool lastupdate command via subprocess."""
    try:
        # Run rrdtool lastupdate command
        result = subprocess.run(
            ["rrdtool", "lastupdate", rrd_path],
            capture_output=True,
            text=True,
            check=True
        )
        
        lines = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]
        if len(lines) < 2:
            raise ValueError(f"Invalid rrdtool output for {rrd_path}")
        
        # First line contains data source names
        ds_names = lines[0].split()
        
        # Last line contains timestamp: values
        data_line = lines[-1]
        
        # Split timestamp and values
        if ':' not in data_line:
            raise ValueError(f"No timestamp found in data line: {data_line}")
            
        timestamp_str, values_str = data_line.split(':', 1)
        timestamp = int(timestamp_str.strip())
        
        # Split values by whitespace and filter out empty strings
        values = [v.strip() for v in values_str.split() if v.strip()]
        
        # Create data dictionary
        data = {}
        for i, ds_name in enumerate(ds_names):
            if i < len(values):
                try:
                    # Convert "U" (unknown) to None, otherwise to float
                    data[ds_name] = None if values[i] == 'U' else float(values[i])
                except ValueError:
                    data[ds_name] = None
            else:
                data[ds_name] = None
        
        return timestamp, data
        
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"rrdtool command failed for {rrd_path}: {e.stderr}")
    except Exception as e:
        raise RuntimeError(f"Failed to parse rrdtool output for {rrd_path}: {e}")

def measurement_for(rrd_file: str) -> str:
    """
    Any RRD under ‹…/resolvers/...› or ‹…/DNS_Resolvers/...› is considered DNS latency.
    Everything else → latency.
    """
    rel = pathlib.Path(rrd_file).relative_to(RRD_DIR)
    return "dns_latency" if rel.parts[0] in ["resolvers", "DNS_Resolvers"] else "latency"

def category_for(rrd_file: str) -> str:
    """
    Determine target category based on directory structure:
    /TopSites/ → topsites
    /Custom/ → custom  
    /Netflix/ → netflix
    /DNS_Resolvers/ → dns
    /websites/ → topsites (legacy)
    Everything else → unknown
    """
    rel = pathlib.Path(rrd_file).relative_to(RRD_DIR)
    directory = rel.parts[0] if len(rel.parts) > 1 else "unknown"
    
    # Directory to category mapping
    category_map = {
        "TopSites": "topsites",
        "websites": "topsites",  # legacy mapping
        "Custom": "custom",
        "Netflix": "netflix", 
        "DNS_Resolvers": "dns",
        "resolvers": "dns"  # legacy mapping
    }
    
    return category_map.get(directory, "unknown")

def probe_type_for(rrd_file: str) -> str:
    """
    Determine probe type based on measurement and directory:
    DNS measurement → dns
    IPv6 targets → fping6
    Everything else → fping
    """
    measurement = measurement_for(rrd_file)
    target_name = pathlib.Path(rrd_file).stem
    
    if measurement == "dns_latency":
        return "dns"
    elif "6" in target_name or "ipv6" in target_name.lower():
        return "fping6"
    else:
        return "fping"

def push(rrd_file: str):
    ts, data = latest(rrd_file)
    measurement = measurement_for(rrd_file)
    target_name = pathlib.Path(rrd_file).stem
    category = category_for(rrd_file)
    probe_type = probe_type_for(rrd_file)

    pt = (Point(measurement)
          .tag("target", target_name)
          .tag("category", category)
          .tag("probe_type", probe_type)
          .time(ts, WritePrecision.S))

    for name, val in data.items():
        if val is not None:
            pt.field(name, float(val))           # keep raw seconds (×1000 in dashboard)

    # Log all exports for debugging
    if measurement == "dns_latency":
        logging.info("DNS export: %s -> measurement=%s, target=%s, fields=%s", 
                    rrd_file, measurement, target_name, list(data.keys()))
    else:
        logging.info("Export: %s -> measurement=%s, target=%s, category=%s, fields=%s", 
                    rrd_file, measurement, target_name, category, list(data.keys()))
    
    try:
        write.write(bucket=INFLUX_BUCKET, record=pt)
        if measurement == "dns_latency":
            logging.info("DNS export SUCCESS: %s written to InfluxDB", target_name)
        else:
            logging.info("Export SUCCESS: %s (%s) written to InfluxDB", target_name, category)
    except Exception as e:
        logging.error("Failed to write %s to InfluxDB: %s", rrd_file, e)

# ───────────────────────── main loop ─────────────────────────────
logging.info("RRD exporter started → %s (bucket: %s)", INFLUX_URL, INFLUX_BUCKET)
while True:
    try:
        rrd_files = glob.glob(os.path.join(RRD_DIR, "**", "*.rrd"), recursive=True)
        logging.info("Processing %d RRD files...", len(rrd_files))
        
        for rrd in rrd_files:
            try:
                push(rrd)
            except Exception as exc:
                logging.warning("%s → %s", rrd, exc)
        
        logging.info("Completed processing cycle, sleeping %d seconds", INTERVAL)
        time.sleep(INTERVAL)
    except KeyboardInterrupt:
        logging.info("Received interrupt signal, shutting down")
        break
    except Exception as e:
        logging.error("Main loop error: %s", e)
        time.sleep(INTERVAL)