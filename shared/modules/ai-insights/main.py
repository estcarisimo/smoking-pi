"""Entry point for the ai-insights report generator.

Default: one-shot (collect -> report -> exit). Pass ``--loop`` to run
forever, sleeping REPORT_INTERVAL seconds (default 86400 = daily) between
reports. A missing ANTHROPIC_API_KEY is logged and skipped -- exit code 0
either way -- so the container never crash-loops on a missing key.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

import reporter

DEFAULT_INTERVAL = 86_400  # seconds (daily)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SmokePing AI insights reporter")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="run forever, generating a report every REPORT_INTERVAL seconds",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("ai-insights")

    interval = int(os.environ.get("REPORT_INTERVAL", DEFAULT_INTERVAL))

    if not args.loop:
        try:
            reporter.run_once()
        except Exception:
            log.exception("Report generation failed")
            return 1
        return 0

    log.info("Running in loop mode (interval: %ss)", interval)
    while True:
        try:
            reporter.run_once()
        except Exception:
            # Loop mode: log and try again next interval rather than dying.
            log.exception("Report generation failed; retrying next interval")
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
