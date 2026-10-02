"""Intervals of attributed traffic: the last 24 h, as a state file that
config-manager's /budget reads, and as InfluxDB points.

The same state file keeps the traffic ledger config-manager's /traffic
reads: ``days`` (per local date, the container's TZ: the uplink's bytes
in and out and the Internet-only part, for KEEP_DAYS days) and ``months``
(per local month: each service's bytes, for KEEP_MONTHS months). Services
by month rather than by day: the file is rewritten every five minutes on
an SD card, and a year of daily rows per service would be most of it.
"""

from __future__ import annotations

import json
import os
import time

INTERVAL = 300
KEEP_SECONDS = 86_400 + 2 * INTERVAL
KEEP_DAYS = 400
KEEP_MONTHS = 25


def add_counts(a: dict[str, tuple[int, int]], b: dict[str, tuple[int, int]]) -> dict:
    """Counters read before a reload, added to the ones read after it."""
    out = dict(a)
    for name, (p, n) in b.items():
        op, on = out.get(name, (0, 0))
        out[name] = (op + p, on + n)
    return out


def _add(acc: dict | None, rx: int, tx: int) -> dict:
    acc = acc if isinstance(acc, dict) else {}
    return {"rx": int(acc.get("rx") or 0) + int(rx), "tx": int(acc.get("tx") or 0) + int(tx)}


def _newest(ledger: dict, keep: int) -> dict:
    out = {k: v for k, v in ledger.items() if isinstance(v, dict)}
    for old in sorted(out)[:-keep]:
        del out[old]
    return out


def add_to_ledger(state: dict, interval: dict) -> dict:
    """``days`` and ``months`` with one interval added to the local date
    and month it ends in (an interval straddling midnight counts for the
    day it ends in: at most five minutes on the wrong side)."""
    t = interval["t"]
    day_key = time.strftime("%Y-%m-%d", time.localtime(t))
    month_key = day_key[:7]
    services = interval["services"]
    days = state.get("days") if isinstance(state.get("days"), dict) else {}
    months = state.get("months") if isinstance(state.get("months"), dict) else {}

    day = dict(days.get(day_key) or {})
    total = _add(day, sum(v["rx"] for v in services.values()),
                 sum(v["tx"] for v in services.values()))
    day.update(total)
    day["seconds"] = round(float(day.get("seconds") or 0) + interval["seconds"], 1)
    if "internet" in interval:
        day["internet"] = _add(day.get("internet"), interval["internet"]["rx"],
                               interval["internet"]["tx"])
    month = dict(months.get(month_key) or {})
    month["seconds"] = round(float(month.get("seconds") or 0) + interval["seconds"], 1)
    by_service = dict(month.get("services") or {})
    for name, v in services.items():
        by_service[name] = _add(by_service.get(name), v["rx"], v["tx"])
    month["services"] = by_service
    return dict(state, days=_newest({**days, day_key: day}, KEEP_DAYS),
                months=_newest({**months, month_key: month}, KEEP_MONTHS))


def record(state: dict, services: dict[str, dict], kinds: dict[str, str],
           now: float, seconds: float,
           internet: dict | None = None) -> tuple[dict, dict]:
    """Append one interval; drop what is older than a day (or stamped in
    the future, after a clock step back); add it to the ledger."""
    interval = {"t": int(now), "seconds": round(seconds, 1),
                "services": {name: {"rx": v["rx"], "tx": v["tx"],
                                    "kind": kinds.get(name, "rest")}
                             for name, v in sorted(services.items())}}
    if internet is not None:
        interval["internet"] = {"rx": int(internet["rx"]), "tx": int(internet["tx"])}
    kept = [i for i in state.get("intervals", [])
            if isinstance(i, dict) and -INTERVAL <= now - i.get("t", 0) <= KEEP_SECONDS]
    kept.append(interval)
    state = add_to_ledger(dict(state, intervals=kept, updated=int(now)), interval)
    return state, interval


def mb_per_day(rx: int, tx: int, seconds: float) -> float:
    return round((rx + tx) / (seconds or 1) * 86_400 / 1e6, 3)


def points(interval: dict):
    from influxdb_client import Point, WritePrecision
    out = []
    for name, v in interval["services"].items():
        out.append(Point("service_traffic")
                   .tag("service", name)
                   .tag("kind", v["kind"])
                   .field("rx_bytes", int(v["rx"]))
                   .field("tx_bytes", int(v["tx"]))
                   .field("seconds", float(interval["seconds"]))
                   .field("mb_per_day", mb_per_day(v["rx"], v["tx"], interval["seconds"]))
                   .time(interval["t"], WritePrecision.S))
    if "internet" in interval:
        v = interval["internet"]
        out.append(Point("internet_traffic")
                   .field("rx_bytes", int(v["rx"]))
                   .field("tx_bytes", int(v["tx"]))
                   .field("seconds", float(interval["seconds"]))
                   .field("mb_per_day", mb_per_day(v["rx"], v["tx"], interval["seconds"]))
                   .time(interval["t"], WritePrecision.S))
    return out


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
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, separators=(",", ":"))
    os.replace(tmp, path)
