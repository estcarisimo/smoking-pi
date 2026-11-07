"""The weekly summary's week, read and judged: shared by the alerter and the MCP server.

The alerter's weekly summary (alerter/weekly.py) tells, once a week and only
when it is news, what the seven calendar days before ``WEEKLY_DAY`` added up
to against the seven before them. Front 10 (2026-11) gave the same week to
the OpenClaw agent as data: the MCP tool ``get_weekly_summary`` returns what
the message says, field by field, so an assistant narrating the week and the
message the alerter sends cannot disagree. Containers cannot import across
each other, so the reading and the judging live here; the alerter keeps the
schedule, the delivery and the rendering of the message (whose text the
tests pin byte for byte, alerter/tests/test_weekly_golden.py).

What is read, and why each reader is required or optional, is
:func:`read_week`'s docstring; what makes a week news is :func:`material`;
the comparison by cause is :func:`compare`; :func:`assemble` does all of it
for one week, and :func:`as_data` turns the result into the tool's answer.

The episodes are the alerter's (``state["episodes"]``, which the MCP server
reads read-only from the same volume) when its record covers the week, the
incident history (``loss_incident``) otherwise. Pure apart from the
``query`` function the caller passes and ``aggregates.target_stats``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import aggregates, definitions, incident_answers, links, localtime, watchdog_actions
from .tsdb import CLAMP_LOSS_RATIO, base_flux, flux_str, influx_bucket, query_influx

log = logging.getLogger("weekly")

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


def week_bounds(slot: float, tz: ZoneInfo) -> tuple[float, float, float]:
    """(previous week's start, this week's start, its stop), epoch seconds:
    00:00 of the slot's day, and seven and fourteen calendar days before it,
    in ``tz``."""
    at = datetime.fromtimestamp(slot, tz=tz)
    stop = localtime.midnight_before(at, tz, 0).timestamp()
    start = localtime.midnight_before(at, tz, WEEK_DAYS).timestamp()
    prev = localtime.midnight_before(at, tz, 2 * WEEK_DAYS).timestamp()
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
    return watchdog_actions.action(message)


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


def read_week(start: float, stop: float, query=None, record: dict | None = None,
              tz: ZoneInfo | None = None) -> dict:
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
    query = query or query_influx
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
    # span still heard it.
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
            before_ms = _median_ms(query, _week_before(lo, tz), _week_before(hi, tz))
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


def _week_before(epoch: float, tz: ZoneInfo | None = None) -> float:
    """The same wall-clock time seven calendar days before, in the
    messages' zone: an hour off across a DST change otherwise."""
    tz = tz or localtime.digest_zone()
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
    query = query or query_influx
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




# ---------------------------------------------------------------------------
# One week, whole: what the message is rendered from and the tool returns
# ---------------------------------------------------------------------------


def assemble(start: float, stop: float, prev: float, query=None,
             record: dict | None = None, known: dict | None = None,
             tz: ZoneInfo | None = None, prev_stop: float | None = None) -> dict:
    """The week ``[start, stop)`` and the one before it ``[prev, prev_stop)``
    (``prev_stop`` defaults to ``start``) read and judged: the week, the
    week before, the recurring windows seen and gone, and whether it is
    news (``material``). ``record``: the alerter's episodes
    (``state["episodes"]``); ``known``: its recurring windows
    (``state["patterns"]``). Raises when the week cannot be read
    (:func:`read_week`)."""
    tz = tz or localtime.digest_zone()
    prev_stop = start if prev_stop is None else prev_stop
    week = read_week(start, stop, query, record, tz)
    before = read_week(prev, prev_stop, query, record, tz)
    from_patterns = pattern_windows(known, start, stop, tz)
    if from_patterns is not None:
        (windows, gone), before_windows = from_patterns, []
    else:
        windows = recurring(week["repeating"], start, stop, tz)
        before_windows = recurring(before["repeating"], prev, prev_stop, tz)
        gone = [w for w in before_windows if not any(_overlaps(w, x) for x in windows)]
    return {"start": start, "stop": stop, "prev": prev, "prev_stop": prev_stop,
            "tz": tz, "week": week, "before": before, "windows": windows,
            "before_windows": before_windows, "gone": gone,
            "material": material(week, windows, gone),
            "label": _week_label(start, stop, tz)}


def _local_midnight(day, tz: ZoneInfo) -> float:
    return localtime.slot_on(datetime(day.year, day.month, day.day), 0, 0, tz).timestamp()


def select(spec: str | None, now: float, tz: ZoneInfo,
           weekday: int = 0) -> tuple[dict | None, str | None]:
    """The week a reader asks for, in ``tz``, weeks starting on ``weekday``
    (0 = Monday, the weekly summary's ``WEEKLY_DAY``):

    - ``last`` (the default): the last complete week, the one the weekly
      summary sent (or skipped) on its day;
    - ``current``: from the start of this week until now, compared with the
      same span a week before;
    - an ISO date (``2026-10-28``): the week that contains it; up to now if
      it is not over.

    ``({"start", "stop", "prev", "prev_stop", "complete"}, None)`` (epoch
    seconds), or ``(None, error)``: a literal sentence, never exception text."""
    text = str(spec or "last").strip().lower()
    today = datetime.fromtimestamp(now, tz=tz).date()
    if text in ("last", "current"):
        anchor = today - timedelta(days=(today.weekday() - weekday) % 7)
        if text == "last":
            anchor -= timedelta(days=WEEK_DAYS)
    else:
        try:
            day = datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            return None, "week must be last, current or an ISO date (YYYY-MM-DD)."
        anchor = day - timedelta(days=(day.weekday() - weekday) % 7)
    start = _local_midnight(anchor, tz)
    stop = _local_midnight(anchor + timedelta(days=WEEK_DAYS), tz)
    prev = _local_midnight(anchor - timedelta(days=WEEK_DAYS), tz)
    if start >= now:
        return None, "That week has not begun."
    if stop <= now:
        return {"start": start, "stop": stop, "prev": prev, "prev_stop": start,
                "complete": True}, None
    # Not over: up to the last whole probe step, against the same span of
    # the week before (a partial week against a whole one reads as better).
    cut = max(start + STEP_S, now - now % STEP_S)
    return {"start": start, "stop": cut, "prev": prev,
            "prev_stop": _week_before(cut, tz), "complete": False}, None


