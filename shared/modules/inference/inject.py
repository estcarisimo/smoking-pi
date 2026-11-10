"""Prove the shared path end to end, on a staging Pi only.

The shared detectors (shared.py, segments.py) and the path meter's episode
rounds shipped in v2.44.0 and v2.51.0, and no shared event has happened
since: none of them had written a point in production by the second read
(2026-11-09). Their unit tests prove each step on fakes; nothing had proved
that a real evening reaches the tables of Connectivity & Quality. This
tool makes one happen, on purpose, where it costs nothing.

It runs inside the inference container, driven by
``shared/scripts/inject-shared-loss.sh``, which holds the production guard
(docs/inference.md, *Proving the shared path on staging*). Every subcommand
refuses to run without ``--staging``, so it is never one typo away on the
reference Pi.

``plan``
    Which evening, on whom, and what the pass must then write. The evening
    lasts ``--hours`` (3) and ends two hours before the bin cache's last pass,
    so it is a closed part of the cache and not the tail a pass re-reads
    from InfluxDB. Who loses: the destinations crossing one network or
    exchange point on their hourly paths (a *planted segment*), chosen so
    that the period qualifies as ``shared`` (a third of the voting operators,
    two kinds) and the module's own scorer can tell them apart; failing
    that, every voter. ``--csv`` takes the evening from a file instead (one
    row per 15-minute bin and target: ``bin,target,loss``, loss a ratio
    0-1, as the cache keeps it), moved to the same place in the window. The
    plan is chosen with the module's scorer on purpose: this proves the
    path from the cache to the dashboards, not the scorer (its unit tests
    do that).
``inject``
    Copies ``loss.csv``/``loss.json`` aside and writes the evening into the
    cache. ``full_at`` is set to now, so the next pass does not re-read the
    whole window from InfluxDB and lose the evening.
``pass``
    One pass as main.run_once runs it, with the shared detector and the
    segments, and without the per-target detectors (their points are not
    what is being proved, and they take minutes).
``widespread``
    One cycle of widespread loss in InfluxDB: a ``loss`` point at
    ``WIDESPREAD_LOSS`` for every target that reported in the last
    15 minutes (the gateway excepted), at a second no point holds, so the
    path meter's episode check (the alerter's rule, common.loss_events)
    sees an episode under way and traces it.
``verify``
    Reads the three outputs back and compares them with the plan.
``cleanup``
    Deletes the injected cycle, restores the cache and deletes every point
    the test made: the ``shared_degradation`` and ``shared_segment`` points
    around the evening, and the ``trigger=episode`` traces since the cycle.
    ``--keep-points`` deletes the injected cycle only, the data the alerter
    reads, and leaves the evening and its outputs in place (for
    screenshots) until a ``cleanup`` without it.

The state of a test is ``inject/`` in the state volume: the plan, and the
cache it copied aside. ``inject`` refuses while one is there.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import bins
import segments
import shared
import store
import tags as tagging
from common.links import target_base_name
from common.tsdb import flux_str, influx_bucket, query_influx

log = logging.getLogger("inference.inject")

BIN_S = shared.BIN_S
CACHE = "loss"
DEFAULT_HOURS = 3
# The evening ends this long before the cache's last pass: the next pass
# re-reads from bins.TAIL_S before it, and must find the evening closed.
CLEAR_OF_TAIL_S = 2 * 3600
# Loss per affected target: the operator with most targets first, each next
# one a step lower, then scaled up until the pooled loss reaches POOLED_PCT
# (the detector needs 2 % over 30 minutes; this leaves no doubt).
TOP_LEVEL = 0.12
STEP_LEVEL = 0.02
MIN_LEVEL = 0.04
MAX_LEVEL = 0.6
POOLED_PCT = 5.0
JITTER = 0.15
# The widespread cycle: over the alerter's WIDESPREAD_LOSS_PCT (30), short of
# total loss, which reads as the uplink down and is not traced.
WIDESPREAD_LOSS = 0.6
WIDESPREAD_LOOKBACK = "-15m"
WIDESPREAD_MEASUREMENTS = ("latency", "dns_latency", "http_latency", "tcp_latency")
FIRST_HOP = "cpe"
# How far a written period may sit from the planned evening and still be it.
SLACK_S = 2 * BIN_S
# Around the evening: where shared points must not exist before the test,
# and where cleanup deletes them.
MARGIN_S = 3600
EPISODE_MEASUREMENTS = ("path_trace", "path_hop", "pathmeter_pass")
TRIGGER = "episode"
NOT_TAGS = {"result", "table", "_start", "_stop", "_time", "_value", "_field",
            "_measurement"}


class PlanError(Exception):
    """The test cannot be planned or run as asked; the message says why."""


def workdir() -> Path:
    return bins.state_dir() / "inject"


def _align(epoch: int) -> int:
    return int(epoch) // BIN_S * BIN_S


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# The evening
# ---------------------------------------------------------------------------


def placement(through: int, hours: int, start: int | None = None) -> tuple[int, int]:
    """[start, end) of the evening. By default it ends CLEAR_OF_TAIL_S before
    the cache's last pass (``through``); a given start must still end before
    the tail the next pass re-reads."""
    if hours < 1:
        raise PlanError("--hours must be at least 1: the detector needs 30 minutes")
    if start is None:
        end = _align(through) - CLEAR_OF_TAIL_S
        start = end - hours * 3600
    else:
        start = _align(start)
        end = start + hours * 3600
    if end > _align(through) - bins.TAIL_S - BIN_S:
        raise PlanError("the evening must end at least 75 minutes before the cache's last "
                        f"pass ({_iso(through)}): the next pass re-reads that tail from InfluxDB")
    return start, end


def levels(affected: list[str], of, n_voters: int,
           pooled_pct: float = POOLED_PCT) -> dict[str, float]:
    """Mean loss (ratio) per affected target: one level per operator, the
    operator with most affected targets highest (then by name), so the
    attribution has one clear leader; scaled up until the pooled loss over
    ``n_voters`` reaches ``pooled_pct``, each at most MAX_LEVEL."""
    by_op: dict[str, list[str]] = {}
    for t in affected:
        by_op.setdefault(of(t)[0], []).append(t)
    order = sorted(by_op, key=lambda op: (-len(by_op[op]), op))
    out = {}
    for i, op in enumerate(order):
        for t in by_op[op]:
            out[t] = max(MIN_LEVEL, TOP_LEVEL - STEP_LEVEL * i)
    total, need = sum(out.values()), pooled_pct / 100.0 * n_voters
    if total and total < need:
        out = {t: min(MAX_LEVEL, v * need / total) for t, v in out.items()}
    return out


def evening(bin_starts: list[int], level: dict[str, float], seed: int = 0) -> pd.DataFrame:
    """``bin, target, loss`` rows: each target at its level, give or take
    JITTER per bin (seeded: the same plan gives the same evening)."""
    rng = np.random.default_rng(seed)
    rows = []
    for t in sorted(level):
        for b in bin_starts:
            v = level[t] * (1 + JITTER * rng.uniform(-1, 1))
            rows.append({"bin": int(b), "target": t, "loss": round(min(1.0, max(0.0, v)), 4)})
    return pd.DataFrame(rows, columns=["bin", "target", "loss"])


def read_csv(path: str) -> pd.DataFrame:
    """A maintainer's evening: ``bin,target,loss``, the bin as epoch seconds
    or an ISO time (its start, on a 15-minute boundary), loss a ratio 0-1."""
    df = pd.read_csv(path, dtype={"target": str})
    missing = {"bin", "target", "loss"} - set(df.columns)
    if missing:
        raise PlanError(f"the CSV needs the columns bin, target, loss; missing {sorted(missing)}")
    df = df.dropna(subset=["bin", "target", "loss"])
    if df.empty:
        raise PlanError("the CSV has no rows")

    def epoch(value) -> int:
        text = str(value).strip()
        if re.fullmatch(r"\d+(\.0+)?", text):
            return int(float(text))
        return int(pd.Timestamp(text).timestamp())  # a naive time is UTC

    out = pd.DataFrame({"bin": [epoch(v) for v in df["bin"]], "target": df["target"].astype(str),
                        "loss": df["loss"].astype(float)})
    if (out["bin"] % BIN_S).any():
        raise PlanError("every bin must start on a 15-minute boundary")
    if (out["loss"] < 0).any() or (out["loss"] > 1).any():
        raise PlanError("loss is a ratio 0-1, as the cache keeps it, not a percent")
    return out.reset_index(drop=True)


def shift(rows: pd.DataFrame, start: int) -> pd.DataFrame:
    """The same evening, moved so its first bin is ``start``."""
    out = rows.copy()
    out["bin"] = out["bin"] - int(out["bin"].min()) + _align(start)
    return out


def apply(cache: pd.DataFrame, rows: pd.DataFrame, span: tuple[int, int],
          clear: list[str]) -> pd.DataFrame:
    """The cache with the evening in it. ``rows`` set the loss of their
    (bin, target); every target in ``clear`` that ``rows`` leave out loses
    nothing in the span. Counts are kept; a bin a target did not report in
    gets that target's usual count and category."""
    out = cache.copy()
    start, end = span
    in_span = (out["bin"] >= start) & (out["bin"] < end)
    named = set(rows["target"])
    out.loc[in_span & out["target"].isin([t for t in clear if t not in named]), "loss"] = 0.0
    if rows.empty:
        return out.sort_values(["bin", "target"], kind="stable").reset_index(drop=True)[
            shared.BIN_COLUMNS]
    given = rows[["bin", "target", "loss"]].rename(columns={"loss": "new"})
    given = given.astype({"bin": "int64", "new": float})
    merged = out.merge(given, on=["bin", "target"], how="outer", indicator=True)
    usual = out.groupby("target")["count"].median().to_dict()
    category = out.groupby("target")["category"].first().to_dict()
    new = (merged["_merge"] != "left_only").to_numpy()
    added = (merged["_merge"] == "right_only").to_numpy()
    merged["loss"] = np.where(new, merged["new"], merged["loss"])
    merged["count"] = np.where(added, merged["target"].map(usual).fillna(3.0), merged["count"])
    merged["category"] = np.where(added, merged["target"].map(category).fillna(""),
                                  merged["category"])
    merged = merged.astype({"bin": "int64", "loss": float, "count": float})
    return (merged.sort_values(["bin", "target"], kind="stable")
            .reset_index(drop=True)[shared.BIN_COLUMNS])


