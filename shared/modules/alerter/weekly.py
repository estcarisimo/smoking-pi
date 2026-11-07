"""The weekly summary: what the week added up to, sent only when it is news.

It replaces the daily digest (front 8, decision of 2026-10-30). In October
on the reference Pi that digest arrived 16 times in 16 days: 10 said "all
clear" with only the Wi-Fi's dBm changed, 5 repeated what alerts had
already pushed, and 1 carried a new fact. A fixed-state report every
morning trains its reader to skip it, and the dead man's switch
(dead_man.py) already proves every five minutes that the monitor is alive,
which was the digest's other job.

Once a week (``WEEKLY_DAY`` at ``WEEKLY_AT`` in ``DIGEST_TZ``, Monday 08:00
by default) it reads the seven calendar days before that day's 00:00, and
the seven before those, and says in order:

1. one line: a good, mixed or bad week against the one before, by cause --
   the Pi's Wi-Fi, your line, your provider, individual destinations --
   then the week's material episodes, at most three;
2. the recurring windows: the alerter's patterns (common.patterns, the
   ones it announced when they appeared and when they were gone), each with
   its days this week and since it began ("17 evenings of 21"); for a week
   its record does not cover, hours of the day in which loss or slowdowns
   the destinations shared came back on several days;
3. the monitor's own availability, the share of the week it could measure,
   and what the watchdog did;
4. at most three destinations with loss of their own above the bar;
5. one Grafana link to exactly that week.

**It is not sent when nothing happened**: no material episode, no
recurring window this week or the week before, and the monitor measured at
least ``MATERIAL_AVAILABILITY_PCT`` of the week. Then it logs one line and
records the skipped slot in state. What "material" means is one gate,
``common.definitions.material()`` / ``materiality()``, the one the
alerter's push policy uses (policy.py): one place for every reader.

**The episodes are the alerter's** (episodes.py, ``state["episodes"]``):
the same ones it pushed or recorded, already judged, the monitor's blind
spans already subtracted. For a week its record does not cover whole (the
first week after an upgrade, a state file lost), the incident history
(``loss_incident``) stands in, judged by the same gate.

**It never claims a quiet week it did not verify.** When the incident
history, the availability, the categories or the inference's periods
cannot be read, nothing is sent -- neither a summary nor a "skipped" -- and
the read is tried again RETRY_S later, MAX_ATTEMPTS times, before the slot
is retired with ``last_error``.

**The week itself is common.weekly's**: reading it, judging it, comparing
it by cause. The MCP server's ``get_weekly_summary`` returns the same week
as data (front 10), so this module keeps only the schedule, the message and
its delivery; the message is pinned byte for byte
(tests/test_weekly_golden.py).

Scheduling is ``schedule.py``'s: one slot a week, idempotent on the
persisted slot, at most ``MAX_ATTEMPTS`` tries ``RETRY_S`` apart, a slot
more than a day late retired without sending.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import clock
import flux
import notifications
import notifier
import schedule
import templates
# aggregates: the destinations' reader, which the tests replace in place.
from common import aggregates, definitions, links  # noqa: F401
from common import weekly as shared

log = logging.getLogger("alerter.weekly")

DEFAULT_DAY = "mon"  # WEEKLY_DAY
DEFAULT_AT = "08:00"  # WEEKLY_AT
# A week's news is still news on Monday evening: a Pi that was off in the
# morning, or an InfluxDB that answered only after a restart, sends it late
# rather than not at all. Past a day the slot is retired.
DEFAULT_MAX_LATENESS_S = 24 * 3600
# Tries per slot (reading the week, or delivering it), RETRY_S apart: an
# InfluxDB restarting at 08:00 is not a lost week. The notifier retries 3x
# itself on each.
MAX_ATTEMPTS = 3
RETRY_S = 1800
# The week's reading and judging are common.weekly's: the MCP server's
# get_weekly_summary returns the same week as data. Re-exported here so the
# alerter's call sites and tests read as they did.
WEEK_DAYS = shared.WEEK_DAYS
MAX_EPISODES = shared.MAX_EPISODES
MAX_DESTINATIONS = shared.MAX_DESTINATIONS
MAX_LOSS_QUERIES = shared.MAX_LOSS_QUERIES
STEP_S = shared.STEP_S
WATCHDOG_BEFORE_S = shared.WATCHDOG_BEFORE_S
WATCHDOG_AFTER_S = shared.WATCHDOG_AFTER_S
UPGRADE_MAX_S = shared.UPGRADE_MAX_S
CAUSE_OF_CLASS = shared.CAUSE_OF_CLASS
CAUSE_TEXT = shared.CAUSE_TEXT
CAUSE_ORDER = shared.CAUSE_ORDER
CAUSE_OF_EPISODE = shared.CAUSE_OF_EPISODE
MONITOR_CAUSE_OF_DETAIL = shared.MONITOR_CAUSE_OF_DETAIL

week_bounds = shared.week_bounds
_rfc3339 = shared._rfc3339
merge = shared.merge
overlap = shared.overlap
upgrade_spans = shared.upgrade_spans
watchdog_action = shared.watchdog_action
recurring = shared.recurring
pattern_windows = shared.pattern_windows
material = shared.material
compare = shared.compare
_overlaps = shared._overlaps
_week_label = shared._week_label
_minutes_by_cause = shared._minutes_by_cause

_legacy_logged = False


# ---------------------------------------------------------------------------
# Settings, and the daily digest's mapped onto them
# ---------------------------------------------------------------------------


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _bool(raw: str) -> bool:
    return raw.lower() in ("1", "true", "yes", "on")


def _legacy() -> bool:
    """True when only the retired digest's settings say anything: an
    install upgraded with DIGEST_ENABLED=true and no WEEKLY_ENABLED."""
    return not _env("WEEKLY_ENABLED") and _bool(_env("DIGEST_ENABLED"))


def settings() -> dict:
    """``enabled``, ``day`` (0 = Monday), ``at`` (HH:MM) and ``tz``.

    An upgrade keeps working: DIGEST_ENABLED=true turns the weekly on, and
    DIGEST_AT gives its time when WEEKLY_AT is unset, with one log line
    saying so (docs/upgrades.md). DIGEST_TZ stays the zone of every message.
    """
    global _legacy_logged
    legacy = _legacy()
    enabled = _bool(_env("WEEKLY_ENABLED")) if _env("WEEKLY_ENABLED") else legacy
    day_raw = _env("WEEKLY_DAY") or DEFAULT_DAY
    at = _env("WEEKLY_AT") or (_env("DIGEST_AT") if legacy else "") or DEFAULT_AT
    tz = _env("DIGEST_TZ") or _env("TZ")
    if legacy and not _legacy_logged:
        _legacy_logged = True
        log.info(
            "DIGEST_ENABLED=true (the retired daily digest) is read as the weekly "
            "summary: %s at %s %s. Set it with: smoking-pi alerts --weekly DAY HH:MM",
            day_raw, at, tz or "UTC")
    return {"enabled": enabled, "day": schedule.parse_weekday(day_raw),
            "day_raw": day_raw, "at": at, "tz": tz}


def zone() -> ZoneInfo:
    """The messages' zone, clock.py's: DIGEST_TZ, then TZ, then UTC."""
    return clock.zone()


# ---------------------------------------------------------------------------
# The week (common.weekly)
# ---------------------------------------------------------------------------


def read_week(start: float, stop: float, query=None, record: dict | None = None) -> dict:
    """common.weekly.read_week in the messages' zone."""
    return shared.read_week(start, stop, query or flux.query_influx, record, zone())


