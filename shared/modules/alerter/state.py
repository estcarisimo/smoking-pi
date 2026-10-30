"""The alerter's persisted state, and the bookkeeping of its signals.

Every rule (evaluator.py) now produces a **signal**, never a notification:
episodes.py folds the signals into one episode per physical fact, and
policy.py decides what of an episode is said. This module keeps, per signal
key, what the rules themselves need to remember between cycles (which
outage was already running and since when, which target is inside a
target_down, which total loss is still in the window) and what the MCP
server's ``list_alert_state`` shows as open:

- first seen           -> recorded (``first_seen``, ``last_seen``)
- still active         -> ``last_seen`` refreshed
- stops being reported -> a "missing" grace period (``missing_since``)
- missing for ALERT_RESOLVE_AFTER seconds -> dropped
- reappears while missing -> it never ended; the grace timer is cleared
- absent because its target has no data -> held open, neither missing nor
  ended (``judged``): silence is not health. Dropped after HOLD_MAX_S of
  no data (a removed or renamed target)
- superseded by a worse signal about the same thing (``supersedes``) ->
  dropped at once
- ``quiet`` signals (the tail of a total loss still in the window, an older
  outage run) are never recorded; one that ``continues`` an open record
  keeps it from ending until the tail is gone

Until front 8 (2026-10-30) this was also where a rule became a message:
each key alerted on its own, re-alerted every ALERT_COOLDOWN and recovered
on its own, which is how one radio hang reached the phone as ten messages
and October as 72 messages for about eleven events. That path is gone.

The state file also carries the episodes (``episodes``), the outbox, the
quiet-hours box and the reports_watcher bookkeeping (``reports``), so the
whole service has a single persisted file. Writes are atomic (tmp file +
os.replace). Path: ``ALERT_STATE_FILE`` env, default
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
# How long a signal must stay absent before it counts as over, and an
# episode clean before it closes (episodes.py).
# Must exceed the widest rule window's point spacing, or a rule sitting near
# its minimum-points threshold will drop out and "recover" on ordinary window
# jitter. 900 s covers three 300 s SmokePing steps; on a slower step the
# caller passes min_resolve_after so it still covers three.
DEFAULT_RESOLVE_AFTER = 900  # seconds; ALERT_RESOLVE_AFTER
# How long an incident whose target has no data is held open before it is
# dropped without a notice. A stalled probe comes back well inside this; a
# removed or renamed target never does, and must not stay open forever.
HOLD_MAX_S = 24 * 3600
# The per-target rules a missing point can silence, and the aggregate ones
# that need at least one judged target to mean anything.
_PER_TARGET_RULES = frozenset({"target_down", "high_loss"})
_AGGREGATE_RULES = frozenset({"uplink_down", "internet_down", "monitor_dns",
                              "outage"})

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


def resolve_after() -> int:
    return _env_int("ALERT_RESOLVE_AFTER", DEFAULT_RESOLVE_AFTER)


def _no_data(record: dict, judged, blind: bool = False) -> bool:
    """Whether ``record`` is absent for lack of data rather than on data."""
    if blind:
        return True
    if judged is None:
        return False
    rule = record.get("rule")
    if rule in _PER_TARGET_RULES:
        return record.get("target") not in judged
    if rule in _AGGREGATE_RULES:
        return not judged
    return False


def track(
    state: dict,
    incidents: list[dict],
    now: float | None = None,
    min_resolve_after: int = 0,
    judged=None,
    blind: bool = False,
) -> list[str]:
    """Fold this cycle's signals into ``state["incidents"]``; return the
    keys that ended this cycle (dropped after their grace period).

    ``min_resolve_after`` floors ALERT_RESOLVE_AFTER: three of the slowest
    target's steps, so a slow probe's ordinary gap is not an end.
    ``judged`` is the set of targets the per-target rules had enough points
    to judge this cycle (evaluator.judged_targets), or None when unknown. An
    absent signal whose target is not in it is held, not ended.
    ``blind``: no rule could be evaluated this cycle (InfluxDB unreadable);
    every open record is held as it is.

    Nothing here notifies: episodes.py and policy.py do.
    """
    if now is None:
        now = time.time()
    resolve = max(resolve_after(), min_resolve_after)
    records: dict = state.setdefault("incidents", {})
    active_keys = set()
    superseded: set[str] = set()
    continued: set[str] = set()
    ended: list[str] = []

    for incident in incidents:
        superseded.update(incident.get("supersedes") or ())
        if incident.get("quiet"):
            if incident.get("continues"):
                continued.add(incident["continues"])
            continue
        key = incident["key"]
        active_keys.add(key)
        record = records.get(key)
        if record is None:
            record = records[key] = {"first_seen": now}
        record.pop("held_since", None)
        if record.pop("missing_since", None) is not None:
            log.info("Signal %s reappeared within the grace period; "
                     "treated as continuously active", key)
        record.update({
            "rule": incident["rule"],
            "severity": incident["severity"],
            "target": incident.get("target"),
            "message": incident["message"],
            "value": incident.get("value"),
            "last_seen": now,
        })
        if incident.get("since") is not None:
            record["since"] = incident["since"]
        if incident.get("transient"):
            record["transient"] = True
        else:
            record.pop("transient", None)

    for key in sorted(set(records) - active_keys):
        record = records[key]
        if key in superseded:
            records.pop(key)
            continue
        if _no_data(record, judged, blind):
            if blind:
                # Nothing was seen during the blind spell: a grace period
                # started before it restarts once there is data again.
                record.pop("missing_since", None)
            held_since = float(record.setdefault("held_since", now))
            if now - held_since >= HOLD_MAX_S:
                records.pop(key)
                log.warning("Signal %s had no data for %d h; dropped",
                            key, HOLD_MAX_S // 3600)
            continue
        record.pop("held_since", None)
        missing_since = record.get("missing_since")
        if missing_since is None:
            record["missing_since"] = now
            continue
        if now - float(missing_since) < resolve or key in continued:
            continue
        records.pop(key)
        ended.append(key)
    return ended
