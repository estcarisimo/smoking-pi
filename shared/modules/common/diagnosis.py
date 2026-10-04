"""What each loss episode was, with the evidence and how sure we are.

The alerter's verdict (alerter/verdict.py) answers "is it me or the
internet?" for the last few minutes, and only when an alert fires. Most
questions come later, through an assistant: "what happened last night?". It
had raw loss events, microcuts and Wi-Fi numbers, and had to assemble a
cause from them on its own -- which is how a hung radio was reported as an
ISP outage for weeks (docs/detection-reliability.md). This module does the
assembling once, deterministically, for a whole window.

Each **incident** is a run of probe steps in which some target lost two or
more pings (the loss-event rule of get_loss_events), across every target at
once, or a confirmed microcut or deaf span with no such run around it. It
gets one **class**:

- ``probe_miss``: one target, one point, nothing else moved. A probe that
  lost two pings, not a problem.
- ``local_wifi``: this host's own Wi-Fi -- the radio heard nothing (deaf),
  dropped its association, or lost the carrier; or the signal was weak while
  the first hop was cutting. The monitor, not the line.
- ``local_link``: the line between the house and the ISP -- the first hop
  (the CPE probe) cut out with several destinations, or every destination
  lost everything while this host's Wi-Fi was still receiving.
- ``destination``: one or two destinations, their peers fine. Theirs.
- ``upstream``: most destinations at once with a clean first hop: beyond
  the line, a wider Internet problem.
- ``unclear``: none of the above fits; the evidence is listed anyway.

and a **confidence**: ``high`` when two or more independent signals agree
and none contradicts, ``low`` when one contradicts or the data a class
needs is missing, ``medium`` otherwise. ``evidence`` lists what supports the
class, ``against`` what weakens it, both as plain sentences with numbers,
so an answer can cite them instead of asserting.

Pure: rows in, incidents out. The MCP tool fetches the rows.
"""

from __future__ import annotations

import re
from typing import Any

# A share of the targets reporting in a step at or above which damage is
# broad rather than site-specific; the alerter's VERDICT_BROAD_PCT.
BROAD_SHARE = 0.6
# Below this many reporting targets breadth means nothing.
MIN_TARGETS = 3
# Events at most this many cycles apart are one incident: one silent cycle
# between two lossy ones does not split it, two do.
GAP_STEPS = 2
# A target that lost everything in this share of the window's steps is not
# an episode: it does not answer (bare amazon.com) or it is gone. Listed
# apart, kept out of breadth.
CHRONIC_SHARE = 0.9
# wifi_link signal below this is weak (the alerter's WIFI_WEAK_DBM), and
# this many weak samples (a minute at 10 s) make the radio's hour weak.
WEAK_DBM = -75.0
WEAK_SAMPLES = 6
# An app-layer sibling counts as lossy at or above this loss ratio.
APP_LOSS = 0.5
TOTAL = 99.9
# The first hop's mean loss in a cycle is elevated when it is this many
# points above its usual level (the window's median cycle). The gateway's
# rate-limit floor sits near 10-20% and moves by a few points on its own.
FLOOR_RISE_PCT = 10.0
# Category of the ISP first hop in `latency` (CPE_IPv4 / CPE_IPv6): its
# ping loss is that rate-limit floor. The 10 s CPE probe (cpe_latency) is
# the first-hop evidence; these points are not destinations.
FIRST_HOP_CATEGORY = "cpe"

CLASSES = ("probe_miss", "local_wifi", "local_link", "destination", "upstream",
           "unclear")

_SITE_SUFFIX = re.compile(r"_(icmp|h[123]|tcp\d*|tcp443)$")


def site_key(target: str | None) -> str:
    """The site a target measures, so ICMP ``cloudflare`` pairs with
    ``Cloudflare_h1`` and ``W_x_com_icmp`` with ``W_x_com_tcp``."""
    return _SITE_SUFFIX.sub("", (target or "").lower())


def _minutes(seconds: float) -> int:
    return max(1, int(round(seconds / 60.0)))