# ---------------------------------------------------------------------------
# What the pass should write
# ---------------------------------------------------------------------------


def expectations(target_pct: dict[str, float], voters: list[str], of) -> dict:
    """What qualification should make of an evening in which each voter
    lost ``target_pct`` (percent) on average: the operators that lost, the
    leader of ``by_operator``, the kinds and the scope -- computed here from
    the plan, by the rule docs/inference.md states, not by shared.qualify."""
    ops: dict[str, list[str]] = {}
    for t in voters:
        op = of(t)[0]
        if op != tagging.UNKNOWN:
            ops.setdefault(op, []).append(t)
    op_loss = {op: float(np.mean([target_pct.get(t, 0.0) for t in ts])) for op, ts in ops.items()}
    lossy = sorted(op for op, v in op_loss.items() if v >= shared.LOSSY_PCT)
    kinds = sorted({of(ops[op][0])[1] for op in lossy})
    share = len(lossy) / len(op_loss) if op_loss else 0.0
    top = max(op_loss, key=lambda op: (op_loss[op], op)) if op_loss else ""
    return {
        "scope": "shared" if share >= shared.SHARED_OPERATOR_SHARE
        and len(kinds) >= shared.MIN_KINDS else "dominated",
        "top_operator": top, "lossy_operators": lossy, "operators": len(op_loss),
        "kinds": kinds,
        "pooled_pct": float(np.mean([target_pct.get(t, 0.0) for t in voters])) if voters else 0.0,
    }