def destinations(start: float, stop: float, excluded: list[tuple[float, float]],
                 query=None) -> list[dict] | None:
    """common.weekly.destinations: at most three, worst first; None unread."""
    return shared.destinations(start, stop, excluded, query or flux.query_influx)


def _pattern_line(w: dict, stop: float) -> str:
    p = w["pattern"]
    part = p.get("part") or "day"
    reporting = int(p.get("reporting") or 0)
    many = ("most destinations" if reporting and 2 * int(p.get("affected") or 0) > reporting
            else "many destinations")
    first = datetime.fromtimestamp(float(p.get("first_seen") or stop), tz=zone())
    since = ("new this week" if w["new"] else
             f"{p.get('days_seen', 0)} {part}s of {p.get('days_observed', 0)} since "
             f"{first.day} {first:%b}")
    return (f"{p['from']}–{p['to']}: packet loss on {many}, {w['days']} {part}s "
            f"of {w['observed']} ({p.get('cause') or 'unclear'}; {since}).")


def _gone_line(w: dict, stop: float) -> str:
    p = w["pattern"]
    last = clock.at(float(p.get("last_seen") or 0), now=stop, weekday=True)
    return (f"{p['from']}–{p['to']}, the {p.get('part') or 'day'} loss window: gone "
            f"({p.get('days_seen', 0)} of {p.get('days_observed', 0)} while it "
            f"lasted; last seen {last}).")


