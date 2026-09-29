#!/usr/bin/env python3
"""Does a host answer a measurement layer at all? Two questions, one exec.

The DNS wizard adopts each service with the whole suite (ICMP, TCP 443,
HTTP/1.1, /2, /3), and many hosts do not serve every layer: ICMP dropped,
no HTTP/3, a CDN fallback or relay name with no web server at all. Such a
layer charts a flat 100% loss forever, which reads as an outage to a person
and to the assistant. config-manager asks this script, inside the SmokePing
container (the probes' own binaries, the RRDs), with one JSON argument:

    {"preflight": [{"name": "W_x_h3", "host": "x.example", "layer": "h3"}],
     "silence": [{"name": "W_x_h3", "rrd": "DNS_Wizard/W_x_h3.rrd"}],
     "window": 86400}

``preflight`` tries each layer once, the way its probe does (fping; a TCP
handshake on 443; curl with the probe's HTTP version, a zero exit status and
that version in the reply). ``silence`` reads each RRD over ``window``
seconds: ``rows`` measured (loss known) and ``answered`` (a median, so at
least one ping came back). Prints one JSON object:

    {"preflight": {"W_x_h3": false}, "silence": {"W_x_h3": {"rows": 288,
     "answered": 0}}, "errors": []}

It decides nothing: the thresholds live in config-manager (wizard_adopt.py).
"""

from __future__ import annotations

import json
import math
import re
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

DATADIR = Path("/data")
FPING = "/usr/sbin/fping"
CURL = "/usr/local/bin/curl-h3"
TIMEOUT = 5  # seconds per attempt: the wizard's HTTP probes use 5 too
WORKERS = 24
# layer -> (curl flag, the http_version curl must report)
HTTP = {"h1": ("--http1.1", "1.1"), "h2": ("--http2", "2"), "h3": ("--http3-only", "3")}
_HOST = re.compile(r"^[A-Za-z0-9.:_-]{1,253}$")

Runner = Callable[[list[str], int], "subprocess.CompletedProcess[str]"]


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def answers(host: str, layer: str, run: Runner = _run,
            connect: Callable[..., Any] = socket.create_connection) -> bool:
    """One try of one layer; False on anything but a clear answer."""
    if not _HOST.match(host) or host.startswith("-"):
        return False
    try:
        if layer == "icmp":
            out = run([FPING, "-c", "3", "-q", "-t", str(TIMEOUT * 200), host], TIMEOUT * 3)
            m = re.search(r"xmt/rcv/%loss = \d+/(\d+)/", out.stderr + out.stdout)
            return bool(m and int(m.group(1)) > 0)
        if layer == "tcp":
            for _ in range(2):
                try:
                    connect((host, 443), timeout=TIMEOUT).close()
                    return True
                except OSError:
                    continue
            return False
        if layer in HTTP:
            flag, version = HTTP[layer]
            out = run([CURL, flag, "-s", "-o", "/dev/null", "-m", str(TIMEOUT),
                       "-w", "%{http_version}", f"https://{host}/"], TIMEOUT * 2)
            return out.returncode == 0 and out.stdout.strip() == version
    except (OSError, subprocess.SubprocessError):
        return False
    return False


def silence(rrd: Path, window: int, run: Runner = _run) -> dict[str, int]:
    """Rows measured and rows answered over the last ``window`` seconds."""
    out = run(["rrdtool", "fetch", str(rrd), "AVERAGE", "-s", f"-{int(window)}"], 30)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"rrdtool exit {out.returncode}")
    lines = out.stdout.splitlines()
    columns = lines[0].split() if lines else []
    if "loss" not in columns or "median" not in columns:
        raise RuntimeError("no loss/median data sources")
    loss_i, median_i = columns.index("loss"), columns.index("median")
    rows = answered = 0
    for line in lines[1:]:
        if ":" not in line:
            continue
        values = line.split(":", 1)[1].split()
        if len(values) != len(columns):
            continue

        def value(i: int) -> float:
            try:
                return float(values[i])
            except ValueError:
                return math.nan

        if math.isnan(value(loss_i)):
            continue  # not measured then (SmokePing down, or before the target)
        rows += 1
        if not math.isnan(value(median_i)):
            answered += 1
    return {"rows": rows, "answered": answered}


def main(argv: list[str], run: Runner = _run) -> dict[str, Any]:
    request = json.loads(argv[0]) if argv else {}
    report: dict[str, Any] = {"preflight": {}, "silence": {}, "errors": []}
    checks = [c for c in request.get("preflight", []) if isinstance(c, dict)]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = pool.map(lambda c: answers(str(c.get("host", "")), str(c.get("layer", "")), run),
                           checks)
        for check, ok in zip(checks, results):
            report["preflight"][str(check.get("name"))] = ok
    window = int(request.get("window", 86400))
    for item in request.get("silence", []):
        name, rel = str(item.get("name")), str(item.get("rrd", ""))
        path = (DATADIR / rel).resolve()
        if DATADIR.resolve() not in path.parents or path.suffix != ".rrd":
            report["errors"].append({"name": name, "error": "not an RRD under the datadir"})
            continue
        if not path.exists():
            continue  # never measured yet: no evidence either way
        try:
            report["silence"][name] = silence(path, window, run)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            report["errors"].append({"name": name, "error": str(exc)[:200]})
    return report


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1:])))
