"""Render the charts: the one that ships with an alert or a digest, and the
one a person asks for to share.

Lives in ``common`` because two containers draw them: the alerter attaches a
chart to an incident, and the MCP server renders one on request (``get_chart``).

Why not Grafana's image renderer: it is a headless Chromium, ~400 MB resident,
seconds of CPU per render, on a Pi that has already hit its soft thermal limit
and frequency-capped. matplotlib draws the same data in about a second with a
one-shot process.

Rule zero: **never raise, and never delay the alert.** Every entry point is
wrapped and returns None on any failure, and matplotlib is imported INSIDE the
functions so an image built without it degrades to text rather than
crash-looping. The picture is an enhancement; the alert is the product.

Design follows the project's data-viz method:

- **No dual axis.** Latency and loss have different scales, so they get two
  stacked panels sharing an x-axis, never twin y-axes.
- **Emphasis, not categorical.** The story is one target, so the subject wears
  a status colour and its peers recede to a muted neutral. Eight hues here
  would bury the point.
- **The loss axis is pinned 0-100.** Autoscaling it makes 4% loss look like a
  catastrophe, and that axis is the one a reader interprets absolutely.
- **Status colour only where it means status.** Digest bars are one hue;
  bars over the alert threshold take the status colour, and every bar carries
  its value as text, so colour never carries meaning alone.

Colours are the project palette's dark chrome and status steps, validated
against the dark surface (critical 3.62:1, warning 9.49:1, muted 4.85:1).
"""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timezone

from common import tsdb

log = logging.getLogger("charts")

# --- palette: dark surface, validated steps -------------------------------
SURFACE = "#1a1a19"
INK = "#ffffff"
MUTED = "#898781"
GRID = "#2c2c2a"
SPINE = "#383835"
SERIES = "#3987e5"  # categorical slot 1 (dark)
STATUS = {"critical": "#d03b3b", "warning": "#fab219", "info": "#3987e5"}
PEER = "#898781"  # muted, drawn at low alpha so it recedes

LIGHT = {
    "SURFACE": "#fcfcfb", "INK": "#0b0b0b", "MUTED": "#898781",
    "GRID": "#e1e0d9", "SPINE": "#c3c2b7", "SERIES": "#2a78d6",
}

DPI = 140
MAX_PEERS = 4
DEFAULT_MAX_BYTES = 700_000  # ~933 KB base64, inside the 2 MB invoke cap


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _theme() -> dict:
    """Static images cannot follow the reader's theme, so one is chosen."""
    if (os.environ.get("CHART_THEME") or "dark").strip().lower() == "light":
        return LIGHT
    return {
        "SURFACE": SURFACE, "INK": INK, "MUTED": MUTED,
        "GRID": GRID, "SPINE": SPINE, "SERIES": SERIES,
    }


def _style(fig, axes, theme):
    fig.patch.set_facecolor(theme["SURFACE"])
    for ax in axes:
        ax.set_facecolor(theme["SURFACE"])
        # Solid hairlines only: dashed grid reads as "threshold" when it is
        # just a grid.
        ax.grid(True, color=theme["GRID"], linewidth=0.6, linestyle="-")
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(theme["SPINE"])
            ax.spines[side].set_linewidth(0.8)
        ax.tick_params(colors=theme["MUTED"], labelsize=8, length=0)


