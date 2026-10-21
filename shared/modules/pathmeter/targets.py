"""What to trace: the destinations measured now, one per target base name.

Target Detail shows one target over every layer by its base name
(``Google``, ``Google_tcp443`` and ``Google_h2`` are all ``Google``,
``common.links.target_base_name``), so a path is traced and stored per base
name too, from the host behind it (the ``target_host`` measurement the
exporter writes). Only names measured in the last two hours count: a target
that was removed keeps its last ``target_host`` point forever.

Names are resolved every cycle (CDN names move), and destinations that land
on the same address are traced once.
"""

from __future__ import annotations

from common.links import target_base_name
from common.tsdb import flux_str, influx_bucket, query_influx

MEASUREMENTS = ("latency", "dns_latency", "http_latency", "tcp_latency")
ACTIVE_HOURS = 2


def hosts_flux() -> str:
    return (f"from(bucket: {flux_str(influx_bucket())}) |> range(start: 0) "
            '|> filter(fn: (r) => r._measurement == "target_host" and r._field == "host") '
            '|> last() |> keep(columns: ["target", "category", "_value"])')


def active_flux() -> str:
    names = " or ".join(f'r._measurement == "{m}"' for m in MEASUREMENTS)
    return ('import "influxdata/influxdb/schema"\n'
            f"schema.tagValues(bucket: {flux_str(influx_bucket())}, tag: \"target\", "
            f"predicate: (r) => {names}, start: -{ACTIVE_HOURS}h)")


def host_of(value: str) -> str:
    """A Targets ``host`` as a name: a URL keeps only its host part."""
    value = value.strip()
    if "://" in value:
        value = value.split("://", 1)[1]
    return value.split("/", 1)[0].split(":", 1)[0] if value.count(":") <= 1 else value


def wanted(query=query_influx) -> dict[str, tuple[str, str]]:
    """base name -> (category, host), for the targets measured now. When
    names of one base point at different hosts, the ICMP one (the bare
    name) wins, else the first in order."""
    active = {row.get("_value") for row in query(active_flux())}
    found: dict[str, tuple[str, str]] = {}
    for row in sorted(query(hosts_flux()), key=lambda r: str(r.get("target"))):
        name, host = row.get("target"), row.get("_value")
        if not name or not host or name not in active:
            continue
        base = target_base_name(name)
        if base not in found or name == base:
            found[base] = (str(row.get("category") or ""), host_of(str(host)))
    return found
