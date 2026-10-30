"""Signals -> episodes: one episode per physical fact.

The rules (evaluator.py), the watchdog (watchdog_events.py) and the
monitor's own health each produce **signals**. Until front 8 each signal was
its own notification, which is how one radio hang reached the phone as an
``outage`` three times (one per 5-minute bucket), an ``uplink_down``, an
``exporter_stale``, a ``microcut_burst`` and their recoveries, and October
as 72 messages for about eleven events (audit of 2026-10-30).

Here the signals of one cycle are folded into the **open episode**: at most
one is open at a time, every signal active while it is open joins it (an
outage, the uplink loss, the stalled exporter, the watchdog's driver reload
and the microcut of the reconnection are one fact; per-destination loss in
the same window is one episode with breadth), and it closes once no signal
has had evidence for ALERT_RESOLVE_AFTER (never under three probe cycles):
a signal's ``end`` is the last cycle its loss was measured, not the last
minute its rule's window still held it.
One open episode is a simplification that holds for one home line: two
unrelated facts in the same minutes are rare, and when they happen the
episode is reclassified and says so (policy.py), rather than two messages.

**What the monitor could not hear is subtracted first.** The monitor's own
blind spans -- this Pi's deaf radio (``wifi_link``), its lost default route
(``host_uplink``), an ``upgrade`` window, a watchdog action, its database
unreadable, its exporter stalled -- are kept on the episode, widened by one
probe cycle on each side, and a probe cycle or a signal inside them is not
evidence about the network. An episode that is all blind is a ``monitor``
episode, never an internet one.

Then, every cycle, the episode is **classified** (one cause,
common.diagnosis.classify_episode) and **judged**
(common.definitions.materiality, the gate the weekly summary and the MCP
tools share), both from the heard minutes only. policy.py decides what of
it is said; this module never notifies.

State: ``state["episodes"]`` = ``{"seq", "open", "closed"}``; ``closed`` is
the record of the last HISTORY_DAYS, newest last, read by the MCP server's
``list_alert_state``.
"""

from __future__ import annotations

import logging
from datetime import datetime

import clock
import evaluator
import flux
import state as state_mod
from common import definitions, diagnosis, microcuts, mutes, patterns

log = logging.getLogger("alerter.episodes")

HISTORY_DAYS = 30
HISTORY_MAX = 500
# Probe cycles kept per episode: 11 hours of a deaf radio is 132.
STEPS_MAX = 600
# The signals about one destination; the rest are about everything.
PER_TARGET_KINDS = frozenset({"high_loss", "target_down"})
TOTAL_KINDS = frozenset(definitions.TOTAL_RULES)
# A per-destination signal active for this long is the destination's state,
# not an event: a target that never answers ICMP (bare amazon.com), or IPv6
# on a host without it, would otherwise keep the one open episode open
# forever, and every later outage would join it unsaid (review of #436).
CHRONIC_SIGNAL_S = 6 * 3600
CHRONIC_KINDS = PER_TARGET_KINDS | {"ipv6_down"}
# An ``upgrade`` start marker with no end closes after this (as inference).
UPGRADE_MAX_S = 1800
UPGRADE_LOOKBACK_S = 2 * 3600
# Cycles before an episode that give the first hop's usual loss (its ICMP
# rate-limit floor): as many as the down window holds, at most this.
HOP_USUAL_STEPS = 12


# ---------------------------------------------------------------------------
# Spans
# ---------------------------------------------------------------------------

def _epoch(value) -> float | None:
    if value is None:
        return None
    if hasattr(value, "timestamp"):
        return float(value.timestamp())
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def deaf_spans(rows: list[dict]) -> list[list]:
    """This Pi's deaf radio, from common.microcuts.uplink_flux rows (deaf
    samples only): samples at most COVER_S apart are one span, each sample
    speaking for the WINDOW_S before it."""
    times = sorted(t for t in (_epoch(r.get("_time")) for r in rows) if t is not None)
    spans: list[list] = []
    for t in times:
        if spans and t - spans[-1][1] <= microcuts.COVER_S:
            spans[-1][1] = t
        else:
            spans.append([t - microcuts.WINDOW_S, t, "radio"])
    return spans


