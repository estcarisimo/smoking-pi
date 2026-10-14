"""What ``diagnose_loss`` and ``get_incident_history`` answer, in one place.

The MCP server's tools shaped these answers on their own, so only an MCP
client could be told what an episode was; the web admin's chat read raw
loss events and attributed them itself. Both now call here: the same
incidents, classes, evidence and links whichever assistant asks.

``query`` is the caller's InfluxDB query function (each container has its
own client, and its tests mock it). Validation of the arguments, and how a
failed query is reported, stay with the caller.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from common import diagnosis_query, links
from common.tsdb import flux_str

Query = Callable[[str], list]

# A week of per-minute Wi-Fi rows, CPE windows and loss events is the most
# one answer should read; diagnose_loss refuses longer windows.
DIAGNOSE_MAX_HOURS = diagnosis_query.MAX_HOURS
DIAGNOSE_MAX_INCIDENTS = 50
HISTORY_MAX_DAYS = 365
HISTORY_MAX_INCIDENTS = 100


def _graph(targets: list[str], start: datetime) -> str | None:
    if not targets:
        return None
    return links.target_links(targets[0], measurement="latency", at=start).get("graph")


def diagnose(query: Query, hours: int, cadences: dict | None = None) -> dict:
    """Every loss incident in the last ``hours``, classified, newest first.
    Raises whatever ``query`` raises."""
    result = diagnosis_query.run(query, hours, cadences or {})
    incidents = result["incidents"][:DIAGNOSE_MAX_INCIDENTS]
    for inc in incidents:
        start = datetime.fromtimestamp(inc.pop("start_epoch"), tz=timezone.utc)
        end = datetime.fromtimestamp(inc.pop("end_epoch"), tz=timezone.utc)
        inc["start"], inc["end"] = start.isoformat(), end.isoformat()
        graph = _graph(inc["targets"], start)
        if graph:
            inc["graph"] = graph
    return {
        "window_hours": hours,
        "incidents": incidents,
        "truncated": len(result["incidents"]) > DIAGNOSE_MAX_INCIDENTS
        or result["rows_truncated"],
        "by_class": result["by_class"],
        "chronic": result["chronic"],
        "coverage": result["coverage"],
        "targets_reporting": result["targets_reporting"],
    }


def history(query: Query, bucket: str, days: int, include_probe_miss: bool = False) -> dict:
    """The stored incidents (``loss_incident``, written by the alerter) of
    the last ``days``, newest first. Raises whatever ``query`` raises."""
    flux = (f'from(bucket: {flux_str(bucket)}) |> range(start: -{int(days)}d) '
            '|> filter(fn: (r) => r._measurement == "loss_incident") '
            '|> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value") '
            '|> group() |> sort(columns: ["_time"], desc: true)')
    rows = query(flux)
    by_class: dict[str, int] = {}
    incidents = []
    for r in rows:
        cls = r.get("class") or "unclear"
        by_class[cls] = by_class.get(cls, 0) + 1
        if cls == "probe_miss" and not include_probe_miss:
            continue
        start = r.get("_time")
        if isinstance(start, datetime):
            start = start.astimezone(timezone.utc)
        end = (datetime.fromtimestamp(int(r["end_epoch"]), tz=timezone.utc)
               if r.get("end_epoch") is not None else None)
        targets = [t for t in (r.get("targets") or "").split(",") if t]
        inc = {
            "class": cls,
            "detail": r.get("detail") or None,
            "confidence": r.get("confidence") or None,
            "summary": r.get("summary") or "",
            "start": start.isoformat() if isinstance(start, datetime) else start,
            "end": end.isoformat() if end else None,
            "minutes": r.get("minutes"),
            "targets": targets,
            "targets_reporting": r.get("targets_reporting"),
            "evidence": [e for e in (r.get("evidence") or "").split(" | ") if e],
            "against": [e for e in (r.get("against") or "").split(" | ") if e],
        }
        # -1: a cut or deaf span with no loss run (incident_history.point).
        if r.get("max_loss_pct") is not None and r["max_loss_pct"] >= 0:
            inc["max_loss_pct"] = r["max_loss_pct"]
        if isinstance(start, datetime):
            graph = _graph(targets, start)
            if graph:
                inc["graph"] = graph
        incidents.append(inc)
    # The oldest incident stored at all, not only in this window: a quiet
    # month and an empty history must not look alike.
    first = query(
        f'from(bucket: {flux_str(bucket)}) |> range(start: 0) '
        '|> filter(fn: (r) => r._measurement == "loss_incident" and r._field == "class") '
        '|> group() |> first()')
    oldest = first[0].get("_time") if first else None
    return {
        "window_days": days,
        "stored_from": oldest.isoformat() if isinstance(oldest, datetime) else oldest,
        "incidents": incidents[:HISTORY_MAX_INCIDENTS],
        "truncated": len(incidents) > HISTORY_MAX_INCIDENTS,
        "by_class": by_class,
    }
