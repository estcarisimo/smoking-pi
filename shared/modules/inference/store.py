"""Results into InfluxDB, one point per period, kept once written.

``persistent_congestion`` (Jitterbug) and ``loss_degradation`` (BCP on
loss), tagged ``target``/``category``, timestamped at the period's start,
with the end in the ``end`` field (epoch seconds) so Grafana can shade the
region. A point is keyed by measurement, tags and start: a period written
again at the same start replaces its point, one written at another start
would be a second point for the same period.

Each pass corrects what earlier passes wrote while a period is comfortably
inside the window: it deletes the target's points from the window's
*settled edge* to now and writes what the detector found there, so a period
found again is replaced, a start moved by a bin or two is rewritten, and
one the detector no longer finds disappears. Before the settled edge,
nothing is deleted and nothing is written:

- the settled edge is ``GUARD`` after the window's start (``edge``), not
  the start itself. Near the start the detectors lose what they found:
  Jitterbug has no preceding segment left to see the jump against, and
  BCP reports a degradation whose start reaches the window's first bin as
  cut by the window (``truncated``). Deleting there erased every period as its
  start neared the window's start (the reference Pi, 2026-11-09: 12 of
  19 congestion periods and at least 8 degradation periods gone);
- a period the detector reports with its start before the settled edge
  is not written, so the record an earlier pass made while the period was
  comfortably inside stays as it was, with the end and numbers that pass
  saw;
- a period the detector now places before the settled edge, whose record
  starts after it (its start moved back across the edge), keeps that
  record: the settled edge moves to it;
- a period after the settled edge that overlaps a kept record is that
  record seen again with a shifted start, and is not written either.

So every period gets at most one point, and once its start passes the
settled edge it is never deleted. The records are read before anything is
deleted; if the read fails, nothing changes.

Delete-then-write is not atomic: if the write fails, the corrected part of
the window shows no period until the next pass an hour later. These are
experimental shades, not alerts, so that is accepted rather than worked
around.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from common.tsdb import flux_str

CONGESTION = "persistent_congestion"
DEGRADATION = "loss_degradation"
# One point per period of loss the whole connection shared (shared.py): no
# target tag, the period is the house's.
SHARED = "shared_degradation"
# Jitterbug on the aggregate stream (aggregate.py): one point per period the
# whole connection stayed congested; no target tag either.
SHARED_CONGESTION = "shared_congestion"
# One point per shared period the path meter's traces could be read for
# (segments.py): the path segment the destinations that lost share, as a
# hypothesis; no target tag either.
SHARED_SEGMENT = "shared_segment"
# One point per pass: how long, how many targets, how many failed.
PASS = "inference_pass"

HOUR = 3600
DAY = 86400
# How far after the window's start the settled edge lies, per measurement:
# what the detector reports before it is not trusted to correct, let alone
# erase, an earlier record. From an hourly replay of the module's own code
# over the reference Pi's raw pings (October to 2026-11-09), around the
# edge of every period the hourly passes erased: Jitterbug misses a
# congestion period on and off until its start is up to 56 hours past the
# window's start (uba_ar, 2026-10-15) and finds each one again beyond
# that, so three days keeps every one with a margin of 16 hours. BCP
# reports a degradation ``truncated`` once its start is in the window's
# first bin and misses one more (UBA, 2026-10-28) an hour in, so three
# hours keeps every one with a full pass to spare. Periods the replay lost
# further in (3 congestion, the degradations seen once or twice) were
# corrections, not the edge, and stay corrections.
GUARD = {
    CONGESTION: 3 * DAY,
    SHARED_CONGESTION: 3 * DAY,
    DEGRADATION: 3 * HOUR,
    # BCP on one series of the whole connection's loss: the same edge.
    SHARED: 3 * HOUR,
    # One hypothesis per shared period, at that period's start.
    SHARED_SEGMENT: 3 * HOUR,
}


def _client():
    from influxdb_client import InfluxDBClient

    return InfluxDBClient(
        url=os.environ.get("INFLUX_URL", "http://influxdb:8086"),
        token=os.environ.get("INFLUX_TOKEN", ""),
        org=os.environ.get("INFLUX_ORG", "smokeping"),
        timeout=60_000,
    )


def _bucket() -> str:
    return os.environ.get("INFLUX_BUCKET", "smokeping")


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _quoted(value: str) -> str:
    """A tag value inside a delete predicate. A quote or a backslash would
    change what the predicate deletes, so a target named with one is
    refused, never escaped."""
    if '"' in value or "\\" in value:
        raise ValueError("unexpected character in a tag value")
    return f'"{value}"'


def measurement_name(measurement: str) -> str:
    """One of the two measurements, as literals: the doctor reads them from
    here to know what the dashboards may ask this module for."""
    if measurement == CONGESTION:
        return "persistent_congestion"
    if measurement == DEGRADATION:
        return "loss_degradation"
    if measurement == SHARED:
        return "shared_degradation"
    if measurement == SHARED_CONGESTION:
        return "shared_congestion"
    if measurement == SHARED_SEGMENT:
        return "shared_segment"
    raise ValueError("unknown measurement")


def points(measurement: str, target: str, category: str, periods: list[dict],
           window_start: int) -> list:
    from influxdb_client import Point, WritePrecision

    name = measurement_name(measurement)
    out = []
    for p in periods:
        if p.get("truncated") or p["start"] <= window_start:
            continue
        pt = (Point(name).tag("target", target).tag("category", category)
              .time(int(p["start"]), WritePrecision.S)
              .field("end", int(p["end"]))
              .field("duration_s", int(p["end"] - p["start"])))
        for key in ("confidence", "jump_ms", "ks_p", "mean_loss_pct", "coverage", "deaf_share",
                    "deaf_loss_share"):
            if p.get(key) is not None:
                pt = pt.field(key, float(p[key]))
        # "loss" or "monitor_deaf" (deaf.py): a field, like everything else
        # here, so a relabeled period rewrites the same point.
        if p.get("class"):
            pt = pt.field("class", str(p["class"]))
        out.append(pt)
    return out


def pass_point(report: dict, now: int):
    """The ``inference_pass`` point of one pass: its duration, how many
    targets it covered and how many failed, and the periods it found."""
    from influxdb_client import Point, WritePrecision

    targets = report.get("targets") or {}
    congested = sum((t.get("congestion") or {}).get("periods", 0) for t in targets.values())
    degraded = sum((t.get("degradation") or {}).get("periods", 0) for t in targets.values())
    # The literal, not PASS: the doctor reads measurement names from here.
    return (Point("inference_pass").time(int(now), WritePrecision.S)
            .field("seconds", float(report.get("seconds") or 0.0))
            .field("targets", int(len(targets)))
            .field("errors", int(report.get("errors") or 0))
            .field("congestion_periods", int(congested))
            .field("degradation_periods", int(degraded))
            .field("shared_periods", int((report.get("shared") or {}).get("periods", 0)))
            .field("shared_congestion_periods",
                   int((report.get("shared_congestion") or {}).get("periods", 0))))


def write_pass(report: dict, now: int, client=None) -> None:
    """Record the pass. A failure here is logged by the caller and never
    fails the pass: the history is a convenience, the results are not."""
    own = client is None
    client = client or _client()
    try:
        from influxdb_client.client.write_api import SYNCHRONOUS

        client.write_api(write_options=SYNCHRONOUS).write(
            bucket=_bucket(), record=pass_point(report, now))
    finally:
        if own:
            client.close()


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Two [start, end) spans share time; adjacent ones do not."""
    return a[0] < b[1] and b[0] < a[1]


