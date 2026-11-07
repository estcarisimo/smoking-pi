"""The per-target figures the weekly summary reads: InfluxDB is mocked at
``query_influx``, in the module that owns it.

Moved here from the ai-insights module when that service was retired; the
alerter's weekly summary still depends on these figures.
"""

import time

import pytest

from common import aggregates

STOP = 1_800_000_000
START = STOP - 24 * 3600


def _rows(values):
    return [{"target": t, "_measurement": "latency", "_value": v}
            for t, v in values.items()]


def _target_rows(flux):
    """Fake Flux responses for the latency/dns_latency aggregate queries."""
    if '"step"' in flux and "last()" in flux:
        return []
    if "median()" in flux and '"median"' in flux:
        return _rows({"google_dns": 0.012, "isp_gw": 0.004})
    if "quantile" in flux:
        return _rows({"google_dns": 0.030, "isp_gw": 0.009})
    if "mean()" in flux:
        # loss stored as 0-1 ratio
        return _rows({"google_dns": 0.02, "isp_gw": 0.0})
    if "max()" in flux:
        return _rows({"google_dns": 0.35})
    if "count()" in flux:
        return _rows({"google_dns": 7})
    raise AssertionError(f"unexpected flux: {flux}")


def _stats(monkeypatch, handler, spans=None):
    monkeypatch.setattr(aggregates, "query_influx", handler)
    return aggregates.target_stats(START, STOP, spans)


def test_target_stats_converts_units_and_sorts_the_worst_first(monkeypatch):
    stats = _stats(monkeypatch, _target_rows)
    by_target = {t["target"]: t for t in stats}

    # latency/dns_latency: seconds -> ms, ratio -> percent
    g = by_target["google_dns"]
    assert g["median_ms"] == 12.0
    assert g["p95_ms"] == 30.0
    assert g["avg_loss_pct"] == 2.0
    assert g["max_loss_pct"] == 35.0
    assert g["loss_events"] == 7

    # targets without loss rows get zero defaults
    isp = by_target["isp_gw"]
    assert isp["avg_loss_pct"] == 0.0
    assert isp["loss_events"] == 0

    assert stats[0]["target"] == "google_dns"


def test_flux_str_rejects_injection():
    assert aggregates.flux_str("smokeping") == '"smokeping"'
    with pytest.raises(ValueError):
        aggregates.flux_str('bucket") |> yield() //"')
    with pytest.raises(ValueError):
        aggregates.flux_str("a\\b")
    with pytest.raises(ValueError):
        aggregates.flux_str("a\nb")


def test_loss_events_are_counted_in_pings_lost_per_target(monkeypatch):
    """One lost DNS query of five (20%) is not an event; the bar comes from
    each target's own ping count, 15% on the default ten."""
    seen = []

    def handler(flux):
        seen.append(flux)
        if '"step"' in flux and "last()" in flux:
            return [{"target": "GoogleDNS", "_field": "pings", "_value": 5}]
        return []

    _stats(monkeypatch, handler)
    events = next(f for f in seen if "|> count()" in f and "r._value >=" in f)
    assert events.startswith('import "dict"\n')
    assert '{key: "GoogleDNS", value: 0.3}' in events
    assert "default: 0.15)" in events


def test_a_failed_cadence_query_still_counts_on_ten_pings(monkeypatch):
    seen = []

    def handler(flux):
        if '"step"' in flux and "last()" in flux:
            raise RuntimeError("influx hiccup")
        seen.append(flux)
        return []

    _stats(monkeypatch, handler)
    assert any("r._value >= 0.15)" in f for f in seen)


def test_the_first_hop_is_not_a_destination_in_the_target_stats(monkeypatch):
    """CPE_IPv4 (category cpe) is evidence about the line, as everywhere
    else; its rate-limit floor would sit in the summary as a lossy target."""
    seen = []

    def handler(flux):
        seen.append(flux)
        return _target_rows(flux)

    _stats(monkeypatch, handler)
    stats = [f for f in seen if "mean()" in f]
    assert stats and all('r.category != "cpe"' in f for f in stats)


