"""Render the example charts the documentation shows, from fixed synthetic
data, with the real renderer (shared/modules/common/charts.py).

    cd shared/modules/alerter && uv run --extra dev python \
        ../../../tools/chart-examples/render.py

Writes docs/img/chart-*.png. Rerun it whenever charts.py changes how a
chart looks, so the documentation shows what Smoking Pi actually sends.
The data is invented (a seeded random walk with one congestion episode),
the drawing is not: no step here touches InfluxDB.
"""

from __future__ import annotations

import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "modules"))
os.environ["TZ"] = "UTC"  # the axis and footer name a zone; keep it stable
time.tzset()

from common import charts  # noqa: E402

OUT = ROOT / "docs" / "img"
# Anchored to the render time, because the footer says when it was drawn
# and the data should end there; the shapes depend only on the offsets.
NOW = datetime.now(timezone.utc).replace(second=0, microsecond=0)
NOW -= timedelta(minutes=NOW.minute % 5)
STEP = timedelta(minutes=5)

# Baseline median latency in seconds, and an episode 3h50m to 2h30m before
# the end: the subject's path congests (latency triples, loss crosses the
# threshold).
BASE = {"Google": 0.012, "Cloudflare": 0.010, "Netflix": 0.018}
EPISODE = (NOW - timedelta(hours=3, minutes=50), NOW - timedelta(hours=2, minutes=30))


def _points(target: str, hours: int):
    rng = random.Random(f"{target}-{hours}")
    times, medians, losses, spreads = [], [], [], []
    t = NOW - timedelta(hours=hours)
    while t <= NOW:
        congested = target == "Google" and EPISODE[0] <= t < EPISODE[1]
        median = BASE[target] * (3.2 if congested else 1.0) + rng.random() * 0.002
        times.append(t)
        medians.append(median)
        if congested:
            losses.append(rng.choice([0.2, 0.3, 0.3, 0.4]))
        else:
            losses.append(0.1 if rng.random() < 0.02 else 0.0)
        jitter = (6 if congested else 2) + rng.random() * 3
        ms = median * 1000
        spreads.append((ms - jitter, ms - jitter / 3, ms + jitter / 2, ms + 3 * jitter))
        t += STEP
    return times, medians, losses, spreads


def _fetch(target, measurement, field, hours):
    times, medians, losses, _ = _points(target, hours)
    return times, (losses if field == "loss" else medians)


def _fetch_spread(target, measurement, hours):
    times, _, _, spreads = _points(target, hours)
    lo, q1, q3, hi = (list(col) for col in zip(*spreads))
    return times, lo, q1, q3, hi


def main() -> int:
    charts._fetch = _fetch
    charts._fetch_spread = _fetch_spread
    peers = ["Cloudflare", "Netflix"]
    first_seen = (EPISODE[0] + timedelta(minutes=15)).timestamp()
    images = {
        "chart-on-request.png": lambda: charts.render_target_chart(
            "Google", "latency", 24, peers),
        "chart-alert.png": lambda: charts.render_incident_chart(
            "Google", "latency", 6, first_seen=first_seen,
            severity="critical", peers=peers),
        "chart-digest.png": lambda: charts.render_digest_chart({
            "window_hours": 24, "targets": [
                {"target": "Google", "avg_loss_pct": 24.2, "p95_ms": 48},
                {"target": "uba_ar", "avg_loss_pct": 6.1, "p95_ms": 182},
                {"target": "Netflix_h3", "avg_loss_pct": 3.4, "p95_ms": 31},
                {"target": "CPE_IPv4", "avg_loss_pct": 1.2, "p95_ms": 4},
                {"target": "Cloudflare", "avg_loss_pct": 0.4, "p95_ms": 14},
            ]}),
    }
    for name, render in images.items():
        png = render()
        if not png:
            print(f"{name}: the renderer returned nothing", file=sys.stderr)
            return 1
        (OUT / name).write_bytes(png)
        print(f"{name}: {len(png)} bytes")
    os.environ["CHART_THEME"] = "dark"
    (OUT / "chart-dark.png").write_bytes(
        charts.render_target_chart("Google", "latency", 24, peers))
    print("chart-dark.png (CHART_THEME=dark)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