def crossings(voters: list[str], traces: dict[str, dict]) -> dict[tuple, set[str]]:
    """Segment -> the voters' destinations (base names) whose path crosses it."""
    bases = {target_base_name(v) for v in voters}
    out: dict[tuple, set[str]] = {}
    for base in sorted(bases & set(traces)):
        if not segments._comparable(traces[base]):
            continue
        for seg in segments.segments(traces[base]):
            out.setdefault(seg, set()).add(base)
    return out


def choose(voters: list[str], of, traces: dict[str, dict], span: tuple[int, int],
           name_of=None, allow_router: bool = False) -> dict:
    """Who loses in the synthetic evening: the destinations behind one
    planted segment when one qualifies as ``shared`` and the scorer can find
    it (strong first, then a network before an exchange before a router,
    then the most affected), else every voter. Routers are planted only with
    ``allow_router``: their addresses would end up in the screenshots."""
    picked = None
    for seg, through in crossings(voters, traces).items():
        if seg[0] == "router" and not allow_router:
            continue
        affected = sorted(v for v in voters if target_base_name(v) in through)
        option = _option(affected, voters, of, traces, span, name_of)
        h = option["hypothesis"]
        if (option["expect"]["scope"] != "shared" or not h
                or h["confidence"] not in ("strong", "weak")):
            continue
        rank = (h["confidence"] != "strong", segments.KIND_RANK[seg[0]], -len(affected),
                str(seg))
        if picked is None or rank < picked[0]:
            option["planted"] = segments.label(seg, name_of)
            picked = (rank, option)
    if picked:
        return picked[1]
    option = _option(sorted(voters), voters, of, traces, span, name_of)
    option["planted"] = None
    return option


