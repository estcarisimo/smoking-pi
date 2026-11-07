"""What changed since a moment: the answer to an assistant's heartbeat.

Front 10 (2026-11) made the OpenClaw agent the voice of the monitor: its
hourly heartbeat asks the Pi "anything new since last time?" and says
nothing (NO_REPLY) unless something is worth saying. Until this module the
MCP tools could answer only per-tool questions (the loss in a window, the
incident history, the ledger), so a heartbeat had two bad choices: call
five tools and judge the result itself -- every hour, with the risk of
re-telling an alert the user already got -- or stay silent.

:func:`answer` reads only what the alerter already wrote, read-only:

- its episode record (``state["episodes"]``: the open episode and the
  closed ones of the last 30 days, each judged material or not, with what
  was pushed about it) -- the monitor's own blind spans (deaf radio, lost
  route, upgrade, watchdog action) are episodes of class ``monitor`` there;
- its recurring windows (``state["patterns"]``), including the ones it
  has yet to announce (``pending``) and the half hours it heard;
- the notification ledger (``ledger.jsonl``), for what was said.

No InfluxDB query at all: an hourly call costs two file reads.

**The verdict** (:func:`verdict`), which the agent may rely on:

- ``worth_mentioning`` when, in the window, at least one of:
  1. a recurring window appeared or disappeared and the alerter neither
     said so nor has it queued to say (``pattern_change``);
  2. a cluster of non-material episodes: CLUSTER_MIN or more starting
     within CLUSTER_S of each other, all in the window
     (``non_material_cluster``) -- the reference Pi records about eight a
     day spread out, which is background, while four in an hour is not;
     they are counted by kind, the class the alerter judged each one;
  3. the monitor was blind for MONITOR_BLIND_MIN_MINUTES (15) or more in an
     episode that closed with nothing pushed about it
     (``monitor_blind_unreported``);
  4. a material episode closed with nothing pushed, held or queued about it
     and no mute covering it (``material_unreported``: a push the alerter
     decided against, for example its hourly ceiling).
- ``material_pushed_already`` when none of those holds but the window had
  material episodes, pattern changes or monitor blindness, every one of
  them already pushed (or held or queued by the alerter, which will deliver
  it): the agent must not repeat them.
- ``nothing_new`` otherwise.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from . import definitions, links
from .notification_ledger import headline as _headline
from .patterns import view as _pattern_view

MAX_DAYS = 7
CLUSTER_MIN = 4
CLUSTER_S = 3600
MAX_MATERIAL = 10
MAX_SERVICES = 5
MAX_PUSHED = 20
# A ledger entry that is, or will be, a message the user gets.
PUSHED_OUTCOMES = ("sent", "handed", "queued", "held", "failed")
VERDICTS = ("nothing_new", "worth_mentioning", "material_pushed_already")

_RELATIVE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(m|min|h|d|w)\s*$", re.IGNORECASE)
_UNIT_S = {"m": 60, "min": 60, "h": 3600, "d": 86400, "w": 7 * 86400}

# A monitor episode's detail (common.diagnosis) in a reader's words.
BLIND_CAUSE = {
    "radio": "the Pi's Wi-Fi radio was deaf",
    "watchdog": "the watchdog was repairing the Pi's Wi-Fi",
    "route": "the Pi lost its default route",
    "database": "the monitor's database could not be read",
    "exporter": "the measurements stopped arriving",
    "upgrade": "an upgrade of the Pi's software",
    "dns": "the Pi's own DNS failed",
}


def parse_since(text, now: float, tz: ZoneInfo) -> tuple[float | None, str | None]:
    """(epoch, error) for ``1h``/``90m``/``24h``/``2d`` or an ISO-8601 time
    (one without an offset is in ``tz``). At most MAX_DAYS back."""
    raw = str(text if text is not None else "").strip()
    if not raw:
        return None, "since is required: an ISO-8601 time or a span like 1h or 24h."
    match = _RELATIVE.match(raw)
    if match:
        since = now - float(match.group(1)) * _UNIT_S[match.group(2).lower()]
    else:
        try:
            when = datetime.fromisoformat(raw.replace("Z", "+00:00").replace("z", "+00:00"))
        except ValueError:
            return None, ("since must be an ISO-8601 time (2026-11-07T08:00:00-06:00) "
                          "or a span like 90m, 1h, 24h or 2d.")
        if when.tzinfo is None:
            when = when.replace(tzinfo=tz)
        since = when.timestamp()
    if since > now:
        return None, "since is in the future."
    if since < now - MAX_DAYS * 86400:
        return None, (f"since may be at most {MAX_DAYS} days back; for longer, use "
                      "get_weekly_summary or get_incident_history.")
    return since, None


def _iso(epoch, tz: ZoneInfo) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), tz=tz).isoformat(timespec="minutes")


def _open_summary(ep: dict) -> dict:
    """The open episode in the shape of a closed one (alerter
    episodes.summary), for the fields read here."""
    judgment = ep.get("judgment") or {}
    facts = ep.get("facts") or {}
    start = ep.get("loss_start") if ep.get("loss_start") is not None else ep.get("start")
    return {
        "id": ep.get("id"), "key": ep.get("key"), "start": start,
        "end": ep.get("loss_end") or ep.get("end") or ep.get("last_seen"),
        "class": judgment.get("class"), "detail": judgment.get("detail"),
        "cause": judgment.get("cause"), "confidence": judgment.get("confidence"),
        "material": bool(ep.get("material")),
        "unmet": (ep.get("materiality") or {}).get("unmet", []),
        "pattern": ep.get("pattern"), "loss_pct": (ep.get("materiality") or {}).get("loss_pct"),
        "down": bool(ep.get("down")), "microcut": bool(ep.get("microcut")),
        "blind_s": ep.get("blind_s", 0), "affected": facts.get("affected", 0),
        "reporting": facts.get("reporting", 0), "breadth_pct": facts.get("breadth_pct", 0.0),
        "sites": facts.get("sites", []),
        "pushed": list((ep.get("policy") or {}).get("sent", [])),
        "night": (ep.get("policy") or {}).get("night"),
        "muted": bool((ep.get("policy") or {}).get("muted")),
        "open": True,
    }


def _pushed_keys(entries: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for e in entries:
        if e.get("key") and e.get("outcome") in PUSHED_OUTCOMES:
            out.setdefault(str(e["key"]), []).append(e)
    return out


def _minutes(ep: dict) -> int:
    try:
        return max(0, round((float(ep["end"]) - float(ep["start"])) / 60.0))
    except (KeyError, TypeError, ValueError):
        return 0


def _blind_minutes(ep: dict) -> int:
    blind = float(ep.get("blind_s") or 0)
    return round(blind / 60.0) if blind else _minutes(ep)


def _scope(ep: dict, service_of) -> dict:
    out = {}
    reporting = int(ep.get("reporting") or 0)
    if reporting:
        out["destinations"] = f"{int(ep.get('affected') or 0)} of {reporting}"
        out["breadth_pct"] = round(float(ep.get("breadth_pct") or 0.0), 1)
    names = []
    for site in ep.get("sites") or ():
        name = service_of(site) if service_of else None
        name = name or links.target_base_name(site)
        if name and name not in names:
            names.append(name)
    if names:
        out["services"] = names[:MAX_SERVICES]
    return out


# A non-material episode's kind, by the class the alerter judged it
# (common.diagnosis.EPISODE_CLASSES): its ``affected`` counts only the
# widest heard cycle, and is 0 for most short ones, so it cannot say how
# many destinations lost.
NON_MATERIAL_KINDS = {
    "destination": ("packet_loss_destination",
                    "short packet-loss episodes on one or two destinations"),
    "local_link": ("packet_loss_line", "short packet-loss episodes on your line"),
    "upstream": ("packet_loss_provider",
                 "short packet-loss episodes beyond your line (your provider)"),
}
UNCLEAR_KIND = ("packet_loss_unclear", "short packet-loss episodes with no clear cause")


def _non_material_kind(ep: dict) -> tuple[str, str]:
    """(kind, words) of a non-material episode, for counting."""
    if ep.get("microcut"):
        return "microcut", "microcuts (cuts under a minute)"
    return NON_MATERIAL_KINDS.get(ep.get("class"), UNCLEAR_KIND)


def clusters(starts: list[float]) -> int:
    """The most episode starts within CLUSTER_S of each other."""
    starts = sorted(starts)
    best, lo = 0, 0
    for hi, t in enumerate(starts):
        while t - starts[lo] > CLUSTER_S:
            lo += 1
        best = max(best, hi - lo + 1)
    return best


def verdict(reasons: list[str], reported_any: bool) -> str:
    if reasons:
        return "worth_mentioning"
    return "material_pushed_already" if reported_any else "nothing_new"


def answer(state: dict, ledger: list[dict], since: float, now: float, tz: ZoneInfo,
           include_non_material: bool = True, service_of=None, muted=None) -> dict:
    """What changed in ``[since, now]``: see the module docstring.
    ``ledger``: the notification ledger's entries (common.notification_ledger.read);
    ``service_of(target) -> str | None``: a target's service label;
    ``muted(key, start) -> bool``: whether a mute covers an episode."""
    box = state.get("episodes") or {}
    said = _pushed_keys([e for e in ledger if float(e.get("decided_at") or 0) >= since - CLUSTER_S])
    episodes = [e for e in box.get("closed") or ()
                if float(e.get("end") or 0) >= since
                or float(e.get("closed_at") or 0) >= since]
    open_ep = box.get("open")
    if open_ep and open_ep.get("judgment"):
        episodes.append(_open_summary(open_ep))

    reasons: list[str] = []
    reported_any = False
    material, monitor, minor = [], [], []
    for ep in episodes:
        cls = ep.get("class")
        key = ep.get("key")
        pushed = list(ep.get("pushed") or ())
        reported = bool(pushed) or bool(said.get(key or "")) or bool(ep.get("night"))
        if cls == "monitor":
            minutes = _blind_minutes(ep)
            item = {"key": key, "state": "ongoing" if ep.get("open") else "closed",
                    "start": _iso(ep.get("start"), tz), "end": _iso(ep.get("end"), tz),
                    "minutes": minutes,
                    "cause": BLIND_CAUSE.get(ep.get("detail"), ep.get("cause")
                                             or "the monitor could not measure"),
                    "reported": reported}
            if ep.get("watchdog_actions"):
                item["watchdog_actions"] = [
                    {"at": _iso(at, tz), "text": text}
                    for at, text in ep["watchdog_actions"] if at is not None]
            monitor.append(item)
            if minutes >= definitions.MONITOR_BLIND_MIN_MINUTES:
                if reported:
                    reported_any = True
                elif not ep.get("open") and "monitor_blind_unreported" not in reasons:
                    reasons.append("monitor_blind_unreported")
            continue
        if ep.get("pattern"):
            continue  # one evening of a recurring window: counted there
        if not ep.get("material"):
            if float(ep.get("start") or 0) >= since:
                minor.append(ep)
            continue
        state_word = ("closed" if not ep.get("open") else
                      "opened" if float(ep.get("start") or 0) >= since else "ongoing")
        item = {"key": key, "state": state_word,
                "start": _iso(ep.get("start"), tz), "end": _iso(ep.get("end"), tz),
                "minutes": _minutes(ep), "class": cls, "cause": ep.get("cause"),
                "confidence": ep.get("confidence"), "down": bool(ep.get("down")),
                "scope": _scope(ep, service_of),
                "pushed": [p.get("kind") for p in pushed if isinstance(p, dict)],
                "reported": reported}
        if ep.get("loss_pct") is not None:
            item["loss_pct"] = round(float(ep["loss_pct"]), 1)
        link = (links.range_link(float(ep["start"]), float(ep["end"]))
                if ep.get("start") is not None and ep.get("end") is not None else None)
        if link:
            item["link"] = link
        is_muted = bool(ep.get("muted")) or bool(
            muted and key and not reported and muted(key, ep.get("start")))
        if is_muted:
            item["muted"] = True
        material.append(item)
        if reported:
            reported_any = True
        elif (not ep.get("open") and not is_muted
              and "material_unreported" not in reasons):
            reasons.append("material_unreported")

    patterns_out, pattern_reported = _patterns(state, said, since, tz)
    if any(p["change"] in ("appeared", "disappeared") and not p["reported"]
           and not p.get("queued") for p in patterns_out):
        reasons.insert(0, "pattern_change")
    reported_any = reported_any or pattern_reported

    largest = clusters([float(e["start"]) for e in minor])
    if largest >= CLUSTER_MIN:
        reasons.append("non_material_cluster")
    non_material: dict = {"total": len(minor), "largest_cluster": largest,
                          "cluster_bar": CLUSTER_MIN}
    if include_non_material and minor:
        kinds: dict[str, dict] = {}
        for ep in minor:
            kind, words = _non_material_kind(ep)
            row = kinds.setdefault(kind, {"kind": kind, "count": 0, "minutes": 0,
                                          "below_bar": {}, "text": words})
            row["count"] += 1
            row["minutes"] += _minutes(ep)
            for why in ep.get("unmet") or ():
                row["below_bar"][why] = row["below_bar"].get(why, 0) + 1
        non_material["by_kind"] = [
            {**row, "text": f"{row['count']} {row['text']}"}
            for row in sorted(kinds.values(), key=lambda r: -r["count"])]

    heard = _heard(state, since, now, tz)
    pushed_list = []
    for e in sorted((e for e in ledger if float(e.get("decided_at") or 0) >= since
                     and e.get("outcome") in PUSHED_OUTCOMES),
                    key=lambda e: float(e.get("decided_at") or 0)):
        pushed_list.append({"kind": e.get("kind"), "key": e.get("key"),
                            "decided": _iso(e.get("decided_at"), tz),
                            "outcome": e.get("outcome"),
                            "headline": _headline(e)})
    record_from = box.get("since")
    out = {
        "since": _iso(since, tz), "as_of": _iso(now, tz), "tz": tz.key,
        "verdict": verdict(reasons, reported_any),
        "reasons": reasons,
        "material": material[-MAX_MATERIAL:],
        "non_material": non_material,
        "patterns": patterns_out,
        "monitor": {"blind": monitor,
                    "blind_minutes": sum(m["minutes"] for m in monitor),
                    "heard": heard},
        "already_pushed": pushed_list[-MAX_PUSHED:],
        "record_from": _iso(record_from, tz),
    }
    if record_from is None or float(record_from) > since:
        out["note"] = ("The alerter's record begins after `since` (or not at all): "
                       "earlier minutes are unknown, not quiet.")
    return out


def _patterns(state: dict, said: dict, since: float, tz: ZoneInfo) -> tuple[list[dict], bool]:
    """Recurring windows that appeared, came back or disappeared in the
    window; whether any change was already said."""
    box = state.get("patterns") or {}
    pending = {(p.get("id"), p.get("event")) for p in box.get("pending") or ()}
    out, reported_any = [], False
    for p in box.get("items") or ():
        if p.get("state") == "gone" and float(p.get("gone_at") or 0) >= since:
            change, event, kind = "disappeared", "gone", "recovery"
        elif float(p.get("created_at") or 0) >= since:
            change, event, kind = "appeared", "new", "alert"
        elif p.get("state") != "gone" and float(p.get("last_seen") or 0) >= since:
            change, event, kind = "repeated", None, None
        else:
            continue
        try:
            v = _pattern_view(p, since, tz)
        except (KeyError, TypeError, ValueError):
            continue
        item = {"change": change, "hours": v["hours"], "class": v["class"],
                "cause": v["cause"], "days_seen": v["days_seen"],
                "days_observed": v["days_observed"],
                "last_seen": _iso(p.get("last_seen"), tz)}
        if kind:
            item["reported"] = any(e.get("type") == kind for e in
                                   said.get(f"pattern:{p.get('id')}", ()))
            if (p.get("id"), event) in pending:
                # The alerter says one pattern change a day; this one is next.
                item["queued"] = True
            reported_any = reported_any or item["reported"] or bool(item.get("queued"))
        out.append(item)
    return out, reported_any


def _heard(state: dict, since: float, now: float, tz: ZoneInfo) -> dict:
    """Half hours of the window the alerter could judge (common.patterns
    marks each one it measured), of the whole half hours in it."""
    marks = (state.get("patterns") or {}).get("heard") or {}
    if not marks:
        return {"half_hours": None, "of": None}
    step = 30 * 60
    t = since - since % step + (step if since % step else 0)
    heard = total = 0
    while t + step <= now:
        local = datetime.fromtimestamp(t, tz=tz)
        slots = marks.get(local.date().isoformat())
        total += 1
        if slots and slots[(local.hour * 60 + local.minute) // 30]:
            heard += 1
        t += step
    return {"half_hours": heard, "of": total}
