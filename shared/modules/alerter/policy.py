"""What of an episode is said: the push policy (decision of 2026-10-30).

Pushed, and only this:

- **The home's Internet down** -- nearly every destination lost everything
  for TOTAL_MIN_CYCLES heard probe cycles, and not because this Pi could not
  hear: one alert, which sounds.
- **Material degradation** (common.definitions.material: measurable, broad,
  persistent, deep, new): one alert, which sounds by day.
- **The end of either**: one silent message with the real duration, the
  scope and the final cause, which may correct the first.
- **The monitor blind for MONITOR_BLIND_MIN_MINUTES or more** (a deaf
  radio, its database unreadable, its exporter stalled): ONE silent message
  once it can measure again -- from when to when, the cause, what the
  watchdog did, and that what happened on the network meanwhile is unknown.
  Nothing while blind: the dead-man check-in (healthchecks.io) covers that.
- **A class change, or a jump in breadth, of an episode already told**: one
  update of the same episode (at most MAX_UPDATES).

Everything else is recorded (episodes.py keeps every closed episode, with
why it was not material) and never pushed, its end included: loss at one or
two destinations, line microcuts under MICROCUT_MAX_S (the weekly summary
counts them), the watchdog's actions resolved sooner, upgrades, resolver
changes, the consequences of an open episode.

**Quiet hours** (QUIET_FROM-QUIET_UNTIL): nothing sounds. An episode that
became material in the night and is still open when they end is alerted
then, as by day; one that began and ended in the night goes, only if
material, into the one silent message quiet.py sends at the end. A silent
message (an end, the monitor's message) wakes nobody and goes as it is.

Mutes (common.mutes, the MCP server's ``mute_alerts`` / ``ack_incident``)
apply to the signals (episodes.signals) and, by ``episode:<id>``, to a whole
episode. ALERT_MAX_PER_HOUR stays as a blast-radius ceiling on alerts and
updates across every episode: unreachable when the policy works.
"""

from __future__ import annotations

import logging
import os

import clock
import episodes
import quiet
import templates
from common import definitions, diagnosis, mutes

log = logging.getLogger("alerter.policy")

MAX_UPDATES = 2
DEFAULT_MAX_PER_HOUR = 6  # ALERT_MAX_PER_HOUR
_HOUR_S = 3600.0


def _max_per_hour() -> int:
    try:
        return int(os.environ.get("ALERT_MAX_PER_HOUR", "") or DEFAULT_MAX_PER_HOUR)
    except ValueError:
        return DEFAULT_MAX_PER_HOUR


def _over_ceiling(state: dict, now: float) -> bool:
    limit = _max_per_hour()
    book = state.setdefault("pushes", [])
    book[:] = [t for t in book if now - float(t) < _HOUR_S]
    return limit > 0 and len(book) >= limit


def _push_kind(ep: dict) -> str | None:
    """"down", "degradation", or None for an episode that is not pushed."""
    judgment = ep.get("judgment") or {}
    if judgment.get("class") == "monitor":
        return None
    if ep.get("down"):
        return "down"
    if ep.get("material") and not ep.get("microcut"):
        return "degradation"
    return None


def decide(state: dict, events: list[dict], now: float) -> list[dict]:
    """The notifications this cycle's episode events call for, in order.
    Marks each episode with what was decided (``policy``)."""
    out: list[dict] = []
    night = quiet.active(now)
    entries = mutes.load()
    for event in events:
        ep = event["episode"]
        pol = ep.setdefault("policy", {})
        if not pol.get("muted") and mutes.find(
                entries, {"key": ep["key"], "rule": "episode"}, now,
                first_seen=ep.get("opened_at")) is not None:
            pol["muted"] = True
            log.info("Episode %s is muted; recorded, not pushed", ep["key"])
        if event["event"] == "closed":
            out += _closed(state, ep, pol, now)
        else:
            out += _open(state, ep, pol, now, night)
    return out


def _open(state: dict, ep: dict, pol: dict, now: float, night: bool) -> list[dict]:
    kind = _push_kind(ep)
    if kind is None or pol.get("muted"):
        return []
    if night:
        # Nothing sounds in the night. Still open when quiet hours end, it
        # is alerted then; over by then, it joins their one summary.
        pol["night"] = kind
        return []
    judgment = ep["judgment"]
    breadth = (ep.get("facts") or {}).get("breadth_pct", 0.0)
    if not pol.get("sent_kind"):
        if _over_ceiling(state, now):
            log.warning("Episode %s: ALERT_MAX_PER_HOUR reached; not pushed",
                        ep["key"])
            return []
        pol.update({"sent_kind": kind, "sent_class": judgment["class"],
                    "sent_breadth": breadth, "updates": 0})
        return [_payload(state, ep, pol, kind, now)]
    escalated = kind == "down" and pol["sent_kind"] != "down"
    reclassed = judgment["class"] != pol.get("sent_class")
    widened = breadth - float(pol.get("sent_breadth") or 0.0) >= definitions.MATERIAL_BREADTH_PCT
    if not (escalated or reclassed or widened):
        return []
    if int(pol.get("updates", 0)) >= MAX_UPDATES or _over_ceiling(state, now):
        return []
    why = ("now the Internet is down" if escalated else
           f"cause now {judgment['cause']} (was {pol.get('sent_cause') or pol.get('sent_class')})"
           if reclassed else f"now {breadth:.0f}% of the destinations")
    pol.update({"sent_kind": kind, "sent_class": judgment["class"],
                "sent_breadth": breadth, "updates": int(pol.get("updates", 0)) + 1})
    return [_payload(state, ep, pol, "update", now, why=why, sound=escalated)]