def settle(periods: list[dict], records: list[tuple[int, int]], window_start: int,
           edge: int, guard: int) -> tuple[int, list[dict]]:
    """Where this pass may correct, and what it writes there.

    ``records`` are the (start, end) of the points earlier passes wrote;
    ``edge`` is where the window would start without INFERENCE_SINCE (or an
    aggregate stream younger than the window): the settled edge is
    ``guard`` after it, and never before ``window_start``. A window pinned
    at its start date does not move toward its periods, so it corrects
    them all until it starts sliding.

    Returns ``(settled, write)``: records starting at or before ``settled``
    stay; the caller deletes the rest of the window, after ``settled``, and
    writes ``write``."""
    first = settled = max(window_start, edge + guard)
    spans = [(int(p["start"]), int(p["end"])) for p in periods]
    # A period the detector now starts before the edge, or reports cut by
    # the window, but whose record starts after it: keep the record. Moving
    # the edge can put another reported period before it, so repeat.
    while True:
        early = [s for p, s in zip(periods, spans) if p.get("truncated") or s[0] <= settled]
        moved = [r[0] for r in records
                 if r[0] > settled and any(_overlaps(r, s) for s in early)]
        if not moved:
            break
        settled = max(moved)
    kept = [r for r in records if r[0] <= settled]
    write = [p for p, s in zip(periods, spans)
             if not p.get("truncated") and s[0] > first
             and not any(_overlaps(r, s) for r in kept)]
    return settled, write


def _records(client, measurement: str, target: str | None, start: int, now: int) -> list:
    """(start, end) of ``measurement``'s points since ``start``: what earlier
    passes wrote, for :func:`settle`."""
    who = f" and r.target == {flux_str(target)}" if target is not None else ""
    flux = (f"from(bucket: {flux_str(_bucket())}) "
            f"|> range(start: {int(start)}, stop: {int(now) + 1}) "
            f"|> filter(fn: (r) => r._measurement == {flux_str(measurement)}{who}"
            ' and r._field == "end") '
            '|> keep(columns: ["_time", "_value"])')
    out = []
    for table in client.query_api().query(flux):
        for record in table.records:
            when, end = record.values.get("_time"), record.values.get("_value")
            if when is not None and end is not None:
                out.append((int(when.timestamp()), int(end)))
    return sorted(out)