def _option(affected, voters, of, traces, span, name_of) -> dict:
    level = levels(affected, of, len(voters))
    pct = {t: level.get(t, 0.0) * 100 for t in voters}
    expect = expectations(pct, voters, of)
    h = None
    if traces:
        h = segments.hypothesis({"start": span[0], "end": span[1],
                                 "mean_loss_pct": expect["pooled_pct"], "target_loss": pct},
                                traces, name_of)
    return {"affected": affected, "levels": level, "expect": expect, "hypothesis": h}


def segment_expectation(h: dict | None) -> dict | None:
    """The part of a planned hypothesis verify compares, or None: with no
    destination traced, segments.run writes nothing."""
    if not h:
        return None
    return {"confidence": h["confidence"], "segment": h.get("segment", ""),
            "traced": h["traced"], "affected": h["affected"], "unaffected": h["unaffected"]}


# ---------------------------------------------------------------------------
# Reading the outputs back
# ---------------------------------------------------------------------------


def points_flux(measurement: str, start: int, stop: int) -> str:
    return (f"from(bucket: {flux_str(influx_bucket())}) "
            f"|> range(start: {int(start)}, stop: {int(stop)}) "
            f"|> filter(fn: (r) => r._measurement == {flux_str(measurement)}) "
            '|> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")')


def episode_flux(since: int) -> str:
    return (f"from(bucket: {flux_str(influx_bucket())}) "
            f"|> range(start: {int(since)}) "
            '|> filter(fn: (r) => r._measurement == "path_trace" and r.trigger == "episode") '
            '|> filter(fn: (r) => r._field == "episode" or r._field == "reached" '
            'or r._field == "first_hop") '
            '|> pivot(rowKey: ["_time", "target"], columnKey: ["_field"], valueColumn: "_value") '
            '|> keep(columns: ["_time", "target", "episode", "reached", "first_hop"])')


def periods(rows: list[dict]) -> list[dict]:
    """Pivoted shared points -> dicts with ``start`` (epoch) and their fields."""
    out = []
    for r in rows:
        if r.get("_time") is None or r.get("end") is None:
            continue
        p = {k: v for k, v in r.items() if k not in NOT_TAGS}
        p["start"] = int(pd.Timestamp(r["_time"]).timestamp())
        p["end"] = int(r["end"])
        out.append(p)
    return sorted(out, key=lambda p: p["start"])


def _check(name: str, ok: bool, saw, expected) -> dict:
    return {"check": name, "ok": bool(ok), "saw": saw, "expected": expected}


def leader(listing: str | None) -> str:
    """The first name of a ``by_operator`` string (``Fastly 9.0 %, ...``)."""
    m = re.match(r"\s*(.*?) [\d.]+ %", listing or "")
    return m.group(1) if m else ""


def check_period(found: list[dict], plan: dict) -> tuple[list[dict], dict | None]:
    """The ``shared_degradation`` checks, and the period checked."""
    start, end = plan["span"]
    expect = plan["expect"]
    over = [p for p in found if p["start"] < end and p["end"] > start]
    checks = [_check("one shared_degradation period over the evening", len(over) == 1,
                     len(over), 1)]
    if not over:
        return checks, None
    p = max(over, key=lambda q: min(q["end"], end) - max(q["start"], start))
    kinds = sorted(k for k in str(p.get("kinds") or "").split(",") if k)
    mean = float(p.get("mean_loss_pct") or 0.0)
    checks += [
        _check("starts with the evening", abs(p["start"] - start) <= SLACK_S,
               _iso(p["start"]), f"{_iso(start)} ± {SLACK_S // 60} min"),
        _check("ends with the evening", abs(p["end"] - end) <= SLACK_S,
               _iso(p["end"]), f"{_iso(end)} ± {SLACK_S // 60} min"),
        _check("scope", p.get("scope") == expect["scope"], p.get("scope"), expect["scope"]),
        _check("operator that lost most (by_operator)",
               leader(p.get("by_operator")) == expect["top_operator"],
               p.get("by_operator"), expect["top_operator"]),
        _check("kinds that lost", kinds == expect["kinds"], ",".join(kinds),
               ",".join(expect["kinds"])),
        _check("pooled loss within half to one and a half the planned",
               0.5 * expect["pooled_pct"] <= mean <= 1.5 * expect["pooled_pct"],
               round(mean, 2), round(expect["pooled_pct"], 2)),
    ]
    return checks, p


