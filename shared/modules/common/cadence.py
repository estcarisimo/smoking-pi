"""Each target's probe cycle: seconds between points, pings per point.

SmokePing's step and ping count are set per probe, and nothing forces them
to be 300 s and 10. Every rule that counts points or measures a span in
cycles -- the alerter's down window, its mean, a widespread step, the MCP
server's episodes -- used to assume those two numbers. On a 600 s probe the
1200 s down window holds two points where three are required, and
``target_down`` could never fire.

The exporter now writes ``step`` and ``pings`` as fields on every
``latency``/``dns_latency`` point (rrd2influx, CADENCE FIELDS), and this
module reads the latest of each per target. A target with neither -- points
written by an older exporter, or an RRD whose spacing could not be read --
gets :data:`DEFAULT`, which is what every probe shipped with.
"""

from __future__ import annotations

from datetime import datetime
from typing import NamedTuple

from .tsdb import base_flux

DEFAULT_STEP = 300
DEFAULT_PINGS = 10
# Far enough back to find a point from every active target, including one
# on a slow step or one that has been down (a down target still writes
# rows: loss is 100%, not missing).
LOOKBACK = "-6h"


class Cadence(NamedTuple):
    step: int
    pings: int


DEFAULT = Cadence(DEFAULT_STEP, DEFAULT_PINGS)


def cadence_flux(measurements: tuple[str, ...] = ("latency", "dns_latency")) -> str:
    """The newest ``step`` and ``pings`` per target."""
    return (
        base_flux(list(measurements), LOOKBACK)
        + '|> filter(fn: (r) => r._field == "step" or r._field == "pings") '
        + '|> group(columns: ["target", "_field"]) '
        + "|> last() "
        + '|> keep(columns: ["target", "_field", "_value"])'
    )


def by_target(rows: list[dict]) -> dict[str, Cadence]:
    """Fold :func:`cadence_flux` rows into ``{target: Cadence}``.

    A missing or non-positive value falls back field by field, so a target
    whose step could not be read still gets its real ping count.
    """
    seen: dict[str, dict[str, int]] = {}
    for row in rows:
        target, field, value = row.get("target"), row.get("_field"), row.get("_value")
        if target is None or field not in ("step", "pings") or value is None:
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            seen.setdefault(target, {})[field] = number
    return {
        target: Cadence(
            fields.get("step", DEFAULT_STEP), fields.get("pings", DEFAULT_PINGS)
        )
        for target, fields in seen.items()
    }


def of(cadences: dict[str, Cadence], target: str | None) -> Cadence:
    """A target's cadence, or :data:`DEFAULT` when it is not known."""
    if target is None:
        return DEFAULT
    return cadences.get(target, DEFAULT)


def longest_step(cadences: dict[str, Cadence]) -> int:
    """The slowest step among ``cadences`` (at least the default).

    Windows that must hold N points of every target are sized from this.
    Never below the default, so an installation reading no cadence at all
    queries exactly what it always did.
    """
    return max([DEFAULT_STEP, *(c.step for c in cadences.values())])


def epoch(value: object) -> float | None:
    """A row's ``_time`` as epoch seconds, or None when unparseable.

    The Influx client hands back datetimes; a test or a CSV hands back ISO
    strings.
    """
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None
