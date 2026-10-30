"""Entry point for the alerting engine.

Loop: every ALERT_INTERVAL seconds (default 60) run
evaluator -> state reconcile -> notifier, then the reports watcher.
Exceptions are logged per iteration and never crash the loop. Pass
``--once`` to run a single iteration (handy for testing rules and
notification wiring). With NOTIFY_MODE=off the engine still evaluates and
logs incidents — visibility without spam.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

import alert_diagnosis
import charts
import clock
import dead_man
import evaluator
import flux
import incident_history
import ledger
import notifications
import notifier
import outbox
import quiet
import reports_watcher
import state
import verdict
import watchdog_events
import weekly
from common import links

DEFAULT_INTERVAL = 60  # seconds; ALERT_INTERVAL
# How long InfluxDB may be unreadable before that is itself an alert: five
# cycles, past a restart of the database or a slow query.
UNREADABLE_AFTER_S = 300
# Good cycles in a row before a failing InfluxDB counts as readable again:
# one good read among timeouts must not reset the clock (review of #334).
READABLE_AFTER_CYCLES = 3
# The rules that read an absence of new points, held back for one stale
# window after a blind spell: the writers could not write while InfluxDB was
# down, and the empty window that leaves is the outage already reported.
_STALE_RULES = frozenset({"exporter_stale", "measurement_stale"})


def _influx_error(exc: Exception) -> bool:
    """Whether ``exc`` is the database failing, not the alerter's own code."""
    if isinstance(exc, (OSError, TimeoutError)):  # ConnectionError included
        return True
    module = type(exc).__module__ or ""
    return module.split(".")[0] in ("influxdb_client", "urllib3", "httpx",
                                    "httpcore", "reactivex")

# Which measurement a rule's target is charted in, so the link
# points at the right dashboard. Unknown rules fall back to the
# default inside links.target_links.
_MEASUREMENT_BY_RULE = {
    "microcut_burst": "cpe_latency",
}

log = logging.getLogger("alerter")


def _links_for(incident: dict) -> dict:
    """Deep links for the incident's target, or {} when unconfigured.

    Never raises: a link failure must not cost the alert that carries it.
    """
    target = incident.get("target")
    if not target:
        return {}
    try:
        return links.target_links(
            name=target,
            measurement=_MEASUREMENT_BY_RULE.get(incident.get("rule", "")),
        )
    except Exception:  # noqa: BLE001 - links are decoration, never a gate
        log.warning("Could not build links for %s", target, exc_info=True)
        return {}


def _peers_by_target(mean_rows: list[dict]) -> dict[str, list[str]]:
    """Same-category siblings for each target, for chart context lines."""
    by_category: dict[str, list[str]] = {}
    category_of: dict[str, str] = {}
    for row in mean_rows:
        target, category = row.get("target"), row.get("category")
        if not target or not category:
            continue
        category_of.setdefault(target, category)
        siblings = by_category.setdefault(category, [])
        if target not in siblings:
            siblings.append(target)
    return {
        target: [s for s in by_category.get(category, []) if s != target]
        for target, category in category_of.items()
    }


def _chart_for(incident: dict, peers: dict[str, list[str]]) -> bytes | None:
    """Render the incident's chart, or None. Never raises.

    This DOES block: it queries Influx and renders through matplotlib inline,
    so a slow query holds up the evaluation loop. That is tolerable because
    it runs only for incidents actually being notified, and both the query and
    the render are bounded -- but it is not a background job, and treating it
    as one is how a chart timeout would turn into a stalled alerter.
    """
    if not _env_bool("ALERT_CHARTS", True):
        return None
    target = incident.get("target")
    if not target:
        return None  # global incidents (exporter_stale) have nothing to plot
    return charts.render_incident_chart(
        target,
        measurement=_MEASUREMENT_BY_RULE.get(incident.get("rule", ""), "latency"),
        hours=_env_int("CHART_HOURS", 6),
        first_seen=incident.get("state", {}).get("first_seen"),
        severity=str(incident.get("severity") or "warning"),
        peers=peers.get(target, []),
    )


