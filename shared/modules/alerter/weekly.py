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

Scheduling is ``schedule.py``'s: one slot a week, idempotent on the
persisted slot, at most ``MAX_ATTEMPTS`` tries ``RETRY_S`` apart, a slot
more than a day late retired without sending.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import clock
import flux
import notifications
import notifier
import schedule
import templates
import watchdog_events
from common import aggregates, definitions, incident_answers, links
from common.tsdb import CLAMP_LOSS_RATIO, base_flux, flux_str, influx_bucket

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
WEEK_DAYS = 7
MAX_EPISODES = 3
MAX_DESTINATIONS = 3
# Episodes whose loss is read (one short query each); the rest of a very
# bad week is judged on the stored maximum.
MAX_LOSS_QUERIES = 30
# The probe step every availability bin and span tail is counted in.
STEP_S = 300
# Around a watchdog action, the minutes that are the action's, not the
# network's: a driver reload or a reboot takes the probes down with it.
WATCHDOG_BEFORE_S = 300
WATCHDOG_AFTER_S = 900
# An upgrade start with no end closes after this, as in inference/shared.py.
UPGRADE_MAX_S = 1800

# What each stored class is, in the reader's words. local_wifi and monitor
# are the Pi's own (common.aggregates.MONITOR_CLASSES): they count against
# its availability, never as the network's episodes.
CAUSE_OF_CLASS = {
    "local_wifi": "wifi",
    "monitor": "dns",
    "local_link": "line",
    "upstream": "provider",
    "destination": "destinations",
    "unclear": "unclear",
}
CAUSE_TEXT = {
    "monitor": "the Pi itself",
    "wifi": "the Pi's Wi-Fi",
    "dns": "the Pi's own DNS",
    "line": "your line",
    "provider": "your provider",
    "destinations": "individual destinations",
    "unclear": "episodes with no single cause",
    "slow": "slowdowns across the connection",
}
CAUSE_ORDER = ("wifi", "dns", "monitor", "line", "provider", "slow", "destinations",
               "unclear")
# The alerter's episode classes (common.diagnosis.EPISODE_CLASSES) in the
# weekly's causes; a monitor episode by what blinded it.
CAUSE_OF_EPISODE = {"local_link": "line", "upstream": "provider",
                    "destination": "destinations", "undetermined": "unclear"}
MONITOR_CAUSE_OF_DETAIL = {"radio": "wifi", "watchdog": "wifi", "route": "wifi",
                           "dns": "dns"}

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
# The week
# ---------------------------------------------------------------------------


def week_bounds(slot: float, tz: ZoneInfo) -> tuple[float, float, float]:
    """(previous week's start, this week's start, its stop), epoch seconds:
    00:00 of the slot's day, and seven and fourteen calendar days before it,
    in ``tz``."""
    at = datetime.fromtimestamp(slot, tz=tz)
    stop = schedule.midnight_before(at, tz, 0).timestamp()
    start = schedule.midnight_before(at, tz, WEEK_DAYS).timestamp()
    prev = schedule.midnight_before(at, tz, 2 * WEEK_DAYS).timestamp()
    return prev, start, stop


def _rfc3339(epoch: float) -> str:
    return datetime.fromtimestamp(int(epoch), tz=ZoneInfo("UTC")).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _epoch(value) -> float | None:
    if value is None:
        return None
    if hasattr(value, "timestamp"):
        return float(value.timestamp())
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _range(start: float, stop: float) -> str:
    return (f"from(bucket: {flux_str(influx_bucket())}) "
            f"|> range(start: {_rfc3339(start)}, stop: {_rfc3339(stop)}) ")


def _destinations(start: float, stop: float, measurements=("latency", "dns_latency")) -> str:
    """The destinations' series over a range: the ISP first hop is
    evidence about the line, not a destination (common.definitions)."""
    return (base_flux(list(measurements), _rfc3339(start), range_stop=_rfc3339(stop))
            + '|> filter(fn: (r) => not exists r.category or r.category != '
            + flux_str(definitions.FIRST_HOP_CATEGORY) + ") ")


