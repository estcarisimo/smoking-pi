"""Experimental: which other layers moved during each detected period.

A period is what the detectors already found: a ``persistent_congestion``
point (Jitterbug on the ICMP RTT floor) or a ``loss_degradation`` point (BCP
on ICMP loss), for one destination. The triage detects nothing. It takes the
period's interval and asks, layer by layer, whether that other signal moved
too, against the 24 hours before:

- ICMP (the period's own destination): how much it rose, as a reference;
- TCP and HTTP to the same destination (``<target>_tcp443``, ``_h1``-``_h3``);
- DNS: the public resolvers measured (``dns_latency``, category ``dns``) and
  the house's own resolution through AdGuard (``dns_resolution`` p50 of
  ``upstreams``) -- resolution in general, not the destination's name;
- the first hop, the uplink's gateway (``cpe_latency``, ms and 0-100 %);
- the Pi's own radio: deaf 15-minute bins (deaf.py), signal and tx rate;
- the path (pathmeter's hourly ``path_trace``);
- breadth: whether other destinations rose (or lost) at the same time.

It **never** feeds Jitterbug's confidence, the class or the detection: it is
a row of evidence beside the period, written on the period's own point
(store.py), so it has exactly the period's life -- rewritten while the period
is in the corrected part of the window, fixed once it settles, gone with it.

The rules, the same for every layer (the design note "Triage vertical por
episodio", 2026-11-09):

- window: the period's 15-minute bins; one bin more each side for point
  events (a deaf bin, loss); an hour more each side for the path, whose
  traces are hourly;
- baseline: the 24 h ending one bin before the start, without the bins of
  the same destination's other periods (else last evening's period would
  raise this evening's baseline);
- statistic: the median of the layer's 15-minute medians; "rose" when
  during - baseline >= max(p90 - p10 of the baseline's bin medians, 10 % of
  the baseline median), relative to each layer's own noise and level;
- "lost" when the mean loss during - the baseline's >= max(1 point, p90 -
  p10 of the baseline's bin means), in percent (the gateway's rate-limited
  ICMP loss floor is what the spread absorbs);
- coverage: at least 75 % of the period's bins with data in that layer, or
  the layer has "no data", never "did not";
- layers with several probes (HTTP's three versions, DNS's resolvers and
  the house's resolution): the layer moved when at least half of the probes
  with data did.

The verdict says what moved, never a cause.
"""

from __future__ import annotations

import logging
import math
import time
from collections import Counter
from collections.abc import Callable, Iterable

import numpy as np
import pandas as pd

log = logging.getLogger("inference.triage")

# Bump when a rule changes, so a reader knows which rules wrote a point.
RULES_VERSION = 1
BIN_S = 900
BASELINE_S = 86400
PAD_BINS = 1
PATH_PAD_S = 3600
PATH_STALE_S = 3 * 3600
MIN_COVERAGE = 0.75
# Not in the design note: a baseline needs some bins to have a spread at
# all. Two hours of the 24 is a floor, not a target.
MIN_BASE_BINS = 8
REL = 0.10
LOSS_PP = 1.0
WIFI_DB = 6.0
BITRATE_RATIO = 0.5

UP, FLAT, LOST, NODATA = "up", "flat", "lost", "nodata"
CHANGED, DETOUR, UNCHANGED = "changed", "detour", "unchanged"

CONGESTION, DEGRADATION = "congestion", "degradation"

# The bins table every layer is read into (series.triage_bins and
# icmp_bins): one row per layer, probe and 15-minute bin; ``med`` in ms
# (dB for the signal, Mbit/s for the rate), ``loss`` in percent.
COLUMNS = ["bin", "layer", "probe", "med", "loss"]
TEXT_COLUMNS = ("layer", "probe")
FLOOR = "floor"

LAYER_NAMES = {"icmp": "ICMP", "tcp": "TCP", "http": "HTTP", "dns": "DNS",
               "hop": "first hop", "wifi": "Wi-Fi"}
# The fields a period point carries are store.py's TRIAGE_* (it writes them
# when present).
# A sentence names at most this many other destinations, then counts the rest.
MAX_NAMED = 5


# ───────────────────────── bins ─────────────────────────

