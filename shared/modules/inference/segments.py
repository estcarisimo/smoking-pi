"""Shared path segments: which part of the paths do the destinations that lost share?

shared.py says when loss rose for the whole connection and who lost. The
path meter (pathmeter, opt-in) records the path to every destination. This
puts the two together for every ``shared`` period, to say where a common
cause would most likely sit -- as a **hypothesis**, never as a finding: two
destinations that lost and cross one router are consistent with that
router, they do not show it. It reads what the other two wrote and writes
one ``shared_segment`` point per period; it probes nothing.

1. **Who lost.** The period's voters (shared.py), by destination (its base
   name; one path counts once) and the mean loss of its voters over the
   period: **affected** at LOSSY_PCT (1 %) or more, as in the
   qualification; **unaffected** under UNAFFECTED_PCT (0.5 %); in between is
   **mild** and counts as neither, so a destination at the threshold cannot
   tip a hypothesis either way.
2. **Their paths.** Per destination (by base name, as the path meter stores
   it), the last ``path_trace`` at or before the period's end and no older
   than LOOKBACK_S before its start: the path it was on then. A destination
   with none is **untraced** and counts in the coverage, not in the
   comparison; so does one whose path has no public hop (the gateway, a
   host on the LAN): it crosses nothing a cause beyond the house would sit
   on, and would make every remote segment look clean.
3. **Segments.** Every trace is split into the segments it crosses:
   - a **router**, by its address. A private address (the home router,
     the access network's 10.x routers) names a router only within the
     network that uses it, so it is named by the public hop before it
     (``10.248.2.13 after 136.25.220.1``), or by itself before any public
     hop (the house's own). A hop that did not answer is no segment;
   - an **AS**, from the trace's AS path (private and unanswered hops are
     not in it, pathmeter's ``as_path``);
   - an **exchange point** (IXP), from the hops answered from one.
4. **What is not known.** A destination that does not cross a segment is
   *unknown* rather than *not through* when its trace cannot show it:
   silent at a TTL where the other traces saw the segment (a router), or never reaching
   the destination (an AS or an exchange: they may sit past where the
   trace went silent).
5. **Scoring.** For each segment, ``recall`` = the share of the traced
   affected destinations that cross it, and ``spill`` = the share of the
   traced unaffected ones that cross it *or may* (unknown counts against the
   hypothesis on both sides). The score is ``recall - spill``. A segment
   every destination crosses (the house's first hops, its access network)
   scores 0: it is common to those that lost and those that did not, so it
   explains nothing on its own, and the text says so instead.
6. **Confidence.** The best of many segments is picked, so a coincidence
   must not read as a cause: with 1-3 % loss, which destinations cross the
   1 % line is partly sampling noise. Each segment carries the chance that
   as many of the destinations through it would have lost if losing had
   nothing to do with the path (``p_value``, hypergeometric, the unknown
   clean ones counted as crossing). ``strong``: recall at least
   STRONG_RECALL, spill at most STRONG_SPILL, at least MIN_AFFECTED affected
   and MIN_UNAFFECTED unaffected traced, COVERAGE_MIN of the destinations
   traced, and a chance under STRONG_P. ``weak``: a score of WEAK_SCORE or
   more with at least MIN_AFFECTED crossing, MIN_UNAFFECTED traced and a
   chance under WEAK_P. The chance is per segment, not corrected for how
   many were tried (they overlap: a router lies inside its AS), so even a
   ``strong`` one is a lead to check, not a result. Otherwise ``none``:
   no segment sets the destinations that lost apart -- which is what a loss
   that is everyone's (too few unaffected to contrast) or nobody's in
   particular reads as.

Every hypothesis also says what each side shares (the first hops all its
traces agree on and the ASes they all cross), so a reader sees "the 9 that
lost share hops 1-5 and AS16591; the 20 that did not share hops 1-5" and
can judge the claim against it.

Only ``shared`` periods are read: a ``dominated`` one is one destination's,
and its own path is its hypothesis. A period no destination was traced for
writes nothing. Experimental, like the rest of the module, and never in the
alert path.
"""

from __future__ import annotations

import ipaddress
import logging
import math

import pandas as pd

import shared
from common.links import target_base_name
from common.tsdb import flux_str, influx_bucket, query_influx

log = logging.getLogger("inference.segments")

LOOKBACK_S = 3 * 3600       # a path older than this before the period is not its path
UNAFFECTED_PCT = 0.5
MIN_AFFECTED = 3
MIN_UNAFFECTED = 3
STRONG_RECALL = 0.8
STRONG_SPILL = 0.2
WEAK_SCORE = 0.5
STRONG_P = 0.01
WEAK_P = 0.05
COVERAGE_MIN = 0.6
ALTERNATIVES = 2
SILENT = "*"
KIND_RANK = {"as": 0, "ixp": 1, "router": 2}


