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
from datetime import datetime, timezone

import flux
from common import cadence, microcuts

# Thresholds (env-tunable).
# Windows below are written for SmokePing's default 300 s step. A probe on a
# slower step gets them stretched to hold the same number of its points
# (see _windows); a faster one simply has more points in them.
#
# A 300 s window holds a single point — too few for DOWN_MIN_POINTS.
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
# microcut_burst fires on any CONFIRMED cut in the last 60 min -- a run of
# two or more consecutive windows above MICROCUT_LOSS_PCT, or one window
# that lost everything -- or on MICROCUT_BURST_N POSSIBLE cuts: isolated
# single windows at 51-99%. It used to count windows, and two isolated
# windows 23 minutes apart were a "burst" (2026-09-07, twice), while the
# floor's tail was never a cut at all. Fourteen days of the reference
# gateway held 17 isolated windows and never three in an hour; a real cut
# (2026-09-19) was six consecutive windows at 100%. See common/microcuts.py.
DEFAULT_MICROCUT_BURST_N = 3
# A window counts as a cut window only above this loss percent. CPE gateways
# commonly rate-limit ICMP, so a 5 pps probe sees a constant loss floor
# (observed: p50 10%, p90 14-22% by day) with no outage at all -- counting
# every window with any loss would flag that floor forever. A real microcut
# drops most of a 10 s window.
DEFAULT_MICROCUT_LOSS_PCT = 50.0  # percent; MICROCUT_LOSS_PCT

DOWN_MIN_POINTS = 3
DOWN_LOSS_RATIO = 0.999  # >= this ratio counts as "no responses"
HEALTHY_LOSS_RATIO = 0.5  # < this mean ratio counts as "healthy" (ipv6 rule)

# high_loss persistence. The rule compares a 15 min MEAN to HIGH_LOSS_PCT, and
# a mean hides how the loss arrived: one probe cycle losing 8 of 10 pings and
# two clean ones average 27%, which cleared 20% and paged as "high loss" --
# eighteen times over, once per target, for a link that blinked for three
# minutes on 2026-09-19. A point only counts toward persistence above
# HIGH_LOSS_POINT_PCT (more than one lost ping of ten), and the rule needs
# HIGH_LOSS_MIN_POINTS of them: loss that lasted, not loss that happened.
DEFAULT_HIGH_LOSS_MIN_POINTS = 2  # HIGH_LOSS_MIN_POINTS
HIGH_LOSS_POINT_PCT = 15.0  # percent; a single lost ping of ten is 10%
# The mean is over 15 min = three steps; persistence is counted over the SAME
# three steps, not the wider down window the raw points come from. Otherwise
# a lossy cycle that has aged out of the mean could still corroborate a
# single new one -- the exact false positive persistence exists to remove.
MEAN_STEPS = 3

# Widespread loss: the same probe cycle lossy on most targets at once. That
# is one event with one cause -- the link, or this host -- and it used to be
# reported once per target: 18 target_down criticals plus 18 high_loss
# warnings, re-sent every cooldown, for the night the Pi's Wi-Fi radio hung
# (2026-09-20: associated at -49 dBm, zero packets received for 3 h 20 min).
# A step counts as widespread when WIDESPREAD_PCT of the targets reporting
# in it lost at least WIDESPREAD_LOSS_PCT; see rule_widespread for what each
# shape becomes.
DEFAULT_WIDESPREAD_PCT = 80.0  # percent of reporting targets; WIDESPREAD_PCT
DEFAULT_WIDESPREAD_LOSS_PCT = 30.0  # percent loss per point; WIDESPREAD_LOSS_PCT
WIDESPREAD_MIN_TARGETS = 3  # below this, breadth means nothing
STEP_S = cadence.DEFAULT_STEP  # the default step; real ones come from cadence
# Points a window must be able to hold for its rule: DOWN_MIN_POINTS plus one
# of slack (see DEFAULT_DOWN_WINDOW), and the four steps of STALE_WINDOW.
DOWN_WINDOW_STEPS = DOWN_MIN_POINTS + 1
STALE_WINDOW_STEPS = 4


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


