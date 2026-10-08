"""Experimental: persistent congestion (Jitterbug) and loss degradation (BCP).

Every ``INFERENCE_INTERVAL`` (3600 s), for each selected ICMP target:

- persistent congestion over the last ``INFERENCE_CONGESTION_DAYS`` (14),
  close to the 16 days of the paper's dataset (congestion.py);
- loss degradation over the last ``INFERENCE_DEGRADATION_DAYS`` (7)
  (degradation.py);

and their periods replace the window's earlier ones in InfluxDB (store.py).
``INFERENCE_SINCE`` (a date) keeps both windows from reaching before it, for
a host that moved or changed provider: history from another connection is
not this one's baseline.

Bayesian change-point detection costs grow with the square of the number of
bins, so the windows are bounded; the process runs at a lower CPU priority
so it never competes with the measurements. Reads InfluxDB only: with
``TSDB_TYPE=clickhouse`` it idles and says so in its status.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import congestion
import degradation
import series
import status
import store

log = logging.getLogger("inference")

DEFAULT_INTERVAL = 3600
DEFAULT_CONGESTION_DAYS = 14
DEFAULT_DEGRADATION_DAYS = 7
DEFAULT_START_DELAY = 120
DAY = 86400


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def since() -> int | None:
    """INFERENCE_SINCE as epoch seconds (UTC midnight of that date), or None."""
    raw = (os.environ.get("INFERENCE_SINCE") or "").strip()
    if not raw:
        return None
    try:
        day = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        log.warning("INFERENCE_SINCE=%r is not a YYYY-MM-DD date; ignoring it", raw)
        return None
    return int(day.timestamp())


def windows(now: int) -> tuple[int, int]:
    """(congestion start, degradation start), each bounded by INFERENCE_SINCE."""
    floor = since() or 0
    c = now - _env_int("INFERENCE_CONGESTION_DAYS", DEFAULT_CONGESTION_DAYS) * DAY
    d = now - _env_int("INFERENCE_DEGRADATION_DAYS", DEFAULT_DEGRADATION_DAYS) * DAY
    return max(c, floor), max(d, floor)


def run_once(now: int | None = None, fetch=series.fetch, list_targets=series.targets,
             find_congestion=congestion.detect, find_degradation=degradation.detect,
             replace=store.replace) -> dict:
    """One pass over every target. A target that fails is logged and skipped;
    the others still run."""
    now = int(time.time()) if now is None else now
    c_start, d_start = windows(now)
    started = time.monotonic()
    report = {"at": now, "targets": {}, "errors": 0,
              "windows": {"congestion_start": c_start, "degradation_start": d_start}}
    for target, category in list_targets(min(c_start, d_start)):
        row = {}
        try:
            pings, loss = fetch(target, min(c_start, d_start))
            c = find_congestion(pings[pings["epoch"] >= c_start])
            row["congestion"] = {"periods": len(c["periods"]),
                                 "change_points": c["change_points"]}
            replace(store.CONGESTION, target, category, c["periods"], c_start, now)
            d = find_degradation(loss[loss["epoch"] >= d_start])
            row["degradation"] = {"periods": len(d["periods"]),
                                  "change_points": d["change_points"]}
            replace(store.DEGRADATION, target, category, d["periods"], d_start, now)
        except Exception:  # noqa: BLE001 -- one target must not stop the others
            log.warning("inference failed for %s", target, exc_info=True)
            row["error"] = True
            report["errors"] += 1
        report["targets"][target] = row
    report["seconds"] = round(time.monotonic() - started, 1)
    log.info("inference pass: %d targets in %.0f s, %d failed",
             len(report["targets"]), report["seconds"], report["errors"])
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("jitterbug").setLevel(logging.WARNING)
    interval = _env_int("INFERENCE_INTERVAL", DEFAULT_INTERVAL)
    if (os.environ.get("TSDB_TYPE") or "influxdb") != "influxdb":
        log.warning("inference reads InfluxDB only; TSDB_TYPE=%s, so it stays idle",
                    os.environ.get("TSDB_TYPE"))
        status.write({"idle": "InfluxDB only", "at": int(time.time())})
        while True:
            time.sleep(DAY)
    try:
        os.nice(10)
    except OSError:
        pass
    status.write({"starting": int(time.time()), "interval": interval})
    time.sleep(_env_int("INFERENCE_START_DELAY", DEFAULT_START_DELAY))
    while True:
        began = time.time()
        report = run_once()
        report["interval"] = interval
        status.write(report)
        time.sleep(max(60.0, interval - (time.time() - began)))


if __name__ == "__main__":
    main()