def _duration_of(event: dict) -> float | None:
    """How long the incident lasted, for the recovery message: from its
    first alert to the cycle the rule stopped firing (``ended_at``), not to
    the end of the resolve grace period. A record saved by an older version
    has no ``ended_at`` and falls back to ``cleared_at``.

    templates renders "was down 23 min" from this; without it that clause
    silently never appears.
    """
    snapshot = event.get("state") or {}
    first_seen = snapshot.get("first_seen")
    ended = snapshot.get("ended_at") or snapshot.get("cleared_at")
    if not first_seen or not ended:
        return None
    return float(ended) - float(first_seen)


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _deliver(current: dict, payload: dict, image: bytes | None = None,
             now: float | None = None) -> None:
    """Send one alert or recovery, or keep it for later. Never raises.

    While the outbox holds anything, the network (or the channel) was down
    at the last try: this joins the queue instead of being sent into it,
    and outbox.flush sends everything as one message once it is back.

    Said once (ledger.py): a notification already delivered, handed to the
    gateway or told in a summary is not handed to anything again. One the
    gateway answered with a failure is with the gateway, which delivers it
    from its own queue; it opens the outbox all the same, so what follows
    waits instead of piling up there too.
    """
    if now is None:
        now = time.time()
    idem = ledger.key_of(payload)
    if ledger.done(current, idem):
        log.info("Notification %s already %s; not sent again", idem,
                 ledger.outcome(current, idem))
        notifications.record(payload, "skipped-duplicate", now)
        return
    # Named before it is held or queued, so the box keeps the id and the
    # summary that later carries it can say so (notifications.py).
    payload["notification_id"] = notifications.new_id()
    receipt = None
    if quiet.holds(payload, now):
        quiet.hold(current, payload, now)
        payload["delivery"], result = "held", ledger.HELD
        told = "held"
    elif outbox.pending(current):
        outbox.enqueue(current, payload, now)
        payload["delivery"], result = "queued", ledger.OUTBOX
        told = "queued"
    else:
        outcome = notifier.send(payload, image=image)
        receipt = notifier.take_receipt()
        if outcome == notifier.SENT:
            payload["delivery"], result = "sent", ledger.SENT
            told = "sent"
        elif outcome == notifier.HANDED:
            outbox.enqueue(current, payload, now, handed=True)
            payload["delivery"], result = "handed", ledger.QUEUED
            told = "handed"
        else:
            outbox.enqueue(current, payload, now)
            payload["delivery"], result = "queued", ledger.FAILED
            # Tried and not delivered: it waits in the outbox.
            told = "failed"
    ledger.record(current, idem, result, now)
    notifications.record(payload, told, now, receipt,
                         entry_id=payload["notification_id"])


def _unreadable(current: dict, exc: Exception, now: float) -> None:
    """InfluxDB could not be read: no rule ran, so say so, and know nothing.

    Before, the iteration failed into the log every minute and nothing was
    sent -- not even that the alerter had gone blind. After
    UNREADABLE_AFTER_S of failures this is a critical of its own, and every
    other open incident is held exactly as it was (``blind``): neither
    recovered nor re-sent, because there is nothing to judge it on.
    """
    monitor = current.setdefault("monitor", {})
    since = float(monitor.setdefault("unreadable_since", now))
    monitor["good_cycles"] = 0
    database = _influx_error(exc)
    log.error("%s (%s) since %s; no rule evaluated",
              "Cannot read InfluxDB" if database else "Evaluation failed",
              type(exc).__name__,
              time.strftime("%H:%M:%S UTC", time.gmtime(since)),
              exc_info=not database)
    incidents = []
    if now - since >= UNREADABLE_AFTER_S:
        at = clock.at(since, now)
        # Same blindness, two causes: the database, or a bug here. A bug must
        # not be reported as the database (review of #334).
        rule, what = (
            ("influx_unreadable", "the alerter cannot read InfluxDB")
            if database else
            ("evaluation_failed", "the alerter's rules fail with an error "
             "of their own (see its log)")
        )
        incidents.append({
            "rule": rule,
            "severity": "critical",
            "key": rule,
            "target": None,
            "message": (
                f"{what} (since {at}, {type(exc).__name__}): no rule can be "
                "evaluated, so no alert from here on means nothing"
            ),
            "value": 0,
        })
    actions = state.reconcile(current, incidents, now=now, blind=True)
    for event in actions["alerts"]:
        _deliver(current, {**event, "type": "alert"}, now=now)
    for event in actions["recoveries"]:
        _deliver(current, {**event, "type": "recovery",
                           "duration_s": _duration_of(event)}, now=now)
    quiet.release(current, now, lambda event: _deliver(current, event, now=now))
    outbox.flush(current, now, send=notifier.send, reachable=notifier.reachable)
    # The weekly summary, the reports and the incident history are skipped:
    # each reads InfluxDB too. A weekly slot past its lateness budget is
    # retired as usual.
    state.save_state(current)
    # Alive, and blind: the outside service says so at once -- once the
    # blindness has lasted as long as influx_unreadable waits, so that an
    # InfluxDB restart is not a page (review of #344). Before that, no
    # check-in at all: the service's grace period covers it.
    if now - since >= UNREADABLE_AFTER_S:
        dead_man.check_in(ok=False, now=now)


