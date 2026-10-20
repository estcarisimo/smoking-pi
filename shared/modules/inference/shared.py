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
   deaf (deaf.py), or when the ISP gateway lost about as much as the
   destinations did in two bins running, or lost at least a fifth on its own:
   that is the first hop or closer, a different question (the alerter's
   verdict says so as ``local_link``). One bin of margin on each side.
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

On 52 days of the reference Pi this found every evening of the August to
October loss and nothing on a single CDN edge's route changes, the radio
hangs or a quiet week; across 100 random sets of ten targets the strong
evenings were found in 99-100 % of them and false periods in 0-2 %.
"""

from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd

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


def frame(rows: list[dict]) -> tuple[pd.DataFrame, dict[str, str]]:
    """Rows -> (bins x targets, indexed by bin START), and target -> category."""
    recs, cats = [], {}
    for r in rows:
        t, v, when = r.get("target"), r.get("_value"), r.get("_time")
        if t is None or v is None or when is None:
            continue
        cats.setdefault(t, r.get("category") or "")
        recs.append((int(pd.Timestamp(when).timestamp()) - BIN_S, t, float(v)))
    if not recs:
        return pd.DataFrame(), cats
    df = pd.DataFrame(recs, columns=["bin", "target", "v"])
    return df.pivot_table(index="bin", columns="target", values="v", aggfunc="last"), cats


def monitor_bins(loss: pd.DataFrame, count: pd.DataFrame, cpe: list[str],
                 deaf_bins: set[int]) -> pd.Series:
    """True for the bins that are the monitor's or the first hop's."""
    idx = loss.index
    out = pd.Series(idx.isin(list(deaf_bins)), index=idx)
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
    period.update({
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


def detect(loss_rows: list[dict], count_rows: list[dict], deaf_bins: set[int], of,
           find=degradation.detect) -> dict:
    """Shared-degradation periods over the window the rows cover.

    Returns ``{"periods": [...], "voters": n, "bins": n}``; with fewer than
    MIN_VOTERS voters there is nothing to say and ``periods`` is empty."""
    loss, cats = frame(loss_rows)
    count, _ = frame(count_rows)
    if loss.empty:
        return {"periods": [], "voters": 0, "bins": 0}
    count = count.reindex(index=loss.index, columns=loss.columns)
    cpe = [t for t in loss.columns if cats.get(t) == CPE_CATEGORY]
    monitor = monitor_bins(loss, count, cpe, deaf_bins)
    pooled, voters, lo = series(loss, count, cats, monitor)
    if len(voters) < MIN_VOTERS:
        return {"periods": [], "voters": len(voters), "bins": int(len(loss))}
    have = pooled.dropna()
    found = find(pd.DataFrame({"epoch": have.index.astype(int), "loss_pct": have.values}))
    for p in found["periods"]:
        span = pooled[(pooled.index >= p["start"]) & (pooled.index < p["end"])]
        p["coverage"] = float(span.notna().mean()) if len(span) else 0.0
        qualify(p, lo, of)
    return {"periods": found["periods"], "voters": len(voters), "bins": int(len(loss)),
            "monitor_bins": int(monitor.sum())}


def run(start: int, deaf_bins: set[int], query=query_influx, tag_store=None,
        find=degradation.detect, now: int | None = None) -> dict:
    """One shared pass from InfluxDB: the bins, the hosts, the tags, the periods."""
    stop = int(time.time()) if now is None else now
    loss_rows = query(bins_flux(start, stop, "mean"))
    count_rows = query(bins_flux(start, stop, "count"))
    hosts = {r["target"]: str(r["_value"]) for r in query(hosts_flux())
             if r.get("target") and r.get("_value")}
    tag_store = tag_store or tagging.Tags()
    try:
        tag_store.refresh(hosts)
    except Exception:  # noqa: BLE001 -- untagged targets read "unknown"; the series stands
        log.warning("could not refresh the target tags", exc_info=True)
    return detect(loss_rows, count_rows, deaf_bins, lambda t: tag_store.of(t, hosts), find=find)