def span_bins(start: int, end: int, pad: int = 0) -> list[int]:
    """The 15-minute bins of [start, end), ``pad`` more each side."""
    first = int(start) // BIN_S * BIN_S - pad * BIN_S
    stop = int(end) + pad * BIN_S
    return list(range(first, stop, BIN_S)) or [first]


def base_bins(start: int, exclude: Iterable[tuple[int, int]] = ()) -> list[int]:
    """The 24 h of bins that end one bin before ``start``, without the
    bins of ``exclude`` (the same destination's other periods)."""
    stop = int(start) // BIN_S * BIN_S - PAD_BINS * BIN_S
    out = set(range(stop - BASELINE_S, stop, BIN_S))
    for s, e in exclude:
        out -= set(span_bins(s, e))
    return sorted(out)


def icmp_bins(target: str, pings: pd.DataFrame, loss: pd.DataFrame) -> pd.DataFrame:
    """A destination's ICMP as triage bins, from what the pass already read
    (series.fetch): ``med`` is the median of the cycles' medians, ``floor``
    the bin's lowest ping, ``loss`` the cycles' mean loss. No query."""
    rows = []
    if len(pings):
        cycles = pings.groupby("epoch")["values"].median()
        b = (cycles.index.to_numpy() // BIN_S) * BIN_S
        med = cycles.groupby(b).median()
        low = pings.groupby((pings["epoch"].to_numpy() // BIN_S) * BIN_S)["values"].min()
        rows.append(pd.DataFrame({"bin": med.index, "layer": "icmp", "probe": target,
                                  "med": med.to_numpy(), "loss": np.nan}))
        rows.append(pd.DataFrame({"bin": low.index, "layer": FLOOR, "probe": target,
                                  "med": low.to_numpy(), "loss": np.nan}))
    if len(loss):
        lo = loss.groupby((loss["epoch"].to_numpy() // BIN_S) * BIN_S)["loss_pct"].mean()
        rows.append(pd.DataFrame({"bin": lo.index, "layer": "icmp_loss", "probe": target,
                                  "med": np.nan, "loss": lo.to_numpy()}))
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    return pd.concat(rows, ignore_index=True).astype({"bin": "int64"})


# ───────────────────────── rules ─────────────────────────

def _spread(values: np.ndarray) -> float:
    return float(np.percentile(values, 90) - np.percentile(values, 10))


def _values(series: dict[int, float], bins: Iterable[int]) -> np.ndarray:
    return np.array([series[b] for b in bins
                     if b in series and not math.isnan(series[b])], dtype=float)


def rise(series: dict[int, float], during: list[int], base: list[int]) -> dict | None:
    """The median rule on one probe: None when the period or the baseline
    has too little data, else ``rose``, ``rise`` (during - baseline, in the
    probe's unit) and the ``threshold`` it was compared to."""
    d, b = _values(series, during), _values(series, base)
    if len(d) < MIN_COVERAGE * len(during) or len(b) < MIN_BASE_BINS:
        return None
    level = float(np.median(b))
    threshold = max(_spread(b), REL * abs(level))
    delta = float(np.median(d)) - level
    return {"rose": delta >= threshold and delta > 0, "rise": delta, "threshold": threshold}


def loss(series: dict[int, float], during: list[int], base: list[int]) -> dict | None:
    """The loss rule on one probe (percent): ``lost`` and the rise in points."""
    d, b = _values(series, during), _values(series, base)
    if len(d) < MIN_COVERAGE * len(during) or len(b) < MIN_BASE_BINS:
        return None
    threshold = max(LOSS_PP, _spread(b))
    delta = float(np.mean(d)) - float(np.mean(b))
    return {"lost": delta >= threshold, "rise": delta, "threshold": threshold}


def _majority(results: list[dict], key: str) -> tuple[bool, float] | None:
    """A layer of several probes: it moved when at least half of the probes
    with data did; its rise is the median of the probes that moved (of
    every probe with data when it did not move), so two of four resolvers
    up by 6 ms do not read as +3."""
    have = [r for r in results if r is not None]
    if not have:
        return None
    movers = [r for r in have if r[key]]
    moved = 2 * len(movers) >= len(have)
    return moved, float(np.median([r["rise"] for r in (movers if moved else have)]))


class Layers:
    """The bins of one pass, by layer and probe, as {bin: value} dicts."""

    def __init__(self, table: pd.DataFrame):
        self._series: dict[tuple[str, str, str], dict[int, float]] = {}
        if table is None or not len(table):
            return
        for (layer, probe), group in table.groupby(["layer", "probe"], sort=False):
            bins_ = group["bin"].astype("int64").to_numpy()
            for col in ("med", "loss"):
                vals = pd.to_numeric(group[col], errors="coerce").to_numpy(dtype=float)
                keep = ~np.isnan(vals)
                if keep.any():
                    self._series[(layer, probe, col)] = dict(
                        zip(bins_[keep].tolist(), vals[keep].tolist()))

    def probes(self, layer: str) -> list[str]:
        return sorted({p for (lay, p, _) in self._series if lay == layer})

    def get(self, layer: str, probe: str, col: str = "med") -> dict[int, float]:
        return self._series.get((layer, probe, col), {})


def probes_of(target: str, layers: Layers) -> dict[str, list[str]]:
    """The TCP and HTTP probes of ``target``: its name plus the suffix the
    configuration gives them, as Target Detail matches them."""
    out = {"tcp": [], "http": []}
    for probe in layers.probes("tcp"):
        if probe in (f"{target}_tcp443", f"{target}_tcp"):
            out["tcp"].append(probe)
    for probe in layers.probes("http"):
        if probe in (f"{target}_h1", f"{target}_h2", f"{target}_h3"):
            out["http"].append(probe)
    return out


def _gateway(layers: Layers, during: list[int]) -> str | None:
    """The uplink's main gateway: the ``cpe_latency`` target with the most
    bins in the period (a second address that answers now and then is
    not the one the house goes through)."""
    best, count = None, -1
    for probe in layers.probes("hop"):
        n = sum(1 for b in during if b in layers.get("hop", probe))
        if n > count:
            best, count = probe, n
    return best


def _layer(layers: Layers, layer: str, probes: list[str], during: list[int],
           padded: list[int], base: list[int]) -> dict:
    """One layer's verdict: rose / lost by majority, or no data."""
    if not probes:
        return {"rose": None, "lost": None, "rise": None, "loss_rise": None}
    r = _majority([rise(layers.get(layer, p), during, base) for p in probes], "rose")
    lo = _majority([loss(layers.get(layer, p, "loss"), padded, base) for p in probes
                    if layers.get(layer, p, "loss")], "lost")
    return {"rose": r[0] if r else None, "rise": r[1] if r else None,
            "lost": lo[0] if lo else None, "loss_rise": lo[1] if lo else None}


def _state(layer: dict, primary: str) -> str:
    """up / lost / flat / nodata; the period's own question first (rise for
    congestion, loss for degradation)."""
    rose, lost = layer.get("rose"), layer.get("lost")
    first, second = ((lost, rose) if primary == DEGRADATION else (rose, lost))
    first_name, second_name = (LOST, UP) if primary == DEGRADATION else (UP, LOST)
    if first:
        return first_name
    if second:
        return second_name
    # The period's own question could not be answered: no data, never
    # "did not", even when the other one could.
    return NODATA if first is None else FLAT


def _wifi(layers: Layers, deaf: set[int] | None, during: list[int], padded: list[int],
          base: list[int]) -> dict:
    """Deaf bins decide first (the radio heard nothing: ``lost``); then a
    signal down by WIFI_DB or a tx rate at or below BITRATE_RATIO of the
    baseline (``up``: the link moved). A host with no ``wifi_link`` at all
    (wired) has no data."""
    deaf_bins = sorted(b for b in padded if deaf and b in deaf)
    signal, rate = layers.get("wifi", "signal_dbm"), layers.get("wifi", "tx_bitrate_mbps")
    out = {"deaf_bins": len(deaf_bins), "signal_drop_db": None, "rate_ratio": None}
    d, b = _values(signal, during), _values(signal, base)
    if len(d) >= MIN_COVERAGE * len(during) and len(b) >= MIN_BASE_BINS:
        out["signal_drop_db"] = float(np.median(b) - np.median(d))
    d, b = _values(rate, during), _values(rate, base)
    if len(d) >= MIN_COVERAGE * len(during) and len(b) >= MIN_BASE_BINS and np.median(b) > 0:
        out["rate_ratio"] = float(np.median(d) / np.median(b))
    if deaf_bins:
        out["state"] = LOST
    elif ((out["signal_drop_db"] is not None and out["signal_drop_db"] >= WIFI_DB)
          or (out["rate_ratio"] is not None and out["rate_ratio"] <= BITRATE_RATIO)):
        out["state"] = UP
    elif out["signal_drop_db"] is not None or out["rate_ratio"] is not None:
        out["state"] = FLAT
    else:
        out["state"] = NODATA
    return out


def path(traces: list[dict], start: int, end: int) -> dict:
    """The path to the destination around the period, from its traces
    (``time``, ``changed``, ``as_changed``, ``as_path``), any order.

    ``changed`` when a trace within an hour of the period carries pathmeter's
    own ``changed``/``as_changed`` (it already asks for two equal traces);
    ``detour`` when one trace there took another AS path than the usual one
    (the most common in the 24 h before, else in the window) without being
    confirmed; ``nodata`` without a trace there, or with a gap over
    PATH_STALE_S between them; else ``unchanged``."""
    traces = sorted(traces, key=lambda t: t["time"])
    lo, hi = int(start) - PATH_PAD_S, int(end) + PATH_PAD_S
    inside = [t for t in traces if lo <= t["time"] <= hi]
    if not inside:
        return {"state": NODATA}
    edges = [lo] + [t["time"] for t in inside] + [hi]
    if max(b - a for a, b in zip(edges, edges[1:])) > PATH_STALE_S:
        return {"state": NODATA}
    before = [t["as_path"] for t in traces
              if int(start) - BASELINE_S <= t["time"] < lo and t.get("as_path")]
    usual_from = before or [t["as_path"] for t in inside if t.get("as_path")]
    usual = Counter(usual_from).most_common(1)[0][0] if usual_from else None
    for t in inside:
        if t.get("changed") or t.get("as_changed"):
            prior = [x for x in traces if x["time"] < t["time"] and x.get("as_path")]
            return {"state": CHANGED, "at": t["time"],
                    "from": prior[-1]["as_path"] if prior else usual,
                    "to": t.get("as_path")}
    for t in inside:
        if usual and t.get("as_path") and t["as_path"] != usual:
            new = [a for a in t["as_path"].split() if a not in usual.split()]
            return {"state": DETOUR, "at": t["time"], "ases": new}
    return {"state": UNCHANGED}


# ───────────────────────── one period ─────────────────────────

def evaluate(kind: str, target: str, period: dict, layers: Layers,
             periods: dict[str, list[tuple[int, int]]], deaf: set[int] | None,
             traces: list[dict] | None, others: list[str]) -> dict:
    """Every layer's verdict for one period of ``target``. ``periods`` are
    every destination's periods this pass (target -> spans), for the
    baselines; ``others`` the other destinations to check for breadth."""
    start, end = int(period["start"]), int(period["end"])
    own = [(s, e) for s, e in periods.get(target, []) if (s, e) != (start, end)]
    during, padded = span_bins(start, end), span_bins(start, end, PAD_BINS)
    base = base_bins(start, own)
    out: dict = {"kind": kind, "target": target, "base_bins": len(base)}

    out["icmp"] = _layer(layers, "icmp", [target] if layers.get("icmp", target) else [],
                         during, padded, base)
    icmp_loss = loss(layers.get("icmp_loss", target, "loss"), padded, base)
    if icmp_loss:
        out["icmp"]["lost"], out["icmp"]["loss_rise"] = icmp_loss["lost"], icmp_loss["rise"]
    own_probes = probes_of(target, layers)
    out["tcp"] = _layer(layers, "tcp", own_probes["tcp"], during, padded, base)
    out["http"] = _layer(layers, "http", own_probes["http"], during, padded, base)
    out["dns"] = _layer(layers, "dns", layers.probes("dns"), during, padded, base)
    gw = _gateway(layers, during)
    out["hop"] = _layer(layers, "hop", [gw] if gw else [], during, padded, base)
    out["wifi"] = _wifi(layers, deaf, during, padded, base)
    out["path"] = path(traces, start, end) if traces is not None else {"state": NODATA}

    moved = []
    for other in others:
        if other == target:
            continue
        o_base = base_bins(start, periods.get(other, []))
        if kind == DEGRADATION:
            r = loss(layers.get("icmp_loss", other, "loss"), padded, o_base)
            hit = bool(r and r["lost"])
        else:
            r = rise(layers.get(FLOOR, other), during, o_base)
            hit = bool(r and r["rose"])
        if hit:
            moved.append(other)
    out["others"] = moved
    for name in ("icmp", "tcp", "http", "dns", "hop"):
        out[name]["state"] = _state(out[name], kind)
    return out


def fields(result: dict) -> dict:
    """The fields the period's point carries (see store.py)."""
    f = {"triage": text(result), "tri_version": RULES_VERSION,
         "tri_others": len(result["others"]), "tri_path": result["path"]["state"],
         "tri_wifi": result["wifi"]["state"]}
    for name in ("tcp", "http", "dns", "hop"):
        f[f"tri_{name}"] = result[name]["state"]
    for name in ("icmp", "tcp", "http", "dns", "hop"):
        if result[name].get("rise") is not None:
            f[f"{name}_rise_ms"] = round(result[name]["rise"], 2)
    return f


# ───────────────────────── text ─────────────────────────

def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _num(x: float, unit: str = "") -> str:
    s = f"{x:+.1f}" if abs(x) < 2 else f"{x:+.0f}"
    return s + unit


def _clock(epoch: int) -> str:
    return time.strftime("%H:%M UTC", time.gmtime(int(epoch)))


def text(result: dict) -> str:
    """One sentence a reader can take at a glance: what ICMP did, what else
    moved with the period, what did not, what had no data, the path and the
    breadth. It names what moved, never a cause."""
    kind, target = result["kind"], result["target"]
    primary, verb, unit = ((LOST, "lost", " points") if kind == DEGRADATION
                           else (UP, "rose", " ms"))
    # The state of a layer that answered the period's question "no" and the
    # other question "yes": it did not move as asked, but it did move.
    other_state = LOST if kind == CONGESTION else UP
    key = "loss_rise" if kind == DEGRADATION else "rise"
    parts = []
    if result.get("base_bins", MIN_BASE_BINS) < MIN_BASE_BINS:
        # Every layer reads "no data" then: say why once, not six times.
        parts.append(f"no baseline: the 24 h before were inside other periods of {target}")
        return "; ".join(parts + _context(result, target, verb)) + "."

    # The sentence always opens with ICMP, the period's own signal: either
    # in the clause of what moved, or in a clause of its own.
    icmp_other = False
    if result["icmp"]["state"] != primary:
        clause, icmp_other = _icmp_unmoved(kind, result["icmp"])
        parts.append(clause)
    dest = [n for n in ("icmp", "tcp", "http") if result[n]["state"] == primary]
    if dest:
        names = _join([LAYER_NAMES[n] for n in dest])
        nums = _join([_num(result[n][key]) for n in dest])
        stat = "mean" if kind == DEGRADATION else "median"
        together = " together" if len(dest) > 1 else ""
        parts.append(f"{names} to {target} {verb}{together} ({stat} {nums}{unit})")
    general = [n for n in ("dns", "hop") if result[n]["state"] == primary]
    too = " too" if dest else ""
    for n in general:
        parts.append(f"{LAYER_NAMES[n]} {verb}{too} ({_num(result[n][key])}{unit})")
    wifi = result["wifi"]
    if wifi["state"] == LOST:
        n = wifi["deaf_bins"]
        parts.append(f"Wi-Fi was deaf in {n} bin{'s' if n != 1 else ''}")
    elif wifi["state"] == UP:
        if wifi.get("signal_drop_db") is not None and wifi["signal_drop_db"] >= WIFI_DB:
            parts.append(f"Wi-Fi signal fell {wifi['signal_drop_db']:.0f} dB")
        else:
            parts.append(f"Wi-Fi tx rate fell to {100 * wifi['rate_ratio']:.0f} %")

    # "Did not" and "no data" answer the period's question itself, read
    # from its own flag: a layer that moved only on the other question (it
    # lost in a congestion period) is in one of them too, and _state's
    # ``lost`` for "no median, but loss" is no data, never "did not".
    asked = "lost" if kind == DEGRADATION else "rose"
    flat = [LAYER_NAMES[n] for n in ("tcp", "http", "dns", "hop")
            if result[n]["state"] != primary and result[n].get(asked) is False]
    if wifi["state"] == FLAT:
        flat.append("Wi-Fi")
    if flat:
        parts.append(f"{_join(flat)} did not")
    nodata = [LAYER_NAMES[n] for n in ("tcp", "http", "dns", "hop")
              if result[n]["state"] != primary and result[n].get(asked) is None]
    if wifi["state"] == NODATA:
        nodata.append("Wi-Fi")
    if nodata:
        parts.append(f"{_join(nodata)}: no data")

    # The other question: loss during a congestion period, a rise during a
    # degradation one. ICMP's answer is in its own clause when it did not
    # move as asked.
    other_flag, other_key = ("lost", "loss_rise") if kind == CONGESTION else ("rose", "rise")
    also = [n for n in ("icmp", "tcp", "http", "dns", "hop")
            if (result[n]["state"] == other_state and n != "icmp")
            or (result[n]["state"] == primary and result[n].get(other_flag))]
    other = " other" if icmp_other else ""
    if kind == CONGESTION:
        if also:
            parts.append(f"{_join([LAYER_NAMES[n] for n in also])} also lost ("
                         + _join([_num(result[n][other_key]) for n in also]) + " points)")
        else:
            parts.append(f"no{other} loss")
    else:
        if also:
            parts.append(f"{_join([LAYER_NAMES[n] for n in also])} also rose (median "
                         + _join([_num(result[n][other_key]) for n in also]) + " ms)")
        else:
            parts.append(f"no{other} latency rise")

    return "; ".join(parts + _context(result, target, verb)) + "."


def _icmp_unmoved(kind: str, icmp: dict) -> tuple[str, bool]:
    """ICMP's clause when it did not move on the period's question, and
    whether it already said ICMP moved on the other one. For congestion the
    question is the median: Jitterbug found the period on the RTT floor, the
    triage reads medians, so a flat median is a reading, not a contradiction.
    For a degradation it is the loss."""
    if kind == DEGRADATION:
        asked, answer, moved, said = "loss", icmp.get("lost"), icmp.get("rose"), \
            "its median rose ({} ms)"
        amount = icmp.get("rise")
    else:
        asked, answer, moved, said = "median", icmp.get("rose"), icmp.get("lost"), \
            "its loss rose ({} points)"
        amount = icmp.get("loss_rise")
    # The question's own answer, not the state: _state reads "lost" when the
    # median had no data but the loss rose, and that median did not "not rise".
    clause = (f"ICMP's {asked}: no data" if answer is None
              else f"ICMP's {asked} did not rise")
    if moved and amount is not None:
        return f"{clause}, though {said.format(_num(amount))}", True
    return clause, False


def _context(result: dict, target: str, verb: str) -> list[str]:
    """The path and the breadth: the end of every sentence."""
    parts = []
    p = result["path"]
    if p["state"] == CHANGED:
        new = [a for a in (p.get("to") or "").split() if a not in (p.get("from") or "").split()]
        change = f" (now via {', '.join('AS' + a for a in new)})" if new else ""
        parts.append(f"path changed at {_clock(p['at'])}{change}")
    elif p["state"] == DETOUR:
        via = f" ({', '.join('AS' + a for a in p['ases'])})" if p.get("ases") else ""
        parts.append(f"path: one trace off the usual path at {_clock(p['at'])}{via}, "
                     "not confirmed")
    elif p["state"] == UNCHANGED:
        parts.append("path unchanged")
    else:
        parts.append("path: no traces")

    others = result["others"]
    primary = LOST if result["kind"] == DEGRADATION else UP
    if others:
        word = "destination" if len(others) == 1 else "destinations"
        named = ", ".join(others[:MAX_NAMED])
        if len(others) > MAX_NAMED:
            named += f" and {len(others) - MAX_NAMED} more"
        parts.append(f"{len(others)} other {word} {verb} too: {named}")
    elif result["icmp"].get("state") == primary:
        parts.append(f"only {target} {verb}")
    else:
        parts.append(f"no other destination {verb}")
    return parts


# ───────────────────────── one pass ─────────────────────────

class Triage:
    """The triage of one pass: reads the layers once, on the first period
    there is to write, and enriches the periods store.py is about to write.

    ``read_layers(start, now)`` returns the bins table of every layer but
    ICMP (series.triage_bins); ``read_paths(targets, start, now)`` the
    traces by target (series.path_traces). ICMP comes from what the pass
    already read (:func:`icmp_bins`, :meth:`add_icmp`). A failure is
    logged and counted, and the period is written without the triage."""

    def __init__(self, targets: list[str], start: int, now: int, deaf: set[int] | None,
                 read_layers: Callable[[int, int], pd.DataFrame],
                 read_paths: Callable[[list[str], int, int], dict[str, list[dict]]],
                 categories: dict[str, str] | None = None):
        self.targets, self.start, self.now, self.deaf = list(targets), int(start), int(now), deaf
        self._read_layers, self._read_paths = read_layers, read_paths
        self.categories = categories or {}
        self._icmp: list[pd.DataFrame] = []
        self.periods: dict[str, list[tuple[int, int]]] = {}
        self._layers: Layers | None = None
        self._failed = False
        self._paths: dict[str, list[dict]] | None = None
        self.seconds = 0.0
        self.read_seconds = 0.0
        self.errors = 0
        self.done = 0

    def add_icmp(self, target: str, pings: pd.DataFrame, loss_: pd.DataFrame,
                 periods: Iterable[dict]) -> None:
        """What the pass read for ``target`` and every period it found."""
        began = time.monotonic()
        try:
            self._icmp.append(icmp_bins(target, pings, loss_))
            self.periods.setdefault(target, []).extend(
                (int(p["start"]), int(p["end"])) for p in periods)
        except Exception:  # noqa: BLE001 -- the triage never fails the pass
            log.warning("triage: could not bin %s", target, exc_info=True)
            self.errors += 1
        self.seconds += time.monotonic() - began

    def _load(self) -> bool:
        """Read the layers once. A read that failed is not retried for every
        destination: the pass writes its periods without the triage."""
        if self._layers is not None or self._failed:
            return self._layers is not None
        began = time.monotonic()
        try:
            table = self._read_layers(self.start, self.now)
            frames = [t for t in [table, *self._icmp] if len(t)]
            self._layers = Layers(pd.concat(frames, ignore_index=True) if frames else None)
        except Exception:  # noqa: BLE001 -- counted once; the periods go without it
            log.warning("triage: could not read the layers", exc_info=True)
            self.errors += 1
            self._failed = True
            self.read_seconds += time.monotonic() - began
            return False
        try:
            self._paths = self._read_paths(self.targets, self.start, self.now)
        except Exception:  # noqa: BLE001 -- the path is one layer; no traces = no data
            log.warning("triage: could not read the traces", exc_info=True)
            self.errors += 1
            self._paths = None
        self.read_seconds += time.monotonic() - began
        return True

    def prepare(self) -> None:
        """Read the layers now, before store.py deletes anything: the reads
        then never sit between a delete and its write. Never raises."""
        began = time.monotonic()
        self._load()
        self.seconds += time.monotonic() - began

    def others(self) -> list[str]:
        """Destinations to check for breadth: the pass's targets except the
        gateway's own (the first hop is its own layer)."""
        return [t for t in self.targets if self.categories.get(t) != "cpe"]

    def enrich(self, kind: str, target: str, write: list[dict]) -> None:
        """Set the triage fields on each period store.py is about to write,
        in place. Never raises."""
        if not write:
            return
        began = time.monotonic()
        if self._load():
            traces = None if self._paths is None else self._paths.get(target, [])
            for p in write:
                try:
                    p.update(fields(evaluate(kind, target, p, self._layers, self.periods,
                                             self.deaf, traces, self.others())))
                    self.done += 1
                except Exception:  # noqa: BLE001 -- this period goes without it
                    log.warning("triage failed for a period of %s", target, exc_info=True)
                    self.errors += 1
        self.seconds += time.monotonic() - began

    def report(self) -> dict:
        return {"periods": self.done, "errors": self.errors,
                "seconds": round(self.seconds, 2), "read_seconds": round(self.read_seconds, 2)}
