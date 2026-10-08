"""The last pass, as a file: what the container healthcheck and a person read.

``python status.py`` prints it; ``python status.py --check`` exits 1 when no
pass finished within two intervals and a half (the first pass included),
which is what the Docker healthcheck runs.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def path() -> Path:
    return Path(os.environ.get("INFERENCE_STATE_DIR", "/var/lib/inference")) / "status.json"


def write(report: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=1, sort_keys=True))
    os.replace(tmp, p)


def read() -> dict:
    try:
        return json.loads(path().read_text())
    except (OSError, ValueError):
        return {}


def healthy(report: dict, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    if "idle" in report:
        return True
    interval = float(report.get("interval") or 3600)
    last = report.get("at") or report.get("starting")
    return bool(last) and now - float(last) <= 2.5 * interval


if __name__ == "__main__":
    current = read()
    if "--check" in sys.argv[1:]:
        sys.exit(0 if healthy(current) else 1)
    print(json.dumps(current, indent=1, sort_keys=True))
