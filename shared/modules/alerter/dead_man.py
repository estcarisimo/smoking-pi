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
- While the alerter cannot read InfluxDB, a GET to URL + ``/fail``: "alive,
  and blind", which the service reports at once instead of after its
  grace period.
- At most one check-in per ``INTERVAL_S``, and at once when the state
  changes (ok after fail, or fail after ok). About 300 requests a day.

The URL is a credential (anyone holding it can check in for this host):
it is never logged; the host is. Never raises, and bounded by
``TIMEOUT_S``: the switch must never cost an alert.
"""

from __future__ import annotations

import logging
import os
import time
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("alerter.dead_man")

INTERVAL_S = 300
TIMEOUT_S = 5.0

# Process memory, not the state file: a restart checks in at once, which
# is right (it is alive), and the state file stays the incidents'.
_last: dict = {"at": None, "ok": None}


def url() -> str | None:
    raw = (os.environ.get("ALERT_DEADMAN_URL") or "").strip()
    if not raw:
        return None
    if urlsplit(raw).scheme not in ("http", "https") or not urlsplit(raw).netloc:
        log.warning("ALERT_DEADMAN_URL is not an http(s) URL; no check-ins")
        return None
    return raw.rstrip("/")


def check_in(ok: bool, now: float | None = None) -> bool | None:
    """Check in when due. True: delivered; False: tried and failed (the
    service will notice the silence on its own); None: not configured or
    not due."""
    base = url()
    if base is None:
        return None
    if now is None:
        now = time.time()
    last_at, last_ok = _last["at"], _last["ok"]
    # A clock that went back (no RTC on a Pi) counts as due.
    if (last_ok == ok and last_at is not None
            and 0 <= now - last_at < INTERVAL_S):
        return None
    _last.update({"at": now, "ok": ok})
    target = base if ok else base + "/fail"
    host = urlsplit(base).hostname or "?"
    try:
        response = httpx.get(target, timeout=TIMEOUT_S)
        if response.status_code < 300:
            if ok != last_ok:
                log.info("Dead man's switch: checked in with %s (%s)", host,
                         "ok" if ok else "fail: the alerter is blind")
            return True
        log.warning("Dead man's switch: %s answered HTTP %d", host,
                    response.status_code)
    except Exception as exc:  # noqa: BLE001 - must never cost an alert
        log.warning("Dead man's switch: could not reach %s (%s)", host,
                    type(exc).__name__)
    return False