# ---------------------------------------------------------------------------
# The message
# ---------------------------------------------------------------------------


def _duration(minutes: float) -> str:
    minutes = int(round(minutes))
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def _times(count: int) -> str:
    return {1: "once", 2: "twice"}.get(count, f"{count} times")




def _cause_part(cause: str, now: int, prev: int, trend: str) -> str:
    what = CAUSE_TEXT[cause]
    unit = " deaf" if cause in ("wifi", "dns") else ""
    if not now:
        return f"{what} better (none; the week before: {_duration(prev)}{unit})"
    then = f"{_duration(prev)}{unit}" if prev else "none"
    word = {"worse": "worse", "better": "better", "same": "as before"}[trend]
    return f"{what} {word} ({_duration(now)}{unit}; the week before: {then})"


def render(week: dict, before: dict, windows: list[dict], before_windows: list[dict],
           tops: list[dict] | None, tz: ZoneInfo,
           gone: list[dict] | None = None) -> str:
    esc, b = templates.esc, templates._b
    ok, watch, bad = (templates.STATUS_EMOJI["ok"], templates.STATUS_EMOJI["watch"],
                      templates.STATUS_EMOJI["bad"])
    verdict, rows = compare(week, before)
    label = _week_label(week["start"], week["stop"], tz)
    light = {"good": ok, "mixed": watch, "bad": bad}[verdict]
    parts = [_cause_part(*row) for row in sorted(rows, key=lambda r: r[3] != "worse")]
    if parts:
        headline = f"{light} {verdict.capitalize()} week, {label}: " + "; ".join(parts) + "."
    else:
        headline = (f"{light} {verdict.capitalize()} week, {label}: no episode worth "
                    f"telling, as the week before.")
    lines = [esc(headline)]

    episodes = week["episodes"]
    if episodes:
        lines += ["", b("What happened")]
        for ep in sorted(episodes, key=lambda e: e["minutes"], reverse=True)[:MAX_EPISODES]:
            sign = bad if ep["minutes"] >= 60 else watch
            if ep["kind"] == "slow":
                detail = (f"latency {ep['median_ms']:.0f} ms, "
                          f"{ep['median_ms'] / ep['before_ms']:.1f}× the same hours "
                          f"a week before")
            else:
                detail = (f"{ep['affected']} of {ep['reporting']} destinations, "
                          f"{ep['loss_pct']:.0f} % loss")
            # Every time a message prints is clock.py's; the week is over,
            # so its times always carry the weekday and the date.
            when = clock.at(ep["start"], now=week["stop"], weekday=True)
            lines.append(esc(f"{sign} {when}, "
                             f"{_duration(ep['minutes'])}: "
                             f"{CAUSE_TEXT[ep['cause']]} — {detail}."))
        if len(episodes) > MAX_EPISODES:
            lines.append(esc(f"{watch} …and {len(episodes) - MAX_EPISODES} more."))
    if week["minor"]:
        lines.append(esc(f"{ok} {week['minor']} smaller episode"
                         f"{'' if week['minor'] == 1 else 's'} below the bar, "
                         f"in the record."))

    if gone is None:
        gone = [w for w in before_windows if not any(_overlaps(w, x) for x in windows)]
    if windows or gone:
        lines += ["", b("Recurring")]
        ended = {id(w["pattern"]) for w in gone if w.get("pattern")}
        for w in windows:
            if w.get("pattern"):
                # Gone this week: its one line is the gone one, below.
                if id(w["pattern"]) not in ended:
                    lines.append(esc(f"{watch} {_pattern_line(w, week['stop'])}"))
                continue
            seen = next((x for x in before_windows if _overlaps(w, x)), None)
            since = (f"also the week before ({seen['days']} days)" if seen
                     else "new: not the week before")
            lines.append(esc(f"{watch} {w['from']:02d}:00–{w['to'] % 24:02d}:00: loss "
                             f"or slowdowns on {w['days']} of 7 days ({since})."))
        for w in gone:
            if w.get("pattern"):
                lines.append(esc(f"{ok} {_gone_line(w, week['stop'])}"))
                continue
            lines.append(esc(f"{ok} {w['from']:02d}:00–{w['to'] % 24:02d}:00, "
                             f"{w['days']} days the week before, did not come back."))

    lines += ["", b("The monitor")]
    pct = week["measured_pct"]
    gaps = [f"{_duration(m)} {CAUSE_TEXT[c]} heard nothing"
            for c, m in sorted(week["deaf_minutes"].items()) if m]
    if week["no_data_minutes"]:
        gaps.append(f"{_duration(week['no_data_minutes'])} with no measurement at all")
    sign = ok if pct >= definitions.MATERIAL_AVAILABILITY_PCT else watch
    text = f"{sign} It measured {pct:.1f} % of the week"
    lines.append(esc(text + (": " + ", ".join(gaps) if gaps and pct < 100 else "") + "."))
    acts = week["watchdog"]
    done = [f"{verb} {_times(acts[kind])}" for kind, verb in (
        ("reload", "reloaded the Wi-Fi driver"), ("reboot", "rebooted the Pi"),
        ("restart", "restarted SmokePing")) if acts.get(kind)]
    if done:
        lines.append(esc(f"{watch} The watchdog " + ", ".join(done) + "."))
    else:
        lines.append(esc(f"{ok} The watchdog had nothing to do."))
    if week["upgrades"]:
        lines.append(esc(f"{ok} {week['upgrades']} upgrade"
                         f"{'' if week['upgrades'] == 1 else 's'}, left out of the above."))

    if tops is None:
        lines += ["", b("Destinations"),
                  esc(f"{watch} Their loss could not be read this week.")]
    elif tops:
        lines += ["", b("Destinations")]
        for t in tops:
            lines.append(esc(f"{watch} {t['label']}: {t['loss_pct']:.1f} % loss of its own."))
    return "\n".join(lines)