def traces_flux(start: int, stop: int) -> str:
    """Every destination's traces in [start, stop]: its path, AS path,
    whether it reached the destination, and the path's hash."""
    return (f"from(bucket: {flux_str(influx_bucket())}) "
            f"|> range(start: {int(start)}, stop: {int(stop) + 1}) "
            '|> filter(fn: (r) => r._measurement == "path_trace" and (r._field == "path" '
            'or r._field == "as_path" or r._field == "reached" or r._field == "path_hash")) '
            '|> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value") '
            '|> keep(columns: ["_time", "target", "path", "as_path", "reached", "path_hash"])')


def ixp_flux(start: int, stop: int) -> str:
    """The hops answered from an exchange point's LAN, in [start, stop]."""
    return (f"from(bucket: {flux_str(influx_bucket())}) "
            f"|> range(start: {int(start)}, stop: {int(stop) + 1}) "
            '|> filter(fn: (r) => r._measurement == "path_hop" and r._field == "ixp") '
            '|> keep(columns: ["_time", "target", "ttl", "_value"])')


def _epoch(value) -> int:
    return int(pd.Timestamp(value).timestamp())


def _private(addr: str) -> bool:
    try:
        return not ipaddress.ip_address(addr).is_global
    except ValueError:
        return True


def paths(trace_rows: list[dict], ixp_rows: list[dict], start: int, end: int) -> dict:
    """base name -> its trace for a period: the last one in
    [start - LOOKBACK_S, end], as ``{"at", "hops", "asns", "ixps", "reached",
    "changed"}``; ``changed`` when the window held more than one path."""
    ixps: dict[tuple[str, int], dict[int, str]] = {}
    for r in ixp_rows:
        if r.get("_time") is None or not r.get("target") or not r.get("_value"):
            continue
        try:
            ttl = int(r.get("ttl"))
        except (TypeError, ValueError):
            continue
        ixps.setdefault((str(r["target"]), _epoch(r["_time"])), {})[ttl] = str(r["_value"])
    out: dict[str, dict] = {}
    hashes: dict[str, set] = {}
    for r in trace_rows:
        if r.get("_time") is None or not r.get("target") or not r.get("path"):
            continue
        at = _epoch(r["_time"])
        if at < start - LOOKBACK_S or at > end:
            continue
        target = str(r["target"])
        hashes.setdefault(target, set()).add(r.get("path_hash") or r["path"])
        if target in out and out[target]["at"] >= at:
            continue
        asns = []
        for token in str(r.get("as_path") or "").split():
            try:
                asns.append(int(token))
            except ValueError:
                pass
        out[target] = {"at": at, "hops": str(r["path"]).split("|"), "asns": asns,
                       "ixps": ixps.get((target, at), {}),
                       "reached": str(r.get("reached")).lower() == "true"}
    for target, trace in out.items():
        trace["changed"] = len(hashes.get(target, ())) > 1
    return out


def segments(trace: dict) -> dict[tuple, int]:
    """The segments one trace crosses -> the TTL where it crosses them (0 for
    an AS, which spans hops)."""
    out: dict[tuple, int] = {}
    before = ""
    for ttl, addr in enumerate(trace["hops"], start=1):
        if not addr or addr == SILENT:
            continue
        if _private(addr):
            out.setdefault(("router", addr, before), ttl)
        else:
            out.setdefault(("router", addr, ""), ttl)
            before = addr
    for asn in trace["asns"]:
        out.setdefault(("as", asn), 0)
    for ttl, name in trace["ixps"].items():
        out.setdefault(("ixp", name), ttl)
    return out


def _silent_at(trace: dict, ttl: int) -> bool:
    hops = trace["hops"]
    if ttl > len(hops):
        return not trace["reached"]
    return hops[ttl - 1] in ("", SILENT)


def label(segment: tuple, name_of=None) -> str:
    """How a segment reads in the hypothesis."""
    if segment[0] == "as":
        name = name_of(segment[1]) if name_of else ""
        return f"AS{segment[1]}" + (f" ({name})" if name else "")
    if segment[0] == "ixp":
        return f"exchange {segment[1]}"
    _, addr, before = segment
    return f"router {addr}" + (f" after {before}" if before else "")


def common_hops(traces: list[dict]) -> int:
    """How many first hops every trace shares: from TTL 1, each TTL where at
    least half of the traces answered and every answer is the same address,
    up to the first where two answers differ or most are silent (one
    router that answered among silent ones is not evidence the others pass
    through it, as in the path meter's own shared prefix)."""
    n = 0
    longest = max((len(t["hops"]) for t in traces), default=0)
    for ttl in range(longest):
        if any(ttl >= len(t["hops"]) for t in traces):
            break
        answers = [t["hops"][ttl] for t in traces if t["hops"][ttl] not in ("", SILENT)]
        if len(set(answers)) != 1 or len(answers) < 0.5 * len(traces):
            break
        n = ttl + 1
    return n


