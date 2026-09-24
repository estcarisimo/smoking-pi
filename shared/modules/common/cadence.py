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

from .tsdb import base_flux, flux_str

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

# A point is a loss EVENT when it lost more than one ping's worth. One lost
# ping per cycle is the Wi-Fi hop's background on the reference host (60-300
# such points a day); two or more is something. Counted in pings, not in a
# percent, because a percent means a different count on every probe: 15%
# is "2 of 10" on FPing but "3 of 20", and "1 of 5" clears it on DNS.
#
# 1.5, not 2: the RRD normalizes each probe cycle onto aligned steps, so
# one cycle's lost pings are split between two rows in proportion to the
# offset (seven days on the reference Pi: loss values at every percent
# from 1 to 13, not at multiples of 10). A threshold of 2 would miss two
# lost pings split 1.2 + 0.8; 1.5 keeps a single lost ping out whole and
# is exactly the old 15% on a 10-ping probe.
EVENT_LOST_PINGS = 1.5
# A ping count above this is a corrupt point, not a probe: SmokePing's own
# limit is far lower, and the API refuses more than 100.
MAX_PINGS = 1000


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
        if number > 0 and not (field == "pings" and number > MAX_PINGS):
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


def lost_pings(ratio: float, pings: int) -> float:
    """Pings' worth lost in a point: its loss ratio times its denominator.
    Fractional by construction (see EVENT_LOST_PINGS)."""
    return min(1.0, max(0.0, float(ratio))) * pings


def is_event(ratio: float, pings: int, min_lost: float = EVENT_LOST_PINGS) -> bool:
    """Whether a point lost at least ``min_lost`` pings' worth."""
    return lost_pings(ratio, pings) >= min_lost


def event_ratio(pings: int, min_lost: float = EVENT_LOST_PINGS) -> float:
    """The loss ratio at which a ``pings``-ping point becomes an event."""
    return min(1.0, min_lost / pings) if pings > 0 else 1.0


def flux_float(value: float) -> str:
    """A float as a Flux literal: fixed-point, never ``1e-05`` -- Flux has no
    exponent syntax -- and always with a decimal point, since ``1`` is an
    int in Flux and comparing it with a float ``_value`` is a type error."""
    text = f"{float(value):.9f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def event_threshold_flux(
    cadences: dict[str, Cadence], min_lost: float = EVENT_LOST_PINGS
) -> tuple[str, str]:
    """Flux for "this point is an event", per target: ``(prelude, expr)``.

    ``prelude`` goes FIRST in the query (Flux imports must lead the script);
    ``expr`` is a float expression of ``r`` to compare ``r._value`` with.
    Targets not in ``cadences`` use the default ping count. A target name
    that is not a safe Flux string is left to the default rather than
    breaking the query.
    """
    default = event_ratio(DEFAULT_PINGS, min_lost)
    pairs = []
    for target, own in sorted(cadences.items()):
        ratio = event_ratio(own.pings, min_lost)
        if ratio == default:
            continue
        try:
            pairs.append(f"{{key: {flux_str(target)}, value: {flux_float(ratio)}}}")
        except ValueError:
            continue
    if not pairs:
        return "", flux_float(default)
    prelude = (
        'import "dict"\n'
        f"event_ratio = dict.fromList(pairs: [{', '.join(pairs)}])\n"
    )
    return prelude, (
        f"dict.get(dict: event_ratio, key: r.target, default: {flux_float(default)})"
    )