def _after_blindness(current: dict, incidents: list[dict], now: float,
                     stale_window: float) -> list[dict]:
    """Bookkeeping for a good cycle; the incidents, less a false stall.

    ``unreadable_since`` clears only after READABLE_AFTER_CYCLES good cycles
    in a row. For one stale window after it clears, a NEW exporter_stale or
    measurement_stale is dropped: the writers could not write while the
    database was down, and that empty window is the outage just reported.
    """
    monitor = current.setdefault("monitor", {})
    if "unreadable_since" in monitor:
        monitor["good_cycles"] = int(monitor.get("good_cycles", 0)) + 1
        if monitor["good_cycles"] >= READABLE_AFTER_CYCLES:
            monitor.pop("unreadable_since")
            monitor.pop("good_cycles", None)
            monitor["readable_since"] = now
    readable_since = monitor.get("readable_since")
    confirming = "unreadable_since" in monitor
    if readable_since is None and not confirming:
        return incidents
    if not confirming and now - float(readable_since) >= stale_window:
        monitor.pop("readable_since")
        return incidents
    records = current.get("incidents", {})
    return [i for i in incidents
            if i.get("rule") not in _STALE_RULES or i.get("key") in records]


def run_iteration() -> None:
    # Loaded first: which incidents are open changes what exporter_stale
    # may conclude from an empty window (evaluator.rule_exporter_stale).
    current = state.load_state()
    try:
        incidents, context = evaluator.evaluate_with_context(
            records=current.get("incidents", {})
        )
    except Exception as exc:  # influx client raises many exception types
        _unreadable(current, exc, time.time())
        return
    incidents = _after_blindness(current, incidents, time.time(),
                                 context["windows"].get("stale", 1200))
    if incidents:
        log.info("%d active incident(s):", len(incidents))
        for incident in incidents:
            log.info(
                "  [%s/%s]%s %s",
                incident["severity"],
                incident["rule"],
                " (quiet: tail of a total loss)" if incident.get("quiet") else "",
                incident["message"],
            )
    else:
        log.info("No active incidents")

    # Classified BEFORE reconcile so the verdict describes the same moment the
    # incidents were measured in, and from records that still hold first_seen
    # for anything reconcile is about to clear.
    # Text for the message, never a gate: a verdict that fails costs its
    # line, not the alerts (before, an exception here silenced them all).
    try:
        call = verdict.classify(
            incidents,
            context["mean_rows"] + context.get("breadth_mean_rows", []),
            context["micro_rows"],
            records=current.get("incidents", {}),
            wifi_rows=context.get("wifi_rows"),
            uplink_changes=context.get("uplink_changes"),
            mean_window=evaluator._format_window(context["windows"]["mean"]),
        )
    except Exception:  # noqa: BLE001 - decoration, never a gate
        log.exception("Verdict failed; alerts go without it")
        call = {}
    actions = state.reconcile(
        current,
        incidents,
        min_resolve_after=evaluator.MEAN_STEPS * context["windows"]["step"],
        judged=context.get("judged"),
    )
    peers = _peers_by_target(context["mean_rows"])
    # Diagnosed once per iteration, and only when a loss alert is going
    # out: the queries are diagnose_loss's, and a quiet minute needs none.
    diagnosed = None
    if any(e.get("rule") in alert_diagnosis.LOSS_RULES for e in actions["alerts"]):
        diagnosed = alert_diagnosis.run(context.get("cadences"))
    for event in actions["alerts"]:
        payload = {
            **event, "type": "alert", "verdict": call,
            "links": _links_for(event),
        }
        found = alert_diagnosis.for_event(event, diagnosed, context["windows"]["step"])
        if found:
            payload["diagnosis"] = found
        # No chart while the outbox holds anything: it would not be sent.
        image = None if outbox.pending(current) else _chart_for(event, peers)
        _deliver(current, payload, image=image)
    for event in actions["recoveries"]:
        # No chart on a recovery: the news IS the recovery, and a second image
        # per incident doubles the render cost for no added answer.
        payload = {
            **event,
            "type": "recovery",
            "links": _links_for(event),
            "duration_s": _duration_of(event),
        }
        _deliver(current, payload)
    # What the host's watchdog did since the last look (a reload, a reboot,
    # a SmokePing restart): once each, through the outbox like any alert.
    try:
        for payload in watchdog_events.collect(current, evaluator._query):
            _deliver(current, payload)
    except Exception:  # noqa: BLE001 - must never cost the cycle's state
        log.exception("Watchdog events failed")
    # What quiet hours held, as one message, once they are over.
    quiet.release(current, time.time(), lambda event: _deliver(current, event))
    # Whatever is waiting, as one message, once delivery works again.
    outbox.flush(current, time.time(), send=notifier.send,
                 reachable=notifier.reachable)

    reports_watcher.check(current)
    # Every half hour, in the background: the incidents that are over, into
    # InfluxDB (loss_incident). Never in the way of the alerts above.
    try:
        incident_history.merge(current)
        incident_history.maybe_record(current, context.get("cadences"))
    except Exception:  # noqa: BLE001 - history must never cost an alert
        log.exception("Incident history check failed")
    # Before save_state, so the fired slot lands in the same atomic write as
    # the incident records it was built from. A crash between the two would
    # otherwise re-send the weekly summary on restart.
    try:
        weekly.check(current)
    except Exception:  # noqa: BLE001 - the weekly must never cost an alert
        log.exception("Weekly summary check failed")
    state.save_state(current)
    # Alive, and judging: the check-in an outside service expects. Last,
    # so an iteration that crashed before here is silence, as it should be.
    dead_man.check_in(ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SmokePing alerting engine")
    parser.add_argument(
        "--once",
        action="store_true",
        help="run a single evaluate/notify iteration and exit",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="send one labeled test message through the configured delivery and exit",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every request URL at INFO. Every minute's InfluxDB query
    # would fill the log, and a Telegram URL carries the bot token
    # (telegram.RedactBotToken redacts it even if this is lowered again).
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    interval = int(os.environ.get("ALERT_INTERVAL", "") or DEFAULT_INTERVAL)
    log.info(
        "SmokePing alerter starting: NOTIFY_MODE=%s interval=%ss "
        "influx=%s state_file=%s",
        notifier.notify_mode(),
        interval,
        os.environ.get("INFLUX_URL", flux.DEFAULT_INFLUX_URL),
        state.state_file(),
    )
    delivery_ok = notifier.preflight()

    if args.test:
        if not delivery_ok:
            log.error("Not sending a test: the delivery preflight failed")
            return 1
        sent = notifier.send_test()
        log.info("Test message %s", "delivered" if sent else "NOT delivered")
        return 0 if sent else 1

    if args.once:
        try:
            run_iteration()
        except Exception:
            log.exception("Iteration failed")
            return 1
        return 0

    while True:
        try:
            run_iteration()
        except Exception:
            # Loop mode: log and retry next interval rather than dying.
            log.exception("Iteration failed; retrying next interval")
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