def route_spans(changes: list[dict], now: float) -> list[list]:
    """This Pi without a default route, from host_uplink change points
    (common.aggregates.parse_uplink_changes): from losing it (``interface``
    empty) to getting one back (``previous`` "none"), or to now."""
    spans: list[list] = []
    lost = None
    for change in changes:
        at = _epoch(change.get("time"))
        if at is None:
            continue
        if not change.get("interface"):
            lost = at if lost is None else lost
        elif change.get("previous") == "none" and lost is not None:
            spans.append([lost, at, "route"])
            lost = None
    if lost is not None:
        spans.append([lost, now, "route"])
    return spans


def upgrade_flux() -> str:
    """The ``upgrade`` markers ``smoking-pi upgrade`` writes (``phase``
    start / end), over the last UPGRADE_LOOKBACK_S."""
    return (f"from(bucket: {flux.flux_str(flux.influx_bucket())}) "
            f"|> range(start: -{UPGRADE_LOOKBACK_S}s) "
            '|> filter(fn: (r) => r._measurement == "upgrade" and r._field == "value") '
            '|> keep(columns: ["_time", "phase"]) |> group() '
            '|> sort(columns: ["_time"])')


def upgrade_spans(rows: list[dict], now: float) -> list[list]:
    """Upgrade windows from their markers: a start closes at its end, at
    UPGRADE_MAX_S, or at ``now`` while it may still be running."""
    events = sorted((t, str(r.get("phase"))) for r in rows
                    if (t := _epoch(r.get("_time"))) is not None)
    spans: list[list] = []
    opened = None
    for at, phase in events:
        if phase == "start":
            if opened is not None:
                spans.append([opened, min(at, opened + UPGRADE_MAX_S), "upgrade"])
            opened = at
        elif phase == "end":
            if opened is not None and at - opened <= UPGRADE_MAX_S:
                spans.append([opened, at, "upgrade"])
            else:
                spans.append([at - 60, at, "upgrade"])
            opened = None
    if opened is not None:
        spans.append([opened, min(now, opened + UPGRADE_MAX_S), "upgrade"])
    return spans


# What the watchdog does, as opposed to what it reports (cli/lib/watchdog.sh).
_ACTIONS = ("reload", "reboot", "restart")


def watchdog_spans(signals: list[dict], step: int) -> list[list]:
    """One probe cycle after each action of the watchdog: a driver reload
    drops the link for seconds, and what the probes saw then is the
    reload's. Its warnings (it could not act) are not actions."""
    return [[float(s["at"]), float(s["at"]) + step, "watchdog"]
            for s in signals
            if s.get("rule") == "watchdog" and s.get("at") is not None
            and s.get("severity") == "info"
            and any(w in str(s.get("message")).lower() for w in _ACTIONS)]


def _merge(spans: list[list]) -> list[list]:
    """Overlapping spans of one reason merged, oldest first."""
    out: list[list] = []
    for lo, hi, reason in sorted(spans, key=lambda s: (s[2], s[0])):
        if out and out[-1][2] == reason and lo <= out[-1][1]:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi, reason])
    return sorted(out)


def _overlap(lo: float, hi: float, a: float, b: float) -> float:
    return max(0.0, min(hi, b) - max(lo, a))


def _covered(lo: float, hi: float, spans: list[list], margin: float) -> bool:
    """Whether [lo, hi] lies inside the blind spans widened by ``margin``."""
    widened = sorted([s[0] - margin, s[1] + margin] for s in spans)
    at = lo
    for a, b in widened:
        if a > at:
            break
        at = max(at, b)
        if at >= hi:
            return True
    return at >= hi


def _union_s(spans: list[list], lo: float, hi: float) -> float:
    """Seconds of [lo, hi] inside any span."""
    cut = sorted([max(lo, s[0]), min(hi, s[1])] for s in spans if s[1] > lo and s[0] < hi)
    total, end = 0.0, lo
    for a, b in cut:
        a = max(a, end)
        if b > a:
            total += b - a
            end = b
    return total


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

