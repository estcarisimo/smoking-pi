"""Render the chart that ships with an alert or a digest.

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
import os
from datetime import datetime, timezone

import flux

log = logging.getLogger("alerter.charts")

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
        flux.base_flux([measurement], f"-{int(hours)}h")
        + f"|> filter(fn: (r) => r.target == {flux.flux_str(target)}) "
        + f'|> filter(fn: (r) => r._field == {flux.flux_str(field)}) '
        + f"|> aggregateWindow(every: {every}m, fn: mean, createEmpty: false) "
        + '|> keep(columns: ["_time", "_value"]) '
        + '|> sort(columns: ["_time"])'
    )


def _fetch(target: str, measurement: str, field: str, hours: int):
    rows = flux.query_influx(_series_flux(target, measurement, field, hours))
    times, values = [], []
    for row in rows:
        when, value = row.get("_time"), row.get("_value")
        if when is None or value is None:
            continue
        times.append(when if isinstance(when, datetime) else None)
        values.append(float(value))
    # InfluxDB returns UTC-aware datetimes and matplotlib formats each one in
    # its OWN tzinfo -- so plotting them raw draws a UTC axis under a footer
    # that names the local zone. An hour's silent offset is exactly what makes
    # someone mis-correlate an incident with what they were doing at the time.
    pairs = [(t.astimezone(), v) for t, v in zip(times, values) if t is not None]
    return [p[0] for p in pairs], [p[1] for p in pairs]


def _to_ms(values: list[float]) -> list[float]:
    """latency median is stored in seconds."""
    return [v * 1000.0 for v in values]


def _loss_pct(values: list[float], measurement: str) -> list[float]:
    """latency/dns_latency loss is a 0-1 ratio; cpe_latency is already 0-100."""
    if measurement == "cpe_latency":
        return [min(100.0, max(0.0, v)) for v in values]
    return [flux.clamp_loss_ratio(v) * 100.0 for v in values]


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
        return _render_incident_chart(
            target, measurement, hours, first_seen, severity, peers or []
        )
    except Exception:  # noqa: BLE001 - a chart must never cost the alert
        log.warning("Chart render failed for %s; sending text only",
                    target, exc_info=True)
        return None


def _render_incident_chart(target, measurement, hours, first_seen, severity, peers):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    theme = _theme()
    accent = STATUS.get(severity, STATUS["warning"])

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

    # Peers first, so the subject draws over them.
    drew_peer = False
    for peer in peers[:MAX_PEERS]:
        p_times, p_vals = _fetch(peer, measurement, "median", hours)
        if p_times:
            ax_lat.plot(p_times, _to_ms(p_vals), color=PEER, alpha=0.45,
                        linewidth=1.0, zorder=2)
            drew_peer = True

    if times:
        ms = _to_ms(medians)
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

    if drew_peer:
        legend = ax_lat.legend(
            handles=[
                plt.Line2D([], [], color=accent, linewidth=2.0, label=target),
                plt.Line2D([], [], color=PEER, alpha=0.45, linewidth=1.0,
                           label="peers in the same category"),
            ],
            loc="upper left", frameon=False, fontsize=8,
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
    fig.supxlabel(
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
