"""Incident lifecycle + dedup state, persisted as a small JSON file.

Lifecycle per incident key:

- first seen           -> record it, fire an "alert" notification
- still active, within ALERT_COOLDOWN of the last notification -> silent
- still active, cooldown elapsed -> fire again (re-notify)
- stops being reported -> enter a "missing" grace period, notify nothing
- missing for ALERT_RESOLVE_AFTER seconds -> fire a "recovery" and drop it
- reappears while missing -> it never recovered; clear the grace timer and
                             stay silent (the cooldown still governs re-alerts)

The grace period exists because an incident that oscillates used to produce
an unbounded stream of notifications. Recovery deleted the record outright,
so the next appearance looked brand new, took the first-seen path, and
alerted immediately — meaning ALERT_COOLDOWN only ever suppressed
*continuously* active incidents and did nothing in the one case where it
matters most. A `target_down` incident flapping on a five-minute cycle sent
roughly fifty messages an hour, indefinitely.

ALERT_MAX_PER_HOUR is a second, independent backstop: a hard ceiling on
notifications per key per rolling hour, so a future lifecycle bug cannot
reach a person's phone at that volume again.

Mutes (``common.mutes``, written by the mcp-server) suppress the *send* only.
They are consulted after the cooldown and after the rate limit, and never call
``_record_notification()`` — see :func:`_apply_mute` for why each of those
positions is load-bearing.

Per-incident bookkeeping: first_seen, last_seen, last_notified,
notified_count, missing_since, recent_notifications (all epoch seconds).

The state file also carries the reports_watcher bookkeeping (``reports``
key) so the whole service has a single persisted file. Writes are atomic
(tmp file + os.replace). Path: ``ALERT_STATE_FILE`` env, default
``/var/lib/alerter/state.json``, falling back to ``/tmp/alerter-state.json``
when the default directory is not writable.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time

from common import mutes

log = logging.getLogger("alerter.state")

DEFAULT_STATE_FILE = "/var/lib/alerter/state.json"
FALLBACK_STATE_FILE = "/tmp/alerter-state.json"  # noqa: S108 (documented fallback)
DEFAULT_COOLDOWN = 3600  # seconds; ALERT_COOLDOWN
# How long an incident must stay absent before it counts as recovered.
# Must exceed the widest rule window's point spacing, or a rule sitting near
# its minimum-points threshold will drop out and "recover" on ordinary window
# jitter. 900 s covers three 300 s SmokePing steps.
DEFAULT_RESOLVE_AFTER = 900  # seconds; ALERT_RESOLVE_AFTER
# Hard ceiling on notifications per incident key per rolling hour. This is a
# blast-radius limit, not a tuning knob: it is meant to be unreachable in
# normal operation and to cap the damage when something else is wrong.
DEFAULT_MAX_PER_HOUR = 6  # ALERT_MAX_PER_HOUR
_HOUR_S = 3600.0

_EMPTY_STATE: dict = {"incidents": {}, "reports": {}}


def _dir_writable(path: str) -> bool:
    directory = os.path.dirname(path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return False
    return os.access(directory, os.W_OK)


def state_file() -> str:
    """Resolve the state-file path (env override, default, or fallback)."""
    override = os.environ.get("ALERT_STATE_FILE")
    if override:
        return override
    if _dir_writable(DEFAULT_STATE_FILE):
        return DEFAULT_STATE_FILE
    return FALLBACK_STATE_FILE


def load_state() -> dict:
    path = state_file()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return json.loads(json.dumps(_EMPTY_STATE))
    except (OSError, ValueError) as exc:
        log.warning("Could not read state file %s (%s); starting fresh", path, exc)
        return json.loads(json.dumps(_EMPTY_STATE))
    if not isinstance(data, dict):
        return json.loads(json.dumps(_EMPTY_STATE))
    data.setdefault("incidents", {})
    data.setdefault("reports", {})
    return data


def save_state(state: dict) -> None:
    """Atomically persist ``state`` (tmp file in the same dir + os.replace)."""
    path = state_file()
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".alerter-state.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _cooldown() -> int:
    return _env_int("ALERT_COOLDOWN", DEFAULT_COOLDOWN)


def _resolve_after() -> int:
    return _env_int("ALERT_RESOLVE_AFTER", DEFAULT_RESOLVE_AFTER)


def _max_per_hour() -> int:
    return _env_int("ALERT_MAX_PER_HOUR", DEFAULT_MAX_PER_HOUR)


def _rate_limited(record: dict, now: float, limit: int) -> bool:
    """Trim the rolling-hour window and report whether the key is over budget.

    Deliberately independent of the cooldown and the grace period: those are
    lifecycle logic and can be defeated by a lifecycle bug. This is a plain
    count of what was actually sent.
    """
    if limit <= 0:
        return False
    recent = [
        float(ts)
        for ts in record.get("recent_notifications", [])
        if now - float(ts) < _HOUR_S
    ]
    record["recent_notifications"] = recent
    return len(recent) >= limit


def _record_notification(record: dict, now: float) -> None:
    record.setdefault("recent_notifications", []).append(now)
    record["last_notified"] = now
    record["notified_count"] = int(record.get("notified_count", 0)) + 1


def _apply_mute(record: dict, incident: dict, entries: list[dict],
                key: str, now: float) -> bool:
    """Record that a mute suppressed this send. True means "do not notify".

    Both call sites invoke this at exactly one point, and each half of that
    position is load-bearing:

    - **After ``_rate_limited()``**, so an alert the ceiling already blocked
      is not also counted as one the mute suppressed. This is an accounting
      guarantee rather than a delivery one -- either order stops the send, and
      the rolling-hour trim is idempotent for a given ``now``, so a later
      unmuted cycle re-trims correctly either way. What breaks under the other
      order is ``muted_suppressed_count``, the one number a user reads to
      judge how much a mute is hiding.
    - **Before ``_record_notification()``**, because the budget counts what
      was actually *sent*. A muted incident consumes nothing.
    - **Inside the incident loop**, so ``last_seen``, the ``missing_since``
      clearing and the severity/message refresh have already run by the time
      we get here. Skipping the rest of the loop body instead would leave the
      incident looking brand new when the mute lifts: it would take the
      first-seen path and alert immediately, which is precisely the flapping
      behaviour PR #34 fixed.

    There is deliberately **no catch-up on unmute**. A still-active incident
    re-alerts once on the normal cooldown path. One that resolved while muted
    has ``notified_count == 0``, and the recovery branch already gates on
    ``notified_count > 0``, so no recovery fires for an alert nobody saw.
    """
    mute = mutes.find(entries, incident, now)
    if mute is None:
        return False
    record["muted_suppressed_count"] = int(
        record.get("muted_suppressed_count", 0)) + 1
    record["muted_until"] = mute.get("until")
    log.info("Incident %s is muted until %s (%d suppressed so far); "
             "not notifying", key, mute.get("until"),
             record["muted_suppressed_count"])
    return True


def reconcile(state: dict, incidents: list[dict], now: float | None = None) -> dict:
    """Fold current incidents into ``state`` and return notification actions.

    Mutates ``state`` in place. Returns
    ``{"alerts": [event, ...], "recoveries": [event, ...]}`` where each
    event carries the incident fields plus a ``state`` snapshot for the
    notifier payload.
    """
    if now is None:
        now = time.time()
    cooldown = _cooldown()
    resolve_after = _resolve_after()
    limit = _max_per_hour()
    records: dict = state.setdefault("incidents", {})
    # Read once per cycle, never written here: the mcp-server owns this file
    # and the alerter's bind mount is read-only. load() returns [] rather than
    # raising, so an unreadable mutes file means everything alerts.
    mute_entries = mutes.load()

    alerts: list[dict] = []
    recoveries: list[dict] = []
    active_keys = set()

    for incident in incidents:
        key = incident["key"]
        active_keys.add(key)
        record = records.get(key)
        if record is None:
            record = {
                "rule": incident["rule"],
                "severity": incident["severity"],
                "target": incident.get("target"),
                "message": incident["message"],
                "value": incident.get("value"),
                "first_seen": now,
                "last_seen": now,
                "notified_count": 0,
                "recent_notifications": [],
            }
            records[key] = record
            if _rate_limited(record, now, limit):  # pragma: no cover - new key
                continue
            if _apply_mute(record, incident, mute_entries, key, now):
                continue
            _record_notification(record, now)
            alerts.append({**incident, "state": _snapshot(record)})
            continue

        # Back inside the grace period: this incident never recovered, so it
        # must not re-announce itself. Silently clear the timer.
        if record.pop("missing_since", None) is not None:
            log.info("Incident %s reappeared within the resolve grace period; "
                     "treating it as continuously active", key)

        record["last_seen"] = now
        record["message"] = incident["message"]
        record["value"] = incident.get("value")
        record["severity"] = incident["severity"]
        if now - float(record.get("last_notified", 0)) < cooldown:
            continue
        if _rate_limited(record, now, limit):
            log.warning(
                "Incident %s hit the notification ceiling (%d/hour); "
                "suppressing until the rolling hour clears", key, limit)
            continue
        if _apply_mute(record, incident, mute_entries, key, now):
            continue
        _record_notification(record, now)
        alerts.append({**incident, "state": _snapshot(record)})

    for key in sorted(set(records) - active_keys):
        record = records[key]
        missing_since = record.get("missing_since")
        if missing_since is None:
            # Start the grace period rather than declaring recovery. A rule
            # sitting near its minimum-points threshold drops out on ordinary
            # window jitter, and calling that a recovery is what turned one
            # flapping target into a notification firehose.
            record["missing_since"] = now
            continue
        if now - float(missing_since) < resolve_after:
            continue

        records.pop(key)
        if int(record.get("notified_count", 0)) > 0:
            recoveries.append(
                {
                    "rule": record.get("rule"),
                    "severity": record.get("severity"),
                    "key": key,
                    "target": record.get("target"),
                    "message": record.get("message"),
                    "value": record.get("value"),
                    "state": _snapshot(record, cleared_at=now),
                }
            )

    return {"alerts": alerts, "recoveries": recoveries}


def _snapshot(record: dict, cleared_at: float | None = None) -> dict:
    snap = {
        "first_seen": record.get("first_seen"),
        "last_seen": record.get("last_seen"),
        "last_notified": record.get("last_notified"),
        "notified_count": record.get("notified_count"),
    }
    if cleared_at is not None:
        snap["cleared_at"] = cleared_at
    return snap