def check_segment(found: list[dict], period: dict | None, plan: dict) -> list[dict]:
    """The ``shared_segment`` checks: the hypothesis at the period's start."""
    want = plan.get("segment")
    if want is None:
        return [_check("no shared_segment (no destination traced)",
                       not found, len(found), 0)]
    if period is None:
        return [_check("shared_segment hypothesis", False, "no period to read it for",
                       want["confidence"])]
    at = [h for h in found if h["start"] == period["start"]]
    if not at:
        return [_check("shared_segment hypothesis at the period's start", False, 0, 1)]
    h = at[0]
    return [
        _check("shared_segment hypothesis at the period's start", True, 1, 1),
        _check("confidence", h.get("confidence") == want["confidence"],
               h.get("confidence"), want["confidence"]),
        _check("segment", (h.get("segment") or "") == want["segment"],
               h.get("segment") or "(none)", want["segment"] or "(none)"),
        _check("destinations traced", int(h.get("traced") or 0) >= 1,
               int(h.get("traced") or 0), f"≥ 1 (planned {want['traced']})"),
    ]


def check_episode(rows: list[dict], plan: dict) -> list[dict]:
    """The ``trigger=episode`` traces the path meter wrote after the
    widespread cycle."""
    ws = plan.get("widespread")
    if not ws:
        return [_check("trigger=episode traces", False, "no widespread cycle injected",
                       "≥ 1 trace")]
    seen = ws.get("seen") or {}
    rule = _check("the alerter's widespread rule saw the cycle",
                  bool(seen) and seen.get("reading") not in ("uplink_down", "monitor_dns"),
                  f"{seen.get('targets_affected')} of {seen.get('targets_total')}"
                  f" ({seen.get('reading') or 'no reading'})" if seen else "no widespread run",
                  "a run the path meter traces")
    traced = sorted({str(r.get("target")) for r in rows if r.get("target")})
    ids = sorted({str(r.get("episode")) for r in rows if r.get("episode")})
    return [rule, _check("trigger=episode traces after the widespread cycle", bool(traced),
                   f"{len(rows)} traces, {len(traced)} destinations"
                   + (f" ({', '.join(traced[:8])}{'…' if len(traced) > 8 else ''})"
                      if traced else ""), "≥ 1 trace"),
            _check("each carries its episode", bool(ids) and all(r.get("episode") for r in rows),
                   ", ".join(ids) or "none", "the episode's start")]


# ---------------------------------------------------------------------------
# Steps (InfluxDB and the state volume)
# ---------------------------------------------------------------------------


def _hosts(query) -> dict[str, str]:
    return {r["target"]: str(r["_value"]) for r in query(shared.hosts_flux())
            if r.get("target") and r.get("_value")}


def _load_manifest() -> dict | None:
    try:
        return json.loads((workdir() / "manifest.json").read_text())
    except (OSError, ValueError):
        return None


def _save_manifest(plan: dict) -> None:
    d = workdir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "manifest.json.tmp"
    tmp.write_text(json.dumps(plan, indent=1, sort_keys=True, default=str))
    os.replace(tmp, d / "manifest.json")


def _cache() -> tuple[pd.DataFrame, dict]:
    loaded = bins._load(CACHE, shared.BIN_COLUMNS, bins.state_dir(), ("target", "category"))
    if loaded is None:
        raise PlanError("no loss bin cache in the state volume yet: let one pass run first")
    return loaded


