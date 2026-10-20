"""Shared degradation: did loss rise for the whole connection, not one service?

The per-target detectors answer for one destination each, and five targets
with a period each are not one shared period. This answers the house-level
question once, on one series built across destinations, with the *same*
degradation detector (degradation.detect, its parameters untouched):

1. **Bins.** Every ICMP target's loss and probe cycles per 15-minute bin
   over the degradation window, from storage-engine shapes only
   (``aggregateWindow`` with ``mean`` and ``count``). Every category takes
   part, INFERENCE_CATEGORIES or not: that setting picks whom the per-target
   detectors analyze, and the shared question is about all of them. The ISP
   gateway is the exception: it tells the monitor's bins apart (step 2).
2. **The monitor's bins are out.** A bin is the monitor's when its radio was
   deaf (deaf.py), when ``smoking-pi upgrade`` was restarting the stack (its
   ``upgrade`` markers, upgrade_bins), or when the ISP gateway lost about as
   much as the destinations did in two bins running, or lost at least a
   fifth on its own: that is the first hop or closer, a different question
   (the alerter's verdict says so as ``local_link``). One bin of margin on
   each side. Upgrades used to read as first-hop cuts (4-6 % everywhere on
   the reference Pi for one, 66 % for another).
3. **Members and voters.** A member reported in at least half the window's
   bins, so a slot that rotates in and out of the target list (the Netflix
   OCA appliances) never weighs. A member votes when at least half of its
   bins lost nothing -- the breadth rule (common.definitions.breadth_voters):
   a host that rate-limits ICMP and always loses a third is not "the
   network". A voter that lost everything for six hours running is out
   while it stays dead, as in the verdict.
4. **One series.** Lost over sent across the voters reporting in each bin,
   when at least 60 % of them (and at least MIN_VOTERS) did.
5. **Detection.** degradation.detect on that series: a change-point segment
   of at least 30 minutes averaging at least 2 %.
6. **Qualification.** A period is ``shared`` when at least a third of the
   voting operators lost at least 1 % in it and they are of at least two
   kinds of infrastructure (tags.py); otherwise it is ``dominated`` and
   names the target that carried most of the loss. Tags qualify a period,
   they do not weight the series: weighting each operator equally lets an
   operator with one target swing the result.
7. **Attribution.** Each period also says who lost: the top operators' and
   every kind's mean loss over its span, and the voters that diverged (lost
   at most a tenth of the pooled loss), as short strings for a tooltip.

On 52 days of the reference Pi this found every evening of the August to
October loss and nothing on a single CDN edge's route changes, the radio
hangs or a quiet week; across 100 random sets of ten targets the strong
evenings were found in 99-100 % of them and false periods in 0-2 %.

The bins are read through bins.py: closed ones come from the state volume,
so a pass reads one hour of InfluxDB, not seven days.
"""

from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd

import bins
import degradation
import tags as tagging
from common.tsdb import flux_str, influx_bucket, query_influx

log = logging.getLogger("inference.shared")

BIN_S = 900
CPE_CATEGORY = "cpe"
MIN_VOTERS = 8
QUORUM = 0.6
MEMBER_SHARE = 0.5
VOTER_CLEAN_SHARE = 0.5
CHRONIC_BINS = 24           # six hours at 100 %
GATEWAY_ALONE = 0.2         # the gateway lost a fifth: the first hop, whatever else did
GATEWAY_FLOOR = 0.03        # ...or this much and half the destinations' loss, two bins running
LOSSY_PCT = 1.0
SHARED_OPERATOR_SHARE = 1 / 3
MIN_KINDS = 2
# Attribution (one string field each): the operators that lost most, every
# kind's loss, and the voters that stayed out of it (lost at most a tenth of
# the pooled loss).
TOP_OPERATORS = 3
DIVERGED_SHARE = 0.1
MAX_LISTED = 5
UPGRADE_MAX_S = 1800      # an upgrade with no end marker closes after this
BIN_COLUMNS = ["bin", "target", "category", "loss", "count"]


def bins_flux(start: int, stop: int, fn: str) -> str:
    """Per target, per 15-minute bin: the mean loss or the cycle count. The
    window is labeled by its end, as aggregateWindow does. ``aggregateWindow``
    comes straight after the filter so InfluxDB computes it in its storage
    engine (a ``keep`` before it measured a third slower on the reference Pi);
    both ends are aligned to the bin so the newest bin is never a clipped one."""
    if fn not in ("mean", "count"):
        raise ValueError("fn")
    return (f"from(bucket: {flux_str(influx_bucket())}) "
            f"|> range(start: {_align(start)}, stop: {_align(stop)}) "
            '|> filter(fn: (r) => r._measurement == "latency" and r._field == "loss") '
            f"|> aggregateWindow(every: 15m, fn: {fn}, createEmpty: false) "
            '|> keep(columns: ["_time", "_value", "target", "category"])')