def _series_flux(target: str, measurement: str, field: str, hours: int) -> str:
    every = max(1, hours * 60 // 120)  # ~120 points whatever the window
    return (
        tsdb.base_flux([measurement], f"-{int(hours)}h")
        + f"|> filter(fn: (r) => r.target == {tsdb.flux_str(target)}) "
        + f'|> filter(fn: (r) => r._field == {tsdb.flux_str(field)}) '
        + f"|> aggregateWindow(every: {every}m, fn: mean, createEmpty: false) "
        + '|> keep(columns: ["_time", "_value"]) '
        + '|> sort(columns: ["_time"])'
    )


def _fetch(target: str, measurement: str, field: str, hours: int):
    rows = tsdb.query_influx(_series_flux(target, measurement, field, hours))
    times, values = [], []
    for row in rows:
        when, value = row.get("_time"), row.get("_value")
        if when is None or value is None:
            continue
        times.append(when if isinstance(when, datetime) else None)
        values.append(float(value))
    # InfluxDB returns UTC-aware datetimes. Convert to the local zone here so
    # the plotted instants match the footer that names that zone; matplotlib
    # ignores each datetime's own tzinfo when formatting the axis, so the
    # locator and formatter are given the zone explicitly too (see the tz
    # comment further down -- both halves are needed). An hour's silent offset
    # is exactly what makes someone mis-correlate an incident with what they
    # were doing at the time.
    pairs = [(t.astimezone(), v) for t, v in zip(times, values) if t is not None]
    return [p[0] for p in pairs], [p[1] for p in pairs]


PING_FIELD_RE = r"/^ping[0-9]+$/"


def _spread_flux(target: str, measurement: str, hours: int) -> str:
    every = max(1, hours * 60 // 120)
    return (
        tsdb.base_flux([measurement], f"-{int(hours)}h")
        + f"|> filter(fn: (r) => r.target == {tsdb.flux_str(target)}) "
        + f"|> filter(fn: (r) => r._field =~ {PING_FIELD_RE}) "
        + f"|> aggregateWindow(every: {every}m, fn: mean, createEmpty: false) "
        + '|> pivot(rowKey: ["_time"], columnKey: ["_field"], '
        'valueColumn: "_value") '
        + '|> sort(columns: ["_time"])'
    )


def _fetch_spread(target: str, measurement: str, hours: int):
    """Per-window min, inner quartiles and max of the individual pings.

    SmokePing keeps every ping of a cycle (``ping1..pingN``), which is what
    its own graphs draw as "smoke" around the median. The band is what makes
    a jittery-but-alive link look different from a clean one at the same
    median, so it is worth a second query. Each row is sorted here rather
    than trusting the RRD's ordering; lost pings are NaN and simply drop out.

    Best effort: the band is decoration on the median line, and a failed
    query for it must not cost the chart -- so this returns empty on error.
    """
    try:
        rows = tsdb.query_influx(_spread_flux(target, measurement, hours))
    except Exception:  # noqa: BLE001
        log.warning("Spread query failed for %s; drawing without the band",
                    target, exc_info=True)
        return [], [], [], [], []
    times, lo, q1, q3, hi = [], [], [], [], []
    for row in rows:
        when = row.get("_time")
        if not isinstance(when, datetime):
            continue
        vals = sorted(
            float(v) for k, v in row.items()
            if k.startswith("ping") and k[4:].isdigit()
            and isinstance(v, (int, float)) and not math.isnan(v)
        )
        if len(vals) < 2:
            continue
        n = len(vals)
        times.append(when.astimezone())
        lo.append(vals[0])
        q1.append(vals[n // 4])
        q3.append(vals[(3 * n) // 4])
        hi.append(vals[-1])
    return (times, _to_ms(lo, measurement), _to_ms(q1, measurement),
            _to_ms(q3, measurement), _to_ms(hi, measurement))


def _minor_grid(ax, theme):
    """Major + minor grid, so a reader can put a number on a point.

    The digest bar chart deliberately does not get this: minor ticks on a
    categorical axis land between the bars and mean nothing.
    """
    from matplotlib.ticker import AutoMinorLocator

    ax.yaxis.set_minor_locator(AutoMinorLocator())
    ax.xaxis.set_minor_locator(AutoMinorLocator())
    ax.grid(True, which="major", color=theme["GRID"], linewidth=0.6,
            linestyle="-")
    ax.grid(True, which="minor", color=theme["GRID"], linewidth=0.3,
            linestyle="-", alpha=0.6)
    ax.tick_params(which="minor", length=0)


def _to_ms(values: list[float], measurement: str = "latency") -> list[float]:
    """latency/dns_latency store seconds; cpe_latency is already in ms.

    Same split as :func:`_loss_pct`. Scaling cpe_latency by 1000 drew the
    microcut chart's 7 ms gateway as 7000 ms -- visibly wrong, but only on a
    chart nobody had compared to the dashboard.
    """
    if measurement == "cpe_latency":
        return [float(v) for v in values]
    return [v * 1000.0 for v in values]


def _loss_pct(values: list[float], measurement: str) -> list[float]:
    """latency/dns_latency loss is a 0-1 ratio; cpe_latency is already 0-100."""
    if measurement == "cpe_latency":
        return [min(100.0, max(0.0, v)) for v in values]
    return [tsdb.clamp_loss_ratio(v) * 100.0 for v in values]


def _save(fig, max_bytes: int) -> bytes | None:
    import io

    for dpi in (DPI, 100):
        buf = io.BytesIO()
        # facecolor is passed explicitly to pin the behaviour. Modern
        # matplotlib defaults rcParams["savefig.facecolor"] to "auto" (use
        # the figure's own colour), but that default was "w" before 2.0 and
        # is a global anyone can set -- and if it is ever white, a dark chart
        # ships with a white border around it. Cheap to pin, invisible to
        # debug if it regresses.
        fig.savefig(
            buf, format="png", dpi=dpi,
            facecolor=fig.get_facecolor(), bbox_inches="tight", pad_inches=0.25,
        )
        data = buf.getvalue()
        if len(data) <= max_bytes:
            return data
    log.warning("Chart exceeded %d bytes even at reduced dpi; sending text only",
                max_bytes)
    return None


def render_incident_chart(
    target: str,
    measurement: str = "latency",
    hours: int = 6,
    first_seen: float | None = None,
    severity: str = "warning",
    peers: list[str] | None = None,
) -> bytes | None:
    """Latency over loss for one target, with its peers as context."""
    try:
        return _render_series_chart(
            target, measurement, hours, peers or [],
            accent=STATUS.get(severity, STATUS["warning"]),
            first_seen=first_seen,
        )
    except Exception:  # noqa: BLE001 - a chart must never cost the alert
        log.warning("Chart render failed for %s; sending text only",
                    target, exc_info=True)
        return None


def render_target_chart(
    target: str,
    measurement: str = "latency",
    hours: int = 24,
    peers: list[str] | None = None,
) -> bytes | None:
    """The same latency-over-loss picture, drawn to be handed to someone.

    This is the on-request chart (MCP ``get_chart``), not an alert: there is
    no incident, so there is no "alert" marker and the series wears the
    ordinary series colour rather than a status one. A status colour on a
    chart with no status would tell the reader something is wrong when the
    whole point of asking may have been to show that nothing is.

    The footer names the source, because this image is meant to leave the
    chat it was requested in -- forwarded to a friend or an ISP -- and once
    it does, nothing else says where it came from.
    """
    try:
        return _render_series_chart(
            target, measurement, hours, peers or [],
            accent=_theme()["SERIES"], first_seen=None, footer="smokeping",
        )
    except Exception:  # noqa: BLE001
        log.warning("Chart render failed for %s", target, exc_info=True)
        return None


def _render_series_chart(
    target, measurement, hours, peers, *, accent, first_seen, footer=None,
):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    theme = _theme()

    times, medians = _fetch(target, measurement, "median", hours)
    loss_times, losses = _fetch(target, measurement, "loss", hours)
    if not times and not loss_times:
        log.info("No chart for %s: no points in the last %dh", target, hours)
        return None

    fig, (ax_lat, ax_loss) = plt.subplots(
        2, 1, sharex=True, figsize=(8, 4.5), dpi=DPI,
        height_ratios=[2, 1], layout="constrained",
    )
    _style(fig, (ax_lat, ax_loss), theme)
    for ax in (ax_lat, ax_loss):
        _minor_grid(ax, theme)

    # The "smoke": spread of the individual pings, drawn under everything so
    # the median line stays the subject. Two bands -- outer min-max, inner
    # quartiles -- read as density without a colour scale.
    s_times, s_lo, s_q1, s_q3, s_hi = _fetch_spread(target, measurement, hours)
    drew_spread = bool(s_times)
    if drew_spread:
        ax_lat.fill_between(s_times, s_lo, s_hi, color=accent, alpha=0.12,
                            linewidth=0, zorder=1)
        ax_lat.fill_between(s_times, s_q1, s_q3, color=accent, alpha=0.22,
                            linewidth=0, zorder=1)

    # Peers first, so the subject draws over them.
    drew_peer = False
    for peer in peers[:MAX_PEERS]:
        p_times, p_vals = _fetch(peer, measurement, "median", hours)
        if p_times:
            ax_lat.plot(p_times, _to_ms(p_vals, measurement), color=PEER,
                        alpha=0.45, linewidth=1.0, zorder=2)
            drew_peer = True

    if times:
        ms = _to_ms(medians, measurement)
        ax_lat.plot(times, ms, color=accent, linewidth=2.0, zorder=3,
                    label=target)
        # Direct-label the last point only -- a number on every point is chaos.
        ax_lat.annotate(
            f"{ms[-1]:.0f} ms", (times[-1], ms[-1]), textcoords="offset points",
            xytext=(6, 0), va="center", color=accent, fontsize=8.5,
        )
    ax_lat.set_ylabel("median latency (ms)", color=theme["MUTED"], fontsize=8.5)
    ax_lat.set_title(
        f"{target} — last {hours}h", color=theme["INK"], fontsize=11,
        loc="left", pad=10,
    )

    if loss_times:
        pct = _loss_pct(losses, measurement)
        ax_loss.plot(loss_times, pct, color=accent, linewidth=1.6, zorder=3)
        ax_loss.fill_between(loss_times, pct, 0, color=accent, alpha=0.25,
                             linewidth=0, zorder=2)
        # An all-zero panel is a large empty box. Say what it means instead --
        # "no loss" is real information; an empty rectangle is not.
        if max(pct) <= 0.0:
            ax_loss.annotate(
                "no loss in this window", (0.5, 0.5),
                xycoords="axes fraction", ha="center", va="center",
                color=theme["MUTED"], fontsize=9,
            )
    # Pinned: an autoscaled loss axis makes 4% look catastrophic.
    ax_loss.set_ylim(0, 100)
    ax_loss.set_yticks([0, 50, 100])
    ax_loss.set_ylabel("loss (%)", color=theme["MUTED"], fontsize=8.5)

    if first_seen:
        # Local, to match the axis the series are drawn on.
        when = datetime.fromtimestamp(first_seen, tz=timezone.utc).astimezone()
        for ax in (ax_lat, ax_loss):
            ax.axvline(when, color=theme["MUTED"], linewidth=1.0, zorder=4)
        ax_lat.annotate(
            "alert", (when, ax_lat.get_ylim()[1]), textcoords="offset points",
            xytext=(4, -10), color=theme["MUTED"], fontsize=8,
        )

    if drew_peer or drew_spread:
        from matplotlib.patches import Patch

        handles = [
            plt.Line2D([], [], color=accent, linewidth=2.0,
                       label=f"{target} (median)"),
        ]
        if drew_spread:
            handles.append(Patch(facecolor=accent, alpha=0.25, linewidth=0,
                                 label="spread of individual pings"))
        if drew_peer:
            handles.append(plt.Line2D([], [], color=PEER, alpha=0.45,
                                      linewidth=1.0,
                                      label="peers in the same category"))
        legend = ax_lat.legend(
            handles=handles, loc="upper left", frameon=False, fontsize=8,
        )
        for text in legend.get_texts():
            text.set_color(theme["MUTED"])

    # matplotlib formats dates with rcParams["timezone"] (UTC) regardless of
    # each datetime's own tzinfo, so the locator AND the formatter both need
    # the zone -- otherwise the axis renders UTC under a footer naming the
    # local zone, and the two disagree by an hour without saying so.
    stamp = datetime.now().astimezone()
    local_tz = stamp.tzinfo
    ax_loss.xaxis.set_major_locator(mdates.AutoDateLocator(tz=local_tz))
    ax_loss.xaxis.set_major_formatter(
        mdates.DateFormatter("%H:%M", tz=local_tz)
    )
    # A 24h+ window crosses midnight, so bare "%H:%M" ticks would repeat
    # without saying which day; add the date once the window is long enough
    # for that to matter.
    if hours > 24:
        ax_loss.xaxis.set_major_formatter(
            mdates.DateFormatter("%d %b %H:%M", tz=local_tz)
        )
    when = stamp.strftime("%a %d %b %Y %H:%M %Z")
    fig.supxlabel(
        f"{footer} · generated {when}" if footer else
        stamp.strftime("%a %d %b %Y · times %Z"),
        color=theme["MUTED"], fontsize=7.5,
    )
    data = _save(fig, _env_int("CHART_MAX_BYTES", DEFAULT_MAX_BYTES))
    plt.close(fig)
    return data


def render_digest_chart(payload: dict) -> bytes | None:
    """Worst targets by mean loss, as a horizontal bar chart."""
    try:
        return _render_digest_chart(payload)
    except Exception:  # noqa: BLE001
        log.warning("Digest chart render failed; sending text only",
                    exc_info=True)
        return None


def _render_digest_chart(payload: dict, threshold_pct: float | None = None):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if threshold_pct is None:
        threshold_pct = float(os.environ.get("HIGH_LOSS_PCT", "") or 20.0)

    targets = [t for t in payload.get("targets", []) if t.get("target")]
    targets.sort(key=lambda t: t.get("avg_loss_pct", 0.0), reverse=True)
    top = targets[:8]
    if not top:
        return None
    # A bar chart of zeros is worse than no chart; the caption says "all clear".
    if all(t.get("avg_loss_pct", 0.0) <= 0.0 for t in top):
        log.info("No digest chart: every target at 0%% loss")
        return None

    theme = _theme()
    names = [t["target"] for t in top][::-1]
    values = [float(t.get("avg_loss_pct", 0.0)) for t in top][::-1]
    p95s = [t.get("p95_ms") for t in top][::-1]

    fig, ax = plt.subplots(
        figsize=(8, 0.34 * len(top) + 1.6), dpi=DPI, layout="constrained"
    )
    _style(fig, (ax,), theme)
    ax.grid(axis="y", visible=False)

    # ONE hue for every bar. Colouring each bar darker-where-bigger would
    # double-encode bar length as hue on nominal categories, and a wall of
    # saturated full-width blocks reads loud besides -- saturated fills belong
    # on small marks and accents, not large blocks.
    #
    # Status still has to be visible, so it rides on the LABEL: over-threshold
    # rows get a marker plus a status-coloured value. That satisfies the status
    # rule properly (icon + label, never colour alone) and keeps discriminating
    # when, as here, every target happens to be over the line.
    # height < 1 leaves a surface gap between bars instead of a border.
    ax.barh(names, values, color=theme["SERIES"], height=0.58, zorder=3)

    span = max(values) or 1.0
    for index, (value, p95) in enumerate(zip(values, p95s)):
        over = value > threshold_pct
        # Outside the bar end, so a short bar never clips its own label.
        loss_label = f"{'▲ ' if over else ''}{value:.1f}%"
        ax.annotate(
            loss_label, (value, index), textcoords="offset points",
            xytext=(6, 0), va="center", fontsize=8.5,
            color=STATUS["warning"] if over else theme["MUTED"],
        )
        # p95 latency is NOT the thing in status -- it wears the muted text
        # token, so the status colour keeps meaning "this loss is over the
        # line" rather than bleeding onto an unrelated number.
        if p95 is not None:
            ax.annotate(
                f"p95 {float(p95):.0f} ms", (value, index),
                textcoords="offset points",
                xytext=(6 + 9.0 * len(loss_label), 0),
                va="center", fontsize=8.5, color=theme["MUTED"],
            )
    ax.set_xlim(0, span * 1.5)
    ax.set_xlabel("mean loss (%)", color=theme["MUTED"], fontsize=8.5)
    ax.set_title(
        f"Worst targets — last {payload.get('window_hours', 24)}h",
        color=theme["INK"], fontsize=11, loc="left", pad=10,
    )
    for label in ax.get_yticklabels():
        label.set_color(theme["INK"])
        label.set_fontsize(9)

    data = _save(fig, _env_int("CHART_MAX_BYTES", DEFAULT_MAX_BYTES))
    plt.close(fig)
    return data


# Target names are operator-editable and have no length limit of their own,
# so the slug needs one: a 300-character target would build a filename some
# downstream store rejects outright. 48 is far longer than any real hostname
# and still leaves the prefix and window legible.
_SLUG_MAX = 48


def chart_filename(target: str, hours: int) -> str:
    """``smokeping-<target>-<hours>h-<unix>.png``, safe for any file store."""
    import time

    slug = "".join(
        ch if ch.isalnum() or ch in "-_" else "-" for ch in str(target or "")
    )
    # Strip separators only at the ends; "..." would otherwise slug to "".
    slug = slug[:_SLUG_MAX].strip("-_") or "chart"
    return f"smokeping-{slug}-{int(hours)}h-{int(time.time())}.png"