def _settle_and_write(client, measurement: str, target: str | None, periods: list[dict],
                      window_start: int, now: int, edge: int | None, to_points) -> int:
    """Read the records, delete after the settled edge, write the rest."""
    edge = window_start if edge is None else int(edge)
    # A point can be no longer than the window it was found in, so records
    # that could overlap anything after the settled edge start after this.
    since = min(window_start, edge) - (now - edge)
    records = _records(client, measurement, target, since, now)
    settled, write = settle(periods, records, window_start, edge, GUARD[measurement])
    if settled < now:
        who = f" AND target={_quoted(target)}" if target is not None else ""
        client.delete_api().delete(
            _iso(settled + 1), _iso(now), f"_measurement={_quoted(measurement)}{who}",
            bucket=_bucket(), org=os.environ.get("INFLUX_ORG", "smokeping"),
        )
    batch = to_points(write)
    if batch:
        from influxdb_client.client.write_api import SYNCHRONOUS

        client.write_api(write_options=SYNCHRONOUS).write(bucket=_bucket(), record=batch)
    return len(batch)


def replace(measurement: str, target: str, category: str, periods: list[dict],
            window_start: int, now: int, client=None, edge: int | None = None) -> int:
    """Correct ``measurement``'s points for ``target`` after the window's
    settled edge (:func:`settle`) with ``periods``; earlier points stay.
    Returns how many points were written."""
    own = client is None
    client = client or _client()
    try:
        name = measurement_name(measurement)
        return _settle_and_write(
            client, name, target, periods, window_start, now, edge,
            lambda keep: points(measurement, target, category, keep, window_start))
    finally:
        if own:
            client.close()


SHARED_NUMBERS = ("mean_loss_pct", "coverage", "lossy_operator_share", "dominant_share")
SHARED_COUNTS = ("voters", "operators")
SHARED_TEXT = ("scope", "kinds", "dominant", "by_operator", "by_kind", "diverged")
CONGESTION_NUMBERS = ("confidence", "jump_ms", "ks_p", "coverage")
SEGMENT_NUMBERS = ("mean_loss_pct", "coverage", "recall", "spill", "p_value")
SEGMENT_COUNTS = ("affected", "unaffected", "mild", "traced", "untraced", "common_hops",
                  "clean_common_hops", "path_changed", "affected_through",
                  "unaffected_through")
SEGMENT_TEXT = ("confidence", "segment", "segment_kind", "hypothesis", "alternatives",
                "common_as", "clean_common_as")
FIELDS = {
    SHARED: (SHARED_NUMBERS, SHARED_COUNTS, SHARED_TEXT),
    SHARED_CONGESTION: (CONGESTION_NUMBERS, SHARED_COUNTS, SHARED_TEXT),
    SHARED_SEGMENT: (SEGMENT_NUMBERS, SEGMENT_COUNTS, SEGMENT_TEXT),
}


def shared_points(periods: list[dict], window_start: int,
                  measurement: str = SHARED) -> list:
    """``shared_degradation`` (or ``shared_congestion``, ``shared_segment``)
    points; a period cut by the window is skipped, as in :func:`points`."""
    from influxdb_client import Point, WritePrecision

    numbers, counts, texts = FIELDS[measurement]
    out = []
    for p in periods:
        if p.get("truncated") or p["start"] <= window_start:
            continue
        pt = (Point(measurement_name(measurement)).time(int(p["start"]), WritePrecision.S)
              .field("end", int(p["end"])).field("duration_s", int(p["end"] - p["start"])))
        for key in numbers:
            if p.get(key) is not None:
                pt = pt.field(key, float(p[key]))
        for key in counts:
            if p.get(key) is not None:
                pt = pt.field(key, int(p[key]))
        for key in texts:
            if p.get(key) is not None:
                pt = pt.field(key, str(p[key]))
        out.append(pt)
    return out


def replace_shared(periods: list[dict], window_start: int, now: int, client=None,
                   measurement: str = SHARED, edge: int | None = None) -> int:
    """Correct the window's ``shared_degradation`` (or ``shared_congestion``,
    ``shared_segment``) points after its settled edge with ``periods``, as
    :func:`replace` does."""
    own = client is None
    client = client or _client()
    try:
        return _settle_and_write(
            client, measurement_name(measurement), None, periods, window_start, now, edge,
            lambda keep: shared_points(keep, window_start, measurement))
    finally:
        if own:
            client.close()