def build(slot: float, query=None, record: dict | None = None,
          known: dict | None = None) -> dict:
    """The week of ``slot`` read and judged: ``{"send": bool, "message":
    ..., ...}``. Raises when the week cannot be read (see read_week).
    ``record``: the alerter's episodes (``state["episodes"]``); ``known``:
    its recurring windows (``state["patterns"]``)."""
    tz = zone()
    prev, start, stop = week_bounds(slot, tz)
    r = shared.assemble(start, stop, prev, query or flux.query_influx, record,
                        known, tz)
    week = r["week"]
    out = {"type": "weekly", "week_start": start, "week_stop": stop,
           "measured_pct": week["measured_pct"],
           "episodes": len(week["episodes"]), "windows": len(r["windows"])}
    if not r["material"]:
        out["send"] = False
        out["label"] = r["label"]
        return out
    # The Connection quality dashboard: the one that shows a week's patterns.
    link = links.week_link(start, stop)
    out.update({
        "send": True,
        "message": render(week, r["before"], r["windows"], r["before_windows"],
                          destinations(start, stop, week["excluded"], query), tz,
                          r["gone"]),
        "links": {"grafana_week": link} if link else {},
    })
    return out


# ---------------------------------------------------------------------------
# The slot
# ---------------------------------------------------------------------------


def handed_to_agent() -> bool:
    """With OpenClaw delivery, OPENCLAW_PUSH=agent and OPENCLAW_WEEKLY=agent,
    the agent's own automation owns the weekly (docs/openclaw-integration.md,
    "Heartbeat and weekly through the agent"); otherwise the alerter sends it.
    OPENCLAW_WEEKLY is written by `smoking-pi openclaw automations` when the
    automation exists: the push alone would lose the weekly of an install
    whose gateway never got one."""
    return (notifier.notify_mode() == "openclaw"
            and notifier.push_mode() == notifier.PUSH_AGENT
            and (os.environ.get("OPENCLAW_WEEKLY") or "").strip().lower() == "agent")


