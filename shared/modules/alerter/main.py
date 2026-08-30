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

import charts
import digest
import evaluator
import flux
import notifier
import reports_watcher
import state
import verdict
from common import links

DEFAULT_INTERVAL = 60  # seconds; ALERT_INTERVAL

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
    """Render the incident's chart, or None. Never raises, never blocks."""
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
    """How long the incident lasted, for the recovery message.

    templates renders "was down 23 min" from this; without it that clause
    silently never appears.
    """
    snapshot = event.get("state") or {}
    first_seen, cleared_at = snapshot.get("first_seen"), snapshot.get("cleared_at")
    if not first_seen or not cleared_at:
        return None
    return float(cleared_at) - float(first_seen)


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


def run_iteration() -> None:
    incidents, context = evaluator.evaluate_with_context()
    if incidents:
        log.info("%d active incident(s):", len(incidents))
        for incident in incidents:
            log.info(
                "  [%s/%s] %s",
                incident["severity"],
                incident["rule"],
                incident["message"],
            )
    else:
        log.info("No active incidents")

    current = state.load_state()
    # Classified BEFORE reconcile so the verdict describes the same moment the
    # incidents were measured in, and from records that still hold first_seen
    # for anything reconcile is about to clear.
    call = verdict.classify(
        incidents,
        context["mean_rows"],
        context["micro_rows"],
        records=current.get("incidents", {}),
    )
    actions = state.reconcile(current, incidents)
    peers = _peers_by_target(context["mean_rows"])
    for event in actions["alerts"]:
        payload = {
            **event, "type": "alert", "verdict": call,
            "links": _links_for(event),
        }
        notifier.notify(payload, image=_chart_for(event, peers))
        # Recorded whether or not delivery succeeded: the digest reports what
        # the alerter DECIDED, and "we tried to tell you and could not" is
        # more useful in a daily summary than a silent gap.
        digest.record_history(current, payload)
    for event in actions["recoveries"]:
        # No chart on a recovery: the news IS the recovery, and a second image
        # per incident doubles the render cost for no added answer.
        payload = {
            **event,
            "type": "recovery",
            "links": _links_for(event),
            "duration_s": _duration_of(event),
        }
        notifier.notify(payload)
        digest.record_history(current, payload)

    reports_watcher.check(current)
    # Before save_state, so the fired slot lands in the same atomic write as
    # the incident records it was built from. A crash between the two would
    # otherwise re-send the digest on restart.
    try:
        digest.check(current)
    except Exception:  # noqa: BLE001 - a digest must never cost an alert
        log.exception("Digest check failed")
    state.save_state(current)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SmokePing alerting engine")
    parser.add_argument(
        "--once",
        action="store_true",
        help="run a single evaluate/notify iteration and exit",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    interval = int(os.environ.get("ALERT_INTERVAL", "") or DEFAULT_INTERVAL)
    log.info(
        "SmokePing alerter starting: NOTIFY_MODE=%s interval=%ss "
        "influx=%s state_file=%s",
        notifier.notify_mode(),
        interval,
        os.environ.get("INFLUX_URL", flux.DEFAULT_INFLUX_URL),
        state.state_file(),
    )
    notifier.preflight()

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