def _overlap(lo: float, hi: float, a: float, b: float) -> float:
    return max(0.0, min(hi, b) - max(lo, a))


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def wifi_minutes(rows: list[dict]) -> list[dict] | None:
    """Per-minute Wi-Fi samples of the uplink interface from ``wifi_link``
    rows (``_time`` epoch in ``_epoch``, ``_field``, ``_value``,
    ``interface``), or None when there are none (a wired host, or no
    collector). Fields read: ``signal_dbm`` (min), ``weak`` (count of
    samples below WEAK_DBM), ``associated`` (min), ``drops`` (carrier_down
    increase), ``uplink`` (max). Only interfaces that carried the default
    route at some point count: a spare radio's signal is not the link's."""
    if not rows:
        return None
    uplink_ifaces = {r.get("interface") for r in rows
                     if r.get("_field") == "uplink" and (r.get("_value") or 0) >= 1}
    by_minute: dict[float, dict] = {}
    for r in rows:
        if uplink_ifaces and r.get("interface") not in uplink_ifaces:
            continue
        epoch, field, value = r.get("_epoch"), r.get("_field"), r.get("_value")
        if epoch is None or value is None or field == "uplink":
            continue
        m = by_minute.setdefault(epoch, {"_epoch": epoch})
        if field == "signal_dbm":
            m["signal_dbm"] = min(float(value), m.get("signal_dbm", float(value)))
        elif field == "weak":
            m["weak"] = m.get("weak", 0) + int(value)
        elif field == "associated":
            m["associated"] = min(float(value), m.get("associated", float(value)))
        elif field == "drops":
            m["drops"] = m.get("drops", 0) + int(value)
    return sorted(by_minute.values(), key=lambda m: m["_epoch"]) or None


def _wifi_in(minutes: list[dict] | None, lo: float, hi: float,
             width: float = 60.0) -> dict | None:
    """The Wi-Fi rows of a span. Rows are ``width`` seconds wide (one
    minute, five past two days) and stamped at their end."""
    if minutes is None:
        return None
    own = [m for m in minutes if lo < m["_epoch"] <= hi + width]
    if not own:
        return None
    signals = [m["signal_dbm"] for m in own if "signal_dbm" in m]
    return {
        "minutes": len(own),
        "min_dbm": min(signals) if signals else None,
        "weak": sum(m.get("weak", 0) for m in own),
        "unassociated_s": width * sum(1 for m in own if m.get("associated", 1) < 1),
        "drops": sum(m.get("drops", 0) for m in own),
    }


def merge_spans(cuts: list[dict]) -> list[dict]:
    """This host's cuts (one per CPE target and protocol for one hang)
    merged into spans: ``start_epoch``, ``seconds``, ``deaf``. Oldest
    first. Not microcuts.deaf_spans: that one drops the epochs."""
    merged: list[dict] = []
    for c in sorted((c for c in cuts if c.get("start_epoch") is not None),
                    key=lambda c: c["start_epoch"]):
        lo, hi = c["start_epoch"], c["start_epoch"] + c["seconds"]
        if merged and lo <= merged[-1]["start_epoch"] + merged[-1]["seconds"]:
            last = merged[-1]
            last["seconds"] = max(last["seconds"], hi - last["start_epoch"])
            if c.get("deaf") == "not associated":
                last["deaf"] = c["deaf"]
            continue
        merged.append({"start_epoch": lo, "seconds": hi - lo, "deaf": c.get("deaf")})
    return merged


