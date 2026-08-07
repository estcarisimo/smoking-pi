#!/usr/bin/env python3
"""
Microcut Detector — high-frequency (5 pps) prober for CPE targets.

Reads CPE IPs from /tmp/cpe_state.json (written by cpe_discovery.py),
probes them with fping in 10-second windows, and writes per-window
latency/loss data to InfluxDB as measurement "cpe_latency".

Pro edition only (requires InfluxDB).
"""

import json
import logging
import os
import statistics
import subprocess
import sys
import time

from influxdb_client import InfluxDBClient, Point, WriteOptions, WritePrecision

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# ── config ───────────────────────────────────────────────────────
INFLUX_URL = os.getenv("INFLUX_URL")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET")
PROBE_RATE = int(os.getenv("CPE_PROBE_RATE", "5"))          # packets/sec
PROBE_WINDOW = int(os.getenv("CPE_PROBE_WINDOW", "10"))     # seconds
# Idle between windows. Previously there was none: the loop slept
# PROBE_WINDOW - elapsed, and elapsed is ~PROBE_WINDOW, so it probed
# back-to-back forever — 5 packets/sec, ~432k/day, on a Pi that was thermally
# throttling. It also pressured the gateway's ICMP rate limiter, so part of the
# constant single-digit "loss floor" was self-inflicted. 20 s idle keeps
# microcut detection (windows are still 10 s of 5 pps) at a third of the cost.
PROBE_IDLE = int(os.getenv("CPE_PROBE_IDLE", "20"))         # seconds
STATE_FILE = "/tmp/cpe_state.json"

TOTAL_PINGS = PROBE_RATE * PROBE_WINDOW   # 50
INTERVAL_MS = 1000 // PROBE_RATE          # 200 ms


# ── InfluxDB client ──────────────────────────────────────────────
_missing = [
    name
    for name, value in (
        ("INFLUX_URL", INFLUX_URL),
        ("INFLUX_TOKEN", INFLUX_TOKEN),
        ("INFLUX_ORG", INFLUX_ORG),
        ("INFLUX_BUCKET", INFLUX_BUCKET),
    )
    if not value
]
if _missing:
    log.error("Missing required environment variables: %s", ", ".join(_missing))
    sys.exit(1)

client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG, timeout=10_000)
write_api = client.write_api(
    write_options=WriteOptions(batch_size=10, flush_interval=5_000)
)


# ── helpers ──────────────────────────────────────────────────────
def load_cpe_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def probe(ip: str, ipv6: bool = False) -> dict | None:
    """
    Run fping at PROBE_RATE pps for PROBE_WINDOW seconds.
    Returns dict with median, loss, min, max, jitter, and individual pings.
    """
    cmd = ["/usr/sbin/fping"]
    if ipv6:
        cmd.append("-6")
    cmd += [
        f"-C{TOTAL_PINGS}",
        f"-p{INTERVAL_MS}",
        "-q",
        ip,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=PROBE_WINDOW + 10)
    except subprocess.TimeoutExpired:
        log.warning("fping timed out for %s", ip)
        return None

    # fping -C output goes to stderr:
    # 1.2.3.4 : 12.5 14.2 - 13.1 ...   (- means lost)
    # 2001:558:100e:9e::2 : 12.5 14.2 - 13.1 ...
    output = result.stderr.strip()
    if not output:
        return None

    # Split on " : " to handle IPv6 addresses that contain colons
    parts = output.split(" : ", 1)
    if len(parts) < 2:
        return None

    tokens = parts[1].split()
    rtts = []
    lost = 0
    for t in tokens:
        if t == "-":
            lost += 1
        else:
            try:
                rtts.append(float(t))
            except ValueError:
                lost += 1

    total = len(tokens)
    loss_pct = (lost / total * 100.0) if total > 0 else 100.0

    if not rtts:
        return {
            "median": 0.0, "loss": 100.0, "min": 0.0, "max": 0.0,
            "jitter": 0.0, "pings": [], "total": total, "lost": lost,
        }

    rtts_sorted = sorted(rtts)
    median = statistics.median(rtts)
    jitter = statistics.stdev(rtts) if len(rtts) > 1 else 0.0

    return {
        "median": median,
        "loss": loss_pct,
        "min": rtts_sorted[0],
        "max": rtts_sorted[-1],
        "jitter": round(jitter, 3),
        "pings": rtts,
        "total": total,
        "lost": lost,
    }


def write_result(ip: str, protocol: str, data: dict):
    """Write probe result to InfluxDB."""
    pt = (
        Point("cpe_latency")
        .tag("target", ip)
        .tag("protocol", protocol)
        .tag("category", "cpe")
        .field("median", data["median"])
        .field("loss", data["loss"])
        .field("min", data["min"])
        .field("max", data["max"])
        .field("jitter", data["jitter"])
        .time(int(time.time()), WritePrecision.S)
    )

    # Add individual pings (up to 50)
    for i, rtt in enumerate(data["pings"], 1):
        pt.field(f"ping{i}", rtt)

    try:
        write_api.write(bucket=INFLUX_BUCKET, record=pt)
        log.info(
            "CPE %s (%s): median=%.1fms loss=%.1f%% jitter=%.1fms [%d/%d]",
            ip, protocol, data["median"], data["loss"], data["jitter"],
            data["total"] - data["lost"], data["total"],
        )
    except Exception as e:
        log.error("InfluxDB write failed for %s: %s", ip, e)


# ── main loop ────────────────────────────────────────────────────
def main():
    log.info(
        "Microcut detector started (rate=%d pps, window=%ds, total=%d pings/cycle)",
        PROBE_RATE, PROBE_WINDOW, TOTAL_PINGS,
    )

    # Wait for CPE discovery to populate state
    for _ in range(30):
        state = load_cpe_state()
        if state.get("ipv4") or state.get("ipv6"):
            break
        log.info("Waiting for CPE discovery...")
        time.sleep(10)

    while True:
        cycle_start = time.time()
        state = load_cpe_state()

        ipv4 = state.get("ipv4")
        ipv6 = state.get("ipv6")

        if not ipv4 and not ipv6:
            log.warning("No CPE targets available, sleeping %ds", PROBE_WINDOW)
            time.sleep(PROBE_WINDOW)
            continue

        if ipv4:
            data = probe(ipv4, ipv6=False)
            if data:
                write_result(ipv4, "ipv4", data)

        if ipv6:
            data = probe(ipv6, ipv6=True)
            if data:
                write_result(ipv6, "ipv6", data)

        # Idle until the next window. The cycle is PROBE_WINDOW of probing plus
        # PROBE_IDLE of quiet, so set CPE_PROBE_IDLE=0 to restore the old
        # continuous behaviour.
        elapsed = time.time() - cycle_start
        remaining = max(0, (PROBE_WINDOW + PROBE_IDLE) - elapsed)
        if remaining > 0:
            time.sleep(remaining)


if __name__ == "__main__":
    main()
