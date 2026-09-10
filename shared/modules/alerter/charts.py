"""Backwards-compatible alias for :mod:`common.charts`.

The renderer moved to ``shared/modules/common`` when the MCP server needed
it too (``get_chart``). This shim keeps ``import charts`` working for
main.py, digest.py and their tests, so the move touched no call site.
"""

from __future__ import annotations

from common.charts import (  # noqa: F401 - re-exported
    DEFAULT_MAX_BYTES,
    DPI,
    LIGHT,
    MAX_PEERS,
    STATUS,
    SURFACE,
    _fetch,
    _loss_pct,
    _save,
    _theme,
    render_digest_chart,
    render_incident_chart,
    render_target_chart,
)

__all__ = [
    "DEFAULT_MAX_BYTES",
    "DPI",
    "LIGHT",
    "MAX_PEERS",
    "STATUS",
    "SURFACE",
    "render_digest_chart",
    "render_incident_chart",
    "render_target_chart",
]
