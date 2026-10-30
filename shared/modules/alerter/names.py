"""What a destination is called in a message: the service, not the target.

A target's name is the configuration's (``Ocean_Reef_Club_Airport_DFW_c567_2``,
``W_netflix_com_icmp``, ``Google_h2``); a person knows the service
(Netflix, Google). The taxonomy says which service each target is, and the
exporter writes it to InfluxDB as ``target_labels`` (field ``service``,
docs/taxonomy.md). This reads those labels, at most once an hour, and
names a target by its service, falling back to a readable form of its name.

Never a reason to lose a message: labels that cannot be read leave the
fallback names.
"""

from __future__ import annotations

import logging
import time

import evaluator
import flux
from common import links

log = logging.getLogger("alerter.names")

CACHE_S = 3600.0

# A category's service, for a target the taxonomy has not labeled: an
# OCA's name is its site (Ocean_Reef_Club_Airport_DFW_c567_2), not Netflix.
_CATEGORY_SERVICE = {"netflix": "Netflix", "netflix_oca": "Netflix"}

_cache: dict = {"at": None, "labels": {}}


def labels_flux() -> str:
    """The newest service label of every target."""
    return (f"from(bucket: {flux.flux_str(flux.influx_bucket())}) "
            "|> range(start: 0) "
            '|> filter(fn: (r) => r._measurement == "target_labels" '
            'and r._field == "service") |> group(columns: ["target"]) '
            '|> last() |> keep(columns: ["target", "_value"])')


def labels(now: float | None = None, query=None) -> dict[str, str]:
    """{target: service}, read at most once per CACHE_S; {} when unread."""
    now = time.time() if now is None else now
    if _cache["at"] is not None and 0 <= now - _cache["at"] < CACHE_S:
        return _cache["labels"]
    try:
        rows = (query or evaluator._query)(labels_flux())
        found = {str(r["target"]): str(r.get("_value") or "").strip()
                 for r in rows or () if r.get("target")}
        _cache["labels"] = {t: v for t, v in found.items() if v}
    except Exception:  # influx client raises many exception types
        log.warning("Service labels could not be read; using target names",
                    exc_info=True)
    _cache["at"] = now
    return _cache["labels"]


def display(target: str, category: str | None = None,
            known: dict[str, str] | None = None) -> str:
    """The service ``target`` measures, for a message.

    The taxonomy's label (any probe of the same site: Google_h2 is
    Google's); a wizard-adopted domain as the domain (W_netflix_com ->
    netflix.com); an unlabeled Netflix cache as Netflix; else the name with
    its probe suffix dropped and spaces for underscores."""
    known = known or {}
    base = links.target_base_name(target or "")
    for name in (target, base):
        if known.get(name):
            return known[name]
    if base.startswith("W_") and len(base) > 2:
        return base[2:].replace("_", ".")
    if category in _CATEGORY_SERVICE:
        return _CATEGORY_SERVICE[category]
    return base.replace("_", " ").strip() or "a destination"
