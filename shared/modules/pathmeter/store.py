"""Paths into InfluxDB, and what the module keeps between cycles.

Per destination (tag ``target``, its base name; tag ``category``), all
stamped at the cycle's start:

- ``path_trace``, one point per trace: ``dst`` (the address traced),
  ``hops`` (the destination's distance, or the last TTL probed when it did
  not answer), ``reached``, ``path`` (``addr|addr|*|...``, ``*`` a silent
  hop), ``path_hash``, ``as_path`` (``64500 64501 15169``), ``changed`` /
  ``as_changed`` / ``dst_changed`` (paths.py), ``first_hop`` (where this
  trace started; earlier hops are the shared prefix);
- ``path_hop``, one point per hop that answered, tagged ``ttl`` (two digits,
  so it sorts): ``addr``, ``asn`` when known with ``as_name`` (its CAIDA
  AS2Org name, ``HANDLE - Organization, CC``) when the table has one,
  ``ixp`` (the exchange point's
  name, from PeeringDB) when the address is on an exchange LAN instead,
  ``ptr`` (its reverse DNS name, names.py) when it has one, ``rtt_ms``. A router's reply
  time is its slow path's, rate-limited: descriptive, never a series to
  alert on.

And ``pathmeter_pass`` once per cycle (no target): ``traces``, ``probes``,
``seconds``, ``failed``, ``prefix_hops``, ``full_traces``, ``waiting``.

The rounds traced during a loss episode (episodes.py) write the same
points, tagged ``trigger=episode``, with ``episode`` (the episode's start)
on ``path_trace`` and ``capped`` (affected destinations left for a later
round) on ``pathmeter_pass``; their ``changed`` flags are always false.
Their ledger and turns are ``episodes.json`` in the state directory.

Addresses are fields, never tags: a tag per router would multiply series.

State (``PATHMETER_STATE_DIR``, default ``/var/lib/pathmeter``):
``state.json`` holds the prefix, when it was learned, and each
destination's stable and candidate path; ``ptr.json`` the routers' reverse
DNS names (names.py). Origin ASes come from the routing table in
``routing/`` (owners.py), never from a cache of their own.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

TRACE = "path_trace"
HOP = "path_hop"
PASS = "pathmeter_pass"


def state_dir() -> Path:
    return Path(os.environ.get("PATHMETER_STATE_DIR", "/var/lib/pathmeter"))


def load(name: str) -> dict:
    try:
        data = json.loads((state_dir() / name).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(name: str, data: dict) -> None:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / (name + ".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True))
    os.replace(tmp, directory / name)


def measurement_name(measurement: str) -> str:
    """The measurements, as literals: the doctor reads them from here to
    know what the dashboards may ask this module for."""
    if measurement == TRACE:
        return "path_trace"
    if measurement == HOP:
        return "path_hop"
    if measurement == PASS:
        return "pathmeter_pass"
    raise ValueError("unknown measurement")


def trace_points(base: str, category: str, at: int, dst: str, hops: list, rtts: list,
                 reached: bool, distance: int | None, first_hop: int, path_text: str,
                 path_hash: str, asns: list[int], flags: dict, asn_of, ixp_of=None,
                 ptr_of=None, trigger: str | None = None, episode: str | None = None,
                 name_of=None) -> list:
    from influxdb_client import Point, WritePrecision

    def tagged(point):
        # Episode rounds (episodes.py) only: the hourly series stay untagged.
        return point.tag("trigger", trigger) if trigger else point

    head = tagged(Point(measurement_name(TRACE)).tag("target", base).tag("category", category))
    if episode:
        head = head.field("episode", episode)
    points = [head
              .field("dst", dst)
              .field("hops", int(distance or len(hops)))
              .field("reached", bool(reached))
              .field("path", path_text)
              .field("path_hash", path_hash)
              .field("as_path", " ".join(str(a) for a in asns))
              .field("changed", bool(flags.get("changed")))
              .field("as_changed", bool(flags.get("as_changed")))
              .field("dst_changed", bool(flags.get("dst_changed")))
              .field("first_hop", int(first_hop))
              .time(at, WritePrecision.S)]
    for ttl, addr in enumerate(hops, start=1):
        if not addr:
            continue
        point = (tagged(Point(measurement_name(HOP)).tag("target", base)
                        .tag("category", category))
                 .tag("ttl", f"{ttl:02d}").field("addr", addr))
        asn = asn_of(addr)
        if asn:
            point = point.field("asn", int(asn))
            # Who the network is, as the table knew it when traced: a
            # dashboard names transit networks without a lookup of its own.
            if name_of and (org := name_of(int(asn))):
                point = point.field("as_name", org)
        # A router answering from its port on an exchange point: named by
        # the exchange (most exchange LANs are not announced, so the AS is
        # then missing; when one is, both are kept).
        if ixp_of and (exchange := ixp_of(addr)):
            point = point.field("ixp", exchange)
        # The router's reverse DNS name (names.py): a hint, not part of the path.
        if ptr_of and (name := ptr_of(addr)):
            point = point.field("ptr", name)
        rtt = rtts[ttl - 1] if ttl - 1 < len(rtts) else None
        if isinstance(rtt, (int, float)):
            point = point.field("rtt_ms", float(rtt))
        points.append(point.time(at, WritePrecision.S))
    return points


def pass_point(at: int, report: dict, trigger: str | None = None):
    from influxdb_client import Point, WritePrecision

    point = Point(measurement_name(PASS))
    if trigger:
        point = point.tag("trigger", trigger).field("capped", int(report.get("capped", 0)))
    return (point
            .field("traces", int(report.get("traces", 0)))
            .field("probes", int(report.get("probes", 0)))
            .field("seconds", float(report.get("seconds", 0.0)))
            .field("failed", int(report.get("failed", 0)))
            .field("prefix_hops", int(report.get("prefix_hops", 0)))
            .field("full_traces", int(report.get("full_traces", 0)))
            .field("waiting", int(report.get("waiting", 0)))
            .time(at, WritePrecision.S))