def make_plan(args, now: int, query=query_influx, tag_store=None, deaf=None) -> dict:
    """Plan the test from the cache, the tags and the hourly paths."""
    import series

    table, meta = _cache()
    start, end = placement(meta["through"], args.hours,
                           None if not args.start else int(pd.Timestamp(args.start).timestamp()))
    span = (start, end)
    d_start = now - int(os.environ.get("INFERENCE_DEGRADATION_DAYS", "") or 7) * 86400
    window = table[table["bin"] >= _align(d_start)]
    deaf_bins = deaf if deaf is not None else series.deaf_bins(d_start, now, query=query)
    hosts = _hosts(query)
    tag_store = tag_store or tagging.Tags()

    def of(t):
        return tag_store.of(t, hosts)

    # The monitor's bins as the pass will see them: the radio deaf, an
    # upgrade restarting the stack (its markers), the gateway losing.
    upgrades = shared.upgrade_bins(query(shared.upgrade_flux(d_start, now)))
    probe = shared.detect_bins(window, set(deaf_bins) | upgrades, of,
                               find=lambda df: {"periods": []})
    voters = list(probe.get("voter_targets") or [])
    if len(voters) < shared.MIN_VOTERS:
        raise PlanError(f"{len(voters)} voters: the shared detector needs {shared.MIN_VOTERS}")
    spans = set(range(start, end, BIN_S))
    hit = sorted(spans & set(probe.get("monitor") or ()))
    if hit:
        raise PlanError(f"{len(hit)} bins of the evening are the monitor's (deaf radio, an "
                        "upgrade, the gateway losing): pick another --start")
    rows = query(segments.traces_flux(start - segments.LOOKBACK_S, end))
    traces = segments.paths(rows, query(segments.ixp_flux(start - segments.LOOKBACK_S, end)),
                            start, end) if rows else {}
    from common import asn
    asn.use_table(bins.state_dir() / "routing")
    cats = window.groupby("target")["category"].first().to_dict()
    gateway = sorted(t for t, c in cats.items() if c == FIRST_HOP)
    if args.csv:
        given = shift(read_csv(args.csv), start)
        given = given[given["bin"] < end]
        unknown = sorted(set(given["target"]) - set(window["target"]))
        given = given[~given["target"].isin(unknown)]
        pct = (given.groupby("target")["loss"].mean() * 100).to_dict()
        option = {"affected": sorted(t for t, v in pct.items() if v >= shared.LOSSY_PCT),
                  "levels": {t: v / 100 for t, v in pct.items()},
                  "expect": expectations(pct, voters, of), "planted": None,
                  "hypothesis": segments.hypothesis(
                      {"start": start, "end": end, "target_loss": {t: pct.get(t, 0.0)
                                                                    for t in voters}},
                      traces, asn.as_name) if traces else None,
                  "skipped": unknown, "pattern": "csv"}
        injected = given
    else:
        option = choose(voters, of, traces, span, asn.as_name, args.allow_router)
        option["pattern"] = "segment" if option["planted"] else "every voter"
        injected = evening(list(range(start, end, BIN_S)), option["levels"], args.seed)
    if option["expect"]["scope"] != "shared":
        raise PlanError("this evening would read as dominated, not shared: "
                        f"{option['expect']['lossy_operators']} of {option['expect']['operators']}"
                        f" operators, kinds {option['expect']['kinds']}")
    before = {m: len(periods(query(points_flux(m, start - MARGIN_S, end + MARGIN_S))))
              for m in (store.SHARED, store.SHARED_SEGMENT)}
    if any(before.values()):
        raise PlanError(f"shared points already exist around the evening {before}: cleanup "
                        "would delete them; pick another --start")
    return {
        "created": now, "span": [start, end], "pattern": option["pattern"],
        "planted": option["planted"], "affected": option["affected"],
        "levels": {t: round(v, 4) for t, v in option["levels"].items()},
        "voters": voters, "expect": option["expect"],
        "segment": segment_expectation(option["hypothesis"]),
        "skipped_targets": option.get("skipped", []),
        "operators": {t: list(of(t)) for t in voters},
        "_rows": injected.to_dict("records"),
        # The gateway stays clean (an evening beyond the house); a synthetic
        # evening is all synthetic, a maintainer's leaves the rest as it was.
        "_clear": gateway + ([] if args.csv else
                             [t for t in voters if t not in option["levels"]]),
    }


def inject(plan: dict, now: int) -> dict:
    """Copy the cache aside and write the evening into it."""
    if _load_manifest():
        raise PlanError("a test is already under way (inject/manifest.json): cleanup first")
    table, meta = _cache()
    d = workdir()
    d.mkdir(parents=True, exist_ok=True)
    for suffix in (".csv", ".json"):
        src = bins.state_dir() / f"{CACHE}{suffix}"
        (d / f"{CACHE}{suffix}").write_bytes(src.read_bytes())
    rows = pd.DataFrame(plan.pop("_rows"), columns=["bin", "target", "loss"])
    out = apply(table, rows, tuple(plan["span"]), plan.pop("_clear"))
    plan["backup"] = {"through": meta["through"], "full_at": meta["full_at"]}
    plan["injected_rows"] = int(len(rows))
    _save_manifest(plan)
    bins._save(CACHE, out, {"from": meta["from"], "through": meta["through"], "full_at": now},
               bins.state_dir())
    return plan


def run_pass(now: int | None = None) -> dict:
    """One shared pass, as main.run_once runs it: the shared detector, then
    the segments; no per-target detector, no aggregate stream, no pass point."""
    import main
    from common import asn

    asn.use_table(bins.state_dir() / "routing")
    return main.run_once(now=now, list_targets=lambda since: [],
                         shared_pass=shared.run,
                         segments_pass=lambda s: segments.run(s, name_of=asn.as_name),
                         record_pass=lambda report, at: None)


def widespread_flux() -> str:
    names = " or ".join(f"r._measurement == {flux_str(m)}" for m in WIDESPREAD_MEASUREMENTS)
    return (f"from(bucket: {flux_str(influx_bucket())}) |> range(start: {WIDESPREAD_LOOKBACK}) "
            f'|> filter(fn: (r) => ({names}) and r._field == "loss") |> last()')


