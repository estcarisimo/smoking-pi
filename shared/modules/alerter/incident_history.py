"""Loss incidents, written down once they are over.

``diagnose_loss`` (common/diagnosis.py) folds the loss of every target into
incidents and says what each one was -- this host's Wi-Fi, the line, one
destination, upstream -- with its evidence and a confidence. It computed
them on demand, for at most the last week, and nothing kept them: last
month's outage could not be asked about, and no chart could mark one.

Every ``EVERY_S`` the alerter diagnoses the last ``hours()`` in the
background (never in the way of an alert) and writes each incident that is
**over** into InfluxDB, measurement ``loss_incident``:

- **Identity is the start.** The point's time is the incident's first lossy
  step, and every value is a field: no tag that could change between two
  runs, so the next run's point for the same incident overwrites this one
  instead of standing beside it. Every field is written every time (empty
  strings, not missing fields), so an overwrite leaves nothing stale.
- **Only incidents that are over.** One whose last step is within
  ``SETTLE_S`` of now can still grow, and late points (the exporter
  backfills after downtime) can still change it.
- **Only incidents wholly inside the window.** One that began before the
  window's first steps is cut at the window's edge. When such an incident
  is over, the run diagnoses again with the window doubled (up to the
  diagnosis's limit, a week) until its start is inside: the incidents that
  matter most are the long ones (the reference Pi's radio was deaf for 11
  hours on 2026-10-11), and a fixed window never sees them whole.

Fields: ``end_epoch``, ``minutes``, ``class``, ``detail``, ``confidence``,
``summary``, ``targets`` (comma-separated), ``targets_affected``,
``targets_reporting``, ``max_loss_pct`` (absent for a cut or deaf span with
no loss run), ``evidence`` and ``against`` (sentences joined with " | ").
``probe_miss`` incidents are kept too; readers leave them out by default.

Retention is the bucket's, like the measurements they explain. InfluxDB
only: this alerter reads and writes InfluxDB.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import time

import flux
from common import cadence, diagnosis, diagnosis_query, tsdb

log = logging.getLogger("alerter.incident_history")

MEASUREMENT = "loss_incident"
DEFAULT_HOURS = 12  # INCIDENT_HISTORY_HOURS; 0 turns it off
EVERY_S = 1800
SETTLE_S = 1800
SEP = " | "

_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1,
                                              thread_name_prefix="incident-history")
_pending: concurrent.futures.Future | None = None


def hours() -> int:
    try:
        value = int(os.environ.get("INCIDENT_HISTORY_HOURS", "") or DEFAULT_HOURS)
    except ValueError:
        return DEFAULT_HOURS
    return max(0, min(value, diagnosis_query.MAX_HOURS))


def finished(result: dict, now: float, window_h: int, step_s: int) -> list[dict]:
    """The incidents of ``result`` that are over and wholly inside the
    window, oldest first. None when the diagnosis hit its row limit: its
    incidents may be missing events, and a wrong class written down stays."""
    if result.get("rows_truncated"):
        return []
    first = now - window_h * 3600 + (diagnosis.GAP_STEPS + 1) * step_s
    done = [i for i in result.get("incidents") or []
            if i.get("start_epoch") is not None and i.get("end_epoch") is not None
            and i["start_epoch"] >= first and i["end_epoch"] <= now - SETTLE_S]
    return sorted(done, key=lambda i: i["start_epoch"])


def point(inc: dict):
    """One incident as a ``loss_incident`` point."""
    from influxdb_client import Point, WritePrecision

    # A literal, not MEASUREMENT: the doctor reads Point("...") to know which
    # measurements exist (panel-measurements-written).
    p = (Point("loss_incident")
         .time(int(inc["start_epoch"]), WritePrecision.S)
         .field("end_epoch", int(inc["end_epoch"]))
         .field("minutes", int(inc.get("minutes") or 0))
         .field("class", str(inc.get("class") or ""))
         .field("detail", str(inc.get("detail") or ""))
         .field("confidence", str(inc.get("confidence") or ""))
         .field("summary", str(inc.get("summary") or diagnosis.summary(inc)))
         .field("targets", ",".join(inc.get("targets") or []))
         .field("targets_affected", int(inc.get("targets_affected") or 0))
         .field("targets_reporting", int(inc.get("targets_reporting") or 0))
         .field("evidence", SEP.join(inc.get("evidence") or []))
         .field("against", SEP.join(inc.get("against") or [])))
    if inc.get("max_loss_pct") is not None:
        p.field("max_loss_pct", float(inc["max_loss_pct"]))
    return p


def cut_at_edge(result: dict, now: float, window_h: int, step_s: int) -> bool:
    """Whether an incident that is over began at the window's edge."""
    first = now - window_h * 3600 + (diagnosis.GAP_STEPS + 1) * step_s
    return any(i.get("start_epoch") is not None and i["start_epoch"] < first
               and (i.get("end_epoch") or now) <= now - SETTLE_S
               for i in result.get("incidents") or [])


def record(window_h: int, cadences: dict | None, now: float | None = None,
           query=None, write=None) -> int:
    """Diagnose the last ``window_h`` (more when an incident that is over
    began before it) and write what is over. Returns how many incidents
    were written."""
    now = time.time() if now is None else now
    step_s = int(cadence.longest_step(cadences or {}))
    query = query or flux.query_influx
    result = diagnosis_query.run(query, window_h, cadences)
    while (cut_at_edge(result, now, window_h, step_s)
           and window_h < diagnosis_query.MAX_HOURS):
        window_h = min(window_h * 2, diagnosis_query.MAX_HOURS)
        log.info("incident history: an incident began before the window; "
                 "diagnosing the last %d h", window_h)
        result = diagnosis_query.run(query, window_h, cadences)
    if result.get("rows_truncated"):
        log.warning("incident history: the diagnosis hit its row limit; nothing written")
    done = finished(result, now, window_h, step_s)
    (write or tsdb.write_points)([point(i) for i in done])
    return len(done)


def maybe_record(state: dict, cadences: dict | None, now: float | None = None) -> bool:
    """Start a background run when one is due; True if one was started.

    Due every EVERY_S, counted from the last start recorded in ``state``
    (so a restart does not run it again at once). A run still going is
    left alone; its failure is logged, never raised."""
    global _pending
    window_h = hours()
    if window_h == 0:
        return False
    now = time.time() if now is None else now
    book = state.setdefault("incident_history", {})
    if now - float(book.get("last_start") or 0) < EVERY_S:
        return False
    if _pending is not None and not _pending.done():
        return False
    book["last_start"] = now
    _pending = _pool.submit(_run, window_h, cadences)
    return True


def _run(window_h: int, cadences: dict | None) -> None:
    try:
        written = record(window_h, cadences)
        log.info("incident history: %d finished incident(s) in the last %d h written",
                 written, window_h)
    except Exception:  # noqa: BLE001 -- history must never cost the alerter
        log.warning("incident history run failed; next try in %d min",
                    EVERY_S // 60, exc_info=True)