def signals(incidents: list[dict], context: dict, now: float,
            extra: list[dict] = (), records: dict | None = None) -> list[dict]:
    """This cycle's signals: the rules' incidents with a span each, plus
    ``extra`` (the watchdog's events, the monitor's own). A muted signal
    (common.mutes, written by the MCP server) is dropped here, before it
    can open or extend an episode. ``records`` (state.track's) give each
    key's ``first_seen``, so an acknowledgement covers the occurrence it
    acknowledged and no later one; a per-destination signal whose record
    is older than CHRONIC_SIGNAL_S is that destination's state and is left
    out, as is a ``quiet`` record retirement with no evidence of its own."""
    records = records or {}
    step = int(context.get("windows", {}).get("step") or evaluator.STEP_S)
    stats = evaluator.step_stats(context.get("down_rows") or [], step)
    last_lossy: dict[str, list[int]] = {}
    for at, s in sorted(stats.items()):
        for target in s["affected"]:
            last_lossy.setdefault(target, []).append(at)
    entries = mutes.load()
    out = []
    for incident in list(incidents) + list(extra):
        signal = dict(incident)
        kind = signal.get("rule")
        if signal.get("quiet") and signal.get("start") is None:
            continue  # a retirement (measurement_stale past its lookback)
        record = records.get(signal.get("key") or "") or {}
        if (kind in CHRONIC_KINDS and record.get("first_seen") is not None
                and now - float(record["first_seen"]) > CHRONIC_SIGNAL_S):
            continue
        if kind in PER_TARGET_KINDS:
            seen = last_lossy.get(signal.get("target") or "")
            signal.setdefault("start", seen[0] - step if seen else now - step)
            signal.setdefault("end", seen[-1] if seen else now)
        elif kind == "watchdog":
            signal.setdefault("start", signal.get("at", now))
            signal.setdefault("end", signal.get("at", now))
        elif kind == "microcut_burst" and signal.get("cuts"):
            signal.setdefault("start", min(c["start"] for c in signal["cuts"]))
            signal.setdefault("end", max(c["end"] for c in signal["cuts"]))
        elif kind == "exporter_stale":
            window = int(context.get("windows", {}).get("stale") or 1200)
            signal.setdefault("start", now - window)
            signal.setdefault("end", now)
        signal.setdefault("start", now - step)
        signal.setdefault("end", now)
        if mutes.find(entries, signal, now,
                      first_seen=record.get("first_seen")) is not None:
            log.info("Signal %s muted; not part of any episode", signal.get("key"))
            continue
        out.append(signal)
    return out


# ---------------------------------------------------------------------------
# Episodes
# ---------------------------------------------------------------------------

def book(state: dict) -> dict:
    box = state.setdefault("episodes", {})
    box.setdefault("seq", 0)
    box.setdefault("open", None)
    box.setdefault("closed", [])
    return box


def _new(box: dict, now: float, start: float) -> dict:
    box["seq"] = int(box["seq"]) + 1
    return {
        "id": box["seq"],
        "key": f"episode:{box['seq']}",
        "opened_at": now,
        "start": start,
        "end": start,
        "last_active": now,
        "signals": {},
        "sites": [],
        "steps": {},
        "blind": [],
        "cuts": [],
        "watchdog": [],
        "wifi": None,
        # For the messages' numbers (templates.py): each destination's
        # category, the first hop's loss in the cycles before the episode
        # (its usual level), and this Pi's weakest Wi-Fi signal meanwhile.
        "category_of": {},
        "hop_before": {},
        "wifi_dbm": None,
        "wifi_weak": 0,
        "policy": {},
    }


def _absorb(ep: dict, signal: dict, now: float) -> None:
    kind = signal.get("rule") or "?"
    entry = ep["signals"].setdefault(kind, {"first": now, "count": 0,
                                            "spans": []})
    entry["last"] = now
    entry["count"] += 1
    span = [float(signal["start"]), float(signal["end"])]
    if entry["spans"] and span[0] <= entry["spans"][-1][1]:
        entry["spans"][-1] = [min(entry["spans"][-1][0], span[0]),
                              max(entry["spans"][-1][1], span[1])]
    else:
        entry["spans"].append(span)
    del entry["spans"][:-50]
    if signal.get("first_hop") and kind in TOTAL_KINDS | {"outage"}:
        entry["first_hop"] = signal["first_hop"]
    if kind in PER_TARGET_KINDS and signal.get("target"):
        entry.setdefault("targets", [])
        if signal["target"] not in entry["targets"]:
            entry["targets"].append(signal["target"])
    if kind == "ipv6_down":
        entry["targets"] = ["ipv6"]
    for cut in signal.get("cuts") or ():
        piece = [float(cut["start"]), float(cut["end"])]
        if piece not in ep["cuts"]:
            ep["cuts"].append(piece)
    if kind == "watchdog":
        told = {w["at"] for w in ep["watchdog"]}
        if signal.get("at") not in told:
            ep["watchdog"].append({"at": signal.get("at"),
                                   "message": signal.get("message"),
                                   "text": signal.get("text"),
                                   "severity": signal.get("severity")})
    ep["start"] = min(ep["start"], span[0])
    ep["end"] = max(ep["end"], span[1])


