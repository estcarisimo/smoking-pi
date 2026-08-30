"""Tests for chart rendering.

The properties that matter are the ones that fail SILENTLY: a chart saved on
a white canvas into a dark design, a loss axis that autoscales so 4% looks
catastrophic, and a render error that takes the alert down with it.
"""

from __future__ import annotations

import struct
import zlib

import pytest

import charts

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


@pytest.fixture()
def fake_influx(monkeypatch):
    """Serve fixed series so no test touches a real InfluxDB."""

    def _fetch(target, measurement, field, hours):
        if field == "loss":
            times, _ = _series()
            return times, [0.5 for _ in times]  # 50% as a 0-1 ratio
        return _series(value=8.0 if target == "subject" else 6.0)

    monkeypatch.setattr(charts, "_fetch", _fetch)


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
    # Row 0: a filter byte then the pixels. matplotlib emits filter 0 or 1 for
    # a flat first row; both leave pixel 0 unchanged.
    assert raw[0] in (0, 1), f"unexpected row filter {raw[0]}"
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
