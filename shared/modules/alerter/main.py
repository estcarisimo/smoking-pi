"""Entry point for the alerting engine.

Loop: every ALERT_INTERVAL seconds (default 60) one cycle, an explicit
pipeline:

    signals (evaluator, watchdog_events, the monitor's own health)
      -> episode (episodes.update: one per physical fact, blind spans
         subtracted)
      -> classification (common.diagnosis.classify_episode)
      -> materiality (common.definitions.materiality)
      -> recurring windows (policy.patterns_said: common.patterns, an
         episode in a known window's hours is not news)
      -> policy (policy.decide: what is said, and how loud)
      -> delivery (_deliver: quiet hours, outbox, notifier)

then the reports watcher, the incident history and the weekly summary. Exceptions
are logged per iteration and never crash the loop. Pass ``--once`` to run a
single iteration. With NOTIFY_MODE=off the engine still evaluates and logs
episodes -- visibility without spam.
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
import episodes
import evaluator
import flux
import incident_history
import ledger
import notifications
import notifier
import outbox
import policy
import quiet
import reports_watcher
import state
import templates
import watchdog_events
import weekly
from common import patterns

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

log = logging.getLogger("alerter")


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
    """InfluxDB could not be read: no rule ran, so know nothing.

    The span is the monitor's blind time: it joins the open episode (or
    opens one, after UNREADABLE_AFTER_S) as a ``database`` blind span, and
    every open signal record is held exactly as it was (``blind``). Nothing
    is pushed while it lasts -- the dead-man check-in says so, outside --
    and an episode that was all blind for MONITOR_BLIND_MIN_MINUTES is told
    once, silently, when the database answers again (policy.py).
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
    signals = []
    if now - since >= UNREADABLE_AFTER_S:
        # Same blindness, two causes: the database, or a bug here. A bug must
        # not be reported as the database (review of #334).
        rule, what = (
            ("influx_unreadable", "the alerter cannot read InfluxDB")
            if database else
            ("evaluation_failed", "the alerter's rules fail with an error "
             "of their own (see its log)")
        )
        signals.append({
            "rule": rule,
            "severity": "critical",
            "key": rule,
            "target": None,
            "message": f"{what} (since {clock.at(since, now)}, {type(exc).__name__})",
            "value": 0,
            "start": since,
            "end": now,
        })
    state.track(current, signals, now=now, blind=True)
    # The monitor's own episode: blind from ``since``, nothing pushed while
    # it lasts (policy.py); told once, silently, when it can read again.
    cycle = {"now": now, "step": evaluator.STEP_S,
             "signals": episodes.signals(signals, {}, now),
             "rows": [], "blind": [[since, now, "database"]],
             "blind_cycle": True}
    _push(current, episodes.update(current, cycle), now)
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


def _cycle(current: dict, incidents: list[dict], context: dict,
           watched: list[dict], now: float) -> dict:
    """This cycle's input to the episodes: signals, rows and blind spans."""
    step = int(context["windows"]["step"])
    blind = (episodes.deaf_spans(context.get("deaf_rows") or [])
             + episodes.route_spans(context.get("uplink_changes") or [], now))
    # Optional like the Wi-Fi evidence: without the markers an upgrade's
    # gap is judged on what the other spans say.
    try:
        blind += episodes.upgrade_spans(evaluator._query(episodes.upgrade_flux()), now)
    except Exception:  # influx client raises many exception types
        log.warning("upgrade markers could not be read", exc_info=True)
    if any(i.get("rule") == "exporter_stale" for i in incidents):
        blind.append([now - context["windows"].get("stale", 1200), now, "exporter"])
    wifi_rows = context.get("wifi_rows") or {}
    has_wifi = any(wifi_rows.get(name) for name in ("uplink", "signal", "rx"))
    # The weakest signal of the last hour and how many samples were weak,
    # for the message's "this Pi's Wi-Fi was stable (-50 dBm)".
    signal = [r for r in wifi_rows.get("signal") or ()
              if r.get("min") is not None and int(r.get("n") or 0) > 0]
    return {
        "now": now,
        "step": step,
        "signals": episodes.signals(incidents, context, now, extra=watched,
                                    records=current.get("incidents")),
        "rows": context.get("down_rows") or [],
        "blind": blind,
        "wifi": "receiving" if has_wifi else None,
        "wifi_dbm": min((float(r["min"]) for r in signal), default=None),
        "wifi_weak": sum(int(r.get("weak") or 0) for r in signal),
    }