def update(state: dict, cycle: dict) -> list[dict]:
    """Fold one cycle into the episodes. Returns what changed:
    ``[{"event": "opened"|"active"|"closed", "episode": ...}]``.

    ``cycle``: ``now``, ``step``, ``signals`` (:func:`signals`), ``rows``
    (the down window's loss points), ``blind`` (spans [lo, hi, reason]
    seen this cycle), ``wifi`` ("receiving" when this Pi has Wi-Fi
    telemetry, else None), ``blind_cycle`` (True when nothing could be
    evaluated: such a cycle neither extends nor closes an episode).
    """
    box = book(state)
    now = float(cycle["now"])
    # Since when the record is whole: the weekly summary reads these
    # episodes for a week only when it began before that week.
    box.setdefault("since", now)
    step = int(cycle.get("step") or evaluator.STEP_S)
    close_after = max(state_mod.resolve_after(), 3 * step)
    # A signal speaks for its evidence, not for its rule's window: a cut
    # stays in microcut_burst's hour long after it ended. Only a signal
    # whose evidence is newer than ``close_after`` opens or extends an
    # episode; an older one is the past of an episode already judged.
    sigs = [s for s in cycle.get("signals") or ()
            if float(s["end"]) > now - close_after]
    ep = box["open"]
    events: list[dict] = []
    if ep is None and sigs:
        ep = box["open"] = _new(box, now, min(float(s["start"]) for s in sigs))
        events.append({"event": "opened", "episode": ep})
        log.info("Episode %s opened by %s", ep["key"],
                 sorted({s.get("rule") for s in sigs}))
    if ep is None:
        _prune(box, now)
        return events

    ep["step"] = step
    for signal in sigs:
        _absorb(ep, signal, now)
    if sigs:
        ep["last_active"] = max(float(ep["last_active"]),
                                max(float(s["end"]) for s in sigs))
    if cycle.get("wifi"):
        ep["wifi"] = cycle["wifi"]
    if cycle.get("wifi_dbm") is not None:
        dbm = float(cycle["wifi_dbm"])
        ep["wifi_dbm"] = dbm if ep.get("wifi_dbm") is None else min(ep["wifi_dbm"], dbm)
        ep["wifi_weak"] = max(int(ep.get("wifi_weak") or 0),
                              int(cycle.get("wifi_weak") or 0))
    for row in cycle.get("rows") or ():
        if row.get("target") and row.get("category"):
            ep.setdefault("category_of", {})[row["target"]] = row["category"]
    margin = step
    blind = [list(s) for s in cycle.get("blind") or ()
             if s[1] >= ep["start"] - margin and s[0] <= now + margin]
    blind += watchdog_spans(sigs, step)
    ep["blind"] = _merge(ep["blind"] + blind)
    before = ep.setdefault("hop_before", {})
    for at, stats in evaluator.step_stats(cycle.get("rows") or [], step).items():
        if at > ep["start"] - step:
            ep["steps"][str(at)] = stats
        elif stats.get("hop_loss_pct") is not None:
            before[str(at)] = stats["hop_loss_pct"]
    # The HOP_USUAL_STEPS cycles nearest the episode's start.
    for key in sorted(before, key=float)[:-HOP_USUAL_STEPS]:
        del before[key]
    if len(ep["steps"]) > STEPS_MAX:
        for key in sorted(ep["steps"], key=float)[:-STEPS_MAX]:
            del ep["steps"][key]

    judge(ep, box, now, step, state.get("patterns"))
    if not events:
        events.append({"event": "active", "episode": ep})

    # Not while the monitor is still blind: what it cannot hear now is not
    # clean, and an episode closed in the middle of a hang would open again
    # as a second one when the next rule fires.
    # Nor while no destination has reported for two cycles: no data is not
    # clean (the stall itself becomes exporter_stale only later).
    if (not sigs and not cycle.get("blind_cycle") and heard_now(cycle)
            and now - float(ep["last_active"]) >= close_after):
        ep["closed_at"] = now
        box["open"] = None
        box["closed"].append(summary(ep))
        events = [{"event": "closed", "episode": ep}]
        log.info("Episode %s closed: %s, %s", ep["key"],
                 ep["judgment"]["class"], ep["judgment"]["cause"])
    _prune(box, now)
    return events