def classify(target_loss: dict[str, float]) -> dict[str, list[str]]:
    """Destinations by base name (the path meter's name), each by the mean
    loss of its voters over the period, in percent: one destination is one
    path, so it counts once, on one side."""
    by_base: dict[str, list[float]] = {}
    for target, loss in target_loss.items():
        if loss is not None and not pd.isna(loss):
            by_base.setdefault(target_base_name(target), []).append(float(loss))
    out = {"affected": [], "unaffected": [], "mild": []}
    for name, values in sorted(by_base.items()):
        loss = sum(values) / len(values)
        key = ("affected" if loss >= shared.LOSSY_PCT
               else "unaffected" if loss < UNAFFECTED_PCT else "mild")
        out[key].append(name)
    return out


def _comparable(trace: dict) -> bool:
    """Whether a trace reaches past the house: a destination whose path has
    no public hop (the gateway, a host on the LAN) crosses none of the
    segments a common cause beyond it would sit on, and is no contrast."""
    return any(h not in ("", SILENT) and not _private(h) for h in trace["hops"])


def p_value(through: int, a_in: int, a_total: int, total: int) -> float:
    """The chance that ``a_in`` or more of the ``through`` destinations
    crossing a segment would be among the ``a_total`` that lost if who lost
    had nothing to do with the path (hypergeometric, one-sided)."""
    denominator = math.comb(total, through)
    if not denominator:
        return 1.0
    return sum(math.comb(a_total, k) * math.comb(total - a_total, through - k)
               for k in range(a_in, min(through, a_total) + 1)) / denominator


def hypothesis(period: dict, traces: dict[str, dict], name_of=None) -> dict | None:
    """The segment hypothesis of one shared period (its ``target_loss``, the
    voters' mean loss, from shared.qualify), or None when no destination was
    traced."""
    groups = classify(period.get("target_loss") or {})
    traced = {g: [t for t in ts if t in traces and _comparable(traces[t])]
              for g, ts in groups.items()}
    a_all, u_all = traced["affected"], traced["unaffected"]
    compared = len(groups["affected"]) + len(groups["unaffected"])
    if not a_all and not u_all:
        return None
    crossing = {t: segments(traces[t]) for t in a_all + u_all}
    # Every TTL a segment was seen at: a router another path reaches one hop
    # further is still that router, and a clean trace silent at any of them
    # may cross it unseen.
    candidates: dict[tuple, set[int]] = {}
    for t in a_all:
        for seg in crossing[t]:
            candidates[seg] = set()
    for t in a_all + u_all:
        for seg, ttl in crossing[t].items():
            if seg in candidates:
                candidates[seg].add(ttl)
    scored = []
    for seg, ttls in candidates.items():
        a_in = sum(seg in crossing[t] for t in a_all)
        u_in = sum(seg in crossing[t] for t in u_all)
        u_unknown = sum(seg not in crossing[t] and _unknown(seg, ttls, traces[t])
                        for t in u_all)
        recall = a_in / len(a_all) if a_all else 0.0
        spill = (u_in + u_unknown) / len(u_all) if u_all else 1.0
        through = sum(seg in c for c in crossing.values())
        ttl = max(ttls)
        # Best first; on a tie the narrower segment (fewer destinations
        # through it), then an AS before an exchange before a router (a
        # network is the steadier hypothesis: its routers may share the
        # load), then the deeper one, then a stable order.
        scored.append(((-(recall - spill), through, KIND_RANK[seg[0]], -ttl, str(seg)),
                       {"segment": seg, "ttl": ttl, "affected_through": a_in,
                        "unaffected_through": u_in, "unaffected_unknown": u_unknown,
                        "recall": recall, "spill": spill, "score": recall - spill,
                        # Unknown clean ones counted as crossing, as in spill.
                        "p": p_value(a_in + u_in + u_unknown, a_in, len(a_all),
                                     len(a_all) + len(u_all))}))
    scored.sort(key=lambda kv: kv[0])
    best = scored[0][1] if scored else None
    coverage = (len(a_all) + len(u_all)) / compared if compared else 0.0
    confidence = "none"
    # Too few clean destinations to contrast is no contrast: one clean
    # gateway or one quiet host does not set the others apart.
    if best and best["affected_through"] >= MIN_AFFECTED and len(u_all) >= MIN_UNAFFECTED:
        if (best["recall"] >= STRONG_RECALL and best["spill"] <= STRONG_SPILL
                and coverage >= COVERAGE_MIN and best["p"] < STRONG_P):
            confidence = "strong"
        elif best["score"] >= WEAK_SCORE and best["p"] < WEAK_P:
            confidence = "weak"
    lost = [traces[t] for t in a_all]
    clean = [traces[t] for t in u_all]
    out = {
        "start": period["start"], "end": period["end"],
        "mean_loss_pct": period.get("mean_loss_pct"),
        "affected": len(groups["affected"]), "unaffected": len(groups["unaffected"]),
        "mild": len(groups["mild"]),
        "traced": len(a_all) + len(u_all),
        "untraced": compared - len(a_all) - len(u_all),
        "coverage": coverage,
        "common_hops": common_hops(lost), "common_as": common_as(lost),
        "clean_common_hops": common_hops(clean), "clean_common_as": common_as(clean),
        "path_changed": sum(traces[t]["changed"] for t in a_all + u_all),
        "confidence": confidence,
    }
    if confidence != "none":
        out.update({
            "segment": label(best["segment"], name_of), "segment_kind": best["segment"][0],
            "affected_through": best["affected_through"],
            "unaffected_through": best["unaffected_through"],
            "recall": best["recall"], "spill": best["spill"], "p_value": best["p"],
            "alternatives": "; ".join(
                f"{label(s['segment'], name_of)} {s['affected_through']}/{len(a_all)} "
                f"vs {s['unaffected_through']}/{len(u_all)}"
                for _, s in scored[1:1 + ALTERNATIVES] if s["score"] > 0),
        })
    else:
        out.update({"segment": "", "segment_kind": "none"})
    out["hypothesis"] = _text(out, len(a_all), u_all)
    return out