def _mean_loss_flux(window_s: int = MEAN_STEPS * STEP_S) -> str:
    """Mean clamped loss ratio per target+category over ``window_s``:
    MEAN_STEPS of the slowest target's steps, 15 min on the default one."""
    return (
        flux.base_flux(["latency", "dns_latency"], f"-{int(window_s)}s")
        + '|> filter(fn: (r) => r._field == "loss") '
        + flux.CLAMP_LOSS_RATIO
        + '|> group(columns: ["target", "category"]) '
        + "|> mean()"
    )


def _microcut_flux(loss_pct: float) -> str:
    """Every cpe window above MICROCUT_LOSS_PCT in the last 60m, in time
    order, so the rule can fold them into cuts (common.microcuts).

    cpe_latency loss is a percent (0-100), so no ratio clamping applies; a
    window counts only above the threshold (see the constant for why "any
    loss at all" is the wrong bar).
    """
    return microcuts.cut_windows_flux("-60m", loss_pct)


# A Wi-Fi uplink signal below this counts as weak (see verdict.py for how
# many weak samples it takes). The mcp-server reads the same variable.
DEFAULT_WIFI_WEAK_DBM = -75.0  # WIFI_WEAK_DBM


def _wifi_signal_flux(weak_dbm: float) -> str:
    """Per interface over the last 60m: samples, samples below weak_dbm, and
    the minimum signal -- one reduce instead of three aggregates."""
    return (
        flux.base_flux(["wifi_link"], "-60m")
        + '|> filter(fn: (r) => r._field == "signal_dbm") '
        + '|> group(columns: ["interface"]) '
        + "|> reduce(identity: {n: 0, weak: 0, min: 0.0}, "
        "fn: (r, accumulator) => ({"
        "n: accumulator.n + 1, "
        f"weak: accumulator.weak + (if r._value < {float(weak_dbm)} then 1 else 0), "
        "min: if accumulator.n == 0 or r._value < accumulator.min "
        "then r._value else accumulator.min}))"
    )


def _wifi_drops_flux() -> str:
    """Carrier drops per interface over the last 60m (increase: reboot-safe)."""
    return (
        flux.base_flux(["wifi_link"], "-60m")
        + '|> filter(fn: (r) => r._field == "carrier_down_count") '
        + '|> group(columns: ["interface"]) |> sort(columns: ["_time"]) '
        + "|> increase() |> last()"
    )


def _wifi_rx_flux(window_s: int) -> str:
    """Packets received per interface over the down window (increase).

    Zero here while ``associated`` and ``signal_dbm`` look fine is the
    signature of a hung radio: the firmware still reports the link and still
    transmits the probes, and nothing ever comes back.
    """
    return (
        flux.base_flux(["wifi_link"], f"-{int(window_s)}s")
        + '|> filter(fn: (r) => r._field == "rx_packets") '
        + '|> group(columns: ["interface"]) |> sort(columns: ["_time"]) '
        + "|> increase() |> last()"
    )


def _wifi_uplink_flux() -> str:
    """Whether each wireless interface carries the default route, now."""
    return (
        flux.base_flux(["wifi_link"], "-10m")
        + '|> filter(fn: (r) => r._field == "uplink") '
        + '|> group(columns: ["interface"]) |> last()'
    )


def _stale_flux(window: int | None = None) -> str:
    """Total latency points written recently (exporter liveness).

    Was 10m, which on a 300 s step is two points at best — and the RRD row
    timestamps lag the write, so the effective margin was near zero and this
    rule flapped in and out on its own. Observed live: three quiet minutes,
    then the incident reappears. Same defect as the old DOWN_WINDOW, so the
    same rule applies: give the window several steps of slack.
    """
    if window is None:
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


