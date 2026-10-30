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

**A recurring window** (common.patterns: the same hours, the same class,
on RECURRING_MIN_DAYS of the last seven days): ONE silent message when it
appears, ONE when it is gone, at most one such message a day
(:func:`patterns_said`). In between its episodes are not news -- recorded
as part of the pattern, their end included -- and the weekly summary
counts its days.

Mutes (common.mutes, the MCP server's ``mute_alerts`` / ``ack_incident``)
apply to the signals (episodes.signals) and, by ``episode:<id>``, to a whole
episode. ALERT_MAX_PER_HOUR stays as a blast-radius ceiling on alerts and
updates across every episode: unreachable when the policy works.
"""

from __future__ import annotations

import logging
import os

from datetime import datetime

import clock
import episodes
import flux
import names
import notifier
import quiet
import state as state_mod
import templates
from common import definitions, links, mutes, patterns

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
    why = {"escalated": escalated, "reclassed": reclassed,
           "words": pol.get("sent_words"), "was_down": pol["sent_kind"] == "down"}
    pol.update({"sent_kind": kind, "sent_class": judgment["class"],
                "sent_breadth": breadth, "updates": int(pol.get("updates", 0)) + 1})
    return [_payload(state, ep, pol, "update", now, why=why, sound=escalated)]


def _closed(state: dict, ep: dict, pol: dict, now: float) -> list[dict]:
    record = episodes.summary(ep)
    # Acknowledged (ack_incident): nothing more, its end included.
    if pol.get("muted"):
        return []
    if pol.get("sent_kind"):
        return [_payload(state, ep, pol, "recovery", now, record=record)]
    if pol.get("night"):
        # Material, and over before quiet hours ended: one line of their
        # summary, silent -- a partly blind one included (review of #436).
        return [_payload(state, ep, pol, "night", now, record=record)]
    # Blind long enough, and nothing else about it said: what the network
    # did in those minutes is unknown, and that is the news.
    if float(ep.get("blind_s") or 0) >= definitions.MONITOR_BLIND_MIN_MINUTES * 60:
        return [_payload(state, ep, pol, "monitor", now, record=record)]
    return []


def recorded(ep: dict) -> dict:
    """The ledger's line for an episode that closed with nothing pushed
    (notifications.py, outcome ``recorded``): its cause, its range, and the
    conditions it missed, or why it was not said."""
    record = episodes.summary(ep)
    pol = ep.get("policy") or {}
    why = ("acknowledged" if pol.get("muted") else
           "part of a recurring loss window, already told" if record.get("pattern") else
           "under a minute" if record["microcut"] else
           "this Pi could not measure, under 15 min" if record["class"] == "monitor" else
           "not material: " + ", ".join(record["unmet"] or ["?"]))
    render = _context(ep, record, pol, float(record["end"]))
    lines = [f"📝 Recorded, not sent ({why}): {templates.what(record, render)}, "
             f"{templates.span(record['start'], record['end'], record['end'], ' to ')}",
             f"Cause: {templates.cause_words(record, render)}"]
    return {"type": "episode", "rule": "episode", "kind": "recorded",
            "key": ep["key"], "target": None, "targets": record["sites"],
            "severity": "info", "silent": True, "episode": record,
            "message": "\n".join(lines)}


def _context(ep: dict, record: dict, pol: dict, now: float, **extra) -> dict:
    """What the renderers (templates.py) need beyond the record: the
    clock, the cycle, each destination's service name, and what was told."""
    m = record.get("measures") or {}
    category_of = ep.get("category_of") or {}
    targets = list(record.get("sites") or []) + [w[0] for w in m.get("worst") or []]
    known = names.labels(now) if targets else {}
    blind_by = record.get("blind_by") or {}
    return {
        "now": now,
        "step": record.get("step") or ep.get("step"),
        "names": {t: names.display(t, category_of.get(t), known) for t in targets},
        "told_kind": pol.get("sent_kind"),
        "told_class": pol.get("sent_class"),
        "told_words": pol.get("sent_words"),
        # Updates actually sent (the counter also caps them).
        "updates": sum(1 for s in pol.get("sent") or () if s.get("kind") == "update"),
        # Only with an assistant to reply to (OpenClaw) is "ack" useful.
        "assistant": notifier.notify_mode() == "openclaw",
        "blind_reason": max(blind_by, key=blind_by.get) if blind_by else None,
        **extra,
    }


def _payload(state: dict, ep: dict, pol: dict, kind: str, now: float,
             why: dict | None = None, sound: bool = False,
             record: dict | None = None) -> dict:
    """One notification about ``ep``: the episode's record and what the
    renderers need (``render``), its text rendered by templates.py --
    ``message`` is the plain one the ledger and summaries read."""
    judgment = ep.get("judgment") or {}
    record = record or episodes.summary(ep)
    final = kind in ("recovery", "monitor", "night")
    extra = {}
    if kind == "update" and why:
        extra = {"was_down": why.get("was_down")}
    render = _context(ep, record, pol, now, **extra)
    if kind == "update":
        render["why"] = templates.update_why(record, render, why or {})
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
        "episode": record,
        "render": render,
        # For the idempotent delivery (ledger.key_of): which episode, and
        # which of its messages.
        "state": {"first_seen": ep.get("opened_at"), "notified_count": count},
    }
    payload["message"] = templates.plain(payload)
    if kind in ("down", "degradation", "update"):
        state.setdefault("pushes", []).append(now)
    pol.setdefault("sent", []).append({"kind": kind, "at": now})
    if kind in ("down", "degradation", "update"):
        # The cause last told: an update corrects it, so the end compares
        # with that, not with the first message (review of #436).
        pol["sent_cause"] = judgment.get("cause")
        pol["sent_words"] = (templates.cause_words(record, render)
                             if record.get("class") != "undetermined" else
                             "the cause was undetermined")
    log.info("Episode %s: %s", ep["key"], kind)
    return payload


