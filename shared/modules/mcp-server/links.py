"""Backwards-compatible alias for :mod:`common.links`.

The deep-link builders moved to ``shared/modules/common`` when the alerter
needed the same URLs for the links it puts in alerts. Keeping the UID maps
in one place is the point: a second copy would drift against Grafana's
provisioning and produce links that look right and 404.

This shim keeps ``import links`` working for the server and its tests.
"""

from __future__ import annotations

from common.links import (
    BACKEND_HINT,
    COMPARE_BY_DB_CATEGORY,
    CONFIG_HINT,
    DASHBOARD_BY_MEASUREMENT,
    DEFAULT_GRAFANA_PORT,
    DEFAULT_WEB_ADMIN_PORT,
    DETAIL_BY_MEASUREMENT,
    MEASUREMENT_BY_PROBE,
    dashboards_match_backend,
    entry_point_links,
    grafana_base,
    grafana_tunnel_base,
    grafana_url,
    has_tunnel_links,
    links_configured,
    measurement_for_probe,
    target_links,
    web_admin_base,
    web_admin_target_url,
    web_admin_tunnel_base,
)

__all__ = [
    "BACKEND_HINT",
    "COMPARE_BY_DB_CATEGORY",
    "CONFIG_HINT",
    "DASHBOARD_BY_MEASUREMENT",
    "DEFAULT_GRAFANA_PORT",
    "DEFAULT_WEB_ADMIN_PORT",
    "DETAIL_BY_MEASUREMENT",
    "MEASUREMENT_BY_PROBE",
    "dashboards_match_backend",
    "entry_point_links",
    "grafana_base",
    "grafana_tunnel_base",
    "grafana_url",
    "has_tunnel_links",
    "links_configured",
    "measurement_for_probe",
    "target_links",
    "web_admin_base",
    "web_admin_target_url",
    "web_admin_tunnel_base",
]
