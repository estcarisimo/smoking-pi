"""Which other layers moved during a period: the rules on synthetic layers
read through mocked Flux results. No production data: the fixture has the
shape of an evening of congestion toward one destination (ICMP and TCP up
by about 10 ms, HTTP by about 50 ms, everything else flat)."""

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

import series
import triage

BIN = triage.BIN_S
DAY = 86400
# A period of two hours, starting on a bin, with two days of data before it.
P0 = 1_793_000_700 // BIN * BIN
P1 = P0 + 2 * 3600
DATA_FROM = P0 - 2 * DAY
DATA_TO = P1 + 6 * 3600
STEP = 300


def _t(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _noise(epoch, amp):
    # Deterministic and small: a baseline needs a spread to have a threshold.
    return amp * np.sin(epoch / 977.0)


def _during(epoch, start=P0, end=P1):
    return start <= epoch < end


class World:
    """What InfluxDB holds, per layer, as functions of time; the Flux
    queries are answered from it."""

    def __init__(self):
        self.icmp = {"Facebook": lambda e: 7.5 + _noise(e, 0.3) + (10 if _during(e) else 0),
                     "Google": lambda e: 9.0 + _noise(e, 0.3),
                     "CPE_IPv4": lambda e: 2.5 + _noise(e, 0.2)}
        self.icmp_loss = {}
        self.tcp = {"Facebook_tcp443": lambda e: 8.5 + _noise(e, 0.3) + (10 if _during(e) else 0)}
        self.http = {f"Facebook_h{v}": (lambda e, v=v: 75 + v + _noise(e, 3)
                                        + (50 if _during(e) else 0)) for v in (1, 2, 3)}
        self.loss = {}  # probe -> fn(epoch) -> ratio 0-1 (or 0-100 for the gateway)
        self.dns = {name: (lambda e: 10 + _noise(e, 0.5))
                    for name in ("CloudflareDNS", "GoogleDNS", "Quad9DNS")}
        self.home = lambda e: 15 + _noise(e, 0.8)
        self.hop = {"gw": lambda e: 8.5 + _noise(e, 0.5)}
        self.hop_loss = {"gw": lambda e: 12 + 3 * np.sin(e / 1300.0)}
        self.wifi = {"signal_dbm": lambda e: -52.0, "tx_bitrate_mbps": lambda e: 420.0}
        self.traces = {"Facebook": [{"time": t, "changed": False, "as_changed": False,
                                     "as_path": "64500 64501 32934"}
                                    for t in range(DATA_FROM + 120, DATA_TO, 3600)]}
        self.queries = []

    def epochs(self, since, stop):
        return range(max(DATA_FROM, since // STEP * STEP), min(DATA_TO, stop), STEP)

    def query(self, flux):
        self.queries.append(flux)
        since = int(flux.split("range(start: ")[1].split(",")[0])
        stop = int(flux.split("stop: ")[1].split(")")[0])
        rows = []
        if "tcp_latency" in flux:
            for m, probes in (("tcp_latency", self.tcp), ("http_latency", self.http)):
                for name, fn in probes.items():
                    for e in self.epochs(since, stop):
                        rows.append({"_time": _t(e), "_measurement": m, "_field": "median",
                                     "_value": fn(e) / 1000, "target": name})
                        lo = self.loss.get(name)
                        rows.append({"_time": _t(e), "_measurement": m, "_field": "loss",
                                     "_value": lo(e) if lo else 0.0, "target": name})
        elif "dns_latency" in flux:
            for name, fn in self.dns.items():
                for e in self.epochs(since, stop):
                    rows.append({"_time": _t(e), "_measurement": "dns_latency",
                                 "_field": "median", "_value": fn(e) / 1000, "target": name})
                    rows.append({"_time": _t(e), "_measurement": "dns_latency",
                                 "_field": "loss", "_value": 0.0, "target": name})
        elif "dns_resolution" in flux:
            for e in self.epochs(since, stop):
                rows.append({"_time": _t(e), "_measurement": "dns_resolution",
                             "_field": "p50", "_value": self.home(e) / 1000})
        elif "cpe_latency" in flux:
            # One query per field, each already a 15-minute aggregate.
            field = "median" if 'r._field == "median"' in flux else "loss"
            for name, fn in self.hop.items():
                fn = fn if field == "median" else self.hop_loss[name]
                for b in range(max(DATA_FROM, since) // BIN * BIN, min(DATA_TO, stop), BIN):
                    rows.append({"_time": _t(b), "_measurement": "cpe_latency",
                                 "_field": field, "_value": fn(b + BIN / 2), "target": name})
        elif "wifi_link" in flux:
            for b in range(max(DATA_FROM, since) // BIN * BIN, min(DATA_TO, stop), BIN):
                for f, fn in self.wifi.items():
                    rows.append({"_time": _t(b), "_measurement": "wifi_link", "_field": f,
                                 "_value": fn(b), "interface": "wlan0"})
                rows.append({"_time": _t(b), "_measurement": "wifi_link", "_field": "uplink",
                             "_value": 1.0, "interface": "wlan0"})
        elif "path_trace" in flux:
            for target, traces in self.traces.items():
                for tr in traces:
                    if since <= tr["time"] <= stop:
                        rows.append({"_time": _t(tr["time"]), "target": target,
                                     "changed": tr["changed"], "as_changed": tr["as_changed"],
                                     "as_path": tr["as_path"]})
        return rows

    def pings(self, target):
        fn = self.icmp[target]
        lo = self.icmp_loss.get(target)
        p, loss = [], []
        for e in range(DATA_FROM, DATA_TO, STEP):
            base = fn(e)
            p += [(e, base + k * 0.1) for k in range(10)]
            loss.append((e, lo(e) if lo else 0.0))
        return (pd.DataFrame(p, columns=["epoch", "values"]),
                pd.DataFrame(loss, columns=["epoch", "loss_pct"]))


def _triage(world, tmp_path, deaf=None, paths=True, targets=("Facebook", "Google", "CPE_IPv4"),
            periods=None):
    targets = list(targets)
    t = triage.Triage(
        targets, DATA_FROM, DATA_TO, deaf or set(),
        read_layers=lambda s, n: series.triage_bins(targets, s, n, query=world.query,
                                                    directory=tmp_path),
        read_paths=(lambda ts, s, n: series.path_traces(ts, s, n, query=world.query))
        if paths else (lambda ts, s, n: {}),
        categories={"Facebook": "topsites", "Google": "topsites", "CPE_IPv4": "cpe"})
    for target in targets:
        pings, loss = world.pings(target)
        own = (periods or {}).get(target, [])
        if target == "Facebook" and periods is None:
            own = [{"start": P0, "end": P1}]
        t.add_icmp(target, pings, loss, own)
    return t


def _one(world, tmp_path, kind=triage.CONGESTION, **kw):
    t = _triage(world, tmp_path, **kw)
    period = {"start": P0, "end": P1}
    t.enrich(kind, "Facebook", [period])
    assert t.errors == 0
    return period


# ───────────────────────── the four cases ─────────────────────────

def test_tcp_and_http_rise_with_icmp(tmp_path):
    p = _one(World(), tmp_path)
    assert (p["tri_tcp"], p["tri_http"], p["tri_dns"], p["tri_hop"], p["tri_wifi"],
            p["tri_path"]) == ("up", "up", "flat", "flat", "flat", "unchanged")
    assert p["tri_others"] == 0 and p["tri_version"] == triage.RULES_VERSION
    assert p["icmp_rise_ms"] == pytest.approx(10, abs=1)
    assert p["tcp_rise_ms"] == pytest.approx(10, abs=1)
    assert p["http_rise_ms"] == pytest.approx(50, abs=3)
    assert p["triage"] == (
        "ICMP, TCP and HTTP to Facebook rose together (median +10, +10 and +49 ms); "
        "DNS, first hop and Wi-Fi did not; no loss; path unchanged; only Facebook rose.")


def test_only_the_first_hop_moves(tmp_path):
    w = World()
    w.icmp["Facebook"] = lambda e: 7.5 + _noise(e, 0.3)
    w.tcp["Facebook_tcp443"] = lambda e: 8.5 + _noise(e, 0.3)
    for v in (1, 2, 3):
        w.http[f"Facebook_h{v}"] = lambda e, v=v: 75 + v + _noise(e, 3)
    w.hop["gw"] = lambda e: 8.5 + _noise(e, 0.5) + (8 if _during(e) else 0)
    p = _one(w, tmp_path)
    assert (p["tri_tcp"], p["tri_http"], p["tri_dns"], p["tri_hop"]) == (
        "flat", "flat", "flat", "up")
    assert p["hop_rise_ms"] == pytest.approx(8, abs=1)
    assert p["triage"].startswith("ICMP's median did not rise; first hop rose (+8 ms); "
                                  "TCP, HTTP, DNS and Wi-Fi did not; no loss;")


def test_a_deaf_bin_next_to_the_period_counts_one_further_does_not(tmp_path):
    p = _one(World(), tmp_path, deaf={P1})  # the bin right after the end: +1 bin
    assert p["tri_wifi"] == "lost"
    assert "Wi-Fi was deaf in 1 bin;" in p["triage"]
    q = _one(World(), tmp_path, deaf={P1 + BIN})  # two bins after: outside
    assert q["tri_wifi"] == "flat"


def test_no_path_data_reads_no_traces_never_unchanged(tmp_path):
    p = _one(World(), tmp_path, paths=False)
    assert p["tri_path"] == "nodata"
    assert "path: no traces" in p["triage"]


# ───────────────────────── the rules ─────────────────────────

def _flat(n=96, level=10.0, amp=0.5):
    return {b * BIN: level + amp * np.sin(b) for b in range(n)}


def test_a_rise_just_over_and_just_under_the_threshold():
    base = list(range(0, 96 * BIN, BIN))
    during = list(range(100 * BIN, 104 * BIN, BIN))
    s = _flat()
    threshold = max(np.percentile(list(s.values()), 90) - np.percentile(list(s.values()), 10),
                    1.0)
    level = float(np.median(list(s.values())))
    over = {**s, **{b: level + threshold + 0.01 for b in during}}
    under = {**s, **{b: level + threshold - 0.01 for b in during}}
    assert triage.rise(over, during, base)["rose"]
    assert not triage.rise(under, during, base)["rose"]


def test_the_threshold_is_ten_percent_of_the_level_when_the_spread_is_smaller():
    base = list(range(0, 96 * BIN, BIN))
    during = list(range(100 * BIN, 104 * BIN, BIN))
    s = _flat(level=100.0, amp=0.1)
    r = triage.rise({**s, **{b: 109.0 for b in during}}, during, base)
    assert r["threshold"] == pytest.approx(10.0, abs=0.1) and not r["rose"]


def test_coverage_under_three_quarters_is_no_data_never_flat():
    base = list(range(0, 96 * BIN, BIN))
    during = list(range(100 * BIN, 104 * BIN, BIN))
    s = {**_flat(), **{100 * BIN: 50.0, 101 * BIN: 50.0}}  # 2 of 4 bins
    assert triage.rise(s, during, base) is None


def test_the_baseline_leaves_out_the_destinations_other_periods(tmp_path):
    # Yesterday evening was congested too: inside the baseline, it would
    # lift the level and widen the spread enough to hide today's rise.
    w = World()
    y0, y1 = P0 - DAY, P1 - DAY
    w.tcp["Facebook_tcp443"] = lambda e: (8.5 + _noise(e, 0.3)
                                          + (10 if _during(e) or _during(e, y0, y1) else 0))
    for b in triage.span_bins(y0, y1):
        assert b in triage.base_bins(P0)
        assert b not in triage.base_bins(P0, [(y0, y1)])
    periods = {"Facebook": [{"start": y0, "end": y1}, {"start": P0, "end": P1}]}
    p = _one(w, tmp_path, periods=periods)
    assert p["tri_tcp"] == "up"


def test_the_gateways_loss_floor_is_not_loss():
    base = list(range(0, 96 * BIN, BIN))
    padded = list(range(99 * BIN, 105 * BIN, BIN))
    floor = {b: 12 + 5 * np.sin(b / BIN) for b in base}  # 7-17 %: rate-limited ICMP
    s = {**floor, **{b: 15.0 for b in padded}}
    r = triage.loss(s, padded, base)
    assert r["threshold"] > 1.0 and not r["lost"]
    assert triage.loss({**floor, **{b: 40.0 for b in padded}}, padded, base)["lost"]


def test_http_needs_at_least_half_of_its_versions(tmp_path):
    w = World()
    w.http["Facebook_h3"] = lambda e: 78 + _noise(e, 3)
    w.http["Facebook_h2"] = lambda e: 77 + _noise(e, 3)
    p = _one(w, tmp_path)  # 1 of 3
    assert p["tri_http"] == "flat"
    w.http["Facebook_h2"] = lambda e: 77 + _noise(e, 3) + (50 if _during(e) else 0)
    q = _one(w, tmp_path / "2")  # 2 of 3
    assert q["tri_http"] == "up"


def test_other_destinations_that_rose_are_named(tmp_path):
    w = World()
    w.icmp["Google"] = lambda e: 9.0 + _noise(e, 0.3) + (6 if _during(e) else 0)
    p = _one(w, tmp_path)
    assert p["tri_others"] == 1
    assert p["triage"].endswith("1 other destination rose too: Google.")


def test_a_destination_without_tcp_or_http_probes_has_no_data_there(tmp_path):
    w = World()
    w.tcp, w.http = {}, {}
    p = _one(w, tmp_path)
    assert (p["tri_tcp"], p["tri_http"]) == ("nodata", "nodata")
    assert "TCP and HTTP: no data" in p["triage"]
    assert "tcp_rise_ms" not in p and "http_rise_ms" not in p


def test_a_degradation_asks_about_loss_first(tmp_path):
    w = World()
    w.icmp_loss["Facebook"] = lambda e: 20.0 if _during(e) else 0.0
    w.loss["Facebook_tcp443"] = lambda e: 0.2 if _during(e) else 0.0
    w.icmp["Facebook"] = lambda e: 7.5 + _noise(e, 0.3)
    w.tcp["Facebook_tcp443"] = lambda e: 8.5 + _noise(e, 0.3)
    for v in (1, 2, 3):
        w.http[f"Facebook_h{v}"] = lambda e, v=v: 75 + v + _noise(e, 3)
    p = _one(w, tmp_path, kind=triage.DEGRADATION)
    assert p["tri_tcp"] == "lost" and p["tri_http"] == "flat"
    assert p["triage"].startswith("ICMP and TCP to Facebook lost together (mean ")
    assert "no latency rise" in p["triage"]


# ───────────────────────── the path ─────────────────────────

def _traces(paths, start=P0 - DAY):
    return [{"time": start + i * 3600, "changed": False, "as_changed": False, "as_path": a}
            for i, a in enumerate(paths)]


def test_a_confirmed_change_names_the_new_ases():
    usual, other = "64500 64501 32934", "64500 6939 1299 32934"
    traces = _traces([usual] * 24 + [usual, other, other])
    traces[-1]["changed"] = True
    r = triage.path(traces, P0, P1)
    assert r["state"] == "changed"
    assert r["to"] == other


def test_one_trace_off_the_usual_path_is_a_detour():
    usual, other = "64500 64501 32934", "64500 6939 1299 32934"
    r = triage.path(_traces([usual] * 24 + [usual, other, usual]), P0, P1)
    assert r["state"] == "detour" and r["ases"] == ["6939", "1299"]


def test_traces_that_stop_for_hours_are_no_data():
    usual = "64500 64501 32934"
    traces = _traces([usual] * 23, start=P0 - DAY) + [
        {"time": P1 + 3000, "changed": False, "as_changed": False, "as_path": usual}]
    assert triage.path(traces, P0 - 3 * 3600, P1)["state"] == "nodata"


# ───────────────────────── the pass ─────────────────────────

def test_a_failed_read_writes_the_periods_without_the_triage_and_counts_once(tmp_path):
    def boom(s, n):
        raise RuntimeError("influx down")

    t = triage.Triage(["Facebook"], DATA_FROM, DATA_TO, set(), read_layers=boom,
                      read_paths=lambda ts, s, n: {})
    a, b = {"start": P0, "end": P1}, {"start": P1 + DAY, "end": P1 + DAY + 3600}
    t.enrich(triage.CONGESTION, "Facebook", [a])
    t.enrich(triage.CONGESTION, "Facebook", [b])
    assert "triage" not in a and "triage" not in b
    assert t.errors == 1


def test_the_layers_are_read_once_per_pass(tmp_path):
    w = World()
    t = _triage(w, tmp_path)
    t.enrich(triage.CONGESTION, "Facebook", [{"start": P0, "end": P1}])
    t.enrich(triage.DEGRADATION, "Facebook", [{"start": P0, "end": P1}])
    t.enrich(triage.CONGESTION, "Google", [{"start": P0, "end": P1}])
    assert sum("cpe_latency" in q for q in w.queries) == 2  # its median and its loss
    assert sum("path_trace" in q for q in w.queries) == 1
    assert t.report()["periods"] == 3


def test_nothing_to_write_reads_nothing(tmp_path):
    w = World()
    t = _triage(w, tmp_path)
    t.enrich(triage.CONGESTION, "Facebook", [])
    assert w.queries == []


def test_a_period_that_fails_does_not_stop_the_next(tmp_path, monkeypatch):
    t = _triage(World(), tmp_path)
    bad, good = {"start": "x", "end": P1}, {"start": P0, "end": P1}
    t.enrich(triage.CONGESTION, "Facebook", [bad, good])
    assert "triage" not in bad and "triage" in good and t.errors == 1


def test_a_second_pass_reads_only_the_last_25_hours(tmp_path):
    w = World()
    _triage(w, tmp_path).enrich(triage.CONGESTION, "Facebook", [{"start": P0, "end": P1}])
    w.queries.clear()
    targets = ["Facebook", "Google", "CPE_IPv4"]
    later = series.triage_bins(targets, DATA_FROM + 3600, DATA_TO + 3600, query=w.query,
                               directory=tmp_path)
    tail = (DATA_TO - series.TRIAGE_TAIL_S) // BIN * BIN  # the last 25 h, not the window
    assert all(f"range(start: {tail}," in q for q in w.queries)
    assert len(later[later["layer"] == "tcp"])


def test_a_period_whose_day_before_was_another_period_says_so_once(tmp_path):
    # Congested for the whole day before: no baseline is left, and every
    # layer would read "no data" -- the sentence says why instead.
    periods = {"Facebook": [{"start": P0 - DAY - 3600, "end": P0 - 900},
                            {"start": P0, "end": P1}]}
    p = _one(World(), tmp_path, periods=periods)
    assert p["tri_tcp"] == p["tri_dns"] == p["tri_hop"] == "nodata"
    assert p["triage"].startswith("no baseline: the 24 h before were inside other "
                                  "periods of Facebook; path unchanged;")


def test_when_the_destination_itself_did_not_rise_the_sentence_does_not_say_only_it_did(
        tmp_path):
    w = World()
    w.icmp["Facebook"] = lambda e: 7.5 + _noise(e, 0.3)
    w.tcp["Facebook_tcp443"] = lambda e: 8.5 + _noise(e, 0.3)
    for v in (1, 2, 3):
        w.http[f"Facebook_h{v}"] = lambda e, v=v: 75 + v + _noise(e, 3)
    p = _one(w, tmp_path)
    assert p["triage"] == ("ICMP's median did not rise; TCP, HTTP, DNS, first hop and Wi-Fi "
                           "did not; no loss; path unchanged; no other destination rose.")


def test_episode_traces_are_left_out_of_the_path():
    seen = []
    series.path_traces(["Facebook"], P0, P1, query=lambda q: seen.append(q) or [])
    assert "not exists r.trigger" in seen[0]


def test_a_name_with_a_newline_never_reaches_a_flux_regex():
    seen = []
    assert series.path_traces(["Facebook\n"], P0, P1, query=lambda q: seen.append(q) or []) == {}
    assert seen == []


def test_an_unanswered_question_is_no_data_not_did_not():
    # A degradation asks about loss first: latency alone cannot say "did not".
    assert triage._state({"rose": False, "lost": None}, triage.DEGRADATION) == "nodata"
    assert triage._state({"rose": True, "lost": None}, triage.DEGRADATION) == "up"
    assert triage._state({"rose": None, "lost": False}, triage.CONGESTION) == "nodata"
    assert triage._state({"rose": False, "lost": False}, triage.CONGESTION) == "flat"


def test_a_layer_that_moved_reports_the_rise_of_the_probes_that_moved():
    moved, rise = triage._majority([{"rose": True, "rise": 6.0}, {"rose": True, "rise": 6.5},
                                    {"rose": False, "rise": 0.1}, {"rose": False, "rise": -0.2}],
                                   "rose")
    assert moved and rise == pytest.approx(6.25)


# ───────────────────────── the sentence, branch by branch ─────────────────────────

def _layer_result(state, rise=None, loss_rise=None, rose=None, lost=None):
    return {"state": state, "rise": rise, "loss_rise": loss_rise, "rose": rose, "lost": lost}


def _result(kind, icmp, **layers):
    """A result as evaluate() returns it: every layer flat unless given."""
    flat = _layer_result("flat", rise=0.1, loss_rise=0.0, rose=False, lost=False)
    out = {"kind": kind, "target": "X", "base_bins": 96, "icmp": icmp,
           "path": {"state": "unchanged"}, "others": [],
           "wifi": {"state": "flat", "deaf_bins": 0}}
    for n in ("tcp", "http", "dns", "hop"):
        out[n] = layers.get(n, dict(flat))
    return out


CONGESTION_CASES = [
    # ICMP's median rose, without and with loss.
    (_layer_result("up", rise=10, loss_rise=0.0, rose=True, lost=False),
     "ICMP to X rose (median +10 ms); TCP, HTTP, DNS, first hop and Wi-Fi did not; no loss; "
     "path unchanged; only X rose."),
    (_layer_result("up", rise=10, loss_rise=5, rose=True, lost=True),
     "ICMP to X rose (median +10 ms); TCP, HTTP, DNS, first hop and Wi-Fi did not; "
     "ICMP also lost (+5 points); path unchanged; only X rose."),
    # ICMP's median did not rise, without and with loss: the flat-median
    # case used to lose its clause when the loss rose.
    (_layer_result("flat", rise=0.2, loss_rise=0.0, rose=False, lost=False),
     "ICMP's median did not rise; TCP, HTTP, DNS, first hop and Wi-Fi did not; no loss; "
     "path unchanged; no other destination rose."),
    (_layer_result("lost", rise=0.2, loss_rise=5, rose=False, lost=True),
     "ICMP's median did not rise, though its loss rose (+5 points); TCP, HTTP, DNS, "
     "first hop and Wi-Fi did not; no other loss; path unchanged; no other destination rose."),
    # No data on the median, without and with loss.
    (_layer_result("nodata"),
     "ICMP's median: no data; TCP, HTTP, DNS, first hop and Wi-Fi did not; no loss; "
     "path unchanged; no other destination rose."),
    # _state reads this one "lost" (the loss answered, the median could not).
    (_layer_result("lost", loss_rise=5, lost=True),
     "ICMP's median: no data, though its loss rose (+5 points); TCP, HTTP, DNS, first hop "
     "and Wi-Fi did not; no other loss; path unchanged; no other destination rose."),
]


@pytest.mark.parametrize("icmp, sentence", CONGESTION_CASES)
def test_a_congestion_sentence_opens_with_what_icmps_median_did(icmp, sentence):
    assert triage.text(_result(triage.CONGESTION, icmp)) == sentence


DEGRADATION_CASES = [
    # ICMP's loss rose, without and with a median rise.
    (_layer_result("lost", rise=0.2, loss_rise=4, rose=False, lost=True),
     "ICMP to X lost (mean +4 points); TCP, HTTP, DNS, first hop and Wi-Fi did not; "
     "no latency rise; path unchanged; only X lost."),
    (_layer_result("lost", rise=12, loss_rise=4, rose=True, lost=True),
     "ICMP to X lost (mean +4 points); TCP, HTTP, DNS, first hop and Wi-Fi did not; "
     "ICMP also rose (median +12 ms); path unchanged; only X lost."),
    # ICMP's loss did not rise, without and with a median rise: the same
    # gap, mirrored.
    (_layer_result("flat", rise=0.2, loss_rise=0.1, rose=False, lost=False),
     "ICMP's loss did not rise; TCP, HTTP, DNS, first hop and Wi-Fi did not; "
     "no latency rise; path unchanged; no other destination lost."),
    (_layer_result("up", rise=12, loss_rise=0.1, rose=True, lost=False),
     "ICMP's loss did not rise, though its median rose (+12 ms); TCP, HTTP, DNS, first hop "
     "and Wi-Fi did not; no other latency rise; path unchanged; no other destination lost."),
    # No data on the loss, without and with a median rise.
    (_layer_result("nodata"),
     "ICMP's loss: no data; TCP, HTTP, DNS, first hop and Wi-Fi did not; no latency rise; "
     "path unchanged; no other destination lost."),
    # _state reads this one "up" (the median answered, the loss could not).
    (_layer_result("up", rise=12, rose=True),
     "ICMP's loss: no data, though its median rose (+12 ms); TCP, HTTP, DNS, first hop "
     "and Wi-Fi did not; no other latency rise; path unchanged; no other destination lost."),
]


@pytest.mark.parametrize("icmp, sentence", DEGRADATION_CASES)
def test_a_degradation_sentence_opens_with_what_icmps_loss_did(icmp, sentence):
    assert triage.text(_result(triage.DEGRADATION, icmp)) == sentence


def test_a_layer_that_moved_only_on_the_other_question_still_did_not():
    # Congestion: TCP lost but its median did not rise, so it is in "did
    # not" (the period's question) and in "also lost".
    icmp = _layer_result("lost", rise=0.2, loss_rise=5, rose=False, lost=True)
    tcp = _layer_result("lost", rise=0.1, loss_rise=3, rose=False, lost=True)
    assert triage.text(_result(triage.CONGESTION, icmp, tcp=tcp)) == (
        "ICMP's median did not rise, though its loss rose (+5 points); TCP, HTTP, DNS, "
        "first hop and Wi-Fi did not; TCP also lost (+3 points); path unchanged; "
        "no other destination rose.")
    # Degradation, mirrored: HTTP's median rose but it did not fail more.
    icmp = _layer_result("lost", rise=0.2, loss_rise=4, rose=False, lost=True)
    http = _layer_result("up", rise=30, loss_rise=0.0, rose=True, lost=False)
    assert triage.text(_result(triage.DEGRADATION, icmp, http=http)) == (
        "ICMP to X lost (mean +4 points); TCP, HTTP, DNS, first hop and Wi-Fi did not; "
        "HTTP also rose (median +30 ms); path unchanged; only X lost.")


def test_a_layer_with_no_answer_to_the_question_is_no_data_even_when_it_moved_otherwise():
    # TCP's median had no data, its loss rose: _state says "lost", the
    # sentence says no data and the loss, never "did not".
    icmp = _layer_result("up", rise=10, loss_rise=0.0, rose=True, lost=False)
    tcp = _layer_result("lost", loss_rise=3, lost=True)
    assert triage._state({"rose": None, "lost": True}, triage.CONGESTION) == "lost"
    assert triage.text(_result(triage.CONGESTION, icmp, tcp=tcp)) == (
        "ICMP to X rose (median +10 ms); HTTP, DNS, first hop and Wi-Fi did not; "
        "TCP: no data; TCP also lost (+3 points); path unchanged; only X rose.")


def test_a_rise_elsewhere_says_too_only_after_the_destination_rose():
    hop = _layer_result("up", rise=8, loss_rise=0.0, rose=True, lost=False)
    flat_icmp = _layer_result("flat", rise=0.2, loss_rise=0.0, rose=False, lost=False)
    up_icmp = _layer_result("up", rise=10, loss_rise=0.0, rose=True, lost=False)
    assert triage.text(_result(triage.CONGESTION, flat_icmp, hop=hop)).startswith(
        "ICMP's median did not rise; first hop rose (+8 ms); TCP, HTTP, DNS and Wi-Fi did not;")
    assert triage.text(_result(triage.CONGESTION, up_icmp, hop=hop)).startswith(
        "ICMP to X rose (median +10 ms); first hop rose too (+8 ms);")


def test_no_baseline_says_why_once_for_either_kind():
    for kind, verb in ((triage.CONGESTION, "rose"), (triage.DEGRADATION, "lost")):
        result = _result(kind, _layer_result("nodata"))
        result["base_bins"] = 2
        assert triage.text(result) == (
            "no baseline: the 24 h before were inside other periods of X; path unchanged; "
            f"no other destination {verb}.")


def test_many_other_destinations_are_counted_past_five():
    result = {"kind": triage.CONGESTION, "target": "X", "path": {"state": "nodata"},
              "others": [f"T{i}" for i in range(8)], "icmp": {"state": "up"}}
    assert triage._context(result, "X", "rose")[-1] == (
        "8 other destinations rose too: T0, T1, T2, T3, T4 and 3 more")


def test_the_cache_of_an_earlier_target_set_is_removed(tmp_path):
    w = World()
    series.triage_bins(["Facebook"], DATA_FROM, DATA_TO, query=w.query, directory=tmp_path)
    series.triage_bins(["Facebook", "Google"], DATA_FROM, DATA_TO, query=w.query,
                       directory=tmp_path)
    assert len({f.name.split(".")[0] for f in tmp_path.glob("triage-*")}) == 1
