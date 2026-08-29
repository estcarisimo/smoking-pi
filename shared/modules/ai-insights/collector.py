"""Backwards-compatible alias for :mod:`common.aggregates`.

The aggregate queries moved to ``shared/modules/common`` when the alerter's
scheduled digest needed the same numbers. This shim keeps ``import
collector`` working for the reporter and its tests.
"""

from __future__ import annotations

from common.aggregates import (
    LOSS_EVENT_PCT,
    MAX_TARGETS,
    MAX_WORST_WINDOWS,
    collect,
    flux_str,
    influx_bucket,
    query_influx,
)

__all__ = [
    "LOSS_EVENT_PCT",
    "MAX_TARGETS",
    "MAX_WORST_WINDOWS",
    "collect",
    "flux_str",
    "influx_bucket",
    "query_influx",
]
