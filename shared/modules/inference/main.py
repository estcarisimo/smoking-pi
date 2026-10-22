"""Experimental: persistent congestion (Jitterbug) and loss degradation (BCP).

Every ``INFERENCE_INTERVAL`` (3600 s), for each selected ICMP target:

- persistent congestion over the last ``INFERENCE_CONGESTION_DAYS`` (14),
  close to the 16 days of the paper's dataset (congestion.py);
- loss degradation over the last ``INFERENCE_DEGRADATION_DAYS`` (7)
  (degradation.py);

and their periods replace the window's earlier ones in InfluxDB (store.py).
Then, once per pass, shared degradation over the same window as the loss
(shared.py): one series across every destination, for the whole connection;
and Jitterbug, unchanged, on an aggregate stream of every voter's pings
over its own floor (aggregate.py), over the congestion window.
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
from pathlib import Path

import aggregate
import congestion
import deaf
import degradation
import series
import shared
import status
import store
from common import asn

log = logging.getLogger("inference")

DEFAULT_INTERVAL = 3600
DEFAULT_CONGESTION_DAYS = 14
DEFAULT_DEGRADATION_DAYS = 7
START_DELAY = 120  # seconds: let InfluxDB come up first
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
             replace=store.replace, read_deaf=series.deaf_bins,
             record_pass=store.write_pass, shared_pass=None,
             replace_shared=store.replace_shared, aggregate_pass=None,
             replace_shared_congestion=None) -> dict:
    """One pass over every target. A target that fails is logged and skipped;
    the others still run. ``shared_pass(start, deaf_bins)`` runs the shared
    detector once after them (main() passes shared.run), and
    ``aggregate_pass(shared_result, c_start, d_start, now)`` Jitterbug on the
    aggregate stream after that, with the shared pass's voters (main() passes
    aggregate.run)."""
    if replace_shared_congestion is None:
        def replace_shared_congestion(periods, start, end):
            return store.replace_shared(periods, start, end,
                                        measurement=store.SHARED_CONGESTION)
    now = int(time.time()) if now is None else now
    c_start, d_start = windows(now)
    started = time.monotonic()
    report = {"at": now, "targets": {}, "errors": 0,
              "windows": {"congestion_start": c_start, "degradation_start": d_start}}
    try:
        selected = list_targets(min(c_start, d_start))
    except Exception:  # noqa: BLE001 -- InfluxDB down: report it, try next pass
        log.warning("could not list the targets; skipping this pass", exc_info=True)
        report["errors"] += 1
        report["failed"] = "listing targets"
        selected = []
    if not selected and "failed" not in report:
        log.warning("no target matched INFERENCE_CATEGORIES / INFERENCE_TARGETS")
    # The host's deaf bins, once per pass: every target shares the radio.
    deaf_started = time.monotonic()
    deaf_bins = read_deaf(d_start, now) if (selected or shared_pass) else set()
    report["deaf_bins"] = len(deaf_bins)
    report["deaf_seconds"] = round(time.monotonic() - deaf_started, 1)
    for target, category in selected:
        row = {}
        try:
            pings, loss = fetch(target, min(c_start, d_start))
            c_pings = pings[pings["epoch"] >= c_start]
            c = find_congestion(c_pings)
            deaf.coverage(c["periods"], c_pings["epoch"])
            row["congestion"] = {"periods": len(c["periods"]),
                                 "change_points": c["change_points"]}
            replace(store.CONGESTION, target, category, c["periods"], c_start, now)
            d_loss = loss[loss["epoch"] >= d_start]
            d = find_degradation(d_loss)
            deaf.coverage(d["periods"], d_loss["epoch"])
            deaf.label(d["periods"], deaf_bins)
            row["degradation"] = {"periods": len(d["periods"]),
                                  "change_points": d["change_points"],
                                  "monitor_deaf": sum(1 for p in d["periods"]
                                                      if p.get("class") == deaf.MONITOR_DEAF)}
            replace(store.DEGRADATION, target, category, d["periods"], d_start, now)
        except Exception:  # noqa: BLE001 -- one target must not stop the others
            log.warning("inference failed for %s", target, exc_info=True)
            row["error"] = True
            report["errors"] += 1
        report["targets"][target] = row
    s = None
    if shared_pass is not None:
        shared_started = time.monotonic()
        try:
            s = shared_pass(d_start, deaf_bins)
            # Too few voters says more about the data than about the network:
            # keep what earlier passes wrote rather than clear the window.
            if s["voters"] >= shared.MIN_VOTERS:
                replace_shared(s["periods"], d_start, now)
            report["shared"] = {"periods": len(s["periods"]), "voters": s["voters"],
                                "shared": sum(1 for p in s["periods"] if p.get("scope") == "shared"),
                                "upgrade_bins": s.get("upgrade_bins", 0),
                                "seconds": round(time.monotonic() - shared_started, 1)}
        except Exception:  # noqa: BLE001 -- the per-target results stand without it
            log.warning("shared degradation failed", exc_info=True)
            report["shared"] = {"error": True}
            report["errors"] += 1
    if aggregate_pass is not None and s is not None and s["voters"] >= shared.MIN_VOTERS:
        try:
            a = aggregate_pass(s, c_start, d_start, now)
            # An empty stream (the first pass found no cycle with a quorum)
            # says nothing: keep what earlier passes wrote.
            if a["cycles"]:
                replace_shared_congestion(a["periods"], max(c_start, a.get("since") or 0), now)
            report["shared_congestion"] = {
                "periods": len(a["periods"]), "cycles": a["cycles"], "added": a["added"],
                "base_ms": a.get("base_ms"), "dropped": a.get("dropped"),
                "build_seconds": a.get("build_seconds"), "seconds": a.get("seconds")}
        except Exception:  # noqa: BLE001 -- the other results stand without it
            log.warning("shared congestion failed", exc_info=True)
            report["shared_congestion"] = {"error": True}
            report["errors"] += 1
    report["seconds"] = round(time.monotonic() - started, 1)
    if not report["errors"]:
        report["last_ok"] = now
    log.info("inference pass: %d targets in %.0f s, %d failed",
             len(report["targets"]), report["seconds"], report["errors"])
    if selected:
        try:
            record_pass(report, now)
        except Exception:  # noqa: BLE001 -- the history is a convenience
            log.warning("could not record the pass", exc_info=True)
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
    time.sleep(START_DELAY)
    # Operators come from the routing table (CAIDA RouteViews prefix-to-AS),
    # downloaded once a day into the state volume and looked up locally;
    # Team Cymru only for the AS names and what the table has no route for.
    table = asn.use_table(Path(os.environ.get("INFERENCE_STATE_DIR", "/var/lib/inference"))
                          / "routing")
    while True:
        began = time.time()
        try:
            table.refresh()
        except Exception:  # noqa: BLE001 -- the table is an aid; the pass goes on without it
            log.exception("routing table refresh failed")
        report = run_once(shared_pass=shared.run, aggregate_pass=aggregate.run)
        report["interval"] = interval
        status.write(report)
        time.sleep(max(60.0, interval - (time.time() - began)))


if __name__ == "__main__":
    main()
