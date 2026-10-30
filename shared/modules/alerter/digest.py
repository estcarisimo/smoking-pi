"""The once-a-day summary: what the week's worth of ticks added up to.

Alerts answer "something just happened". Nobody gets a message on a good day,
which means silence carries two meanings -- nothing broke, or the monitoring
stopped. A digest resolves that ambiguity on a schedule.

Three properties are load-bearing.

**It never claims health it did not verify.** If ``collect()`` raises, or
returns no targets, nothing is sent. "All healthy" when the truth is "InfluxDB
did not answer" is the worst thing this file could say, because it converts a
broken monitor into a reassuring message -- exactly the failure the digest
exists to catch.

**It is bounded structurally, not by a budget.** One slot per day, idempotent
on the persisted slot (see ``schedule.py``), at most ``MAX_ATTEMPTS`` retries.
There is no rate limiter to tune because there is nothing to limit.

**It is quiet.** ``DIGEST_SILENT`` defaults true: a daily summary is something
you read, not something that should buzz a phone at 08:30.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import charts
import clock
import notifier
import schedule
import templates
from common import aggregates, definitions, links, mutes

log = logging.getLogger("alerter.digest")

DEFAULT_AT = "08:30"
DEFAULT_WINDOW_HOURS = 24
DEFAULT_HISTORY_MAX = 200
HISTORY_TTL_S = 48 * 3600

# The notifier already retries 3x internally, so this caps the worst case at
# 9 HTTP attempts across three ticks rather than an unbounded daily retry.
MAX_ATTEMPTS = 3

# Loss at or above this reads as a bad day for a target in the digest text.
# Separate from HIGH_LOSS_PCT (which fires alerts): a digest is retrospective
# and should mention things that never crossed the alerting bar.
NOTABLE_LOSS_PCT = 2.0


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "")).strip())
    except (TypeError, ValueError):
        return default


def enabled() -> bool:
    return _env_bool("DIGEST_ENABLED", False)


def record_history(state: dict, event: dict, now: float | None = None) -> None:
    """Append one notification the alerter decided to send.

    Recorded whether or not delivery succeeded, deliberately: "we tried to
    tell you and could not" belongs in a daily summary more than a silent
    gap does. ``delivery`` says which: "sent", or "queued" in the outbox
    (delivered later inside one summary, recorded as type "undelivered").

    ``state["incidents"]`` only holds *live* records -- reconcile pops a key
    on recovery -- so by 08:30 an incident that fired and cleared at 03:00 has
    left no trace. Without this, the digest could only ever report "nothing is
    wrong right now", which is not what "the last 24 hours" means.
    """
    if now is None:
        now = time.time()
    history = state.setdefault("history", [])
    history.append(
        {
            "ts": now,
            "key": event.get("key"),
            "rule": event.get("rule"),
            "severity": event.get("severity"),
            "type": event.get("type", "alert"),
            "target": event.get("target"),
            "delivery": event.get("delivery"),
        }
    )
    prune_history(state, now)


def prune_history(state: dict, now: float | None = None) -> None:
    """Drop entries older than the TTL, then cap the count. Bounded either way."""
    if now is None:
        now = time.time()
    limit = max(1, _env_int("DIGEST_HISTORY_MAX", DEFAULT_HISTORY_MAX))
    history = [
        entry
        for entry in state.get("history", [])
        if now - float(entry.get("ts", 0) or 0) <= HISTORY_TTL_S
    ]
    state["history"] = history[-limit:]


def _history_since(state: dict, cutoff: float) -> list[dict]:
    return [
        entry
        for entry in state.get("history", [])
        if float(entry.get("ts", 0) or 0) >= cutoff
    ]


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def build(
    state: dict, hours: int | None = None, now: float | None = None
) -> dict | None:
    """Assemble the digest payload, or None when there is nothing trustworthy.

    Returning None is a real outcome, not an error path: it is how "we could
    not ask" stays distinguishable from "all clear".
    """
    if now is None:
        now = time.time()
    if hours is None:
        hours = max(1, _env_int("DIGEST_WINDOW_HOURS", DEFAULT_WINDOW_HOURS))

    try:
        data = aggregates.collect(hours)
    except Exception:  # noqa: BLE001 - any failure means we cannot claim health
        log.warning("Digest: aggregate collection failed; sending nothing",
                    exc_info=True)
        return None

    targets = [t for t in data.get("targets", []) if t.get("target")]
    if not targets:
        log.warning(
            "Digest: no targets returned for the last %dh; sending nothing "
            "rather than reporting a healthy network from an empty result",
            hours,
        )
        return None

    fired = _history_since(state, now - hours * 3600)
    alerts = [e for e in fired if e.get("type") == "alert"]
    recoveries = [e for e in fired if e.get("type") == "recovery"]
    active = state.get("incidents", {})
    # Every digest reports what is muted. Muting is the one feature here that
    # can cause a missed outage, so the daily message a user already reads is
    # the right place to surface a mute they set two days ago and forgot.
    active_mutes = mutes.active(mutes.load(), now)
    suppressed = sum(
        int(r.get("muted_suppressed_count", 0) or 0) for r in active.values()
    )

    incidents = data.get("incidents")
    worst = max(targets, key=lambda t: float(t.get("avg_loss_pct", 0.0) or 0.0))
    lossy = [
        t for t in targets
        if float(t.get("avg_loss_pct", 0.0) or 0.0) >= NOTABLE_LOSS_PCT
    ]

    return {
        "type": "digest",
        "window_hours": hours,
        "generated_at": data.get("generated_at"),
        "targets": targets,
        "target_total": data.get("target_total", len(targets)),
        "cpe": data.get("cpe", []),
        "wifi": data.get("wifi") or {},
        "uplink": data.get("uplink") or {},
        "resolver": data.get("resolver") or {},
        "incidents": incidents,
        "alerts_fired": len(alerts),
        "recoveries": len(recoveries),
        "active_incidents": len(active),
        "lossy": lossy,
        "worst": worst,
        "active_mutes": [mutes.describe(e, now) for e in active_mutes],
        "muted_suppressed": suppressed,
        "message": render(
            hours, targets, lossy, worst, len(alerts), len(recoveries),
            len(active), active_mutes, suppressed, wifi=data.get("wifi") or {},
            uplink=data.get("uplink") or {},
            resolver=data.get("resolver") or {},
            incidents=incidents, now=now,
        ),
        "links": links.entry_point_links(hours=hours),
    }


def render(
    hours: int,
    targets: list[dict],
    lossy: list[dict],
    worst: dict,
    alerts: int,
    recoveries: int,
    active: int,
    active_mutes: list[dict] | None = None,
    suppressed: int = 0,
    wifi: dict | None = None,
    uplink: dict | None = None,
    resolver: dict | None = None,
    incidents: dict | None = None,
    now: float | None = None,
) -> str:
    """The digest text, in the same shape as everything else this bot sends.

    Traffic lights and sections, matching ``templates.STATUS_EMOJI`` and the
    report shape in the OpenClaw skill -- a reader should not have to learn a
    second vocabulary because a message arrived on a schedule instead of in
    response to a fault.
    """
    esc = templates.esc
    b = templates._b
    ok, watch, bad = (
        templates.STATUS_EMOJI["ok"],
        templates.STATUS_EMOJI["watch"],
        templates.STATUS_EMOJI["bad"],
    )

    high = definitions.high_loss_pct()
    listed = (incidents or {}).get("incidents") or []
    # Minutes in which the monitor itself could not measure (its own Wi-Fi
    # or DNS, common.aggregates.MONITOR_CLASSES): every target's loss in
    # them is the monitor's, so a high mean is no destination's problem.
    monitor_minutes = int((incidents or {}).get("monitor_minutes") or 0)

    def own_loss(target: dict) -> float:
        """The target's loss outside those minutes (aggregates'
        avg_loss_pct_measurable): a destination dead all day is still red
        after a five-minute deaf radio, one renamed into a five-hour one is
        not. 0 when it has no point outside them."""
        if "avg_loss_pct_measurable" not in target:
            return float(target.get("avg_loss_pct", 0.0) or 0.0)
        return float(target.get("avg_loss_pct_measurable") or 0.0)

    if active or max((own_loss(t) for t in targets), default=0.0) >= high:
        headline = f"{bad} Last {hours}h: problems worth a look."
    elif monitor_minutes:
        headline = (f"{watch} Last {hours}h: the monitor itself could not "
                    f"measure for {_duration(monitor_minutes)}.")
    elif lossy or alerts or listed:
        headline = f"{watch} Last {hours}h: mostly fine, a few rough edges."
    else:
        headline = f"{ok} Last {hours}h: all clear."

    lines = [headline, ""]

    lines.append(b("Activity"))
    if alerts or recoveries:
        lines.append(
            f"{watch} {_plural(alerts, 'alert', 'alerts')} fired, "
            f"{_plural(recoveries, 'recovery', 'recoveries')}."
        )
    else:
        lines.append(f"{ok} No alerts fired.")
    if active:
        lines.append(f"{bad} {_plural(active, 'incident', 'incidents')} still open.")

    # The window's incidents, classified as the alerts and the assistants
    # classify them (common.diagnosis). A day's mean loss hides a ten-minute
    # cut of the line, and blames two dozen destinations for a deaf radio.
    if incidents is None:
        lines.append(f"{watch} Incidents could not be read for this window.")
    elif listed:
        for incident in listed[:5]:
            minutes = int(incident.get("minutes") or 0)
            when = _clock(incident.get("start"), now)
            lines.append(
                f"{watch} {esc(when)}, {_duration(minutes)}: "
                f"{esc(incident.get('summary') or incident['class'])}")
        if len(listed) > 5:
            lines.append(f"{watch} …and {len(listed) - 5} more incidents.")

    lines.append("")
    lines.append(b("Targets"))
    if lossy:
        # Worst first, capped: a digest is a summary, not a table dump.
        # A target's own loss first: five heard only through a deaf radio
        # must not push the one red destination under "…and N more".
        for target in sorted(
            lossy, key=lambda t: (own_loss(t), float(t.get("avg_loss_pct", 0.0) or 0.0)),
            reverse=True,
        )[:5]:
            loss = float(target.get("avg_loss_pct", 0.0) or 0.0)
            light = bad if own_loss(target) >= high else watch
            median = target.get("median_ms")
            detail = f"{loss:.1f}% loss"
            if monitor_minutes and "avg_loss_pct_measurable" in target:
                own = target.get("avg_loss_pct_measurable")
                detail += (" (all while the monitor could not measure)"
                           if own is None
                           else f" ({float(own):.1f}% while it could)")
            if median is not None:
                detail += f", {float(median):.1f} ms median"
            lines.append(f"{light} {esc(target['target'])} — {detail}.")
        if len(lossy) > 5:
            lines.append(f"{watch} …and {len(lossy) - 5} more with loss.")
        if monitor_minutes:
            lines.append(
                f"{watch} These include the {_duration(monitor_minutes)} the "
                f"monitor could not measure: that loss is not the targets'.")
    else:
        lines.append(f"{ok} All {len(targets)} pinged and DNS targets clean.")

    # Only when the host is on Wi-Fi: the hop every measurement above
    # crossed, in one line, so a bad night on the wireless side is not read
    # as a bad night at the ISP.
    if wifi and wifi.get("min_dbm") is not None:
        lines.append("")
        lines.append(b("Local link"))
        drops = int(wifi.get("disconnects") or 0)
        # Weak as everywhere else: WIFI_WEAK_SAMPLES readings under
        # WIFI_WEAK_DBM, not one dip of the minimum.
        weak = int(wifi.get("weak_samples") or 0) >= definitions.wifi_weak_samples()
        light = bad if drops >= 3 else (watch if drops or weak else ok)
        where = esc(str(wifi.get("ssid") or wifi.get("interface") or "wi-fi"))
        if wifi.get("channel"):
            where += f" ch{wifi['channel']}"
        detail = f"{wifi['min_dbm']:.0f} dBm min"
        if wifi.get("median_dbm") is not None:
            detail = f"{wifi['median_dbm']:.0f} dBm median, " + detail
        if wifi.get("tx_bitrate_mbps"):
            detail += f", {wifi['tx_bitrate_mbps']:.0f} Mb/s"
        detail += f", {_plural(drops, 'disconnect', 'disconnects')}"
        if wifi.get("roams"):
            detail += f", {_plural(int(wifi['roams']), 'roam', 'roams')}"
        lines.append(f"{light} Wi-Fi {where} — {detail}.")

    # Only when the uplink changed: every series above has a before and an
    # after that crossed different links, and the morning reader should
    # know before comparing them. A quiet day adds nothing.
    changes = (uplink or {}).get("changes") or []
    if changes:
        if not (wifi and wifi.get("min_dbm") is not None):
            lines.append("")
            lines.append(b("Local link"))
        for change in reversed(changes[-3:]):  # newest first, then the count of older ones
            text = aggregates.describe_uplink_change(change)
            lines.append(f"{watch} {esc(text[0].upper() + text[1:])}.")
        if len(changes) > 3:
            lines.append(f"{watch} …and {_plural(len(changes) - 3, 'earlier change', 'earlier changes')}.")

    # Only when the resolver changed hands: CDN-backed targets may move to
    # other servers at that moment, so the numbers before and after are not
    # the same path either. A quiet day adds nothing.
    resolver_changes = (resolver or {}).get("changes") or []
    if resolver_changes:
        if not changes and not (wifi and wifi.get("min_dbm") is not None):
            lines.append("")
            lines.append(b("Local link"))
        for change in reversed(resolver_changes[-3:]):
            text = aggregates.describe_resolver_change(change)
            lines.append(f"{watch} {esc(text[0].upper() + text[1:])}.")
        if len(resolver_changes) > 3:
            lines.append(f"{watch} …and {_plural(len(resolver_changes) - 3, 'earlier change', 'earlier changes')}.")

    # Only when something is muted: a "Muted: nothing" line every morning
    # would train the reader to skip the section that matters on the one day
    # it is not empty.
    if active_mutes:
        lines.append("")
        lines.append(b("Muted"))
        for entry in active_mutes[:5]:
            described = mutes.describe(entry)
            scope = described.get("key") or described.get("target") or "*"
            if described.get("rule") and not described.get("key"):
                scope = f"{scope}/{described['rule']}"
            hours_left = described["remaining_minutes"] / 60
            detail = f"{hours_left:.1f}h left"
            if described["reason"]:
                detail += f" — {esc(described['reason'])}"
            lines.append(f"{watch} {esc(str(scope))} ({detail}).")
        if len(active_mutes) > 5:
            lines.append(f"{watch} …and {len(active_mutes) - 5} more muted.")
        if suppressed:
            lines.append(
                f"{watch} {_plural(suppressed, 'alert', 'alerts')} suppressed "
                f"by these mutes."
            )

    return "\n".join(lines)


def _duration(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def _clock(iso: str | None, now: float | None = None) -> str:
    """An ISO timestamp in the digest's zone (clock.py), the weekday kept:
    ``14:05`` today, ``Mon 26 Oct 14:05`` before."""
    if not iso:
        return "?"
    try:
        when = datetime.fromisoformat(iso)
        if when.tzinfo is None:  # state is UTC; never the container's clock
            when = when.replace(tzinfo=timezone.utc)
        return clock.at(when, now, weekday=True)
    except ValueError:
        return iso


def check(state: dict, now: float | None = None) -> bool:
    """Fire the digest if its slot is due. Returns True when one was sent.

    Never raises: this runs inside the same iteration as alert delivery, and
    a digest problem must not cost an alert.
    """
    if not enabled():
        return False
    if now is None:
        now = time.time()

    record = state.setdefault("digest", {})
    slot, reason = schedule.due_slot(
        record.get("last_fired_slot"),
        now,
        os.environ.get("DIGEST_AT") or DEFAULT_AT,
        os.environ.get("DIGEST_TZ") or os.environ.get("TZ"),
        _env_int("DIGEST_MAX_LATENESS", schedule.DEFAULT_MAX_LATENESS_S),
    )

    if reason == "disabled":
        log.warning(
            "DIGEST_AT=%r is not HH:MM; the digest is disabled until it is "
            "fixed (the alert path is unaffected)",
            os.environ.get("DIGEST_AT"),
        )
        return False
    if reason == "not_due":
        return False
    if reason == "skipped_stale":
        # Retire the slot without sending, so a Pi that was off for two days
        # catches up silently instead of delivering yesterday's news now.
        log.info(
            "Digest slot %s is stale (more than the lateness budget old); "
            "recording it as fired without sending", slot
        )
        record["last_fired_slot"] = slot
        record["attempts"] = 0
        return False

    # The retry budget belongs to ONE slot. Without this, a day that failed
    # twice and then went quiet (a restart, a powered-off Pi) hands its count
    # to the next day, which then gets a single attempt instead of three --
    # the budget silently shrinking the longer delivery has been unreliable,
    # which is backwards.
    if record.get("attempts_slot") != slot:
        record["attempts"] = 0
        record["attempts_slot"] = slot

    attempts = int(record.get("attempts", 0) or 0)
    if attempts >= MAX_ATTEMPTS:
        log.warning(
            "Digest for slot %s failed %d times; giving up on it and waiting "
            "for the next slot", slot, attempts
        )
        record["last_fired_slot"] = slot
        record["attempts"] = 0
        return False

    try:
        payload = build(state, now=now)
    except Exception:  # noqa: BLE001 - never let the digest cost an alert
        log.exception("Digest build failed unexpectedly")
        payload = None

    if payload is None:
        # Retire the slot: the failure is already logged, and retrying an
        # unreachable InfluxDB three times an hour apart adds nothing. The
        # point is that we do NOT send a cheerful message instead.
        record["last_fired_slot"] = slot
        record["attempts"] = 0
        record["last_error"] = "no data"
        return False

    image = (
        charts.render_digest_chart(payload)
        if _env_bool("ALERT_CHARTS", True)
        else None
    )
    payload["silent"] = _env_bool("DIGEST_SILENT", True)

    if notifier.notify(payload, image=image):
        record["last_fired_slot"] = slot
        record["attempts"] = 0
        record.pop("last_error", None)
        log.info("Digest delivered for slot %s", slot)
        return True

    # Delivery failed. Leave last_fired_slot alone so the next tick retries
    # this same slot, and count the attempt so it cannot retry forever.
    record["attempts"] = attempts + 1
    record["attempts_slot"] = slot
    record["last_error"] = "delivery failed"
    log.warning("Digest delivery failed (attempt %d/%d)",
                attempts + 1, MAX_ATTEMPTS)
    return False