def series_of(rows: list[dict]) -> list[tuple[str, dict]]:
    """(measurement, tags) of every series that reported, the gateway left out."""
    out, seen = [], set()
    for r in rows:
        tags_ = {k: str(v) for k, v in r.items() if k not in NOT_TAGS and v is not None}
        if tags_.get("category") == FIRST_HOP or not r.get("_measurement"):
            continue
        key = (r["_measurement"], tuple(sorted(tags_.items())))
        if key not in seen:
            seen.add(key)
            out.append((str(r["_measurement"]), tags_))
    return out


def widespread(plan: dict, now: int, query=query_influx, write=None) -> dict:
    """Write one cycle of widespread loss and say whether the alerter's rule
    (as the path meter asks it) sees an episode under way."""
    from influxdb_client import Point, WritePrecision

    from common import cadence, loss_events
    from common.tsdb import write_points

    if plan.get("widespread"):
        raise PlanError("the widespread cycle is already injected")
    at = int(now) - 20
    if at % 60 == 0:
        at -= 7
    found = series_of(query(widespread_flux()))
    if not found:
        raise PlanError("no target reported in the last 15 minutes")
    measurements = sorted({m for m, _ in found})
    held = [m for m in measurements if query(
        f"from(bucket: {flux_str(influx_bucket())}) |> range(start: {at}, stop: {at + 2}) "
        f"|> filter(fn: (r) => r._measurement == {flux_str(m)}) |> limit(n: 1)")]
    if held:
        raise PlanError(f"points already exist at {_iso(at)} in {held}; try again in a minute")
    before = len(query(episode_flux(at - 3600)))
    points = []
    for m, tags_ in found:
        p = Point(m)
        for k, v in sorted(tags_.items()):
            p = p.tag(k, v)
        points.append(p.field("loss", WIDESPREAD_LOSS).time(at, WritePrecision.S))
    plan["widespread"] = {"at": at, "measurements": measurements, "series": len(points),
                          "episode_traces_before": before}
    _save_manifest(plan)
    (write or write_points)(points)
    cadences = cadence.by_target(query(cadence.cadence_flux()))
    runs = loss_events.answer(query, 1, cadences=cadences).get("widespread") or []
    newest = runs[0] if runs else None
    plan["widespread"]["seen"] = newest
    _save_manifest(plan)
    return plan["widespread"]


def verify(plan: dict, query=query_influx, episode: bool = True) -> dict:
    start, end = plan["span"]
    found = periods(query(points_flux(store.SHARED, start - MARGIN_S, end + MARGIN_S)))
    p_checks, period = check_period(found, plan)
    seg = periods(query(points_flux(store.SHARED_SEGMENT, start - MARGIN_S, end + MARGIN_S)))
    s_checks = check_segment(seg, period, plan)
    ws = plan.get("widespread")
    e_rows = query(episode_flux(ws["at"])) if ws else []
    e_checks = check_episode(e_rows, plan) if episode else []
    return {"shared_degradation": {"checks": p_checks, "period": _public(period)},
            "shared_segment": {"checks": s_checks,
                               "hypothesis": _public(next((h for h in seg if period
                                                           and h["start"] == period["start"]),
                                                          None))},
            "episode": {"checks": e_checks},
            "ok": all(c["ok"] for c in p_checks + s_checks + e_checks)}


def _public(p: dict | None) -> dict | None:
    if not p:
        return None
    return {k: (_iso(v) if k in ("start", "end") else v) for k, v in p.items()}