# ---------------------------------------------------------------------------
# Recurring windows
# ---------------------------------------------------------------------------

# How far back the inference's shared periods are read for a new pattern.
_INFERENCE_LOOKBACK_S = (patterns.WINDOW_DAYS + 1) * 86400


def _inference_days(pattern: dict, query, tz) -> int | None:
    """Days of the pattern on which the inference (profile ``inference``)
    also found shared degradation in its hours: supporting evidence, None
    when it could not be read. Off, the query is simply empty."""
    src = (f"from(bucket: {flux.flux_str(flux.influx_bucket())}) "
           f"|> range(start: -{_INFERENCE_LOOKBACK_S}s) "
           '|> filter(fn: (r) => r._measurement == "shared_degradation" and '
           '(r._field == "end" or r._field == "class")) '
           '|> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value") '
           '|> group() |> sort(columns: ["_time"])')
    try:
        rows = query(src)
    except Exception:  # influx client raises many exception types
        log.warning("Inference periods could not be read; the pattern stands "
                    "on the episodes", exc_info=True)
        return None
    days = set()
    for row in rows or ():
        lo, hi = episodes._epoch(row.get("_time")), episodes._epoch(row.get("end"))
        if lo is None or hi is None or row.get("class") == "monitor_deaf":
            continue
        day = patterns.match_day(pattern, lo, hi, tz)
        if day is not None:
            days.add(day)
    return len(days)


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _cause_line(pattern: dict) -> str:
    cls = pattern["class"]
    what = {"upstream": "your provider", "local_link":
            "your line (the modem or the line to the ISP)",
            "destination": "the destinations themselves"}[cls]
    if cls == "upstream" and patterns.peak_hour(pattern):
        what += " at peak hour"
    confidence = pattern.get("confidence")
    return f"Looks like {what}" + (f" ({confidence} confidence)." if confidence else ".")


def _loss(pattern: dict) -> str:
    lo, hi = pattern.get("loss_min"), pattern.get("loss_max")
    if lo is None:
        return "loss"
    if round(lo) == round(hi):
        return f"about {lo:.0f} % loss"
    return f"{lo:.0f}–{hi:.0f} % loss"


