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
import notifier
import reports_watcher
import state

DEFAULT_INTERVAL = 60  # seconds; ALERT_INTERVAL

log = logging.getLogger("alerter")


def run_iteration() -> None:
    incidents = evaluator.evaluate()
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
    actions = state.reconcile(current, incidents)
    for event in actions["alerts"]:
        notifier.notify({**event, "type": "alert"})
    for event in actions["recoveries"]:
        notifier.notify({**event, "type": "recovery"})

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
        os.environ.get("INFLUX_URL", "http://localhost:8086"),
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