def _align(epoch: int) -> int:
    return int(epoch) // BIN_S * BIN_S


def hosts_flux() -> str:
    """The host behind each target, as the exporter last recorded it."""
    return (f"from(bucket: {flux_str(influx_bucket())}) |> range(start: 0) "
            '|> filter(fn: (r) => r._measurement == "target_host" and r._field == "host") '
            '|> last() |> keep(columns: ["target", "_value"])')


def records(rows: list[dict]) -> pd.DataFrame:
    """aggregateWindow rows -> ``bin`` (the window's START), ``target``,
    ``category`` and ``v``; rows missing a time, a value or a target dropped."""
    cols = ["_time", "_value", "target", "category"]
    df = pd.DataFrame.from_records(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
    df = df.dropna(subset=["_time", "_value", "target"])
    when = pd.to_datetime(df["_time"], utc=True)
    epoch = (when - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)
    return pd.DataFrame({"bin": epoch.astype("int64") - BIN_S, "target": df["target"],
                         "category": df["category"].fillna("").astype(str),
                         "v": df["_value"].astype(float)}).reset_index(drop=True)


def frame(rows: list[dict]) -> tuple[pd.DataFrame, dict[str, str]]:
    """Rows -> (bins x targets, indexed by bin START), and target -> category."""
    rec = records(rows)
    if rec.empty:
        return pd.DataFrame(), {}
    return _pivot(rec, "v"), _categories(rec)


def _pivot(table: pd.DataFrame, value: str) -> pd.DataFrame:
    return table.pivot_table(index="bin", columns="target", values=value, aggfunc="last")


def _categories(table: pd.DataFrame) -> dict[str, str]:
    return table.groupby("target", sort=False)["category"].first().to_dict()


def fetch_bins(since: int, stop: int, query=query_influx) -> pd.DataFrame:
    """The bins of [since, stop), one row per target and bin: ``loss`` (the
    mean) and ``count`` (the probe cycles), as bins.read keeps them."""
    loss = records(query(bins_flux(since, stop, "mean"))).rename(columns={"v": "loss"})
    count = records(query(bins_flux(since, stop, "count"))).rename(columns={"v": "count"})
    out = loss.merge(count, on=["bin", "target"], how="outer", suffixes=("", "_c"))
    out["category"] = out["category"].fillna(out.pop("category_c")).fillna("")
    return out[BIN_COLUMNS]


def upgrade_flux(start: int, stop: int) -> str:
    """The ``upgrade`` markers ``smoking-pi upgrade`` writes, one point at each
    end, from UPGRADE_MAX_S before the window so an upgrade that began just
    before it is seen."""
    return (f"from(bucket: {flux_str(influx_bucket())}) "
            f"|> range(start: {int(start) - UPGRADE_MAX_S}, stop: {int(stop)}) "
            '|> filter(fn: (r) => r._measurement == "upgrade" and r._field == "value") '
            '|> keep(columns: ["_time", "phase"])')


def upgrade_bins(rows: list[dict]) -> set[int]:
    """The 15-minute bins (by start epoch) an upgrade spanned. A ``start``
    closes at the first ``end`` within UPGRADE_MAX_S, or at UPGRADE_MAX_S
    after it; an ``end`` with no ``start`` before it (its write failed, or
    it is before the window) is its own bin. No markers, no bins:
    monitor_bins pads them by one bin on each side, as the deaf ones."""
    events = sorted((int(pd.Timestamp(r["_time"]).timestamp()), str(r.get("phase")))
                    for r in rows if r.get("_time") is not None)
    spans, opened = [], None
    for when, phase in events:
        if phase == "start":
            if opened is not None:
                spans.append((opened, min(when, opened + UPGRADE_MAX_S)))
            opened = when
        elif phase == "end":
            if opened is not None and when - opened <= UPGRADE_MAX_S:
                spans.append((opened, when))
            else:
                if opened is not None:
                    spans.append((opened, opened + UPGRADE_MAX_S))
                spans.append((when, when))
            opened = None
    if opened is not None:
        spans.append((opened, opened + UPGRADE_MAX_S))
    return {b for s, e in spans for b in range(_align(s), _align(e) + BIN_S, BIN_S)}


def monitor_bins(loss: pd.DataFrame, count: pd.DataFrame, cpe: list[str],
                 known: set[int]) -> pd.Series:
    """True for the bins that are the monitor's or the first hop's.
    ``known``: the bins already known to be the monitor's (deaf, upgrade)."""
    idx = loss.index
    out = pd.Series(idx.isin(list(known)), index=idx)
    if cpe:
        dests = [c for c in loss.columns if c not in cpe]
        pooled = (loss[dests] * count[dests]).sum(axis=1) / count[dests].sum(axis=1)
        gw = loss[cpe].max(axis=1)
        near = (gw >= GATEWAY_FLOOR) & (gw >= 0.5 * pooled)
        out |= (gw >= GATEWAY_ALONE) | (near & (near.shift(1, fill_value=False)
                                                | near.shift(-1, fill_value=False)))
    return out | out.shift(1, fill_value=False) | out.shift(-1, fill_value=False)


def chronic(loss: pd.DataFrame) -> pd.DataFrame:
    """True where a target is inside a run of CHRONIC_BINS or more total-loss bins."""
    dead = loss >= 0.999
    out = pd.DataFrame(False, index=loss.index, columns=loss.columns)
    for t in loss.columns:
        run = (dead[t] != dead[t].shift()).cumsum()
        out[t] = dead[t] & (dead[t].groupby(run).transform("sum") >= CHRONIC_BINS)
    return out


def series(loss: pd.DataFrame, count: pd.DataFrame, cats: dict[str, str],
           monitor: pd.Series) -> tuple[pd.Series, list[str], pd.DataFrame]:
    """(pooled loss percent per bin, the voters, their loss with the monitor's
    and the chronic bins blanked)."""
    members = [t for t in loss.columns if cats.get(t) != CPE_CATEGORY
               and loss[t].notna().sum() >= MEMBER_SHARE * len(loss)]
    lo = loss[members].copy()
    ct = count.reindex(columns=members).copy()
    gone = chronic(lo)
    lo.loc[monitor.values] = np.nan
    lo = lo.mask(gone)
    ct = ct.where(lo.notna())
    clean = (lo == 0).sum() / lo.notna().sum().replace(0, np.nan)
    voters = [t for t in members if clean.get(t, 0) >= VOTER_CLEAN_SHARE]
    if not voters:
        return pd.Series(dtype=float), [], lo.iloc[:, :0]
    lo, ct = lo[voters], ct[voters]
    reporting = lo.notna().sum(axis=1)
    pooled = (lo * ct).sum(axis=1) / ct.sum(axis=1) * 100
    need = max(MIN_VOTERS, QUORUM * len(voters))
    return pooled.where(reporting >= need), voters, lo


def qualify(period: dict, lo: pd.DataFrame, of) -> dict:
    """Shared or dominated, and by whom: fields set on ``period`` in place.
    ``of(target)`` -> (operator, kind)."""
    span = lo[(lo.index >= period["start"]) & (lo.index < period["end"])] * 100
    per_target = span.mean()
    ops: dict[str, list[str]] = {}
    for t in lo.columns:
        operator = of(t)[0]
        # An untagged target (its lookups have not succeeded yet) is no
        # operator: lumping them into one would dilute the share.
        if operator != tagging.UNKNOWN:
            ops.setdefault(operator, []).append(t)
    op_loss = {op: float(per_target[ts].mean()) for op, ts in ops.items()
               if per_target[ts].notna().any()}
    lossy = [op for op, v in op_loss.items() if v >= LOSSY_PCT]
    # An untagged target is not evidence of a second kind of infrastructure.
    kinds = sorted({of(ops[op][0])[1] for op in lossy})
    total = float(per_target.sum()) or 1.0
    top = per_target.idxmax() if per_target.notna().any() else ""
    share = len(lossy) / len(op_loss) if op_loss else 0.0
    by_kind: dict[str, list[float]] = {}
    for op, ts in ops.items():
        for t in ts:
            if pd.notna(per_target.get(t)):
                by_kind.setdefault(of(t)[1], []).append(float(per_target[t]))
    pooled = float(period.get("mean_loss_pct") or 0.0)
    diverged = sorted((float(v), t) for t, v in per_target.items()
                      if pd.notna(v) and v <= DIVERGED_SHARE * pooled)
    period.update({
        "by_operator": _listing(sorted(op_loss.items(), key=lambda kv: -kv[1])[:TOP_OPERATORS]),
        "by_kind": _listing(sorted(((k, float(np.mean(v))) for k, v in by_kind.items()),
                                   key=lambda kv: -kv[1])),
        "diverged": _listing([(t, v) for v, t in diverged[:MAX_LISTED]],
                             more=len(diverged) - MAX_LISTED),
        "voters": int(len(lo.columns)),
        "operators": int(len(op_loss)),
        "lossy_operator_share": share,
        "kinds": ",".join(kinds),
        "dominant": str(top),
        "dominant_share": float(per_target.max() / total) if top else 0.0,
        "scope": "shared" if share >= SHARED_OPERATOR_SHARE and len(kinds) >= MIN_KINDS
        else "dominated",
    })
    return period


def _listing(pairs, more: int = 0) -> str:
    """``name 12.3 %, name 0.4 %`` (and ``+N more``): compact enough for a
    tooltip, and a string field, so a period's attribution is one point."""
    text = ", ".join(f"{name} {value:.1f} %" for name, value in pairs)
    return f"{text}, +{more} more" if more > 0 and text else text


def detect(loss_rows: list[dict], count_rows: list[dict], deaf_bins: set[int], of,
           find=degradation.detect) -> dict:
    """Shared-degradation periods over the window the rows cover (see
    detect_bins)."""
    loss = records(loss_rows).rename(columns={"v": "loss"})
    count = records(count_rows).rename(columns={"v": "count"})[["bin", "target", "count"]]
    return detect_bins(loss.merge(count, on=["bin", "target"], how="left"), deaf_bins, of, find)


def detect_bins(table: pd.DataFrame, monitor_set: set[int], of,
                find=degradation.detect) -> dict:
    """Shared-degradation periods over the bins of ``table`` (BIN_COLUMNS).
    ``monitor_set`` is the bins known to be the monitor's before looking at
    the loss: the radio deaf, an upgrade.

    Returns ``{"periods": [...], "voters": n, "bins": n}``; with fewer than
    MIN_VOTERS voters there is nothing to say and ``periods`` is empty."""
    table = table.dropna(subset=["loss"])
    if table.empty:
        return {"periods": [], "voters": 0, "bins": 0}
    loss, cats = _pivot(table, "loss"), _categories(table)
    count = _pivot(table.dropna(subset=["count"]), "count") if table["count"].notna().any() \
        else pd.DataFrame()
    count = count.reindex(index=loss.index, columns=loss.columns)
    cpe = [t for t in loss.columns if cats.get(t) == CPE_CATEGORY]
    monitor = monitor_bins(loss, count, cpe, monitor_set)
    pooled, voters, lo = series(loss, count, cats, monitor)
    if len(voters) < MIN_VOTERS:
        return {"periods": [], "voters": len(voters), "bins": int(len(loss))}
    have = pooled.dropna()
    found = find(pd.DataFrame({"epoch": have.index.astype(int), "loss_pct": have.values}))
    for p in found["periods"]:
        span = pooled[(pooled.index >= p["start"]) & (pooled.index < p["end"])]
        p["coverage"] = float(span.notna().mean()) if len(span) else 0.0
        qualify(p, lo, of)
    # The voters and the monitor's bins, for the aggregate stream (aggregate.py).
    return {"periods": found["periods"], "voters": len(voters), "bins": int(len(loss)),
            "monitor_bins": int(monitor.sum()), "voter_targets": list(voters),
            "monitor": {int(b) for b in monitor.index[monitor.values]}}


def run(start: int, deaf_bins: set[int], query=query_influx, tag_store=None,
        find=degradation.detect, now: int | None = None, directory=None) -> dict:
    """One shared pass from InfluxDB: the bins (closed ones from the state
    volume, bins.py), the upgrades, the hosts, the tags, the periods."""
    stop = int(time.time()) if now is None else now
    table = bins.read("loss", start, stop, lambda a, b: fetch_bins(a, b, query),
                      BIN_COLUMNS, directory, text=("target", "category"))
    upgrades = upgrade_bins(query(upgrade_flux(start, stop)))
    hosts = {r["target"]: str(r["_value"]) for r in query(hosts_flux())
             if r.get("target") and r.get("_value")}
    tag_store = tag_store or tagging.Tags()
    try:
        tag_store.refresh(hosts)
    except Exception:  # noqa: BLE001 -- untagged targets read "unknown"; the series stands
        log.warning("could not refresh the target tags", exc_info=True)
    out = detect_bins(table, set(deaf_bins) | upgrades, lambda t: tag_store.of(t, hosts),
                      find=find)
    out["upgrade_bins"] = len(upgrades)
    return out
