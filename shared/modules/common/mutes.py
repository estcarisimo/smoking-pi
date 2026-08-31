"""Alert mutes: suppression windows written by one container, read by another.

Muting is the one feature here that can *cause* a missed outage, so both the
data model and the file layout are built to make that hard.

**Single-writer by construction.** Two containers share this state and neither
locks:

===================================  ===========  ==========================
file                                 writer       readers
===================================  ===========  ==========================
``/var/lib/alerter/state.json``      alerter      alerter (rw), mcp (**ro**)
``/var/lib/alerter-mutes/mutes.json``  mcp-server   mcp (rw), alerter (**ro**)
===================================  ===========  ==========================

Neither container ever read-modify-writes a file the other writes, so there is
no race to lock against — and no lock to leak. The ``:ro`` bind mounts in
docker-compose make that an OS-enforced invariant rather than a convention the
next PR can quietly break.

Cross-container *reads* are safe because :func:`save` writes through a temp
file in the same directory plus ``os.replace`` (the same discipline
``alerter/state.py`` already uses for crash-safety). ``os.replace`` is atomic
within a filesystem, so a reader sees the whole old file or the whole new one,
never a half-written one.

**Expiry is evaluated at read time**, never by a background sweep, and pruned
lazily on the next write. That keeps readers read-only: the alerter can decide
a mute has expired without needing write access to a file it must not write.

**A mute is never allowed to be the reason an alert is lost silently.**
:func:`load` returns ``[]`` for a missing or corrupt file rather than raising,
because delivery must not depend on this file being readable — the failure mode
of an unparseable mutes file has to be "everything alerts", not "nothing does".
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time

log = logging.getLogger("common.mutes")

DEFAULT_MUTES_FILE = "/var/lib/alerter-mutes/mutes.json"

# An unbounded mute is how a real outage gets swallowed at 2am and nobody
# notices until morning. Callers clamp to this rather than rejecting, so
# "mute it for a week" still does something useful and says what it did.
MAX_HOURS = 24

# Muting everything is legitimate (planned ISP work, a house move) but must be
# typed deliberately -- it can never be what a caller gets by omitting an
# argument.
WILDCARD = "*"

_MISSING_LOGGED = False


def mutes_file() -> str:
    return os.environ.get("ALERT_MUTES_FILE") or DEFAULT_MUTES_FILE


def load(path: str | None = None) -> list[dict]:
    """Read every mute entry, expired ones included. Never raises.

    Returns ``[]`` when the file is missing, unreadable, or malformed. That is
    deliberate and load-bearing: this is read on the alerter's delivery path,
    and a mute file nobody can parse must not be able to stop alerts. The
    missing-file case is logged once rather than every cycle, because "no mutes
    configured" is the normal state and would otherwise fill the log.
    """
    global _MISSING_LOGGED
    path = path or mutes_file()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        if not _MISSING_LOGGED:
            log.debug("No mutes file at %s; no alerts are muted", path)
            _MISSING_LOGGED = True
        return []
    except (OSError, ValueError) as exc:
        log.warning(
            "Could not read mutes file %s (%s); treating every alert as "
            "unmuted. Alerts are never suppressed by a file we cannot read.",
            path, exc)
        return []

    entries = data.get("mutes") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        log.warning("Mutes file %s has no mute list; ignoring it", path)
        return []
    return [e for e in entries if isinstance(e, dict)]


def active(entries: list[dict], now: float | None = None) -> list[dict]:
    """The subset of ``entries`` still in force at ``now``.

    An entry with no ``until`` is treated as expired rather than eternal: a
    malformed entry must fail toward alerting, not toward silence.
    """
    if now is None:
        now = time.time()
    live = []
    for entry in entries:
        try:
            until = float(entry["until"])
        except (KeyError, TypeError, ValueError):
            continue
        if until > now:
            live.append(entry)
    return live


def matches(entry: dict, incident: dict) -> bool:
    """Whether one mute covers one incident.

    An entry carrying ``key`` is an acknowledgement of one specific incident
    and matches that key alone -- ``target``/``rule`` are not consulted, so an
    ack can never widen into a category-wide silence.

    Otherwise ``target`` and ``rule`` are independent filters and both must
    match when present, so ``{"target": "amazon", "rule": "high_loss"}``
    silences noisy loss on that one target without also hiding it going down
    entirely.

    An entry with none of the three matches nothing. Falling through to
    "matches everything" would turn a malformed entry -- or a tool call that
    lost its arguments -- into a total blackout.
    """
    key = entry.get("key")
    if key is not None:
        return incident.get("key") == key

    target = entry.get("target")
    rule = entry.get("rule")
    if target is None and rule is None:
        return False
    if target is not None and target != WILDCARD:
        if incident.get("target") != target:
            return False
    if rule is not None and rule != WILDCARD:
        if incident.get("rule") != rule:
            return False
    return True


def find(entries: list[dict], incident: dict, now: float | None = None) -> dict | None:
    """The first active mute covering ``incident``, or None."""
    for entry in active(entries, now):
        if matches(entry, incident):
            return entry
    return None


def save(entries: list[dict], path: str | None = None,
         now: float | None = None) -> None:
    """Atomically write ``entries``, dropping expired ones on the way out.

    Pruning happens here, on the write path, because only the mcp-server may
    write this file -- doing it on read would force the alerter to write a
    file its bind mount deliberately makes read-only.
    """
    path = path or mutes_file()
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    payload = {"mutes": active(entries, now)}
    fd, tmp_path = tempfile.mkstemp(prefix=".mutes.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def describe(entry: dict, now: float | None = None) -> dict:
    """A mute rendered for a human or an agent to read back."""
    if now is None:
        now = time.time()
    try:
        until = float(entry.get("until", 0))
    except (TypeError, ValueError):
        until = 0.0
    remaining = max(0, int(until - now))
    described = {
        "target": entry.get("target"),
        "rule": entry.get("rule"),
        "reason": entry.get("reason") or "",
        "until": until,
        "until_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(until)),
        "remaining_minutes": remaining // 60,
    }
    if entry.get("key") is not None:
        described["key"] = entry["key"]
        described["clear_on_recovery"] = bool(entry.get("clear_on_recovery"))
    return described
