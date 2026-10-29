"""Results into InfluxDB, one point per period, replaced on every run.

``persistent_congestion`` (Jitterbug) and ``loss_degradation`` (BCP on
loss), tagged ``target``/``category``, timestamped at the period's start,
with the end in the ``end`` field (epoch seconds) so Grafana can shade the
region. Each run deletes its window's points for the target before writing
the new ones: a period found again is replaced, not duplicated, and one the
detector no longer finds disappears. Points before the window are kept.

A period that begins at the window's first bin was cut by the window: its
real start is earlier and an earlier run recorded it. It is not written, so
the earlier record stays, with the end that run saw.

Delete-then-write is not atomic: if the write fails, the window shows no
period until the next pass an hour later. These are experimental shades,
not alerts, so that is accepted rather than worked around.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

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
        for key in ("confidence", "jump_ms", "ks_p", "mean_loss_pct", "coverage", "deaf_share"):
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


def replace(measurement: str, target: str, category: str, periods: list[dict],
            window_start: int, now: int, client=None) -> int:
    """Delete ``measurement``'s points for ``target`` in [window_start, now]
    and write ``periods``. Returns how many points were written."""
    own = client is None
    client = client or _client()
    try:
        client.delete_api().delete(
            _iso(window_start), _iso(now),
            f"_measurement={_quoted(measurement)} AND target={_quoted(target)}",
            bucket=_bucket(), org=os.environ.get("INFLUX_ORG", "smokeping"),
        )
        batch = points(measurement, target, category, periods, window_start)
        if batch:
            from influxdb_client.client.write_api import SYNCHRONOUS

            client.write_api(write_options=SYNCHRONOUS).write(bucket=_bucket(), record=batch)
        return len(batch)
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
                   measurement: str = SHARED) -> int:
    """Delete the window's ``shared_degradation`` (or ``shared_congestion``,
    ``shared_segment``) points and write ``periods``."""
    own = client is None
    client = client or _client()
    try:
        client.delete_api().delete(
            _iso(window_start), _iso(now), f"_measurement={_quoted(measurement_name(measurement))}",
            bucket=_bucket(), org=os.environ.get("INFLUX_ORG", "smokeping"),
        )
        batch = shared_points(periods, window_start, measurement)
        if batch:
            from influxdb_client.client.write_api import SYNCHRONOUS

            client.write_api(write_options=SYNCHRONOUS).write(bucket=_bucket(), record=batch)
        return len(batch)
    finally:
        if own:
            client.close()
