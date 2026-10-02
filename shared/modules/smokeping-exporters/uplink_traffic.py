#!/usr/bin/env python3
"""What the Pi actually sends and receives on its uplink, every five minutes.

The measurement budget (config-manager/budget.py) is an *estimate*: the
configured probes times a per-sample cost measured once. This is the
meter beside it: the kernel's byte counters for the interface the default
route leaves through (wifi_link.uplink_interface), read from /proc/net/dev.
The smokeping container runs on the host network, so those are the Pi's
own counters. When a VPN owns the default route (a Tailscale exit node,
WireGuard), the interface is the tunnel and the figure is the traffic
inside it, not what leaves the radio.

It counts everything the Pi does on that interface, not only the
measurements: the DNS observer, the microcut detector's pings to the
router, apt, image pulls, an assistant on the same host, the tunnels. When
the meter reads far above the estimate, that is where the rest went.

Writes:

* ``uplink_traffic`` (tag ``interface``): ``rx_bytes``, ``tx_bytes`` and
  ``seconds`` for each interval, and ``mb_per_day``, the interval's rate
  as decimal megabytes a day. Only with TSDB_TYPE=influxdb.
* ``/config/uplink_traffic.json`` (UPLINK_TRAFFIC_STATE): the last 24 h of
  intervals and the last counter reading, which config-manager's /budget
  reads to report the measured figure beside the estimate, and ``days``,
  the traffic ledger (/traffic): bytes in and out per local date (TZ) for
  the last KEEP_DAYS days. Kept on the config volume, so a container
  restart picks the counters up where they were instead of losing an
  interval.

An interval is dropped, not guessed, when a counter went backwards (the
host rebooted, the driver reset) or the interface changed. Pro edition.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

import wifi_link

log = logging.getLogger("uplink_traffic")

INTERVAL = 300
KEEP_SECONDS = 86_400 + 2 * INTERVAL
# A reading older than this is not a baseline: the gap would average an
# outage of the exporter into one interval.
MAX_GAP = 3 * INTERVAL
PROC_NET_DEV = Path("/proc/net/dev")
DEFAULT_STATE = "/config/uplink_traffic.json"
# The ledger: a year and a month, so this month can be set beside the same
# month a year ago. About 60 bytes a day.
KEEP_DAYS = 400


def read_counters(name: str, path: Path = PROC_NET_DEV) -> tuple[int, int] | None:
    """(rx_bytes, tx_bytes) of one interface, from /proc/net/dev."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    for line in lines[2:]:
        iface, sep, rest = line.partition(":")
        if not sep or iface.strip() != name:
            continue
        fields = rest.split()
        if len(fields) < 9:
            return None
        try:
            return int(fields[0]), int(fields[8])
        except ValueError:
            return None
    return None


