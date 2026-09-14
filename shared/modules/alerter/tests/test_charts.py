"""Tests for chart rendering.

The properties that matter are the ones that fail SILENTLY: a chart saved on
a white canvas into a dark design, a loss axis that autoscales so 4% looks
catastrophic, and a render error that takes the alert down with it.
"""

from __future__ import annotations

import struct
import zlib

import pytest

from common import charts

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _series(n=40, value=8.0):
    from datetime import datetime, timedelta, timezone

    start = datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc)
    return (
        [start + timedelta(minutes=3 * i) for i in range(n)],
        [value for _ in range(n)],
    )


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("CHART_THEME", "CHART_MAX_BYTES", "HIGH_LOSS_PCT"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def no_spread(monkeypatch):
    """Default the dispersion band to "no data" so a render never reaches
    InfluxDB for it. The band's own query is swallowed on error, so without
    this a test that mocks only ``_fetch`` would still construct a client --
    and the lazy-client import test would fail depending on test order."""
    monkeypatch.setattr(charts, "_fetch_spread",
                        lambda *a, **k: ([], [], [], [], []))


@pytest.fixture()
def fake_influx(monkeypatch):
    """Serve fixed series so no test touches a real InfluxDB."""

    def _fetch(target, measurement, field, hours):
        if field == "loss":
            times, _ = _series()
            return times, [0.5 for _ in times]  # 50% as a 0-1 ratio
        return _series(value=8.0 if target == "subject" else 6.0)

    def _fetch_spread(target, measurement, hours):
        times, med = _series(value=8.0)
        ms = [v * 1000.0 for v in med]
        return (times, [m - 3 for m in ms], [m - 1 for m in ms],
                [m + 1 for m in ms], [m + 4 for m in ms])

    monkeypatch.setattr(charts, "_fetch", _fetch)
    monkeypatch.setattr(charts, "_fetch_spread", _fetch_spread)


def _first_pixel(png: bytes) -> tuple[int, int, int]:
    """Decode the top-left pixel of a PNG without an image library."""
    pos, width, bit_depth, color_type, idat = 8, None, None, None, b""
    while pos < len(png):
        length = struct.unpack(">I", png[pos:pos + 4])[0]
        tag = png[pos + 4:pos + 8]
        data = png[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            width, _h, bit_depth, color_type = (*struct.unpack(">II", data[:8]),
                                                data[8], data[9])
        elif tag == b"IDAT":
            idat += data
        elif tag == b"IEND":
            break
        pos += length + 12
    assert bit_depth == 8, "expected 8-bit channels"
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    raw = zlib.decompress(idat)
    # Row 0: a filter byte then the pixels. Accept all five PNG filter types
    # rather than the 0/1 matplotlib happens to emit today -- for the FIRST
    # pixel of the FIRST row every predictor is 0 (Sub reads left, Up reads
    # above, Average reads both, Paeth reads all three; none exist yet), so
    # pixel 0 is unchanged under any of them. Pinning to 0/1 would fail on a
    # matplotlib or zlib version that filters differently, testing the
    # encoder's choices instead of the colour we came here to check.
    assert raw[0] in (0, 1, 2, 3, 4), f"invalid PNG row filter {raw[0]}"
    return tuple(raw[1:1 + 3]) if channels >= 3 else (raw[1],) * 3


# ---------------------------------------------------------------------------
# Incident chart
# ---------------------------------------------------------------------------


def test_renders_a_png(fake_influx):
    png = charts.render_incident_chart("subject", severity="critical")
    assert png and png.startswith(PNG_MAGIC)


def test_the_canvas_is_not_white(fake_influx):
    """The dark theme must actually render dark."""
    png = charts.render_incident_chart("subject", severity="critical")
    r, g, b = _first_pixel(png)
    assert (r, g, b) != (255, 255, 255), "chart saved on a white canvas"
    assert max(r, g, b) < 80, f"surface too light for the dark theme: {(r, g, b)}"


def test_a_white_savefig_default_cannot_leak_through(fake_influx):
    """REINTRODUCTION TEST for the explicit facecolor= argument.

    rcParams["savefig.facecolor"] is a global, and was "w" by default before
    matplotlib 2.0. If anything sets it, a figure that omits facecolor= ships
    with a white canvas under a dark chart. Passing it explicitly pins the
    behaviour; drop that argument and this test fails.
    """
    import matplotlib

    original = matplotlib.rcParams["savefig.facecolor"]
    matplotlib.rcParams["savefig.facecolor"] = "w"
    try:
        png = charts.render_incident_chart("subject", severity="critical")
        r, g, b = _first_pixel(png)
        assert max(r, g, b) < 80, (
            f"a white savefig default leaked into the render: {(r, g, b)}"
        )
    finally:
        matplotlib.rcParams["savefig.facecolor"] = original


def test_light_theme_is_actually_light(fake_influx, monkeypatch):
    monkeypatch.setenv("CHART_THEME", "light")
    r, g, b = _first_pixel(charts.render_incident_chart("subject"))
    assert min(r, g, b) > 200, f"light theme rendered dark: {(r, g, b)}"


def test_no_data_yields_no_chart(monkeypatch):
    monkeypatch.setattr(charts, "_fetch", lambda *a, **k: ([], []))
    assert charts.render_incident_chart("subject") is None


def test_an_influx_error_never_raises(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("influx is down")

    monkeypatch.setattr(charts, "_fetch", _boom)
    assert charts.render_incident_chart("subject") is None


def test_output_respects_the_byte_cap(fake_influx, monkeypatch):
    monkeypatch.setenv("CHART_MAX_BYTES", "1")
    assert charts.render_incident_chart("subject") is None


def test_peers_do_not_break_the_render(fake_influx):
    png = charts.render_incident_chart("subject", peers=["a", "b", "c", "d", "e"])
    assert png and png.startswith(PNG_MAGIC)


# ---------------------------------------------------------------------------
# Dispersion band
# ---------------------------------------------------------------------------


def _pivoted_rows(pings_per_row):
    from datetime import datetime, timedelta, timezone

    start = datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc)
    rows = []
    for i, pings in enumerate(pings_per_row):
        row = {"_time": start + timedelta(minutes=3 * i)}
        row.update({f"ping{k + 1}": v for k, v in enumerate(pings)})
        rows.append(row)
    return rows


def test_spread_is_min_quartiles_max_of_each_window(monkeypatch):
    """The band is computed from the pings as stored, sorted here -- it must
    not trust the RRD ordering, and lost pings (None) drop out."""
    from common import tsdb

    rows = _pivoted_rows([
        [0.010, 0.004, 0.006, 0.008, 0.005, 0.007, 0.009, 0.011, None, 0.012],
    ])
    monkeypatch.undo()  # drop the autouse "no spread" patch for this test
    monkeypatch.setattr(tsdb, "query_influx", lambda flux: rows)
    times, lo, q1, q3, hi = charts._fetch_spread("subject", "latency", 6)
    assert len(times) == 1
    assert lo == [4.0] and hi == [12.0]          # in ms
    assert q1 == [6.0] and q3 == [10.0]          # 9 valid pings: idx 2 and 6
    assert lo[0] <= q1[0] <= q3[0] <= hi[0]


def test_a_window_with_one_ping_has_no_band(monkeypatch):
    from common import tsdb

    monkeypatch.undo()
    monkeypatch.setattr(tsdb, "query_influx",
                        lambda flux: _pivoted_rows([[0.005, None, None]]))
    assert charts._fetch_spread("subject", "latency", 6) == ([], [], [], [], [])


def test_a_failed_spread_query_does_not_cost_the_chart(monkeypatch):
    """REINTRODUCTION TEST: the band is decoration; the median line is the
    chart. Let the spread query raise and the chart must still render."""
    from common import tsdb

    def _fetch(target, measurement, field, hours):
        return _series(value=8.0)

    def _boom(flux):
        raise RuntimeError("influx is down")

    monkeypatch.undo()
    monkeypatch.setattr(charts, "_fetch", _fetch)
    monkeypatch.setattr(tsdb, "query_influx", _boom)
    png = charts.render_incident_chart("subject")
    assert png and png.startswith(PNG_MAGIC)


def test_spread_flux_selects_only_ping_fields():
    flux = charts._spread_flux("subject", "latency", 24)
    assert "/^ping[0-9]+$/" in flux
    assert "pivot(" in flux
    assert '"subject"' in flux


# ---------------------------------------------------------------------------
# Shareable (on-request) chart
# ---------------------------------------------------------------------------


def test_target_chart_renders_a_png(fake_influx):
    png = charts.render_target_chart("subject", hours=24)
    assert png and png.startswith(PNG_MAGIC)


def test_target_chart_carries_no_incident_marks(monkeypatch):
    """No incident means no "alert" line and no status colour: a status hue
    on a chart with no status tells the reader something is wrong."""
    seen = {}

    def _spy(target, measurement, hours, peers, *, accent, first_seen,
             footer=None):
        seen.update(accent=accent, first_seen=first_seen, footer=footer,
                    peers=peers)
        return b"png"

    monkeypatch.setattr(charts, "_render_series_chart", _spy)
    assert charts.render_target_chart("subject", peers=["p"]) == b"png"
    assert seen["first_seen"] is None
    assert seen["accent"] == charts._theme()["SERIES"]
    # "info" shares the series blue on purpose; critical/warning must not leak.
    assert seen["accent"] not in (charts.STATUS["critical"],
                                  charts.STATUS["warning"])
    assert seen["footer"] == "smokeping"
    assert seen["peers"] == ["p"]


def test_target_chart_never_raises(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("influx is down")

    monkeypatch.setattr(charts, "_fetch", _boom)
    assert charts.render_target_chart("subject") is None


def test_target_chart_with_no_data_is_none(monkeypatch):
    monkeypatch.setattr(charts, "_fetch", lambda *a, **k: ([], []))
    assert charts.render_target_chart("subject") is None


def test_long_windows_render(fake_influx):
    """> 24h switches the tick format to include the day; must still render."""
    png = charts.render_target_chart("subject", hours=72)
    assert png and png.startswith(PNG_MAGIC)


# ---------------------------------------------------------------------------
# Loss scaling
# ---------------------------------------------------------------------------


def test_latency_loss_is_a_ratio_and_cpe_loss_is_a_percent():
    """The two measurements store loss in different units.

    Clamping cpe_latency the way latency is clamped would turn 100% into 1%.
    """
    assert charts._loss_pct([0.5], "latency") == [50.0]
    assert charts._loss_pct([50.0], "cpe_latency") == [50.0]
    # Legacy latency points were packet counts; they clamp to 100%, not 2000%.
    assert charts._loss_pct([20.0], "latency") == [100.0]


# ---------------------------------------------------------------------------
# Digest chart
# ---------------------------------------------------------------------------


def _digest(values):
    return {
        "window_hours": 24,
        "targets": [
            {"target": f"t{i}", "avg_loss_pct": v, "p95_ms": 10.0}
            for i, v in enumerate(values)
        ],
    }


def test_digest_renders(fake_influx):
    png = charts.render_digest_chart(_digest([30.0, 12.0, 3.0]))
    assert png and png.startswith(PNG_MAGIC)


def test_an_all_healthy_digest_has_no_chart():
    """A bar chart of zeros is worse than no chart; the caption says so."""
    assert charts.render_digest_chart(_digest([0.0, 0.0, 0.0])) is None


def test_an_empty_digest_has_no_chart():
    assert charts.render_digest_chart({"targets": []}) is None


def test_digest_never_raises_on_bad_input():
    assert charts.render_digest_chart({"targets": [{"target": "x",
                                                    "avg_loss_pct": "nonsense"}]}) is None


def test_cpe_latency_is_already_in_ms():
    """REINTRODUCTION TEST: cpe_latency stores ms, the others seconds. Scaling
    the gateway's 7 ms by 1000 drew the microcut chart three orders of
    magnitude wrong."""
    assert charts._to_ms([0.007], "latency") == [7.0]
    assert charts._to_ms([0.007], "dns_latency") == [7.0]
    assert charts._to_ms([7.0], "cpe_latency") == [7.0]


def test_spread_uses_the_measurement_scale(monkeypatch):
    from common import tsdb

    monkeypatch.undo()
    monkeypatch.setattr(tsdb, "query_influx",
                        lambda flux: _pivoted_rows([[4.0, 6.0, 8.0, 12.0]]))
    _t, lo, _q1, _q3, hi = charts._fetch_spread("gw", "cpe_latency", 6)
    assert lo == [4.0] and hi == [12.0]


# ---------------------------------------------------------------------------
# Axis ceiling and loss threshold
# ---------------------------------------------------------------------------


def test_a_few_spikes_do_not_own_the_latency_axis():
    """REINTRODUCTION TEST: 120 windows around 50 ms with nine spikes to
    180 ms. Let the max set the axis and the typical shape is pressed into
    the bottom fifth; the ceiling must sit near the p90 and count the rest."""
    hi = [50.0] * 111 + [180.0] * 9
    q3 = [35.0] * 120
    med = [9.0] * 120
    ceiling, clipped, peak = charts._latency_ceiling(hi, q3, med, [])
    assert ceiling < 100, f"ceiling {ceiling} lets nine spikes own the axis"
    assert clipped == 9
    assert peak == 180.0


def test_the_median_and_peers_are_never_cut():
    hi = [20.0] * 120
    ceiling, clipped, _ = charts._latency_ceiling(hi, [15.0] * 120, [90.0] * 120,
                                                  [120.0] * 50)
    assert ceiling >= 120.0
    assert clipped == 0


def test_the_inner_band_keeps_headroom():
    """The p90 of a flat outer band equals the band; the inner band must
    still get a third of headroom above it."""
    ceiling, _, _ = charts._latency_ceiling([40.0] * 120, [40.0] * 120,
                                            [10.0] * 120, [])
    assert ceiling >= 1.3 * 40.0


def test_no_band_falls_back_to_the_medians():
    ceiling, clipped, peak = charts._latency_ceiling([], [], [8.0, 9.0, 300.0], [])
    assert ceiling >= 300.0 and clipped == 0 and peak == 300.0
    assert charts._latency_ceiling([], [], [], []) == (0.0, 0, 0.0)


def test_loss_threshold_follows_the_measurement(monkeypatch):
    monkeypatch.setenv("MICROCUT_LOSS_PCT", "60")
    monkeypatch.setenv("HIGH_LOSS_PCT", "25")
    assert charts._loss_threshold("cpe_latency") == (60.0, "microcut threshold")
    assert charts._loss_threshold("latency") == (25.0, "alert threshold")
    assert charts._loss_threshold("dns_latency") == (25.0, "alert threshold")


def test_loss_threshold_defaults_match_the_alerter():
    assert charts._loss_threshold("cpe_latency")[0] == 50.0
    assert charts._loss_threshold("latency")[0] == 20.0


def test_spiky_chart_still_renders(monkeypatch):
    times, med = _series(value=0.009)
    spread = (times, [5.0] * len(times), [7.0] * len(times), [12.0] * len(times),
              [50.0] * (len(times) - 4) + [180.0] * 4)
    monkeypatch.setattr(charts, "_fetch",
                        lambda t, m, f, h: (times, [0.0] * len(times)) if f == "loss"
                        else (times, med))
    monkeypatch.setattr(charts, "_fetch_spread", lambda *a, **k: spread)
    png = charts.render_target_chart("subject", hours=6)
    assert png and png.startswith(PNG_MAGIC)
