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
    for event in actions["alerts"]:
        notifier.notify(
            {**event, "type": "alert", "verdict": call, "links": _links_for(event)}
        )
    for event in actions["recoveries"]:
        notifier.notify(
            {**event, "type": "recovery", "links": _links_for(event)}
        )

    reports_watcher.check(current)
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