def cleanup(plan: dict | None, now: int, client=None, keep_points: bool = False,
            query=query_influx) -> dict:
    """Put staging back. The injected loss cycle always goes first: it is
    raw measurement data the alerter and the assistant read. Then, unless
    ``keep_points``, the cache is restored and the outputs are deleted --
    the cache first, so a pass running meanwhile finds no evening to write
    again. With ``keep_points`` the cache keeps the evening too (restored
    alone, the next hourly pass would correct the period away) and the
    test stays under way until a cleanup without it."""
    report: dict = {"cache": "kept with the evening" if keep_points else "no copy to restore",
                    "deleted": []}
    d = workdir()
    own = client is None and bool(plan)
    if plan:
        client = client or store._client()
    try:
        def delete(a, b, predicate):
            client.delete_api().delete(_iso(a), _iso(b), predicate, bucket=influx_bucket(),
                                       org=os.environ.get("INFLUX_ORG", "smokeping"))
            report["deleted"].append(f"{predicate} {_iso(a)}..{_iso(b)}")

        ws = (plan or {}).get("widespread")
        if ws and not ws.get("deleted"):
            for m in ws["measurements"]:
                delete(ws["at"], ws["at"] + 1, f"_measurement={store._quoted(m)}")
            ws["deleted"] = now
            if keep_points:
                _save_manifest(plan)
        if keep_points:
            return report
        if (d / f"{CACHE}.csv").exists() and (d / f"{CACHE}.json").exists():
            for suffix in (".csv", ".json"):
                os.replace(d / f"{CACHE}{suffix}", bins.state_dir() / f"{CACHE}{suffix}")
            report["cache"] = "restored"
        if not plan:
            return report
        start, end = plan["span"]
        if ws:
            for m in EPISODE_MEASUREMENTS:
                delete(ws["at"], now, f"_measurement={store._quoted(m)} AND "
                                      f"trigger={store._quoted(TRIGGER)}")
        for m in (store.SHARED, store.SHARED_SEGMENT):
            delete(start - MARGIN_S, end + MARGIN_S, f"_measurement={store._quoted(m)}")
    finally:
        if own:
            client.close()
    report["left"] = {m: len(periods(query(points_flux(m, start - MARGIN_S, end + MARGIN_S))))
                      for m in (store.SHARED, store.SHARED_SEGMENT)}
    if ws:
        report["left"]["episode traces"] = len(query(episode_flux(ws["at"])))
    os.replace(d / "manifest.json", d / f"done-{int(now)}.json")
    return report


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _print(obj) -> None:
    print(json.dumps(obj, indent=1, sort_keys=True, default=str))


def _text(result: dict) -> str:
    lines = []
    for part in ("shared_degradation", "shared_segment", "episode"):
        lines.append(part)
        if not result[part]["checks"]:
            lines.append("  (not asked for)")
        for c in result[part]["checks"]:
            lines.append(f"  [{'ok' if c['ok'] else 'FAIL'}] {c['check']}: {c['saw']}"
                         f" (expected {c['expected']})")
    lines.append("ALL PASSED" if result["ok"] else "SOME CHECKS FAILED")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="inject.py", description=__doc__.split("\n")[0])
    parser.add_argument("--staging", action="store_true",
                        help="required: shared/scripts/inject-shared-loss.sh passes it after "
                             "its production guard")
    sub = parser.add_subparsers(dest="step", required=True)
    for name in ("plan", "inject"):
        p = sub.add_parser(name)
        p.add_argument("--csv", help="the evening, bin,target,loss (loss 0-1)")
        p.add_argument("--start", help="the evening's start (ISO, UTC); default: it ends "
                                       "two hours before the last pass")
        p.add_argument("--hours", type=int, default=DEFAULT_HOURS)
        p.add_argument("--seed", type=int, default=0)
        p.add_argument("--allow-router", action="store_true",
                       help="may plant a router segment (its address shows in the outputs)")
    sub.add_parser("pass")
    sub.add_parser("widespread")
    v = sub.add_parser("verify")
    v.add_argument("--json", action="store_true")
    v.add_argument("--no-episode", action="store_true",
                   help="the episode traces were not asked for (the wrapper's --no-episode)")
    v.add_argument("--episode-only", action="store_true",
                   help="exit 0 once the episode traces are there (for the wrapper's wait)")
    c = sub.add_parser("cleanup")
    c.add_argument("--keep-points", action="store_true")
    args = parser.parse_args(argv)
    if not args.staging:
        print("inject.py writes loss into the bin cache and InfluxDB: it runs on a staging "
              "Pi only, through shared/scripts/inject-shared-loss.sh (docs/inference.md, "
              "'Proving the shared path on staging').", file=sys.stderr)
        return 2
    now = int(time.time())
    try:
        if args.step in ("plan", "inject"):
            plan = make_plan(args, now)
            if args.step == "inject":
                plan = inject(plan, now)
            else:
                plan.pop("_rows")
                plan.pop("_clear")
            _print(plan)
            return 0
        if args.step == "cleanup":
            _print(cleanup(_load_manifest(), now, keep_points=args.keep_points))
            return 0
        plan = _load_manifest()
        if not plan:
            raise PlanError("no test under way: run inject first")
        if args.step == "pass":
            report = run_pass(now)
            plan["pass"] = {k: report.get(k) for k in ("shared", "shared_segment", "errors")}
            _save_manifest(plan)
            _print(plan["pass"])
            return 0 if not report.get("errors") else 1
        if args.step == "widespread":
            _print(widespread(plan, now))
            return 0
        result = verify(plan, episode=not args.no_episode)
        if args.episode_only:
            return 0 if all(c["ok"] for c in result["episode"]["checks"]) else 1
        print(json.dumps(result, indent=1, default=str) if args.json else _text(result))
        return 0 if result["ok"] else 1
    except PlanError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
