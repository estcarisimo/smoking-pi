"""Deliver ai-insights Markdown reports through the notifier.

Scans ``REPORTS_DIR`` (default ``/reports``) for ``report-*.md`` files
newer than the last delivered one (tracked, by mtime, in the shared state
file under the ``reports`` key) and delivers at most one per
``REPORT_DELIVERY_INTERVAL`` seconds (default 86400). Content is truncated
to ``REPORT_MAX_CHARS`` (default 3500 — Telegram-friendly). A missing
reports directory is skipped quietly (the ai-insights service is opt-in).
"""

from __future__ import annotations

import glob
import logging
import os
import time

import notifier

log = logging.getLogger("alerter.reports")

DEFAULT_REPORTS_DIR = "/reports"
DEFAULT_DELIVERY_INTERVAL = 86_400  # seconds; REPORT_DELIVERY_INTERVAL
DEFAULT_MAX_CHARS = 3_500  # REPORT_MAX_CHARS

HEADER = "Daily network health report:\n\n"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def check(state: dict, now: float | None = None) -> bool:
    """Deliver the newest undelivered report, if the interval allows.

    Mutates ``state["reports"]`` on successful delivery. Returns True when
    a report was delivered.
    """
    if now is None:
        now = time.time()

    reports_dir = os.environ.get("REPORTS_DIR", DEFAULT_REPORTS_DIR)
    if not os.path.isdir(reports_dir):
        return False

    interval = _env_int("REPORT_DELIVERY_INTERVAL", DEFAULT_DELIVERY_INTERVAL)
    max_chars = _env_int("REPORT_MAX_CHARS", DEFAULT_MAX_CHARS)

    rstate = state.setdefault("reports", {})
    last_ts = float(rstate.get("last_delivered_ts", 0) or 0)
    last_mtime = float(rstate.get("last_delivered_mtime", 0) or 0)

    # last_ts == 0 means nothing has ever been delivered: send immediately.
    if last_ts and now - last_ts < interval:
        return False

    candidates = []
    for path in glob.glob(os.path.join(reports_dir, "report-*.md")):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime > last_mtime:
            candidates.append((mtime, path))
    if not candidates:
        return False

    mtime, path = max(candidates)
    try:
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
    except OSError as exc:
        log.warning("Could not read report %s: %s", path, exc)
        return False

    if len(content) > max_chars:
        content = content[:max_chars]

    delivered = notifier.notify(
        {
            "type": "report",
            "rule": "daily_report",
            "severity": "info",
            "target": None,
            "message": HEADER + content,
        }
    )
    if delivered:
        rstate["last_delivered_ts"] = now
        rstate["last_delivered_mtime"] = mtime
        rstate["last_delivered_file"] = os.path.basename(path)
        log.info("Delivered report %s", os.path.basename(path))
    return delivered
