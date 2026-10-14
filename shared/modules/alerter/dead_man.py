"""The dead man's switch: a check-in that something off this host expects.

Every alert this stack sends comes from the host being watched. When that
host hangs, loses its SD card or its power, nothing says so: the alerts
stop, and silence reads as "all well" (the digest exists for the same
reason, once a day). So the alerter checks in with an outside service
(ALERT_DEADMAN_URL; healthchecks.io's ping URLs are the shape, and a
self-hosted Healthchecks works the same), and that service raises the alarm
when the check-ins stop. It is the only alert that works when the whole
house is down.

- After every evaluation that ran, a GET to the URL: "alive, and judging".
  Alive is not "delivering": a broken channel (a revoked bot token) is the
  outbox's to report, not this switch's.
- Once the alerter has been unable to read InfluxDB for as long as its own
  ``influx_unreadable`` alert waits, a GET to URL + ``/fail``: "alive, and
  blind", which the service reports at once instead of after its grace
  period. Not before: an InfluxDB restart (an upgrade, a backup) is not
  worth a page (review of #344).
- At most one check-in per ``INTERVAL_S`` (or per ALERT_INTERVAL, when that
  is longer), at once when the state changes, and never two tries within
  ``RETRY_S``: a failed one is retried a minute later, not five, and a
  flapping database cannot make it ping every cycle. About 300 a day.

The URL is a credential (anyone holding it can check in for this host):
it is never logged; the host is. Never raises, and bounded by
``TIMEOUT_S``: the switch must never cost an alert.
"""

from __future__ import annotations

import logging
import os
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

log = logging.getLogger("alerter.dead_man")

INTERVAL_S = 300
RETRY_S = 60
TIMEOUT_S = 5.0

# Process memory, not the state file: a restart checks in at once, which
# is right (it is alive), and the state file stays the incidents'. What was
# last DELIVERED decides the cadence; a failed try only sets the retry.
_last: dict = {"sent_at": None, "sent_ok": None, "tried_at": None}


def url() -> str | None:
    raw = (os.environ.get("ALERT_DEADMAN_URL") or "").strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        log.warning("ALERT_DEADMAN_URL is not an http(s) URL; no check-ins")
        return None
    return raw


def _target(base: str, ok: bool) -> str:
    """The URL itself, or its ``/fail`` twin: on the path, so a query
    string (``?create=1``) stays a query string."""
    parts = urlsplit(base)
    path = parts.path.rstrip("/")
    return urlunsplit(parts._replace(path=path if ok else path + "/fail"))


def _due(ok: bool, now: float) -> bool:
    sent_at, sent_ok, tried_at = (_last["sent_at"], _last["sent_ok"],
                                  _last["tried_at"])
    # A clock that went back (no RTC on a Pi) counts as due.
    if tried_at is not None and 0 <= now - tried_at < RETRY_S:
        return False
    if ok != sent_ok or sent_at is None:
        return True
    return not (0 <= now - sent_at < INTERVAL_S)


def check_in(ok: bool, now: float | None = None) -> bool | None:
    """Check in when due. True: delivered; False: tried and failed (retried
    after RETRY_S; the service notices a longer silence on its own); None:
    not configured or not due."""
    base = url()
    if base is None:
        return None
    if now is None:
        now = time.time()
    if not _due(ok, now):
        return None
    _last["tried_at"] = now
    changed = ok != _last["sent_ok"]
    host = urlsplit(base).hostname or "?"
    try:
        response = httpx.get(_target(base, ok), timeout=TIMEOUT_S)
        if response.status_code < 300:
            _last.update({"sent_at": now, "sent_ok": ok})
            if changed:
                log.info("Dead man's switch: checked in with %s (%s)", host,
                         "ok" if ok else "fail: the alerter is blind")
            return True
        log.warning("Dead man's switch: %s answered HTTP %d", host,
                    response.status_code)
    except Exception as exc:  # noqa: BLE001 - must never cost an alert
        log.warning("Dead man's switch: could not reach %s (%s)", host,
                    type(exc).__name__)
    return False