def merge(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return aggregates.merge_spans([(lo, hi) for lo, hi in spans if hi > lo])


def overlap(lo: float, hi: float, spans: list[tuple[float, float]]) -> float:
    """Seconds of ``[lo, hi)`` inside the merged ``spans``."""
    return sum(max(0.0, min(hi, b) - max(lo, a)) for a, b in spans)


def upgrade_spans(rows: list[dict]) -> list[tuple[float, float]]:
    """``smoking-pi upgrade``'s markers as spans, paired as the inference
    pairs them (inference/shared.upgrade_bins): a start closes at the first
    end within UPGRADE_MAX_S, else UPGRADE_MAX_S after it."""
    events = sorted((t, str(r.get("phase"))) for r in rows
                    if (t := _epoch(r.get("_time"))) is not None)
    spans, opened = [], None
    for when, phase in events:
        if phase == "start":
            if opened is not None:
                spans.append((opened, min(when, opened + UPGRADE_MAX_S)))
            opened = when
        elif phase == "end":
            if opened is not None and when - opened <= UPGRADE_MAX_S:
                spans.append((opened, when))
            elif opened is not None:
                spans.append((opened, opened + UPGRADE_MAX_S))
            opened = None
    if opened is not None:
        spans.append((opened, opened + UPGRADE_MAX_S))
    return spans


def watchdog_action(message: str) -> str | None:
    """reload, reboot or restart for an action the watchdog took; None for
    its bookkeeping, a dry run, a refusal or a failure (which is no action)."""
    text = message.lower()
    if watchdog_events.severity(message) != "info" or "would " in text:
        return None
    if "reloading the" in text:
        return "reload"
    if text.endswith("rebooting") or "; rebooting" in text:
        return "reboot"
    if "restarting it" in text:
        return "restart"
    return None


def _optional(query, flux_src: str, what: str) -> list[dict]:
    try:
        return query(flux_src)
    except Exception:  # influx client raises many exception types
        log.warning("Weekly: %s could not be read; left out", what, exc_info=True)
        return []


def _mean_loss_pct(query, targets: list[str], lo: float, hi: float) -> float | None:
    """Mean loss, in percent, of ``targets`` over ``[lo, hi + STEP_S)``: a
    point stamped t measured the step before it."""
    if not targets:
        return None
    names = ", ".join(flux_str(t) for t in sorted(set(targets)))
    rows = query(_destinations(lo, hi + STEP_S)
                 + '|> filter(fn: (r) => r._field == "loss") ' + CLAMP_LOSS_RATIO
                 + f"|> filter(fn: (r) => contains(value: r.target, set: [{names}])) "
                 + "|> group() |> mean()")
    values = [float(r["_value"]) for r in rows if r.get("_value") is not None]
    return round(values[0] * 100.0, 2) if values else None


def _median_ms(query, lo: float, hi: float) -> float | None:
    rows = query(_destinations(lo, hi, ("latency",))
                 + '|> filter(fn: (r) => r._field == "median") |> group() |> median()')
    values = [float(r["_value"]) for r in rows if r.get("_value") is not None]
    return values[0] * 1000.0 if values else None


def _measured_bins(query, start: float, stop: float) -> list[tuple[float, float]]:
    """The STEP_S bins of the week in which any destination wrote a point,
    as spans. aggregateWindow stamps a bin at its end."""
    rows = query(_destinations(start, stop)
                 + '|> filter(fn: (r) => r._field == "loss") |> group() '
                 + f"|> aggregateWindow(every: {STEP_S}s, fn: count, createEmpty: false) "
                 + '|> keep(columns: ["_time", "_value"])')
    ends = {t for r in rows if (r.get("_value") or 0) and (t := _epoch(r.get("_time")))}
    return merge([(max(start, t - STEP_S), min(stop, t)) for t in ends])


def _category_breadth(targets: list[str], categories: dict[str, str]) -> int:
    """Categories in which MATERIAL_CATEGORY_SHARE of the destinations were
    among ``targets``: the gate's second breadth (common.definitions)."""
    total: dict[str, int] = {}
    for target, category in categories.items():
        if category and category != definitions.FIRST_HOP_CATEGORY:
            total[category] = total.get(category, 0) + 1
    hit: dict[str, int] = {}
    for target in set(targets):
        category = categories.get(target)
        if category in total:
            hit[category] = hit.get(category, 0) + 1
    return sum(1 for c, n in hit.items()
               if 100.0 * n / total[c] >= definitions.MATERIAL_CATEGORY_SHARE)


def _from_alerter(closed: list[dict], start: float, stop: float) -> dict:
    """The week's episodes as the alerter recorded and judged them
    (episodes.summary): material ones told, the rest counted, monitor ones
    as the time the monitor could not hear."""
    episodes, repeating, deaf, deaf_by_cause, minor = [], [], [], {}, 0
    for ep in closed:
        lo, hi = max(start, float(ep["start"])), min(stop, float(ep["end"]))
        if hi <= lo:
            continue
        if ep.get("class") == "monitor":
            cause = MONITOR_CAUSE_OF_DETAIL.get(ep.get("detail"), "monitor")
            deaf.append((lo, hi))
            deaf_by_cause[cause] = deaf_by_cause.get(cause, 0.0) + (hi - lo)
            continue
        if ep.get("broad_cycles"):
            repeating.append((lo, hi))
        if ep.get("pattern"):
            # Part of a recurring window: counted there, not as minor.
            continue
        if not ep.get("material"):
            minor += 1
            continue
        episodes.append({
            "kind": "loss", "cause": CAUSE_OF_EPISODE.get(ep.get("class"), "unclear"),
            "start": lo, "end": hi, "minutes": round((hi - lo) / 60.0),
            "affected": int(ep.get("affected") or 0),
            "reporting": int(ep.get("reporting") or 0),
            "loss_pct": float(ep.get("loss_pct") or 0.0),
        })
    return {"episodes": episodes, "repeating": repeating, "deaf": merge(deaf),
            "deaf_by_cause": deaf_by_cause, "minor": minor}


def read_week(start: float, stop: float, query=None, record: dict | None = None) -> dict:
    """Everything one week says, from the shared readers. Raises when the
    incident history, the availability, the categories or the inference's
    periods cannot be read: without any of them a quiet week and an unread
    one look alike. The upgrade markers and the watchdog's events are
    optional: missing, they only exclude less, which errs toward telling.

    The episodes are the alerter's (``record``: ``state["episodes"]``) when
    its record began before the week; otherwise what ``incident_history``
    stored (``loss_incident``, every half hour, INCIDENT_HISTORY_HOURS=0
    turns it off), judged by the same gate. With neither, the weekly sees
    no episode at all, only availability and windows."""
    query = query or flux.query_influx
    bucket = influx_bucket()
    alerter = (record is not None and record.get("since") is not None
               and float(record["since"]) <= start)
    incidents = ([] if alerter else
                 incident_answers.history_between(query, bucket, start, stop))
    measured = _measured_bins(query, start, stop)

    upgrades = upgrade_spans(_optional(
        query, _range(start - UPGRADE_MAX_S, stop)
        + '|> filter(fn: (r) => r._measurement == "upgrade" and r._field == "value") '
        + '|> keep(columns: ["_time", "phase"])', "the upgrade markers"))
    actions: dict[str, int] = {}
    action_spans = []
    for row in _optional(query, _range(start, stop)
                         + '|> filter(fn: (r) => r._measurement == "watchdog_event" '
                         + 'and r._field == "message") |> group() '
                         + '|> keep(columns: ["_time", "_value", "area"])',
                         "the watchdog's events"):
        kind = watchdog_action(str(row.get("_value") or ""))
        at = _epoch(row.get("_time"))
        if kind and at is not None:
            actions[kind] = actions.get(kind, 0) + 1
            action_spans.append((at - WATCHDOG_BEFORE_S, at + WATCHDOG_AFTER_S))
    # Required, like the incidents: without the categories an episode on
    # two of them under 30 % of the destinations would read as minor, and
    # a week told as quiet that was not.
    categories = {
        r["target"]: r.get("category") or ""
        for r in query(_destinations(start, stop)
                       + '|> filter(fn: (r) => r._field == "loss") '
                       + '|> group(columns: ["target", "category"]) |> last() '
                       + '|> keep(columns: ["target", "category"])')
        if r.get("target")}

    monitor = [i for i in incidents if i["class"] in aggregates.MONITOR_CLASSES]
    # A point stamped t measured the step before it: the step after a deaf
    # span still heard it (common.aggregates._collect_incidents).
    deaf = merge([(max(start, i["start_epoch"]), min(stop, i["end_epoch"] + STEP_S))
                  for i in monitor])
    deaf_by_cause: dict[str, float] = {}
    for inc in monitor:
        cause = CAUSE_OF_CLASS[inc["class"]]
        seconds = max(0.0, min(stop, inc["end_epoch"]) - max(start, inc["start_epoch"]))
        deaf_by_cause[cause] = deaf_by_cause.get(cause, 0.0) + seconds
    if alerter:
        own = _from_alerter(record.get("closed") or [], start, stop)
        deaf, deaf_by_cause = own["deaf"], own["deaf_by_cause"]
    excluded = merge(deaf + upgrades + action_spans)

    episodes, repeating, minor = [], [], 0
    if alerter:
        episodes, repeating, minor = own["episodes"], own["repeating"], own["minor"]
    loss_queries = 0
    for inc in incidents:
        if inc["class"] in aggregates.MONITOR_CLASSES:
            continue
        # Clipped to the week: one that runs past Monday 00:00 is counted
        # for its minutes inside it (the next week starts no new one).
        lo, hi = max(start, inc["start_epoch"]), min(stop, inc["end_epoch"])
        minutes = (hi - lo - overlap(lo, hi, excluded)) / 60.0
        reporting = int(inc.get("targets_reporting") or 0)
        affected = int(inc.get("targets_affected") or 0)
        share = 100.0 * affected / reporting if reporting else 0.0
        # The alerter's gate (common.definitions.materiality), on what the
        # stored incident says; its loss is read only once the rest holds.
        facts = {
            "measurable": minutes > 0,
            "breadth_pct": share if reporting >= definitions.MIN_BREADTH_TARGETS else 0.0,
            "categories": (_category_breadth(inc["targets"], categories)
                           if reporting >= definitions.MIN_BREADTH_TARGETS else 0),
            "minutes": minutes,
            "episodes_in_window": 1,
        }
        unmet = definitions.materiality(**facts, loss_pct=None)
        if "persistence" in unmet or "measurable" in unmet:
            continue
        if "breadth" in unmet:
            minor += 1
            continue
        # Only loss the destinations shared repeats as a window: one flaky
        # site at the same hour every day is that site's (section 4), not
        # the connection's. Below the loss bar it still counts: a nightly
        # low-grade loss on every path is exactly a recurring window.
        repeating.append((lo, hi))
        loss = None
        if loss_queries < MAX_LOSS_QUERIES:
            loss_queries += 1
            try:
                loss = _mean_loss_pct(query, inc["targets"], lo, hi)
            except Exception:  # influx client raises many exception types
                log.warning("Weekly: an episode's loss could not be read; "
                            "judged on its maximum", exc_info=True)
        if loss is None:
            # The stored maximum bounds the mean from above: an episode it
            # cannot clear is not material, one it clears is told.
            loss = inc.get("max_loss_pct")
        if loss is None or not definitions.material(**facts, loss_pct=float(loss)):
            minor += 1
            continue
        episodes.append({
            "kind": "loss", "cause": CAUSE_OF_CLASS.get(inc["class"], "unclear"),
            "start": lo, "end": hi, "minutes": round(minutes),
            "affected": affected, "reporting": reporting,
            "loss_pct": float(loss),
        })

    # Shared periods from the inference (profile `inference`; none when it
    # is off): loss the whole connection shared, and congestion.
    # Required: with the `inference` profile off the query is simply empty,
    # so a failure here is a failure, not "no periods".
    shared = query(
        _range(start, stop)
        + '|> filter(fn: (r) => (r._measurement == "shared_degradation" or '
        + 'r._measurement == "shared_congestion") and '
        + '(r._field == "end" or r._field == "class")) '
        + '|> pivot(rowKey: ["_time", "_measurement"], columnKey: ["_field"], '
        + 'valueColumn: "_value") |> group() |> sort(columns: ["_time"])')
    for row in shared:
        lo, hi = _epoch(row.get("_time")), _epoch(row.get("end"))
        if lo is None or hi is None or row.get("class") == "monitor_deaf":
            continue
        lo, hi = max(start, lo), min(stop, hi)
        minutes = (hi - lo - overlap(lo, hi, excluded)) / 60.0
        if minutes < definitions.MATERIAL_MIN_MINUTES:
            continue
        repeating.append((lo, hi))
        if row.get("_measurement") != "shared_congestion":
            continue
        try:
            now_ms = _median_ms(query, lo, hi)
            before_ms = _median_ms(query, _week_before(lo), _week_before(hi))
        except Exception:  # influx client raises many exception types
            log.warning("Weekly: a slowdown's latency could not be read; left out",
                        exc_info=True)
            continue
        # Shared by the whole connection (the inference's shared periods):
        # breadth is all of it; the gate's latency arm decides.
        if now_ms and before_ms and definitions.material(
                measurable=True, breadth_pct=100.0, categories=0, minutes=minutes,
                episodes_in_window=1, loss_pct=None,
                latency_ratio=now_ms / before_ms):
            episodes.append({
                "kind": "slow", "cause": "slow", "start": lo, "end": hi,
                "minutes": round(minutes), "median_ms": now_ms,
                "before_ms": before_ms,
            })

    week_s = stop - start
    # Measured: a bin with a point, outside the monitor's deaf spans. The
    # gaps and the deaf spans overlap (a reboot while the radio was deaf),
    # so the time lost is their union, never their sum.
    measured_s = sum((hi - lo) - overlap(lo, hi, deaf) for lo, hi in measured)
    no_data_s = week_s - sum(hi - lo for lo, hi in measured)
    return {
        "start": start,
        "stop": stop,
        "episodes": sorted(episodes, key=lambda e: e["start"]),
        "minor": minor,
        "repeating": repeating,
        "deaf_minutes": {c: round(s / 60) for c, s in deaf_by_cause.items()},
        "no_data_minutes": round(max(0.0, no_data_s) / 60),
        "measured_pct": round(100.0 * measured_s / week_s, 1) if week_s else 0.0,
        "watchdog": actions,
        "upgrades": len(upgrades),
        "excluded": excluded,
    }


def _week_before(epoch: float) -> float:
    """The same wall-clock time seven calendar days before, in the
    messages' zone: an hour off across a DST change otherwise."""
    tz = zone()
    local = datetime.fromtimestamp(epoch, tz=tz).replace(tzinfo=None)
    return (local - timedelta(days=WEEK_DAYS)).replace(tzinfo=tz).timestamp()


def recurring(spans: list[tuple[float, float]], start: float, stop: float,
              tz: ZoneInfo) -> list[dict]:
    """Runs of hours of the day that ``spans`` touched on at least
    RECURRING_MIN_DAYS days of ``[start, stop)``, in ``tz``: ``{"from": h,
    "to": h + n, "days": d}``, ``days`` the days with a span in the run.
    A run does not wrap past midnight."""
    days_by_hour: dict[int, set] = {h: set() for h in range(24)}
    for lo, hi in spans:
        lo, hi = max(lo, start), min(hi, stop)
        t = lo
        while t < hi:
            # Local hours, not UTC ones: a zone at a half-hour offset has
            # its hours on the half hour of UTC.
            local = datetime.fromtimestamp(t, tz=tz)
            days_by_hour[local.hour].add(local.date())
            top = local.replace(minute=0, second=0, microsecond=0)
            t = max(t + 60, (top + timedelta(hours=1)).timestamp())
    windows, run = [], []
    for hour in range(25):
        if hour < 24 and len(days_by_hour[hour]) >= definitions.RECURRING_MIN_DAYS:
            run.append(hour)
            continue
        if run:
            days = set().union(*(days_by_hour[h] for h in run))
            windows.append({"from": run[0], "to": run[-1] + 1, "days": len(days)})
            run = []
    return windows


def pattern_windows(box: dict | None, start: float, stop: float,
                    tz: ZoneInfo) -> tuple[list[dict], list[dict]] | None:
    """The week's recurring windows from the alerter's patterns: (seen this
    week, gone this week). None when its record does not cover the week
    whole, so the hours are counted the old way."""
    if not box or box.get("since") is None or float(box["since"]) > start:
        return None
    first = datetime.fromtimestamp(start, tz=tz).date().isoformat()
    last = datetime.fromtimestamp(stop - 1, tz=tz).date().isoformat()
    windows, gone = [], []
    for p in box.get("items") or ():
        week = {k: v for k, v in (p.get("days") or {}).items() if first <= k <= last}
        seen = sum(v == "seen" for v in week.values())
        entry = {"pattern": p, "from": p["from"], "to": p["to"], "days": seen,
                 "observed": sum(v in ("seen", "clear") for v in week.values()),
                 "new": first <= str(p.get("created_day") or "") <= last}
        if seen:
            windows.append(entry)
        if p.get("state") == "gone" and start <= float(p.get("gone_at") or 0) < stop:
            gone.append(entry)
    return windows, gone


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


def _overlaps(a: dict, b: dict) -> bool:
    return a["from"] < b["to"] and b["from"] < a["to"]


def material(week: dict, windows: list[dict], gone: list[dict]) -> bool:
    """Whether the week is news: a material episode, a recurring window
    (this week's, or the week before's that is gone), or a monitor that
    measured less than MATERIAL_AVAILABILITY_PCT of it."""
    return bool(week["episodes"] or windows or gone
                or week["measured_pct"] < definitions.MATERIAL_AVAILABILITY_PCT)


def destinations(start: float, stop: float, excluded: list[tuple[float, float]],
                 query=None) -> list[dict] | None:
    """The destinations with the most loss of their own (outside
    ``excluded``) at or above MATERIAL_LOSS_PCT, one per service, worst
    first; None when they could not be read."""
    query = query or flux.query_influx
    try:
        stats = aggregates.target_stats(start, stop, excluded)
    except Exception:  # influx client raises many exception types
        log.warning("Weekly: the destinations' loss could not be read", exc_info=True)
        return None
    labels = {r["target"]: str(r.get("_value") or "")
              for r in _optional(query, f"from(bucket: {flux_str(influx_bucket())}) "
                                 "|> range(start: 0) "
                                 '|> filter(fn: (r) => r._measurement == "target_labels" '
                                 'and r._field == "service") |> group(columns: ["target"]) '
                                 '|> last() |> keep(columns: ["target", "_value"])',
                                 "the service labels")
              if r.get("target")}
    best: dict[str, dict] = {}
    for entry in stats:
        own = entry.get("avg_loss_pct_measurable")
        if own is None or float(own) < definitions.MATERIAL_LOSS_PCT:
            continue
        name = entry["target"]
        label = labels.get(name) or links.target_base_name(name)
        if label not in best or float(own) > best[label]["loss_pct"]:
            best[label] = {"label": label, "loss_pct": float(own),
                           "median_ms": entry.get("median_ms")}
    return sorted(best.values(), key=lambda d: d["loss_pct"], reverse=True)[:MAX_DESTINATIONS]


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




def _week_label(start: float, stop: float, tz: ZoneInfo) -> str:
    first = datetime.fromtimestamp(start, tz=tz)
    last = datetime.fromtimestamp(stop - 1, tz=tz)
    if first.month == last.month:
        return f"{first.day}–{last.day} {last:%b}"
    return f"{first.day} {first:%b} – {last.day} {last:%b}"


def _minutes_by_cause(week: dict) -> dict[str, int]:
    out = {c: m for c, m in week["deaf_minutes"].items()
           if m >= definitions.MATERIAL_MIN_MINUTES}
    for ep in week["episodes"]:
        out[ep["cause"]] = out.get(ep["cause"], 0) + int(ep["minutes"])
    return out


def compare(week: dict, before: dict) -> tuple[str, list[tuple[str, int, int, str]]]:
    """(``good``/``mixed``/``bad``, [(cause, minutes, minutes before,
    ``worse``/``better``/``same``)]), causes in CAUSE_ORDER. A cause moves
    when its minutes changed by at least MATERIAL_MIN_MINUTES."""
    now, prev = _minutes_by_cause(week), _minutes_by_cause(before)
    rows = []
    for cause in CAUSE_ORDER:
        a, b = now.get(cause, 0), prev.get(cause, 0)
        if not a and not b:
            continue
        step = definitions.MATERIAL_MIN_MINUTES
        trend = "worse" if a - b >= step else "better" if b - a >= step else "same"
        rows.append((cause, a, b, trend))
    trends = {r[3] for r in rows}
    if "worse" in trends:
        verdict = "mixed" if "better" in trends else "bad"
    elif any(r[1] for r in rows):
        verdict = "good" if "better" in trends else "mixed"
    else:
        verdict = "good"
    return verdict, rows


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
        for w in windows:
            if w.get("pattern"):
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
    week = read_week(start, stop, query, record)
    before = read_week(prev, start, query, record)
    from_patterns = pattern_windows(known, start, stop, tz)
    if from_patterns is not None:
        (windows, gone), before_windows = from_patterns, []
    else:
        windows = recurring(week["repeating"], start, stop, tz)
        before_windows = recurring(before["repeating"], prev, start, tz)
        gone = [w for w in before_windows if not any(_overlaps(w, x) for x in windows)]
    out = {"type": "weekly", "week_start": start, "week_stop": stop,
           "measured_pct": week["measured_pct"],
           "episodes": len(week["episodes"]), "windows": len(windows)}
    if not material(week, windows, gone):
        out["send"] = False
        out["label"] = _week_label(start, stop, tz)
        return out
    # The Connection quality dashboard: the one that shows a week's patterns.
    link = links.week_link(start, stop)
    out.update({
        "send": True,
        "message": render(week, before, windows, before_windows,
                          destinations(start, stop, week["excluded"], query), tz,
                          gone),
        "links": {"grafana_week": link} if link else {},
    })
    return out


# ---------------------------------------------------------------------------
# The slot
# ---------------------------------------------------------------------------


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