def pattern_message(pattern: dict, event: str, now: float) -> str:
    """The words of a pattern's one message: when it appears, or when it
    is gone. Informative: what it is, how sure, the evidence."""
    part = pattern["part"]
    hours = f"from {pattern['from']} to {pattern['to']}"
    if event == "gone":
        last = pattern.get("last_seen")
        when = (datetime.fromtimestamp(float(last), tz=clock.zone()).strftime("%A")
                if last else "never")
        return "\n".join([
            f"✅ The {part} loss window did not appear in "
            f"{patterns.absent_days(pattern)} days; last seen {when}.",
            f"{pattern['from']} to {pattern['to']}, seen on {pattern['days_seen']} "
            f"of {pattern['days_observed']} {part}s while it lasted."])
    today = datetime.fromtimestamp(now, tz=clock.zone()).date()
    seen, heard = patterns.recent_counts(pattern, today)
    run = patterns.streak(pattern)
    affected, reporting = pattern.get("affected") or 0, pattern.get("reporting") or 0
    many = ("most destinations" if reporting and 2 * affected > reporting
            else "many destinations")
    head = (f"🔁 For the {_ordinal(run)} {part} in a row, " if run >= seen
            else f"🔁 On {seen} of the last {heard} {part}s, ")
    lines = [head + f"packet loss on {many} {hours}.",
             _cause_line(pattern),
             f"{seen} of {heard} {part}s, {affected} of {reporting} destinations, "
             f"{_loss(pattern)}."]
    if pattern.get("inference_days"):
        lines.append(f"The inference found it too on {pattern['inference_days']} of them.")
    if pattern["class"] in ("upstream", "local_link"):
        lines.append("With this you can complain to your provider"
                     + (" → the last one is the graph link." if _last_link(pattern)
                        else "."))
    return "\n".join(lines)


def _last_link(pattern: dict) -> str | None:
    """The Connection quality dashboard over the last occurrence."""
    last = pattern.get("last")
    if not last or not links.links_configured():
        return None
    pad = 1800
    return links.grafana_url("connectivity-quality-v1", extra={
        "from": str(int((float(last["start"]) - pad) * 1000)),
        "to": str(int((float(last["end"]) + pad) * 1000))})


def _pattern_payload(pattern: dict, event: str, now: float) -> dict:
    gone = event == "gone"
    link = None if gone else _last_link(pattern)
    payload = {
        # An alert and its recovery to the idempotent delivery
        # (ledger.key_of): one of each per pattern, ever.
        "type": "recovery" if gone else "alert",
        "rule": "pattern",
        "kind": f"pattern_{event}",
        "key": f"pattern:{pattern['id']}",
        "target": None,
        "severity": "info",
        "silent": True,
        "message": pattern_message(pattern, event, now),
        "pattern": patterns.view(pattern, now, clock.zone()),
        "state": {"first_seen": pattern.get("created_at"), "notified_count": 1},
    }
    if link:
        payload["links"] = {"graph": link}
    log.info("Pattern %s: %s", pattern["id"], event)
    return payload


def patterns_said(state: dict, now: float, step: int, query=None) -> list[dict]:
    """Fold the episodes into the recurring windows (common.patterns) and
    return what of them is said: at most one silent message a day, the
    pattern's appearance or its end. Re-judges the open episode when a
    pattern now explains it, so it is recorded as part of the pattern
    rather than pushed."""
    tz = clock.zone()
    box = patterns.book(state)
    record = episodes.book(state)
    eps = list(record["closed"])
    ep = record["open"]
    if ep is not None and ep.get("judgment"):
        eps.append({**episodes.summary(ep), "open": True})
    margin = max(state_mod.resolve_after(), 3 * step)
    changes = patterns.update(box, eps, now, tz, margin=margin)
    pending = box.setdefault("pending", [])
    for change in changes:
        pattern = change["pattern"]
        if change["event"] == "new" and query is not None:
            pattern["inference_days"] = _inference_days(pattern, query, tz)
        item = {"event": change["event"], "id": pattern["id"],
                "created_at": pattern.get("created_at")}
        if change["event"] == "gone" and {**item, "event": "new"} in pending:
            # Never announced: nothing to take back.
            pending.remove({**item, "event": "new"})
            continue
        pending.append(item)
    if changes and ep is not None:
        episodes.rejudge(state, now, step)
    today = datetime.fromtimestamp(now, tz=tz).date().isoformat()
    if not pending or box.get("said_day") == today:
        return []
    item = pending.pop(0)
    # By id and birth: a re-formed pattern of the same id is another one.
    pattern = next((p for p in box["items"] if p["id"] == item["id"]
                    and p.get("created_at") == item.get("created_at")), None)
    if pattern is None:
        return []
    key = f"pattern:{pattern['id']}"
    if mutes.find(mutes.load(), {"key": key, "rule": "pattern"}, now,
                  first_seen=pattern.get("created_at")) is not None:
        log.info("Pattern %s is muted; its %s not said", pattern["id"], item["event"])
        return []
    box["said_day"] = today
    return [_pattern_payload(pattern, item["event"], now)]
