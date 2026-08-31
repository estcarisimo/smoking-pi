"""Deterministic alert rules evaluated against InfluxDB.

Each rule returns a list of incident dicts:

    {"rule": ..., "severity": ..., "key": ..., "target": ...,
     "message": ..., "value": ...}

``key`` is the stable dedup key used by state.py. All thresholds are
env-tunable; see the constants below for names and defaults. The rule
functions themselves are pure (rows in, incidents out) so they can be
tested without an InfluxDB — only :func:`evaluate` touches the network.
"""

from __future__ import annotations

import os

import flux

# Thresholds (env-tunable).
# SmokePing probes on a 300 s step, so a 300 s window holds a single point —
# too few for DOWN_MIN_POINTS.
#
# This was 900 s, which is the SHORTEST window that can hold DOWN_MIN_POINTS,
# and that is exactly the problem: 900/300 = 3 points only when the window
# boundary and the export cadence line up perfectly. Any skew yields 2, the
# rule stops matching, and the incident looks resolved. Observed live as a
# clean five-minute cycle — four minutes present, one minute absent, forever.
# 1200 s holds four points where three are required, so one point of slack
# absorbs the jitter. Keep DOWN_WINDOW/300 strictly greater than
# DOWN_MIN_POINTS if you retune either.
DEFAULT_DOWN_WINDOW = 1200  # seconds; DOWN_WINDOW
DEFAULT_HIGH_LOSS_PCT = 20.0  # percent; HIGH_LOSS_PCT
# Exporter-liveness window. Four 300 s steps, so a burst arriving late does not
# read as a stall. See _stale_flux for why 10m was too tight.
DEFAULT_STALE_WINDOW = 1200  # seconds; STALE_WINDOW
# Microcut windows per 60 min needed to fire; MICROCUT_BURST_N.
# This counts OBSERVED windows, so it must track the probe's duty cycle. The
# detector samples a 10 s window every CPE_PROBE_WINDOW + CPE_PROBE_IDLE
# seconds — 30 s by default, so ~120 windows/hour. It used to probe
# back-to-back (~360 windows/hour) where 6 was the threshold; at a third of the
# sample rate the same real event rate yields a third as many observations, so
# 2 keeps the sensitivity equivalent. Raise it if bursts read as noisy, and
# rescale it whenever you change CPE_PROBE_IDLE.
DEFAULT_MICROCUT_BURST_N = 2
# A window counts as a microcut only above this loss percent. CPE gateways
# commonly rate-limit ICMP, so a 5 pps probe sees a constant single-digit loss
# floor (observed: p50 10%, p99 30%) with no outage at all — counting every
# window with any loss would flag that floor forever. A real microcut drops
# most of a 10 s window.
DEFAULT_MICROCUT_LOSS_PCT = 50.0  # percent; MICROCUT_LOSS_PCT

DOWN_MIN_POINTS = 3
DOWN_LOSS_RATIO = 0.999  # >= this ratio counts as "no responses"
HEALTHY_LOSS_RATIO = 0.5  # < this mean ratio counts as "healthy" (ipv6 rule)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _format_window(seconds: int) -> str:
    """Render a window the way it was configured, not rounded down to it.

    The message exists to tell an operator which window was queried, so a
    90s window must not read as "1m" — that sends them looking for a
    discrepancy that isn't there.
    """
    if seconds and seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _query(flux_src: str) -> list[dict]:
    """Indirection over flux.query_influx (patched in tests)."""
    return flux.query_influx(flux_src)


# ---------------------------------------------------------------------------
# Flux sources
# ---------------------------------------------------------------------------

def _down_points_flux(window_s: int) -> str:
    """Raw loss points per target over latency+dns_latency (clamped in py)."""
    return (
        flux.base_flux(["latency", "dns_latency"], f"-{int(window_s)}s")
        + '|> filter(fn: (r) => r._field == "loss") '
        + '|> keep(columns: ["_value", "_time", "target"]) '
    )


def _mean_loss_flux() -> str:
    """Mean clamped loss ratio per target+category over the last 15m."""
    return (
        flux.base_flux(["latency", "dns_latency"], "-15m")
        + '|> filter(fn: (r) => r._field == "loss") '
        + flux.CLAMP_LOSS_RATIO
        + '|> group(columns: ["target", "category"]) '
        + "|> mean()"
    )