def heard_now(cycle: dict) -> bool:
    """Whether the monitor could hear this cycle: not blind now, and some
    destination reported within two probe cycles."""
    if cycle.get("blind_cycle"):
        return False
    now = float(cycle["now"])
    step = int(cycle.get("step") or evaluator.STEP_S)
    newest = max((t for t in (_epoch(r.get("_time")) for r in cycle.get("rows") or ())
                  if t is not None), default=None)
    return not (any(float(s[1]) >= now - step for s in cycle.get("blind") or ())
                or newest is None or newest <= now - 2 * step)


def rejudge(state: dict, now: float, step: int) -> None:
    """Judge the open episode again, after the patterns changed: one that
    just made a pattern of the evenings before it is no longer news."""
    box = book(state)
    if box["open"] is not None:
        judge(box["open"], box, now, step, state.get("patterns"))


def _prune(box: dict, now: float) -> None:
    keep = now - HISTORY_DAYS * 86400
    box["closed"] = [e for e in box["closed"]
                     if float(e.get("end") or 0) >= keep][-HISTORY_MAX:]


# ---------------------------------------------------------------------------
# Judgment
# ---------------------------------------------------------------------------

def _heard_steps(ep: dict, step: int) -> list[tuple[int, dict]]:
    """The episode's probe cycles the monitor could hear, oldest first. A
    point stands for the step that ends at its time, as SmokePing's RRD
    rows do."""
    out = []
    for key, stats in ep["steps"].items():
        at = int(float(key))
        if at < ep["start"]:
            continue
        if _covered(at - step, at, ep["blind"], step):
            continue
        out.append((at, stats))
    return sorted(out, key=lambda x: x[0])


def _longest_run(steps: list[int], step: int) -> int:
    best = run = 0
    prev = None
    for at in steps:
        run = run + 1 if prev is not None and at - prev == step else 1
        best = max(best, run)
        prev = at
    return best