def _push(current: dict, events: list[dict], now: float,
          cadences: dict | None = None, step: int = evaluator.STEP_S) -> None:
    """The policy's notifications for these episode events, delivered.
    A failure in the policy costs this cycle's messages, never its state."""
    try:
        payloads = policy.decide(current, events, now)
    except Exception:  # noqa: BLE001 - must never cost the cycle's state
        log.exception("Push policy failed")
        return
    # An episode that closed with nothing pushed is recorded in the ledger
    # all the same, with why: "why was I not told?" has an answer there too.
    for event in events:
        ep = event["episode"]
        if event["event"] == "closed" and not (ep.get("policy") or {}).get("sent"):
            notifications.record(policy.recorded(ep), "recorded", now)
    for payload in payloads:
        image = None
        if payload.get("kind") in ("down", "degradation"):
            # The last hours diagnosed, as the MCP tool does: a second
            # reading that disagrees is the message's contrary signal
            # (templates.choose), never a second cause.
            found = alert_diagnosis.for_event(
                {"rule": "outage", "target": None},
                alert_diagnosis.run(cadences), step, now=now)
            if found:
                payload["diagnosis"] = found
                payload["message"] = templates.plain(payload)
            # The one chart: on the opening message of a material episode.
            # Not while the outbox holds anything: it would not be sent.
            # Nor for one quiet hours hold: it would be dropped.
            if not outbox.pending(current) and not quiet.holds(payload, now):
                image = _chart_for(current, payload, now)
        _deliver(current, payload, image=image, now=now)


def _chart_for(current: dict, payload: dict, now: float) -> bytes | None:
    """The opening message's chart: the destination that lost most, with
    the others of its category as context, from a cycle before the episode
    to now. None when there is nothing to draw. Never raises -- and it
    blocks (a query and a render), which is why only an opening message
    gets one."""
    try:
        record = payload.get("episode") or {}
        ep = (current.get("episodes") or {}).get("open") or {}
        category_of = ep.get("category_of") or {}
        worst = (record.get("measures") or {}).get("worst") or []
        target = worst[0][0] if worst else next(iter(record.get("sites") or ()), None)
        if not target:
            target = next((t for t, c in sorted(category_of.items())
                           if c != evaluator.FIRST_HOP_CATEGORY), None)
        if not target:
            return None
        category = category_of.get(target)
        peers = sorted(t for t, c in category_of.items()
                       if c == category and t != target)
        hours = max(1, min(6, int((now - float(record.get("start") or now)) // 3600) + 2))
        return charts.render_incident_chart(
            target,
            measurement="dns_latency" if category == "dns" else "latency",
            hours=hours,
            first_seen=record.get("start"),
            severity=str(payload.get("severity") or "warning"),
            peers=peers,
        )
    except Exception:  # noqa: BLE001 - a chart never costs the alert
        log.warning("Chart for %s failed; sending text only", payload.get("key"),
                    exc_info=True)
        return None


def _patterns(current: dict, cycle: dict | None, now: float, step: int) -> list[dict]:
    """The recurring windows: this cycle's half hour marked heard when the
    monitor could hear it, the episodes folded in, and the one message a
    pattern's appearance or end calls for. Never costs the cycle."""
    try:
        if cycle is not None and episodes.heard_now(cycle):
            patterns.mark_heard(patterns.book(current), now, clock.zone())
        return policy.patterns_said(current, now, step, evaluator._query)
    except Exception:  # noqa: BLE001 - must never cost the cycle's state
        log.exception("Recurring-window check failed")
        return []


def run_iteration() -> None:
    # Loaded first: which signals are open changes what exporter_stale
    # may conclude from an empty window (evaluator.rule_exporter_stale).
    current = state.load_state()
    try:
        incidents, context = evaluator.evaluate_with_context(
            records=current.get("incidents", {})
        )
    except Exception as exc:  # influx client raises many exception types
        _unreadable(current, exc, time.time())
        return
    now = time.time()
    incidents = _after_blindness(current, incidents, now,
                                 context["windows"].get("stale", 1200))
    if incidents:
        log.info("%d active signal(s):", len(incidents))
        for incident in incidents:
            log.info("  [%s/%s]%s %s", incident["severity"], incident["rule"],
                     " (quiet)" if incident.get("quiet") else "",
                     incident["message"])
    else:
        log.info("No active signals")
    step = int(context["windows"]["step"])
    state.track(current, incidents, now=now,
                min_resolve_after=evaluator.MEAN_STEPS * step,
                judged=context.get("judged"))
    # What the host's watchdog did since the last look (a reload, a reboot,
    # a SmokePing restart): signals, and spans the monitor could not hear.
    watched = watchdog_events.collect(current, evaluator._query, now)
    # Guarded like the policy: a bug here costs this cycle's episode, never
    # the state save, the outbox or the check-in below (review of #436).
    try:
        cycle = _cycle(current, incidents, context, watched, now)
        events = episodes.update(current, cycle)
    except Exception:  # noqa: BLE001 - must never cost the cycle's state
        log.exception("Episode update failed")
        cycle, events = None, []
    # Before the policy: an episode that makes a pattern of the evenings
    # before it is recorded as part of it, not pushed on its own.
    said = _patterns(current, cycle, now, step)
    _push(current, events, now, context.get("cadences"), step)
    for payload in said:
        _deliver(current, payload, now=now)
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