def first_hop_floor(rows: list[dict], step_s: int) -> dict:
    """``{"cycles": {step: mean loss %}, "usual": median}`` from per-cycle
    mean ``cpe_latency`` loss rows (``_epoch``, ``_value`` 0-100), every
    CPE target and protocol averaged. Empty when there are none."""
    sums: dict[int, list[float]] = {}
    for r in rows:
        if r.get("_epoch") is None or r.get("_value") is None:
            continue
        step = int(r["_epoch"] // step_s) * step_s
        sums.setdefault(step, []).append(float(r["_value"]))
    cycles = {s: sum(v) / len(v) for s, v in sums.items()}
    if not cycles:
        return {}
    ordered = sorted(cycles.values())
    return {"cycles": cycles, "usual": ordered[len(ordered) // 2]}


def _floor_in(floor: dict, lo: float, hi: float, step_s: int) -> float | None:
    own = [v for s, v in (floor.get("cycles") or {}).items() if lo < s <= hi + step_s]
    return sum(own) / len(own) if own else None


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


def chronic_targets(events: list[dict], window_steps: int) -> list[str]:
    """Targets that lost everything in CHRONIC_SHARE of the window's steps."""
    if window_steps <= 0:
        return []
    total: dict[str, int] = {}
    for e in events:
        if e["loss_pct"] >= TOTAL:
            total[e["target"]] = total.get(e["target"], 0) + 1
    return sorted(t for t, n in total.items() if n >= CHRONIC_SHARE * window_steps)


def fold_incidents(events: list[dict], step_s: int,
                   exclude: set[str] | frozenset = frozenset()) -> list[dict]:
    """Fold loss events of every target into incidents: runs of steps with
    any event, no more than GAP_STEPS apart. Oldest first.

    ``events``: ``target``, ``category``, ``loss_pct`` (0-100), ``_epoch``.
    Each incident: ``steps`` {step epoch: {target: loss_pct}}, ``lo``/``hi``
    epochs (a point at t covers the step before it), ``categories``.
    """
    by_step: dict[int, dict[str, float]] = {}
    categories: dict[str, str | None] = {}
    for e in events:
        if (e.get("_epoch") is None or e["target"] in exclude
                or (e.get("category") or "").lower() == FIRST_HOP_CATEGORY):
            continue
        step = int(e["_epoch"] // step_s) * step_s
        hits = by_step.setdefault(step, {})
        hits[e["target"]] = max(hits.get(e["target"], 0.0), float(e["loss_pct"]))
        categories.setdefault(e["target"], e.get("category"))
    incidents: list[dict] = []
    for step in sorted(by_step):
        if incidents and step - incidents[-1]["_last"] <= GAP_STEPS * step_s:
            inc = incidents[-1]
        else:
            inc = {"steps": {}}
            incidents.append(inc)
        inc["steps"][step] = by_step[step]
        inc["_last"] = step
    for inc in incidents:
        inc.pop("_last")
        steps = sorted(inc["steps"])
        inc["lo"] = steps[0] - step_s
        inc["hi"] = steps[-1]
        targets = {t for s in steps for t in inc["steps"][s]}
        inc["categories"] = {t: categories.get(t) for t in targets}
    return incidents


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _confidence(evidence: list[str], against: list[str]) -> str:
    if against:
        return "low"
    return "high" if len(evidence) >= 2 else "medium"


def classify(inc: dict, ctx: dict) -> dict:
    """One incident -> its class, confidence and evidence.

    ``ctx``: ``step_s``; ``reporting`` {step: targets reporting};
    ``cuts`` link cuts and ``deaf`` this host's spans, each with
    ``start_epoch``, ``seconds``, ``confirmed`` (cuts) or ``deaf`` (spans);
    ``cpe`` whether the CPE probe reported in the window; ``wifi`` per
    minute (:func:`wifi_minutes`) or None; ``app`` {site: [(epoch,
    loss_ratio)]} for app-layer events and ``app_sites`` the sites with
    app-layer data; ``ipv6`` predicate on (target, category).
    """
    step_s = ctx["step_s"]
    lo, hi = inc["lo"], inc["hi"]
    span = hi - lo
    steps = inc["steps"]
    targets = sorted(inc["categories"])
    n = len(targets)
    shares = []
    peak = (0, 0)
    total_peak = (0, 0)
    total_steps = 0
    for s, hits in steps.items():
        reporting = ctx["reporting"].get(s, 0)
        if reporting >= MIN_TARGETS:
            shares.append(len(hits) / reporting)
            if len(hits) / reporting >= max(shares):
                peak = (len(hits), reporting)
            lost = sum(1 for v in hits.values() if v >= TOTAL)
            if lost >= 0.8 * reporting:
                total_steps += 1
                total_peak = max(total_peak, (lost, reporting))
    share = max(shares, default=0.0)
    reporting = max((ctx["reporting"].get(s, 0) for s in steps), default=0)
    broad = n >= MIN_TARGETS and share >= BROAD_SHARE
    max_loss = max(v for hits in steps.values() for v in hits.values())

    cuts = [c for c in ctx.get("cuts") or []
            if _overlap(lo, hi, c["start_epoch"], c["start_epoch"] + c["seconds"]) > 0]
    confirmed_cuts = [c for c in cuts if c.get("confirmed")]
    deaf_s = sum(_overlap(lo, hi, d["start_epoch"], d["start_epoch"] + d["seconds"])
                 for d in ctx.get("deaf") or [])
    wifi = _wifi_in(ctx.get("wifi"), lo, hi, ctx.get("wifi_width", 60.0))
    floor = ctx.get("floor") or {}
    floor_now = _floor_in(floor, lo, hi, step_s)
    floor_up = (floor_now is not None
                and floor_now - floor["usual"] >= FLOOR_RISE_PCT)
    floor_line = (f"the first hop (CPE) lost {floor_now:.0f}% on average then, "
                  f"against its usual {floor['usual']:.0f}%"
                  if floor_now is not None else None)
    # Most destinations, though never most at the same moment: a low-grade
    # loss shared by every path. Breadth over the incident, not per cycle.
    spread = (not broad and n >= MIN_TARGETS and reporting
              and n / reporting >= BROAD_SHARE)

    def app_sibling(target: str) -> str | None:
        """'lossy', 'clean', or None (no app-layer data for the site)."""
        site = site_key(target)
        if site not in (ctx.get("app_sites") or set()):
            return None
        for epoch, ratio in (ctx.get("app") or {}).get(site, []):
            if lo - step_s < epoch <= hi + step_s and ratio >= APP_LOSS:
                return "lossy"
        return "clean"

    out = {
        "start_epoch": lo,
        "end_epoch": hi,
        "minutes": _minutes(span),
        "targets": targets,
        "targets_affected": n,
        "targets_reporting": reporting,
        "max_loss_pct": round(max_loss, 1),
        "all_lost_steps": total_steps,
    }
    evidence: list[str] = []
    against: list[str] = []

    def done(cls: str, detail: str | None = None) -> dict:
        out.update({"class": cls, "detail": detail,
                    "confidence": _confidence(evidence, against),
                    "evidence": evidence, "against": against})
        if cls in ("local_link", "upstream") and not ctx.get("cpe"):
            # Without the first hop the line cannot be told from beyond it.
            out["confidence"] = "low" if out["confidence"] == "low" else "medium"
        return out

    # 1. This host's Wi-Fi. A deaf or dropped radio makes every probe lose
    #    everything it sends, so nothing beyond it can be judged.
    # Only a fault that covers most of the span explains it; a minute of
    # deafness inside a five-hour episode is context, not the cause.
    wifi_reasons = []
    wifi_context = []
    unassoc_s = wifi["unassociated_s"] if wifi else 0.0
    if deaf_s:
        line = (f"this host's Wi-Fi heard nothing for {_minutes(deaf_s)} of the "
                f"{_minutes(span)} minutes (no packet received)")
        (wifi_reasons if deaf_s >= 0.5 * span else wifi_context).append(line)
    if unassoc_s:
        line = (f"this host's Wi-Fi was not associated for "
                f"{_minutes(unassoc_s)} minute(s)")
        (wifi_reasons if unassoc_s >= 0.5 * span else wifi_context).append(line)
    if wifi and wifi["drops"]:
        line = f"this host's Wi-Fi carrier dropped {wifi['drops']} time(s)"
        (wifi_reasons if len(steps) <= 3 else wifi_context).append(line)
    weak = bool(wifi and wifi["weak"] >= WEAK_SAMPLES and wifi["min_dbm"] is not None)
    if wifi_reasons:
        evidence.extend(wifi_reasons)
        if total_steps or broad:
            evidence.append(f"{n} of {reporting} destinations lost packets at once, "
                            "as a deaf monitor makes them")
        detail = ("deaf" if deaf_s >= 0.5 * span else "disassociated"
                  if unassoc_s >= 0.5 * span else "carrier")
        return done("local_wifi", detail)
    against.extend(wifi_context)

    def wifi_held() -> str | None:
        """What the Wi-Fi did, said only when nothing in ``against``
        contradicts it."""
        if wifi is None or wifi_context:
            return None
        if wifi["weak"] == 0 and wifi["min_dbm"] is not None:
            return (f"this host's Wi-Fi stayed associated, never weaker than "
                    f"{wifi['min_dbm']:.0f} dBm, with no carrier drop")
        return "this host's Wi-Fi stayed associated, with no carrier drop"
    if weak and (confirmed_cuts or broad):
        evidence.append(f"this host's Wi-Fi signal fell to {wifi['min_dbm']:.0f} dBm "
                        f"({wifi['weak']} weak samples)")
        if confirmed_cuts:
            evidence.append("the first hop cut out at the same time, across that same air")
        return done("local_wifi", "weak_signal")

    # 2. The line: everything lost everything while the radio still heard,
    #    or the first hop cut out together with several destinations.
    if total_steps and n >= MIN_TARGETS:
        evidence.append(f"nearly every destination lost every packet in "
                        f"{total_steps} cycle(s) (up to {total_peak[0]} of the "
                        f"{total_peak[1]} reporting)")
        if wifi is not None and not wifi_context:
            evidence.append("this host's Wi-Fi was associated and receiving the whole "
                            "time, so the monitor itself was not deaf")
        elif wifi is None:
            against.append("no Wi-Fi data for this host: its own link (cable, port) "
                           "cannot be ruled out")
        if confirmed_cuts:
            evidence.append(f"the first hop (CPE) cut out too ({len(confirmed_cuts)} cut(s))")
        return done("local_link", "outage")
    if confirmed_cuts and n >= 2:
        evidence.append(f"the first hop (CPE) cut out ({len(confirmed_cuts)} confirmed "
                        f"cut(s)) while {n} destinations lost packets")
        if broad:
            evidence.append(f"{n} of {reporting} destinations affected")
        if weak:
            against.append(f"this host's Wi-Fi was weak at the time "
                           f"({wifi['min_dbm']:.0f} dBm): the air may be the cause")
        return done("local_link", "cuts")

    if (broad or spread) and floor_up:
        evidence.append(f"{n} of {reporting} destinations lost packets"
                        + (" at once" if broad else " over the span"))
        evidence.append(floor_line + ": the air or the line, which every path "
                        "crosses")
        if wifi_held():
            evidence.append(wifi_held())
        return done("local_link", "degraded")

    # 3. Broad, with a clean first hop: beyond the line.
    if spread and ctx.get("cpe") and floor_now is not None:
        evidence.append(f"{n} of {reporting} destinations lost a ping or more over "
                        f"{_minutes(span)} minutes, never most of them at once "
                        f"(at most {share:.0%} in a cycle)")
        evidence.append(floor_line + ": the first-hop probe crosses the same Wi-Fi "
                        "and line every 30 s, and they carried it normally")
        if wifi_held():
            evidence.append(wifi_held())
        out_ = done("upstream", "spread")
        # A shared low-grade loss is an inference, never certain.
        out_["confidence"] = "medium" if out_["confidence"] == "high" else out_["confidence"]
        return out_
    if broad:
        evidence.append(f"{peak[0]} of {peak[1]} destinations lost packets in the "
                        f"same cycle ({n} over the episode)")
        cats = {c for c in inc["categories"].values() if c}
        if len(cats) >= 2:
            evidence.append(f"across {len(cats)} kinds of destination "
                            f"({', '.join(sorted(cats))})")
        if ctx.get("cpe"):
            if cuts:
                against.append("the first hop had a possible (unconfirmed) cut then")
            else:
                evidence.append(floor_line or "the first hop (CPE) stayed up the "
                                "whole time")
        else:
            against.append("no first-hop (CPE) data: the line cannot be cleared")
        return done("upstream")

    sibling = {t: app_sibling(t) for t in targets}

    # 4. One point on one target with nothing around it: the probe.
    kept = list(against)
    if n == 1 and len(steps) == 1 and max_loss < TOTAL and not cuts and not kept:
        t = targets[0]
        evidence.append(f"one point on {t} ({max_loss:.0f}% loss) and nothing else "
                        "around it")
        if sibling[t] == "clean":
            evidence.append("the same site answered over TCP/HTTP at the time")
        elif sibling[t] == "lossy":
            against.append("the same site also failed over TCP/HTTP then")
        if not against:
            return done("probe_miss")
        evidence.clear()
        against[:] = kept

    # 5. One or two destinations, their peers fine: theirs.
    if n <= 2:
        detail = None
        cats = {inc["categories"][t] for t in targets}
        if all((c or "").lower().startswith("dns") for c in cats):
            detail = "dns"
        elif ctx.get("ipv6") and all(ctx["ipv6"](t, inc["categories"][t])
                                     for t in targets):
            detail = "ipv6"
        evidence.append(f"only {', '.join(targets)} lost packets "
                        f"({n} of {reporting} destinations)")
        if len(steps) >= 2:
            evidence.append(f"for {len(steps)} cycles, not a single point")
        lossy = [t for t in targets if sibling[t] == "lossy"]
        clean = [t for t in targets if sibling[t] == "clean"]
        if lossy:
            evidence.append(f"{', '.join(lossy)} also failed over TCP/HTTP")
        if clean:
            against.append(f"{', '.join(clean)} answered over TCP/HTTP at the time: "
                           "the site may only be dropping pings")
        if cuts:
            against.append("the first hop had a possible cut then")
        return done("destination", detail)

    evidence.append(f"{n} of {reporting} destinations lost packets "
                    f"(at most {share:.0%} in a cycle), neither one site nor most "
                    "of them")
    if floor_line:
        evidence.append(floor_line)
    if wifi_held():
        evidence.append(wifi_held())
    if cuts:
        evidence.append("the first hop had a possible cut then")
    out_ = done("unclear")
    out_["confidence"] = "low"
    return out_


def standalone(cut_or_span: dict, kind: str, ctx: dict) -> dict:
    """A confirmed microcut (``kind='cut'``) or a deaf span (``'deaf'``)
    with no loss incident around it: the targets' 5-minute points did not
    see it, the 10-second CPE probe did."""
    lo = cut_or_span["start_epoch"]
    hi = lo + cut_or_span["seconds"]
    wifi = _wifi_in(ctx.get("wifi"), lo, hi, ctx.get("wifi_width", 60.0))
    seconds = int(cut_or_span["seconds"])
    out = {"start_epoch": lo, "end_epoch": hi, "minutes": _minutes(seconds),
           "seconds": seconds, "targets": [], "targets_affected": 0,
           "targets_reporting": 0, "max_loss_pct": None, "all_lost_steps": 0}
    evidence: list[str] = []
    against: list[str] = []
    if kind == "deaf":
        evidence.append(f"this host's Wi-Fi heard nothing for {seconds} s "
                        f"({cut_or_span.get('deaf') or 'deaf'})")
        evidence.append("the first-hop probe lost everything for exactly that span")
        cls, detail = "local_wifi", "deaf"
    elif wifi and wifi["weak"] >= WEAK_SAMPLES and wifi["min_dbm"] is not None:
        evidence.append(f"the first hop (CPE) cut out for {seconds} s")
        evidence.append(f"this host's Wi-Fi signal fell to {wifi['min_dbm']:.0f} dBm then")
        cls, detail = "local_wifi", "weak_signal"
    else:
        evidence.append(f"the first hop (CPE) cut out for {seconds} s "
                        f"({cut_or_span.get('windows', 0)} windows, worst "
                        f"{cut_or_span.get('max_loss_pct', 0):.0f}%)")
        if cut_or_span.get("total"):
            evidence.append("every probe in it was lost")
        if wifi is None:
            against.append("no Wi-Fi data for this host: its own link cannot be "
                           "ruled out")
        elif not wifi["unassociated_s"] and not wifi["drops"]:
            evidence.append("this host's Wi-Fi was receiving the whole time")
        cls, detail = "local_link", "microcut"
    out.update({"class": cls, "detail": detail,
                "confidence": _confidence(evidence, against),
                "evidence": evidence, "against": against})
    return out


_SUMMARY = {
    "probe_miss": "A probe missed a few pings; nothing else moved. Not a problem.",
    "local_wifi": "This host's own Wi-Fi, not the line: the monitor could not "
                  "hear, so nothing beyond it can be judged for that span.",
    "local_link": "The line between the house and the ISP (router, modem or the "
                  "ISP's access).",
    "destination": "Those destinations, not you: their peers were fine.",
    "upstream": "Beyond your line: most destinations at once while the first hop "
                "stayed up.",
    "unclear": "No single cause fits the evidence.",
}

_DETAIL = {
    "deaf": "The radio was associated but received nothing (or not associated): "
            "the monitor was deaf.",
    "disassociated": "The radio lost its association with the access point.",
    "carrier": "The radio's carrier dropped.",
    "weak_signal": "The signal was weak while the first hop cut out: the router "
                   "or the air.",
    "outage": "Every destination lost everything while the monitor still heard "
              "its network.",
    "cuts": "The first hop cut out with several destinations.",
    "microcut": "A brief cut the 5-minute targets did not see.",
    "degraded": "The first hop's loss rose with everything else's: the air or "
                "the line, not one site.",
    "spread": "A low-grade loss on most paths, never most at once, while the "
              "first hop was normal.",
    "dns": "Only DNS resolvers were affected.",
    "ipv6": "Only IPv6 destinations were affected.",
}


def summary(incident: dict) -> str:
    """One sentence for an incident, from its class and detail."""
    text = _SUMMARY[incident["class"]]
    detail = incident.get("detail")
    return f"{text} {_DETAIL[detail]}" if detail in _DETAIL else text


def diagnose(events: list[dict], ctx: dict, window_steps: int) -> dict:
    """Every incident in the window, classified. Newest first.

    Returns ``incidents`` (each with ``summary``), ``by_class`` counts and
    ``chronic`` targets left out."""
    chronic = chronic_targets(events, window_steps)
    if chronic:
        # They report in every cycle but are not destinations that can fail.
        ctx = {**ctx, "reporting": {s: max(0, n - len(chronic))
                                    for s, n in ctx["reporting"].items()}}
    incidents = [classify(inc, ctx)
                 for inc in fold_incidents(events, ctx["step_s"], set(chronic))]

    def covered(lo: float, hi: float) -> bool:
        return any(_overlap(lo, hi, i["start_epoch"] - ctx["step_s"],
                            i["end_epoch"] + ctx["step_s"]) > 0 for i in incidents)

    extra = []
    for span in ctx.get("deaf") or []:
        if not covered(span["start_epoch"], span["start_epoch"] + span["seconds"]):
            extra.append(standalone(span, "deaf", ctx))
    for cut in ctx.get("cuts") or []:
        if not cut.get("confirmed"):
            continue
        lo, hi = cut["start_epoch"], cut["start_epoch"] + cut["seconds"]
        if covered(lo, hi) or any(_overlap(lo, hi, e["start_epoch"], e["end_epoch"]) > 0
                                  for e in extra):
            continue
        extra.append(standalone(cut, "cut", ctx))
    incidents.extend(extra)
    for inc in incidents:
        inc["summary"] = summary(inc)
    incidents.sort(key=lambda i: i["start_epoch"], reverse=True)
    by_class: dict[str, int] = {}
    for inc in incidents:
        by_class[inc["class"]] = by_class.get(inc["class"], 0) + 1
    return {"incidents": incidents, "by_class": by_class, "chronic": chronic}