def _closed(state: dict, ep: dict, pol: dict, now: float) -> list[dict]:
    record = episodes.summary(ep)
    if pol.get("sent_kind"):
        return [_payload(state, ep, pol, "recovery", now, record=record)]
    if pol.get("muted"):
        return []
    # Blind long enough, and nothing else about it said: what the network
    # did in those minutes is unknown, and that is the news.
    if float(ep.get("blind_s") or 0) >= definitions.MONITOR_BLIND_MIN_MINUTES * 60:
        return [_payload(state, ep, pol, "monitor", now, record=record)]
    if pol.get("night"):
        # Material, and over before quiet hours ended: one line of their
        # summary, silent.
        return [_payload(state, ep, pol, "night", now, record=record)]
    return []


def recorded(ep: dict) -> dict:
    """The ledger's line for an episode that closed with nothing pushed
    (notifications.py, outcome ``recorded``): its cause, its range, and the
    conditions it missed, or why it was not said."""
    record = episodes.summary(ep)
    pol = ep.get("policy") or {}
    why = ("acknowledged" if pol.get("muted") else
           "a line microcut under a minute" if record["microcut"] else
           "the monitor's own, blind under 15 min" if record["class"] == "monitor" else
           "not material: " + ", ".join(record["unmet"] or ["?"]))
    lines = [f"📝 Recorded, not sent: {why}",
             f"Cause: {record['cause']}",
             f"Range: {_range(record, record['end'], False)}"]
    return {"type": "episode", "rule": "episode", "kind": "recorded",
            "key": ep["key"], "target": None, "targets": record["sites"],
            "severity": "info", "silent": True, "episode": record,
            "message": "\n".join(lines)}


_HEAD = {
    "down": "🔴 Internet down",
    "degradation": "🟡 Internet degraded",
    "update": "🔁 Update",
    "recovery": "✅ Over",
    "night": "🌙 In the night",
    "monitor": "🛠 Could not measure",
}


def _payload(state: dict, ep: dict, pol: dict, kind: str, now: float,
             why: str | None = None, sound: bool = False,
             record: dict | None = None) -> dict:
    """One notification about ``ep``. Minimal and structured on purpose --
    head, cause and confidence, range, scope, signals -- for the
    formatting that follows to dress."""
    judgment = ep.get("judgment") or {}
    record = record or episodes.summary(ep)
    lines = [f"{_HEAD[kind]}: {why}" if kind == "update" and why else _HEAD[kind]]
    lines += _body(kind, ep, pol, record, now)
    final = kind in ("recovery", "monitor", "night")
    count = 1 if kind in ("down", "degradation") else int(pol.get("updates", 0)) + 1
    payload = {
        "type": "recovery" if final else "alert",
        "rule": "episode",
        "kind": kind,
        "key": ep["key"],
        "target": None,
        "severity": ("critical" if kind == "down" or (kind == "update" and sound)
                     else "info" if final else "warning"),
        # Only an alert sounds, and an update only when it escalates to the
        # Internet being down; nothing sounds in quiet hours (quiet.py holds
        # what is sent then).
        "silent": not (kind in ("down", "degradation") or sound),
        "message": "\n".join(lines),
        "episode": record,
        # For the idempotent delivery (ledger.key_of): which episode, and
        # which of its messages.
        "state": {"first_seen": ep.get("opened_at"), "notified_count": count},
    }
    if kind in ("down", "degradation", "update"):
        state.setdefault("pushes", []).append(now)
    pol.setdefault("sent", []).append({"kind": kind, "at": now})
    if kind in ("down", "degradation"):
        pol["sent_cause"] = judgment.get("cause")
    log.info("Episode %s: %s", ep["key"], kind)
    return payload


def _range(record: dict, now: float, open_: bool) -> str:
    start = clock.at(record["start"], now)
    if open_:
        return f"since {start}"
    return (f"{start} to {clock.at(record['end'], now)} "
            f"({templates._duration(record['duration_s']) or 'under a minute'})")


def _body(kind: str, ep: dict, pol: dict, record: dict, now: float) -> list[str]:
    judgment = ep.get("judgment") or {}
    lines = []
    if kind == "monitor":
        lo, hi = episodes.blind_range(ep) or (record["start"], record["end"])
        lines.append(f"Range: {clock.at(lo, now)} to {clock.at(hi, now)} "
                     f"({templates._duration(hi - lo) or 'under a minute'})")
        lines.append("Cause: " + (judgment.get("cause")
                                  if judgment.get("class") == "monitor"
                                  else diagnosis.monitor_cause(ep.get("facts") or {})))
        actions = [w.get("message") for w in ep.get("watchdog") or []
                   if w.get("message")]
        lines.append("Watchdog: " + ("; ".join(actions[:3]) if actions
                                      else "nothing (not enabled, or no action)"))
        lines.append("What happened on the network meanwhile is unknown.")
        return lines
    open_ = kind in ("down", "degradation", "update")
    cause = judgment.get("cause") or diagnosis.UNDETERMINED_CAUSE
    confidence = judgment.get("confidence")
    corrected = (kind == "recovery" and pol.get("sent_cause")
                 and pol["sent_cause"] != cause)
    lines.append(f"Cause: {cause}"
                 + (f" ({confidence} confidence)" if confidence and
                    judgment.get("class") != "undetermined" else "")
                 + (f" — corrects the first message ({pol['sent_cause']})"
                    if corrected else ""))
    lines.append(f"Range: {_range(record, now, open_)}")
    scope = f"Scope: {record['breadth_pct']:.0f}% of the destinations"
    if record.get("categories"):
        scope += f", {record['categories']} categories"
    if record.get("loss_pct") is not None:
        scope += f", mean loss {record['loss_pct']:.1f}%"
    lines.append(scope)
    if judgment.get("evidence"):
        lines.append("Signals: " + "; ".join(judgment["evidence"][:3]))
    return lines