def _microcut_flux(loss_pct: float) -> str:
    """Count of microcut cpe windows per target+protocol over the last 60m.

    cpe_latency loss is a percent (0-100), so no ratio clamping applies; a
    window counts only above MICROCUT_LOSS_PCT (see the constant for why
    "any loss at all" is the wrong bar).
    """
    return (
        flux.base_flux(["cpe_latency"], "-60m")
        + '|> filter(fn: (r) => r._field == "loss") '
        + f"|> filter(fn: (r) => r._value > {float(loss_pct)}) "
        + '|> group(columns: ["target", "protocol"]) '
        + "|> count()"
    )


def _stale_flux() -> str:
    """Total latency points written recently (exporter liveness).

    Was 10m, which on a 300 s step is two points at best — and the RRD row
    timestamps lag the write, so the effective margin was near zero and this
    rule flapped in and out on its own. Observed live: three quiet minutes,
    then the incident reappears. Same defect as the old DOWN_WINDOW, so the
    same rule applies: give the window several steps of slack.
    """
    window = _env_int("STALE_WINDOW", DEFAULT_STALE_WINDOW)
    return (
        flux.base_flux(["latency"], f"-{window}s")
        + '|> filter(fn: (r) => r._field == "loss") '
        + "|> group() "
        + "|> count()"
    )


# ---------------------------------------------------------------------------
# Rules (pure)
# ---------------------------------------------------------------------------

def rule_target_down(rows: list[dict], min_points: int = DOWN_MIN_POINTS) -> list[dict]:
    """critical: ALL points in the window are >=99.9% loss, with >=3 points."""
    by_target: dict[str, list[float]] = {}
    for row in rows:
        target = row.get("target")
        value = row.get("_value")
        if target is None or value is None:
            continue
        by_target.setdefault(target, []).append(flux.clamp_loss_ratio(value))

    incidents = []
    for target, losses in sorted(by_target.items()):
        if len(losses) >= min_points and all(v >= DOWN_LOSS_RATIO for v in losses):
            incidents.append(
                {
                    "rule": "target_down",
                    "severity": "critical",
                    "key": f"target_down:{target}",
                    "target": target,
                    "message": (
                        f"{target} down: 100% loss across all "
                        f"{len(losses)} probes in the window"
                    ),
                    "value": 100.0,
                }
            )
    return incidents


def rule_high_loss(
    mean_rows: list[dict],
    exclude: set[str] | None = None,
    threshold_pct: float | None = None,
) -> list[dict]:
    """warning: mean loss over 15m above HIGH_LOSS_PCT (excl. down targets)."""
    if threshold_pct is None:
        threshold_pct = _env_float("HIGH_LOSS_PCT", DEFAULT_HIGH_LOSS_PCT)
    exclude = exclude or set()

    # A target usually has one category row; average if there are several.
    sums: dict[str, list[float]] = {}
    for row in mean_rows:
        target = row.get("target")
        value = row.get("_value")
        if target is None or value is None or target in exclude:
            continue
        sums.setdefault(target, []).append(float(value))

    incidents = []
    for target, values in sorted(sums.items()):
        pct = 100.0 * sum(values) / len(values)
        if pct > threshold_pct:
            incidents.append(
                {
                    "rule": "high_loss",
                    "severity": "warning",
                    "key": f"high_loss:{target}",
                    "target": target,
                    "message": f"{target}: mean loss {pct:.1f}% over 15m",
                    "value": round(pct, 2),
                }
            )
    return incidents


def rule_microcut_burst(
    count_rows: list[dict], burst_n: int | None = None
) -> list[dict]:
    """warning: >= MICROCUT_BURST_N microcut cpe windows in the last 60m."""
    if burst_n is None:
        burst_n = _env_int("MICROCUT_BURST_N", DEFAULT_MICROCUT_BURST_N)
    loss_pct = _env_float("MICROCUT_LOSS_PCT", DEFAULT_MICROCUT_LOSS_PCT)

    incidents = []
    for row in sorted(
        count_rows,
        key=lambda r: (r.get("target") or "", r.get("protocol") or ""),
    ):
        target = row.get("target")
        protocol = row.get("protocol") or "?"
        value = row.get("_value")
        if target is None or value is None:
            continue
        count = int(value)
        if count >= burst_n:
            incidents.append(
                {
                    "rule": "microcut_burst",
                    "severity": "warning",
                    "key": f"microcut_burst:{target}/{protocol}",
                    "target": target,
                    "message": (
                        f"{target} ({protocol}): {count} windows over "
                        f"{loss_pct:g}% loss in the last 60m (microcut burst)"
                    ),
                    "value": count,
                }
            )
    return incidents