def judge(ep: dict, box: dict, now: float, step: int,
          known: dict | None = None) -> None:
    """Classify the episode and judge its materiality, on heard minutes.
    ``known``: ``state["patterns"]`` (common.patterns); an episode in the
    hours of one of them, of its class and its usual size, is not novel."""
    ep["pattern"] = None
    blind = ep["blind"]
    heard = _heard_steps(ep, step)
    broad = [(at, s) for at, s in heard if evaluator.broad_step(s)]
    total_pct = definitions.widespread_pct()
    total = [at for at, s in heard
             if s["reporting"] >= evaluator.WIDESPREAD_MIN_TARGETS
             and s["lost_pct"] >= total_pct]
    kinds = set()
    monitor_kinds = set()
    first_hop = None
    sites: list[str] = []
    for kind, entry in ep["signals"].items():
        if kind in diagnosis.MONITOR_KINDS:
            monitor_kinds.add(kind)
            continue
        if all(_covered(lo, hi, blind, step) for lo, hi in entry["spans"]):
            continue
        kinds.add(kind)
        if entry.get("first_hop"):
            first_hop = entry["first_hop"]
        if kind in PER_TARGET_KINDS:
            sites += [t for t in entry.get("targets") or () if t not in sites]
    hops = [s.get("first_hop") for _, s in broad or heard if s.get("first_hop")]
    if "lost" in hops:
        first_hop = first_hop or "lost"
    elif hops and first_hop is None:
        first_hop = "answers"
    cuts = [c for c in ep["cuts"] if not _covered(c[0], c[1], blind, step)]
    span_lo, span_hi = float(ep["start"]), max(float(ep["end"]), float(ep["start"]))
    blind_by: dict[str, float] = {}
    for lo, hi, reason in blind:
        seconds = _overlap(lo, hi, span_lo - step, span_hi + step)
        if seconds:
            blind_by[reason] = blind_by.get(reason, 0.0) + seconds
    widest = max((s for _, s in broad or heard), default=None,
                 key=lambda s: (s["share_pct"], len(s["categories"])))
    facts = {
        "blind": blind_by,
        "kinds": sorted(kinds),
        "monitor_kinds": sorted(monitor_kinds),
        "lossy_steps": len(broad),
        "total_steps": _longest_run(total, step),
        "first_hop": first_hop,
        "line_cut": bool(cuts),
        "wifi": ep.get("wifi"),
        "breadth_pct": widest["share_pct"] if widest and broad else 0.0,
        "categories": len(widest["categories"]) if widest and broad else 0,
        "sites": sites,
        "reporting": widest["reporting"] if widest else 0,
        "affected": len(widest["affected"]) if widest and broad else 0,
    }
    judgment = diagnosis.classify_episode(facts)
    longest_cut = max((c[1] - c[0] for c in cuts), default=0.0)
    run = _longest_run([at for at, _ in broad], step)
    down = (facts["total_steps"] >= definitions.TOTAL_MIN_CYCLES
            and judgment["class"] != "monitor")
    microcut = (bool(cuts) and longest_cut < definitions.MICROCUT_MAX_S
                and run <= 1 and not facts["total_steps"])
    recent = [e for e in box["closed"]
              if e.get("broad_cycles") and e.get("class") != "monitor"
              and float(e.get("start") or 0) >= now - definitions.MATERIAL_EPISODES_WINDOW_S]
    loss = (sum(s["mean_loss_pct"] for _, s in broad) / len(broad)) if broad else None
    unmet = definitions.materiality(
        measurable=bool(broad or kinds),
        breadth_pct=facts["breadth_pct"],
        categories=facts["categories"],
        minutes=run * step / 60.0,
        episodes_in_window=len(recent) + (1 if broad else 0),
        loss_pct=loss,
        # No rule measures latency against last week's same hours yet:
        # the condition is in the gate, and nothing feeds it.
        latency_ratio=None,
        # Novelty is decided below, once the loss span is known.
        novel=True,
    )
    if judgment["class"] == "monitor":
        unmet = sorted(set(unmet) | {"measurable"})
    ep["facts"] = facts
    ep["measures"] = _measures(ep, heard, broad, blind_by, longest_cut)
    ep["judgment"] = judgment
    ep["materiality"] = {"unmet": unmet, "loss_pct": None if loss is None
                         else round(loss, 2), "cycles": run}
    ep["material"] = not unmet
    ep["down"] = down
    ep["microcut"] = microcut
    ep["longest_cut_s"] = round(longest_cut)
    ep["blind_s"] = round(_union_s(blind, span_lo - step, max(span_hi, now) + step))
    ep["broad_cycles"] = len(broad)
    heard_lossy = [at for at, s in heard if s["affected"]]
    ep["loss_start"] = heard_lossy[0] - step if heard_lossy else None
    ep["loss_end"] = heard_lossy[-1] if heard_lossy else None
    # A known pattern (a recurring window, common.patterns) explains an
    # episode of its class in its hours: recorded as part of it, not news.
    ep["pattern"] = (patterns.explaining(known, {
        "class": judgment["class"], "start": ep["loss_start"],
        "end": ep["loss_end"], "loss_pct": loss,
        "breadth_pct": facts["breadth_pct"]}, clock.zone()) if heard_lossy else None)
    if ep["pattern"]:
        unmet = sorted(set(unmet) | {"novelty"})
        ep["materiality"]["unmet"] = unmet
        ep["material"] = False


def _median(values: list[float]) -> float | None:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else None


