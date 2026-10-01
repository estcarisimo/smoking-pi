"""Intervals of attributed traffic: the last 24 h, as a state file that
config-manager's /budget reads, and as InfluxDB points."""

from __future__ import annotations

import json
import os

INTERVAL = 300
KEEP_SECONDS = 86_400 + 2 * INTERVAL


def add_counts(a: dict[str, tuple[int, int]], b: dict[str, tuple[int, int]]) -> dict:
    """Counters read before a reload, added to the ones read after it."""
    out = dict(a)
    for name, (p, n) in b.items():
        op, on = out.get(name, (0, 0))
        out[name] = (op + p, on + n)
    return out


def record(state: dict, services: dict[str, dict], kinds: dict[str, str],
           now: float, seconds: float) -> tuple[dict, dict]:
    """Append one interval; drop what is older than a day (or stamped in
    the future, after a clock step back)."""
    interval = {"t": int(now), "seconds": round(seconds, 1),
                "services": {name: {"rx": v["rx"], "tx": v["tx"],
                                    "kind": kinds.get(name, "rest")}
                             for name, v in sorted(services.items())}}
    kept = [i for i in state.get("intervals", [])
            if isinstance(i, dict) and -INTERVAL <= now - i.get("t", 0) <= KEEP_SECONDS]
    kept.append(interval)
    return dict(state, intervals=kept, updated=int(now)), interval


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