def rule_exporter_stale(
    stale_rows: list[dict], window_s: int | None = None
) -> list[dict]:
    """critical: zero ``latency`` points written in ``window_s`` (global).

    ``window_s`` is reported in the message rather than hardcoded, so the
    text cannot drift from the window actually queried the way the old
    literal "10m" did after STALE_WINDOW was introduced.
    """
    total = 0
    for row in stale_rows:
        value = row.get("_value")
        if value is not None:
            total += int(value)
    if total > 0:
        return []
    if window_s is None:
        window_s = _env_int("STALE_WINDOW", DEFAULT_STALE_WINDOW)
    return [
        {
            "rule": "exporter_stale",
            "severity": "critical",
            "key": "exporter_stale",
            "target": None,
            "message": (
                f"no latency points written in the last "
                f"{_format_window(window_s)} — "
                "RRD exporter appears stalled"
            ),
            "value": 0,
        }
    ]


def _is_ipv6_target(target: str, category: str | None) -> bool:
    cat = (category or "").lower()
    return target.endswith("6") or "fping6" in cat or "ipv6" in cat


def rule_ipv6_down(mean_rows: list[dict]) -> list[dict]:
    """warning: all IPv6 targets at 100% loss (15m) while IPv4 is healthy.

    Emits a single aggregate incident so a broken v6 path does not page
    once per target.
    """
    v6_down: list[str] = []
    v6_ok = False
    v4_healthy = False
    seen_v6: set[str] = set()

    for row in mean_rows:
        target = row.get("target")
        value = row.get("_value")
        if target is None or value is None:
            continue
        ratio = float(value)
        if _is_ipv6_target(target, row.get("category")):
            if target in seen_v6:
                continue
            seen_v6.add(target)
            if ratio >= DOWN_LOSS_RATIO:
                v6_down.append(target)
            else:
                v6_ok = True
        elif ratio < HEALTHY_LOSS_RATIO:
            v4_healthy = True

    if v6_down and not v6_ok and v4_healthy:
        return [
            {
                "rule": "ipv6_down",
                "severity": "warning",
                "key": "ipv6_down",
                "target": "ipv6",
                "message": (
                    "IPv6 connectivity appears down: "
                    f"{len(v6_down)} IPv6 target(s) at 100% loss for 15m "
                    "while IPv4 targets are healthy"
                ),
                "value": len(v6_down),
            }
        ]
    return []


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def evaluate_with_context() -> tuple[list[dict], dict]:
    """Run all rules and ALSO return the rows they were derived from.

    The verdict ("is it me or the internet?") needs breadth across every
    target, the CPE microcut counts, and exporter liveness -- which is
    exactly what these four queries already fetch and then throw away.
    Returning them means the verdict costs no additional Flux queries,
    which matters on a Pi that has already hit its thermal limit.
    """
    down_window = _env_int("DOWN_WINDOW", DEFAULT_DOWN_WINDOW)

    down_rows = _query(_down_points_flux(down_window))
    mean_rows = _query(_mean_loss_flux())
    micro_rows = _query(
        _microcut_flux(_env_float("MICROCUT_LOSS_PCT", DEFAULT_MICROCUT_LOSS_PCT))
    )
    stale_rows = _query(_stale_flux())

    incidents = rule_target_down(down_rows)
    down_targets = {i["target"] for i in incidents}
    incidents += rule_high_loss(mean_rows, exclude=down_targets)
    incidents += rule_microcut_burst(micro_rows)
    incidents += rule_exporter_stale(
        stale_rows, _env_int("STALE_WINDOW", DEFAULT_STALE_WINDOW)
    )
    incidents += rule_ipv6_down(mean_rows)
    context = {
        "down_rows": down_rows,
        "mean_rows": mean_rows,
        "micro_rows": micro_rows,
        "stale_rows": stale_rows,
    }
    return incidents, context


def evaluate() -> list[dict]:
    """Run all rules against InfluxDB and return the active incidents."""
    return evaluate_with_context()[0]
