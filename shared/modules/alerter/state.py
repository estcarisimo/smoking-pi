"""Incident lifecycle + dedup state, persisted as a small JSON file.

Lifecycle per incident key:

- first seen           -> record it, fire an "alert" notification
- still active, within ALERT_COOLDOWN of the last notification -> silent
- still active, cooldown elapsed -> fire again (re-notify)
- no longer reported   -> fire a "recovery" notification (if it ever fired)
                          and drop the record

Per-incident bookkeeping: first_seen, last_seen, last_notified,
notified_count (all epoch seconds).

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

log = logging.getLogger("alerter.state")

DEFAULT_STATE_FILE = "/var/lib/alerter/state.json"
FALLBACK_STATE_FILE = "/tmp/alerter-state.json"  # noqa: S108 (documented fallback)
DEFAULT_COOLDOWN = 3600  # seconds; ALERT_COOLDOWN

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


def _cooldown() -> int:
    try:
        return int(os.environ.get("ALERT_COOLDOWN", "") or DEFAULT_COOLDOWN)
    except ValueError:
        return DEFAULT_COOLDOWN


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
    records: dict = state.setdefault("incidents", {})

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
                "last_notified": now,
                "notified_count": 1,
            }
            records[key] = record
            alerts.append({**incident, "state": _snapshot(record)})
            continue

        record["last_seen"] = now
        record["message"] = incident["message"]
        record["value"] = incident.get("value")
        record["severity"] = incident["severity"]
        if now - float(record.get("last_notified", 0)) >= cooldown:
            record["last_notified"] = now
            record["notified_count"] = int(record.get("notified_count", 0)) + 1
            alerts.append({**incident, "state": _snapshot(record)})

    for key in sorted(set(records) - active_keys):
        record = records.pop(key)
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