def _step_of(value: object, step_s: int = STEP_S) -> int | None:
    """A point's probe step as epoch seconds, or None when unparseable.

    The Influx client hands back datetimes; a test or a CSV hands back ISO
    strings. Every target's point for one cycle shares the same RRD-aligned
    timestamp, but bucketing to ``step_s`` costs nothing and holds if it ever
    does not.
    """
    ts = cadence.epoch(value)
    if ts is None:
        return None
    return int(ts // step_s) * step_s


def _lossy_points_by_target(
    points: list[dict],
    min_pct: float,
    steps: int = MEAN_STEPS,
    cadences: dict[str, cadence.Cadence] | None = None,
) -> dict[str, int]:
    """How many raw points per target lost more than ``min_pct``, within the
    latest ``steps`` probe cycles present in ``points`` -- each target's own
    cycles, counted back from the newest point of any target.

    Rows without a usable ``_time`` are counted regardless: a caller with
    untimed rows gets the plain count rather than nothing.
    """
    cadences = cadences or {}
    timed = [
        (_step_of(r.get("_time"), cadence.of(cadences, r.get("target")).step), r)
        for r in points
    ]
    known = [step for step, _ in timed if step is not None]
    newest = max(known) if known else None
    counts: dict[str, int] = {}
    for step, row in timed:
        target = row.get("target")
        value = row.get("_value")
        if target is None or value is None:
            continue
        if step is not None and newest is not None and step < (
            newest - (steps - 1) * cadence.of(cadences, target).step
        ):
            continue
        if flux.clamp_loss_ratio(value) * 100.0 >= min_pct:
            counts[target] = counts.get(target, 0) + 1
    return counts


def rule_high_loss(
    mean_rows: list[dict],
    exclude: set[str] | None = None,
    threshold_pct: float | None = None,
    points: list[dict] | None = None,
    min_points: int | None = None,
    cadences: dict[str, cadence.Cadence] | None = None,
    window_s: int = MEAN_STEPS * STEP_S,
) -> list[dict]:
    """warning: mean loss over ``window_s`` (15m on the default step) above
    HIGH_LOSS_PCT (excl. down targets).

    With ``points`` (the raw down-window rows) the loss must also have
    PERSISTED: at least ``min_points`` points above HIGH_LOSS_POINT_PCT in
    the latest MEAN_STEPS cycles -- the same span as the mean. A single bad
    probe cycle can push a 15 min mean over the threshold on its own, and
    one cycle is a blink, not high loss. Without ``points`` the rule is the
    bare mean comparison, for callers that have no raw rows.
    """
    if threshold_pct is None:
        threshold_pct = _env_float("HIGH_LOSS_PCT", DEFAULT_HIGH_LOSS_PCT)
    if min_points is None:
        min_points = _env_int("HIGH_LOSS_MIN_POINTS", DEFAULT_HIGH_LOSS_MIN_POINTS)
    exclude = exclude or set()
    persisted = (
        _lossy_points_by_target(points, HIGH_LOSS_POINT_PCT, cadences=cadences)
        if points is not None
        else None
    )

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
        if pct > threshold_pct and (
            persisted is None or persisted.get(target, 0) >= min_points
        ):
            incidents.append(
                {
                    "rule": "high_loss",
                    "severity": "warning",
                    "key": f"high_loss:{target}",
                    "target": target,
                    "message": (
                        f"{target}: mean loss {pct:.1f}% over "
                        f"{_format_window(window_s)}"
                    ),
                    "value": round(pct, 2),
                }
            )
    return incidents


def microcut_rows(cuts: list[dict], window_rows: list[dict]) -> list[dict]:
    """Per target+protocol: cut windows, confirmed and possible cuts -- the
    shape the verdict reads (``_value`` = cut windows, as before)."""
    keys = {(r.get("target"), r.get("protocol")) for r in window_rows}
    rows = []
    for target, protocol in sorted(keys, key=lambda k: (k[0] or "", k[1] or "")):
        own = [c for c in cuts if c["target"] == target and c["protocol"] == protocol]
        rows.append(
            {
                "target": target,
                "protocol": protocol,
                "_value": sum(c["windows"] for c in own),
                "cuts": sum(1 for c in own if c["confirmed"]),
                "possible": sum(1 for c in own if not c["confirmed"]),
            }
        )
    return rows


def rule_microcut_burst(
    window_rows: list[dict], burst_n: int | None = None
) -> list[dict]:
    """warning: a confirmed cut, or MICROCUT_BURST_N possible ones, in 60m.

    ``window_rows`` are the raw cut windows from :func:`_microcut_flux`;
    they are folded into cuts here (common.microcuts.fold_cuts), so the
    message can say "1 cut of 2 min 40 s (6 windows, all at 100%)" rather
    than "6 windows over 50%".
    """
    if burst_n is None:
        burst_n = _env_int("MICROCUT_BURST_N", DEFAULT_MICROCUT_BURST_N)
    loss_pct = _env_float("MICROCUT_LOSS_PCT", DEFAULT_MICROCUT_LOSS_PCT)

    cuts = microcuts.fold_cuts(window_rows)
    incidents = []
    for row in microcut_rows(cuts, window_rows):
        target, protocol = row["target"], row["protocol"] or "?"
        if target is None:
            continue
        if row["cuts"] < 1 and row["possible"] < burst_n:
            continue
        own = [c for c in cuts if c["target"] == target and c["protocol"] == row["protocol"]]
        incidents.append(
            {
                "rule": "microcut_burst",
                "severity": "warning",
                "key": f"microcut_burst:{target}/{protocol}",
                "target": target,
                "message": (
                    f"{target} ({protocol}): {microcuts.describe_cuts(own)} "
                    f"over {loss_pct:g}% loss in the last 60m"
                ),
                "value": row["_value"],
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


def _hhmm(step: int) -> str:
    return datetime.fromtimestamp(step, tz=timezone.utc).strftime("%H:%M UTC")


def rule_widespread(
    rows: list[dict],
    min_points: int = DOWN_MIN_POINTS,
    share_pct: float | None = None,
    loss_pct: float | None = None,
    min_targets: int = WIDESPREAD_MIN_TARGETS,
    step_s: int = STEP_S,
) -> list[dict]:
    """One incident for a loss that hit most targets in the same probe cycle.

    Points are bucketed to ``step_s`` -- the slowest target's step, so every
    target has a point in every bucket. A faster target's points in one
    bucket are averaged: it counts as lossy, or as lost, for that cycle when
    its mean is, never on one point of several.

    Per step in the down window, the share of reporting targets that lost
    at least WIDESPREAD_LOSS_PCT, and the share that lost everything. Then:

    - ``uplink_down`` (critical): the latest ``min_points`` steps ALL have
      WIDESPREAD_PCT of targets at 100%. Every destination including the
      first hop is unreachable from this host -- the monitor's own uplink is
      gone (a hung radio, a dropped association, an unplugged cable) and
      nothing beyond it can be judged. Persists while that holds; the
      verdict names the Wi-Fi radio when wifi_link shows it hung.
    - ``outage`` (warning, transient): otherwise, a run of steps where
      WIDESPREAD_PCT of targets were lossy. A brief cut of the link, keyed by
      the run's first step so a second cut is a second incident. Transient:
      it leaves the window on its own and there is nothing to "recover".

    Either one is the whole story; the per-target incidents it would have
    fanned out into are dropped by :func:`suppress_widespread`.
    """
    if share_pct is None:
        share_pct = _env_float("WIDESPREAD_PCT", DEFAULT_WIDESPREAD_PCT)
    if loss_pct is None:
        loss_pct = _env_float("WIDESPREAD_LOSS_PCT", DEFAULT_WIDESPREAD_LOSS_PCT)

    ratios: dict[tuple[int, str], list[float]] = {}
    for row in rows:
        target = row.get("target")
        value = row.get("_value")
        step = _step_of(row.get("_time"), step_s)
        if target is None or value is None or step is None:
            continue
        ratios.setdefault((step, target), []).append(flux.clamp_loss_ratio(value))

    reporting: dict[int, set[str]] = {}
    lossy: dict[int, set[str]] = {}
    lost: dict[int, set[str]] = {}
    for (step, target), values in ratios.items():
        ratio = sum(values) / len(values)
        reporting.setdefault(step, set()).add(target)
        if ratio * 100.0 >= loss_pct:
            lossy.setdefault(step, set()).add(target)
        if ratio >= DOWN_LOSS_RATIO:
            lost.setdefault(step, set()).add(target)

    steps = sorted(s for s, targets in reporting.items() if len(targets) >= min_targets)
    if not steps:
        return []

    def share(bucket: dict[int, set[str]], step: int) -> float:
        return 100.0 * len(bucket.get(step, ())) / len(reporting[step])

    # Adjacent in the list is not adjacent in time: a cycle in which too few
    # targets reported is simply absent from ``steps``. "Consecutive" and
    # "the same span" mean gaps of at most one missing cycle.
    def contiguous(run: list[int]) -> bool:
        return all(b - a <= 2 * step_s for a, b in zip(run, run[1:]))

    latest = steps[-min_points:]
    if (
        len(latest) >= min_points
        and contiguous(latest)
        and all(share(lost, s) >= share_pct for s in latest)
    ):
        step = latest[-1]
        n_lost, n_all = len(lost[step]), len(reporting[step])
        return [
            {
                "rule": "uplink_down",
                "severity": "critical",
                "key": "uplink_down",
                "target": None,
                "message": (
                    f"{n_lost} of {n_all} targets at 100% loss for "
                    f"{len(latest)} consecutive probe cycles — this host's "
                    "uplink is down; nothing beyond it can be judged"
                ),
                "value": round(share(lost, step), 1),
            }
        ]

    incidents = []
    run: list[int] = []
    for step in steps + [None]:
        if (
            step is not None
            and share(lossy, step) >= share_pct
            and (not run or step - run[-1] <= 2 * step_s)
        ):
            run.append(step)
            continue
        if run:
            first, last = run[0], run[-1]
            n_lossy = max(len(lossy[s]) for s in run)
            n_all = max(len(reporting[s]) for s in run)
            # Edge cycles of a cut are partial by construction (it started
            # or ended mid-cycle); total loss everywhere else is total loss.
            lost_steps = sum(1 for s in run if share(lost, s) >= share_pct)
            all_lost = (
                lost_steps >= len(run) - 2 if len(run) > 2 else lost_steps == len(run)
            )
            minutes = (last - first) // 60 + step_s // 60
            incidents.append(
                {
                    "rule": "outage",
                    "severity": "warning",
                    "key": f"outage:{first}",
                    "target": None,
                    "transient": True,
                    "message": (
                        f"{n_lossy} of {n_all} targets "
                        f"{'lost every packet' if all_lost else 'lost packets'} "
                        f"in the same {minutes}-minute span at {_hhmm(first)} — "
                        "a brief cut of the link, not a site problem"
                    ),
                    "value": n_lossy,
                }
            )
            run = []
        if step is not None and share(lossy, step) >= share_pct:
            run = [step]
    return incidents


def suppress_widespread(incidents: list[dict], widespread: list[dict]) -> list[dict]:
    """Drop the per-target incidents a widespread one already explains.

    ``target_down`` and ``high_loss`` go whenever anything widespread is
    active: their windows overlap the event, and after the uplink returns
    every target's 15 min mean stays over the threshold for ten more minutes,
    which would page eighteen "high loss" warnings as the good news arrives.
    ``microcut_burst`` goes only under ``uplink_down`` -- the first hop at
    100% for hours is the same fact, and "99 windows over 50%" says nothing
    the critical did not. Under a brief ``outage`` it stays: that the first
    hop was cutting too is the evidence the verdict line reads.
    """
    if not widespread:
        return incidents
    uplink_down = any(i.get("rule") == "uplink_down" for i in widespread)
    dropped = {"target_down", "high_loss"}
    if uplink_down:
        dropped.add("microcut_burst")
    return [i for i in incidents if i.get("rule") not in dropped]


def _is_ipv6_target(target: str, category: str | None) -> bool:
    cat = (category or "").lower()
    return target.endswith("6") or "fping6" in cat or "ipv6" in cat


def rule_ipv6_down(
    mean_rows: list[dict], window_s: int = MEAN_STEPS * STEP_S
) -> list[dict]:
    """warning: all IPv6 targets at 100% loss over the mean window (15m on
    the default step) while IPv4 is healthy.

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
                    f"{len(v6_down)} IPv6 target(s) at 100% loss for "
                    f"{_format_window(window_s)} "
                    "while IPv4 targets are healthy"
                ),
                "value": len(v6_down),
            }
        ]
    return []


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _windows(cadences: dict[str, cadence.Cadence]) -> dict[str, int]:
    """The rule windows for these cadences, in seconds.

    DOWN_WINDOW and STALE_WINDOW are honored as configured, but never
    shorter than the slowest target needs: a 1200 s down window holds two
    points of a 600 s probe, where DOWN_MIN_POINTS are required, and
    target_down could never fire. On the default step nothing changes.
    """
    longest = cadence.longest_step(cadences)
    return {
        "down": max(
            _env_int("DOWN_WINDOW", DEFAULT_DOWN_WINDOW), DOWN_WINDOW_STEPS * longest
        ),
        "stale": max(
            _env_int("STALE_WINDOW", DEFAULT_STALE_WINDOW),
            STALE_WINDOW_STEPS * longest,
        ),
        "mean": MEAN_STEPS * longest,
        "step": longest,
    }


def evaluate_with_context() -> tuple[list[dict], dict]:
    """Run all rules and ALSO return the rows they were derived from.

    The verdict ("is it me or the internet?") needs breadth across every
    target, the CPE microcut counts, and exporter liveness -- which is
    exactly what these four queries already fetch and then throw away.
    Returning them means the verdict costs no additional Flux queries,
    which matters on a Pi that has already hit its thermal limit.

    The one addition is the Wi-Fi uplink: four aggregate queries over the
    small wifi_link measurement (a reduce, two increases and a last), which
    return nothing at all on a wired host.
    """
    # The cadence refines the windows; a failed query must not silence
    # every rule for the cycle, so it falls back to the default step.
    try:
        cadences = cadence.by_target(_query(cadence.cadence_flux()))
    except Exception:  # influx client raises many exception types
        cadences = {}
    windows = _windows(cadences)
    down_window = windows["down"]

    down_rows = _query(_down_points_flux(down_window))
    mean_rows = _query(_mean_loss_flux(windows["mean"]))
    micro_rows = _query(
        _microcut_flux(_env_float("MICROCUT_LOSS_PCT", DEFAULT_MICROCUT_LOSS_PCT))
    )
    stale_rows = _query(_stale_flux(windows["stale"]))
    wifi_rows = {
        "signal": _query(_wifi_signal_flux(
            _env_float("WIFI_WEAK_DBM", DEFAULT_WIFI_WEAK_DBM))),
        "drops": _query(_wifi_drops_flux()),
        "uplink": _query(_wifi_uplink_flux()),
        "rx": _query(_wifi_rx_flux(down_window)),
    }

    incidents = rule_target_down(down_rows)
    down_targets = {i["target"] for i in incidents}
    incidents += rule_high_loss(
        mean_rows,
        exclude=down_targets,
        points=down_rows,
        cadences=cadences,
        window_s=windows["mean"],
    )
    incidents += rule_microcut_burst(micro_rows)
    # The verdict reads the folded shape, not the raw windows.
    micro_rows = microcut_rows(microcuts.fold_cuts(micro_rows), micro_rows)
    widespread = rule_widespread(down_rows, step_s=windows["step"])
    incidents = widespread + suppress_widespread(incidents, widespread)
    incidents += rule_exporter_stale(stale_rows, windows["stale"])
    incidents += rule_ipv6_down(mean_rows, windows["mean"])
    context = {
        "down_rows": down_rows,
        "mean_rows": mean_rows,
        "micro_rows": micro_rows,
        "stale_rows": stale_rows,
        "wifi_rows": wifi_rows,
        "windows": windows,
    }
    return incidents, context


def evaluate() -> list[dict]:
    """Run all rules against InfluxDB and return the active incidents."""
    return evaluate_with_context()[0]