def load_state(path: str) -> dict:
    try:
        with open(path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        return {"intervals": []}
    if not isinstance(state, dict) or not isinstance(state.get("intervals"), list):
        return {"intervals": []}
    return state


def save_state(path: str, state: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, separators=(",", ":"))
    os.replace(tmp, path)


def local_day(t: float) -> str:
    """The local date (the container's TZ) an interval ending at ``t``
    belongs to. An interval that straddles midnight counts for the day it
    ends in: at most five minutes on the wrong side."""
    return time.strftime("%Y-%m-%d", time.localtime(t))


def add_to_ledger(days: dict, interval: dict, keep: int = KEEP_DAYS) -> dict:
    """``days`` with one interval added to its local date, and only the
    newest ``keep`` dates kept."""
    out = {k: v for k, v in days.items() if isinstance(v, dict)}
    key = local_day(interval["t"])
    day = dict(out.get(key) or {})
    day["rx"] = int(day.get("rx") or 0) + int(interval["rx"])
    day["tx"] = int(day.get("tx") or 0) + int(interval["tx"])
    day["seconds"] = round(float(day.get("seconds") or 0) + float(interval["seconds"]), 1)
    if interval.get("interface") and interval["interface"] not in day.get("interfaces", []):
        day["interfaces"] = sorted([*day.get("interfaces", []), interval["interface"]])
    out[key] = day
    for old in sorted(out)[:-keep]:
        del out[old]
    return out


def step(state: dict, interface: str | None, counters: tuple[int, int] | None,
         now: float, mono: float | None = None) -> tuple[dict, dict | None]:
    """Advance the state by one reading; return it and the new interval
    (``{"t", "interface", "rx", "tx", "seconds"}``) or None.

    ``mono`` is CLOCK_MONOTONIC, which is system-wide and survives a
    container restart (not a reboot, which resets the counters anyway).
    Elapsed time comes from it when both readings have it, so NTP stepping
    the clock of a Pi without an RTC does not stretch or shrink an
    interval; the wall clock only stamps it."""
    last = state.get("last")
    interval = None
    if interface and counters:
        elapsed = None
        if last and last.get("interface") == interface:
            if mono is not None and last.get("mono") is not None:
                elapsed = mono - last["mono"]
            else:
                elapsed = now - last.get("t", 0)
        if elapsed is not None and 0 < elapsed <= MAX_GAP:
            rx = counters[0] - last.get("rx", 0)
            tx = counters[1] - last.get("tx", 0)
            if rx >= 0 and tx >= 0:
                interval = {"t": int(now), "interface": interface, "rx": rx, "tx": tx,
                            "seconds": round(elapsed, 1)}
        new_last = {"t": now, "mono": mono, "interface": interface,
                    "rx": counters[0], "tx": counters[1]}
    else:
        new_last = None
    # The last day, and nothing stamped in the future: a clock stepped
    # back would otherwise keep those rows until it caught up.
    kept = [i for i in state.get("intervals", [])
            if -INTERVAL <= now - i.get("t", 0) <= KEEP_SECONDS]
    days = state.get("days") if isinstance(state.get("days"), dict) else {}
    if interval:
        kept.append(interval)
        days = add_to_ledger(days, interval)
    return {"last": new_last, "intervals": kept, "days": days,
            "updated": int(now)}, interval


def mb_per_day(interval: dict) -> float:
    seconds = interval["seconds"] or 1
    return round((interval["rx"] + interval["tx"]) / seconds * 86_400 / 1e6, 3)


def point_for(interval: dict):
    from influxdb_client import Point, WritePrecision
    return (Point("uplink_traffic")
            .tag("interface", interval["interface"])
            .field("rx_bytes", int(interval["rx"]))
            .field("tx_bytes", int(interval["tx"]))
            .field("seconds", float(interval["seconds"]))
            .field("mb_per_day", mb_per_day(interval))
            # Its own end, not the five-minute slot: after a restart mid-slot
            # a short interval would otherwise overwrite the previous one.
            .time(interval["t"], WritePrecision.S))


def influx_writer():
    """A write function, or None when this stack does not write InfluxDB."""
    if os.environ.get("TSDB_TYPE", "influxdb") != "influxdb":
        return None
    needed = ("INFLUX_URL", "INFLUX_TOKEN", "INFLUX_ORG", "INFLUX_BUCKET")
    if not all(os.getenv(n) for n in needed):
        log.warning("InfluxDB settings missing: the state file only")
        return None
    from influxdb_client import InfluxDBClient
    from influxdb_client.client.write_api import SYNCHRONOUS
    client = InfluxDBClient(url=os.environ["INFLUX_URL"], token=os.environ["INFLUX_TOKEN"],
                            org=os.environ["INFLUX_ORG"], timeout=10_000)
    api = client.write_api(write_options=SYNCHRONOUS)
    bucket = os.environ["INFLUX_BUCKET"]
    return lambda points: api.write(bucket=bucket, record=points)


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    path = os.environ.get("UPLINK_TRAFFIC_STATE") or DEFAULT_STATE
    write = influx_writer()
    state = load_state(path)
    log.info("uplink traffic meter started (every %ds, state %s)", INTERVAL, path)
    while True:
        started = time.time()
        interface = wifi_link.uplink_interface()
        counters = read_counters(interface) if interface else None
        state, interval = step(state, interface, counters, started, time.monotonic())
        try:
            save_state(path, state)
        except OSError as exc:
            log.warning("state not saved: %s", type(exc).__name__)
        if interval and write:
            try:
                write([point_for(interval)])
            except Exception as exc:
                # The type only: an InfluxDB error's message can carry the token.
                log.error("traffic not written this cycle: %s", type(exc).__name__)
        # On the five-minute boundary, like SmokePing's step.
        time.sleep(max(1.0, INTERVAL - (time.time() % INTERVAL)))


if __name__ == "__main__":
    sys.exit(main())