def _iso(epoch: float | None, tz: ZoneInfo) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), tz=tz).isoformat(timespec="minutes")


def _hours(w: dict) -> str:
    if isinstance(w.get("from"), int):
        return f"{w['from']:02d}:00–{w['to'] % 24:02d}:00"
    return f"{w['from']}–{w['to']}"


def _pattern_data(w: dict, tz: ZoneInfo, gone: bool) -> dict:
    p = w["pattern"]
    out = {"hours": _hours(w), "part_of_day": p.get("part") or "day",
           "cause": p.get("cause") or "unclear",
           "state": "gone" if gone else ("new" if w["new"] else "active"),
           "days_this_week": w["days"], "observed_this_week": w["observed"],
           "days_seen": int(p.get("days_seen") or 0),
           "days_observed": int(p.get("days_observed") or 0),
           "first_seen": _iso(p.get("first_seen"), tz),
           "last_seen": _iso(p.get("last_seen"), tz)}
    reporting = int(p.get("reporting") or 0)
    if reporting:
        out["typical_destinations"] = f"{int(p.get('affected') or 0)} of {reporting}"
    return out


def patterns_data(windows: list[dict], before_windows: list[dict],
                  gone: list[dict], tz: ZoneInfo) -> list[dict]:
    """The recurring windows as the message's "Recurring" section lists
    them: the alerter's patterns with their counts, or (for a week its
    record does not cover) hours of the day that came back."""
    out = []
    ended = {id(w["pattern"]) for w in gone if w.get("pattern")}
    for w in windows:
        if w.get("pattern"):
            if id(w["pattern"]) not in ended:
                out.append(_pattern_data(w, tz, False))
            continue
        seen = next((x for x in before_windows if _overlaps(w, x)), None)
        out.append({"hours": _hours(w), "state": "active" if seen else "new",
                    "days_this_week": w["days"],
                    "days_the_week_before": seen["days"] if seen else 0})
    for w in gone:
        if w.get("pattern"):
            out.append(_pattern_data(w, tz, True))
            continue
        out.append({"hours": _hours(w), "state": "gone", "days_this_week": 0,
                    "days_the_week_before": w["days"]})
    return out


def as_data(r: dict, tops: list[dict] | None) -> dict:
    """:func:`assemble`'s week as the MCP tool ``get_weekly_summary`` returns
    it: the sections of the weekly message, as fields. Times in the
    messages' zone; loss in percent; durations in minutes."""
    tz, week, before = r["tz"], r["week"], r["before"]
    verdict, rows = compare(week, before)
    episodes = []
    for ep in sorted(week["episodes"], key=lambda e: e["minutes"], reverse=True):
        item = {"kind": ep["kind"], "cause": ep["cause"],
                "cause_text": CAUSE_TEXT[ep["cause"]],
                "start": _iso(ep["start"], tz), "end": _iso(ep["end"], tz),
                "minutes": int(ep["minutes"])}
        if ep["kind"] == "slow":
            item.update(median_ms=round(ep["median_ms"], 1),
                        median_ms_week_before=round(ep["before_ms"], 1))
        else:
            item.update(destinations_affected=ep["affected"],
                        destinations_reporting=ep["reporting"],
                        loss_pct=round(ep["loss_pct"], 1))
        link = links.range_link(ep["start"], ep["end"])
        if link:
            item["link"] = link
        episodes.append(item)
    out = {
        "week": {"start": _iso(r["start"], tz), "stop": _iso(r["stop"], tz),
                 "label": r["label"], "tz": tz.key,
                 "compared_with": {"start": _iso(r["prev"], tz),
                                   "stop": _iso(r["prev_stop"], tz)}},
        "has_material": bool(r["material"]),
        "verdict": verdict,
        "by_cause": [{"cause": c, "cause_text": CAUSE_TEXT[c], "minutes": a,
                      "minutes_week_before": b, "trend": t} for c, a, b, t in rows],
        "episodes": episodes[:MAX_EPISODES],
        "more_episodes": max(0, len(episodes) - MAX_EPISODES),
        "smaller_episodes": week["minor"],
        "patterns": patterns_data(r["windows"], r["before_windows"], r["gone"], tz),
        "monitor": {
            "measured_pct": week["measured_pct"],
            "blind_minutes": {CAUSE_TEXT[c]: m for c, m in
                              sorted(week["deaf_minutes"].items()) if m},
            "no_data_minutes": week["no_data_minutes"],
            "watchdog_actions": {k: week["watchdog"].get(k, 0)
                                 for k in ("reload", "reboot", "restart")},
            "upgrades": week["upgrades"],
            "availability_bar_pct": definitions.MATERIAL_AVAILABILITY_PCT,
        },
        "destinations": (None if tops is None else
                         [{"service": t["label"], "loss_pct": round(t["loss_pct"], 1)}
                          for t in tops]),
    }
    link = links.week_link(r["start"], r["stop"])
    if link:
        out["links"] = {"grafana_week": link}
    return out