def check(state: dict, now: float | None = None, query=None) -> bool:
    """Send the weekly if its slot is due and the week is news. True when
    one was sent. Never raises: it runs inside the alert loop's iteration."""
    conf = settings()
    if not conf["enabled"]:
        return False
    now = time.time() if now is None else now
    record = state.setdefault("weekly", {})
    if conf["day"] is None:
        slot, reason = None, "disabled"
    else:
        slot, reason = schedule.due_slot(
            record.get("last_fired_slot"), now, conf["at"], conf["tz"],
            DEFAULT_MAX_LATENESS_S, weekday=conf["day"])
    if reason == "disabled":
        if not record.get("warned_disabled"):
            record["warned_disabled"] = True
            log.warning("WEEKLY_DAY=%r / WEEKLY_AT=%r is not a day and an HH:MM; "
                        "the weekly summary is off until it is fixed (alerts are "
                        "unaffected)", conf["day_raw"], conf["at"])
        return False
    record.pop("warned_disabled", None)
    if reason == "not_due":
        return False
    if reason == "skipped_stale":
        log.info("Weekly slot %s is past the lateness budget; retired without sending",
                 slot)
        record.update(last_fired_slot=slot, attempts=0)
        return False
    if handed_to_agent():
        # `smoking-pi openclaw` keeps an OpenClaw automation, "Smoking Pi
        # weekly", that reads this week through get_weekly_summary and
        # narrates it. Both sending would say the week twice; the week is
        # not even read here, which spares InfluxDB the week's queries.
        log.info("Weekly for slot %s handed to the agent automation "
                 "(OPENCLAW_PUSH=agent, OPENCLAW_WEEKLY=agent): not sent by the "
                 "alerter", slot)
        record.update(last_fired_slot=slot, attempts=0, handed_to_agent_slot=slot)
        record.pop("last_error", None)
        record.pop("next_try", None)
        return False

    if record.get("attempts_slot") != slot:
        record.update(attempts=0, attempts_slot=slot)
        record.pop("next_try", None)
    attempts = int(record.get("attempts", 0) or 0)
    if attempts >= MAX_ATTEMPTS:
        log.warning("Weekly for slot %s failed %d times (%s); waiting for the next "
                    "week", slot, attempts, record.get("last_error"))
        record.update(last_fired_slot=slot, attempts=0)
        record.pop("next_try", None)
        return False
    if now < float(record.get("next_try") or 0):
        return False

    def failed(why: str) -> bool:
        # Not retired: the next try is RETRY_S away, within the lateness
        # budget, and the slot is retired after MAX_ATTEMPTS of them.
        record.update(attempts=attempts + 1, attempts_slot=slot,
                      last_error=why, next_try=now + RETRY_S)
        return False

    try:
        payload = build(slot, query, state.get("episodes"), state.get("patterns"))
    except Exception:  # noqa: BLE001 - an unread week is never a quiet one
        log.warning("Weekly: the week could not be read (attempt %d/%d); nothing "
                    "sent", attempts + 1, MAX_ATTEMPTS, exc_info=True)
        return failed("no data")

    if not payload["send"]:
        log.info("Weekly for %s not sent: no material episode, no recurring "
                 "window, the monitor measured %.1f %% of the week",
                 payload["label"], payload["measured_pct"])
        record.update(last_fired_slot=slot, attempts=0)
        record["last_skipped"] = {"slot": slot, "week_start": payload["week_start"],
                                  "measured_pct": payload["measured_pct"]}
        record.pop("last_error", None)
        record.pop("next_try", None)
        return False

    payload["silent"] = _bool(_env("WEEKLY_SILENT") or "true")
    outcome = notifier.send(payload)
    # The notification ledger (notifications.py) records every try, as it
    # does every other message: what was said, and when.
    notifications.record(payload, outcome, now, notifier.take_receipt())
    if outcome in (notifier.SENT, notifier.HANDED):
        # HANDED: the OpenClaw gateway has it and may still deliver it, so
        # sending it again could say the week twice (notifier.send).
        record.update(last_fired_slot=slot, attempts=0, last_sent_slot=slot)
        record.pop("last_error", None)
        record.pop("next_try", None)
        log.info("Weekly %s for slot %s",
                 "delivered" if outcome == notifier.SENT
                 else "handed to the gateway (not resent)", slot)
        return True
    log.warning("Weekly delivery failed (attempt %d/%d)", attempts + 1, MAX_ATTEMPTS)
    return failed("delivery failed")