def common_as(traces: list[dict]) -> str:
    """The ASes every trace crosses, in the first trace's order:
    ``AS19165 AS16591``."""
    if not traces:
        return ""
    every = set.intersection(*(set(t["asns"]) for t in traces))
    return " ".join(f"AS{a}" for a in dict.fromkeys(traces[0]["asns"]) if a in every)


def _unknown(seg: tuple, ttls, trace: dict) -> bool:
    """Whether a trace that does not show ``seg`` (seen at ``ttls`` on the
    traces that do) may still cross it."""
    if seg[0] == "router":
        return any(_silent_at(trace, ttl) for ttl in ttls)
    return not trace["reached"]


def _shares(n: int, who: str, hops: int, ases: str) -> str:
    what = [f"hops 1–{hops}" if hops > 1 else "hop 1"] if hops else []
    what += [ases] if ases else []
    return f"the {n} that {who} share " + (" and ".join(what) if what else "no hop")


def _text(h: dict, a_traced: int, clean: list[str]) -> str:
    """A few sentences a person can read in a table cell: who lost, what each
    side shares, and the hypothesis or why there is none."""
    u_traced = len(clean)
    head = (f"{h['affected']} lost, {h['unaffected']} did not"
            + (f", {h['mild']} in between" if h["mild"] else "")
            + f" ({h['traced']} traced). ")
    sides = []
    if a_traced:
        sides.append(_shares(a_traced, "lost", h["common_hops"], h["common_as"]))
    if u_traced:
        sides.append(_shares(u_traced, "did not", h["clean_common_hops"],
                             h["clean_common_as"]))
    said = "; ".join(sides)
    head += said[:1].upper() + said[1:] + "."
    if h["confidence"] != "none":
        return head + (f" Hypothesis ({h['confidence']}): {h['segment']}, crossed by"
                       f" {h['affected_through']} of {a_traced} that lost and"
                       f" {h['unaffected_through']} of {u_traced} that did not.")
    if not u_traced:
        return head + (" No traced destination stayed clean, so nothing sets the ones"
                       " that lost apart: no segment is suggested beyond what they all cross.")
    if u_traced < MIN_UNAFFECTED:
        return head + (f" Only {u_traced} traced destination{'s' if u_traced > 1 else ''}"
                       f" stayed clean ({', '.join(clean)}): too few to set the others apart.")
    return head + (" No segment sets the ones that lost apart from the others:"
                   " no common cause on the path is suggested.")


def run(shared_result: dict, query=query_influx, name_of=None) -> list[dict]:
    """The hypotheses of a shared pass's ``shared`` periods (shared.run's
    result), from the path meter's traces; empty without traces."""
    out = []
    for p in shared_result.get("periods") or []:
        if p.get("scope") != "shared" or not p.get("target_loss"):
            continue
        start, end = int(p["start"]), int(p["end"])
        rows = query(traces_flux(start - LOOKBACK_S, end))
        if not rows:
            continue
        found = paths(rows, query(ixp_flux(start - LOOKBACK_S, end)), start, end)
        h = hypothesis(p, found, name_of)
        if h is not None:
            out.append(h)
    return out