def _measures(ep: dict, heard: list, broad: list, blind_by: dict,
              longest_cut: float) -> dict:
    """The numbers a message states (templates.py), over the heard cycles
    in which something was lost: how many destinations, whether ever all at
    once, the first hop's loss against its usual, the resolvers, the worst
    destination, this Pi's radio."""
    lossy = [s for _, s in heard if s["affected"]] or [s for _, s in heard]
    widest = max(lossy, default=None,
                 key=lambda s: (len(s["affected"]), s["share_pct"]))
    hop = [s["hop_loss_pct"] for s in lossy if s.get("hop_loss_pct") is not None]
    dns = [s.get("by_category", {}).get("dns") for s in lossy]
    dns = [d for d in dns if d]
    per_target: dict[str, list[float]] = {}
    for s in lossy:
        for target, pct in (s.get("loss_by_target") or {}).items():
            per_target.setdefault(target, []).append(float(pct))
    # A destination's loss over the cycles it lost in, worst first.
    worst = sorted(((t, round(sum(v) / len(v), 1)) for t, v in per_target.items() if v),
                   key=lambda x: (-x[1], x[0]))[:3]
    cat = ep.get("category_of") or {}
    return {
        "affected": len(widest["affected"]) if widest else 0,
        "reporting": widest["reporting"] if widest else 0,
        "all_at_once": any(s["share_pct"] >= 100.0 for s in lossy),
        "lost_all_cycles": sum(1 for s in lossy if s["lost_pct"] >= 100.0),
        "hop_loss_pct": round(sum(hop) / len(hop), 1) if hop else None,
        "hop_usual_pct": _median(list((ep.get("hop_before") or {}).values())),
        "dns": ([max(d[1] for d in dns), max(d[0] for d in dns)] if dns else None),
        "worst": [[t, pct, cat.get(t)] for t, pct in worst],
        "wifi": ep.get("wifi"),
        "wifi_dbm": ep.get("wifi_dbm"),
        "wifi_weak": int(ep.get("wifi_weak") or 0),
        "radio_blind_s": round(blind_by.get("radio", 0.0) + blind_by.get("route", 0.0)),
        "longest_cut_s": round(longest_cut),
        "broad_steps": len(broad),
        "site_categories": {t: cat.get(t) for t in (ep.get("facts") or {}).get("sites", [])},
    }


def blind_range(ep: dict) -> tuple[float, float] | None:
    """When the monitor could not measure, first to last, or None."""
    if not ep.get("blind"):
        return None
    lo = min(s[0] for s in ep["blind"])
    hi = max(s[1] for s in ep["blind"])
    return lo, hi


def summary(ep: dict) -> dict:
    """What the record keeps of a closed episode (and the MCP shows)."""
    judgment = ep.get("judgment") or {}
    start, end = float(ep["start"]), float(ep["end"])
    if judgment.get("class") == "monitor" and blind_range(ep):
        start, end = blind_range(ep)
    elif ep.get("loss_start") is not None:
        start, end = ep["loss_start"], ep["loss_end"]
    return {
        "id": ep["id"],
        "key": ep["key"],
        "start": start,
        "end": end,
        "duration_s": round(max(0.0, end - start)),
        "closed_at": ep.get("closed_at"),
        "class": judgment.get("class"),
        "detail": judgment.get("detail"),
        "cause": judgment.get("cause"),
        "confidence": judgment.get("confidence"),
        "evidence": judgment.get("evidence", [])[:3],
        "material": bool(ep.get("material")),
        "unmet": (ep.get("materiality") or {}).get("unmet", []),
        "pattern": ep.get("pattern"),
        "loss_pct": (ep.get("materiality") or {}).get("loss_pct"),
        "down": bool(ep.get("down")),
        "microcut": bool(ep.get("microcut")),
        "longest_cut_s": ep.get("longest_cut_s", 0),
        "blind_s": ep.get("blind_s", 0),
        "broad_cycles": ep.get("broad_cycles", 0),
        "breadth_pct": (ep.get("facts") or {}).get("breadth_pct", 0.0),
        "affected": (ep.get("facts") or {}).get("affected", 0),
        "reporting": (ep.get("facts") or {}).get("reporting", 0),
        "categories": (ep.get("facts") or {}).get("categories", 0),
        "signals": {k: v["count"] for k, v in ep["signals"].items()},
        "sites": (ep.get("facts") or {}).get("sites", []),
        "watchdog": [w.get("message") for w in ep.get("watchdog", [])][:5],
        # What the messages say (templates.py): the reading below medium
        # confidence, the strongest contrary evidence, the numbers, and the
        # watchdog's own words with when.
        "suspected": judgment.get("suspected"),
        "against": judgment.get("against", [])[:2],
        "measures": ep.get("measures") or {},
        "watchdog_actions": [[w.get("at"), w.get("text") or w.get("message")]
                             for w in ep.get("watchdog", [])
                             if w.get("severity") == "info"][:3],
        "blind_range": list(blind_range(ep) or []) or None,
        "blind_by": (ep.get("facts") or {}).get("blind", {}),
        "step": ep.get("step"),
        "pushed": list((ep.get("policy") or {}).get("sent", [])),
    }