def test_loss_inside_the_monitors_own_spans_is_not_the_targets(monkeypatch):
    """2026-10-13 on the reference Pi: the radio was deaf for 5 h 40 min,
    and five Netflix targets renamed the day before (#310) had most of
    their points inside that span -- a 70.4% mean that read red in the
    digest. Each target's loss is also given with the span's points left
    out; one measured only inside it has none of its own."""
    deaf_lo, deaf_hi = STOP - 6 * 3600, STOP - 6 * 3600 + 340 * 60
    span = (deaf_lo + 1, deaf_hi + 300)
    stamp = lambda e: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(e))  # noqa: E731
    span_range = f"range(start: {stamp(span[0])}, stop: {stamp(span[1])})"
    whole = {"steady": (288, 68 / 288), "renamed": (68, 1.0), "dead": (288, 1.0)}
    inside = {"steady": 68, "renamed": 68, "dead": 68}  # all at 100% loss
    seen = []

    def handler(flux):
        seen.append(flux)
        if '"step"' in flux:
            return []
        in_span = span_range in flux
        if '"median"' in flux:
            return _rows({t: 0.01 for t in whole})
        if "mean()" in flux:
            return _rows({t: m for t, (n, m) in whole.items()})
        if "max()" in flux:
            return _rows({t: 1.0 for t in whole})
        if "sum()" in flux and in_span:
            return _rows({t: float(n) for t, n in inside.items()})
        if "count()" in flux:
            source = inside if in_span else {t: n for t, (n, _) in whole.items()}
            return _rows(source)  # every point inside is an event, and dead's all
        return []

    stats = _stats(monkeypatch, handler, [span])
    by = {t["target"]: t for t in stats}

    assert by["steady"]["avg_loss_pct"] == 23.61
    assert by["steady"]["avg_loss_pct_measurable"] == 0.0
    assert by["renamed"]["avg_loss_pct"] == 100.0
    assert by["renamed"]["avg_loss_pct_measurable"] is None
    assert by["renamed"]["loss_events_measurable"] == 0
    assert by["dead"]["avg_loss_pct_measurable"] == 100.0
    # The target with loss of its own leads, whatever the plain means say.
    assert stats[0]["target"] == "dead"
    # The span's points, by a pushed-down range.
    in_span = [f for f in seen if span_range in f]
    assert in_span and all('r.category != "cpe"' in f for f in in_span)


def test_a_span_past_the_window_is_clipped_to_it(monkeypatch):
    seen = []

    def handler(flux):
        seen.append(flux)
        return _target_rows(flux)

    _stats(monkeypatch, handler, [(START - 600, START + 600), (STOP + 1, STOP + 2)])
    stamp = lambda e: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(e))  # noqa: E731
    clipped = f"range(start: {stamp(START)}, stop: {stamp(START + 600)})"
    assert any(clipped in f for f in seen)
    assert not any(stamp(STOP + 1) in f for f in seen)


def test_without_monitor_spans_the_measurable_figures_are_the_plain_ones(monkeypatch):
    seen = []

    def handler(flux):
        seen.append(flux)
        return _target_rows(flux)

    g = {t["target"]: t for t in _stats(monkeypatch, handler)}["google_dns"]
    assert g["avg_loss_pct_measurable"] == g["avg_loss_pct"] == 2.0
    assert g["loss_events_measurable"] == g["loss_events"] == 7
    assert not any("sum()" in f for f in seen)


def test_overlapping_monitor_spans_are_merged():
    assert aggregates.merge_spans([(5, 9), (0, 3), (3, 4), (8, 12)]) == [(0, 4), (5, 12)]


def test_a_failed_span_query_leaves_the_plain_figures(monkeypatch):
    """The measurable figures are a refinement: a failed in-span query
    costs them, not the summary."""

    def handler(flux):
        if "sum()" in flux:
            raise RuntimeError("influx timeout")
        return _target_rows(flux)

    stats = _stats(monkeypatch, handler, [(STOP - 3600, STOP - 300)])
    g = {t["target"]: t for t in stats}["google_dns"]
    assert g["avg_loss_pct_measurable"] == g["avg_loss_pct"] == 2.0
    assert g["loss_events_measurable"] == 7
