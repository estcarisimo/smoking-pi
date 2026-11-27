"""The node agent (profile ``fleet``): ``python -m fleet.agent``.

This release only reads its settings and identity, says whether the node is
enrolled, and idles. Enrollment, heartbeats and replication come in later
releases (docs/fleet.md, "What is not built yet"). Whatever it does, the
agent is never in the measurement path: if it stops, nothing else changes.
"""

from __future__ import annotations

import json
import sys

from . import runner, settings, status
from .layout import node_paths


def describe(cfg: settings.Settings, paths=None) -> tuple[dict, str]:
    """The status report and the one log line, from settings and the state dir."""
    paths = paths or node_paths()
    enrollment: dict = {}
    if paths.enrollment.is_file():
        try:
            enrollment = json.loads(paths.enrollment.read_text())
        except (OSError, ValueError):
            enrollment = {}
    if not cfg.has_identity:
        state = "no_identity"
        line = ("fleet agent: no node id (FLEET_NODE_ID is empty or invalid); "
                "run 'smoking-pi fleet status' on the host to create one. Idle.")
    elif enrollment:
        state = "enrolled"
        line = (f"fleet agent: node {cfg.node_id} ({cfg.node_name}, site {cfg.site_name}) "
                f"is enrolled in fleet {enrollment.get('fleet_id', '?')}; this release does "
                "not send heartbeats yet. Idle.")
    else:
        state = "not_enrolled"
        line = (f"fleet agent: node {cfg.node_id} ({cfg.node_name}, site {cfg.site_name}) "
                "is not enrolled; nothing is sent. Idle.")
    report = status.snapshot(
        role="agent", state=state, node_id=cfg.node_id or None, node_name=cfg.node_name,
        site_name=cfg.site_name, lead_urls=list(cfg.lead_urls),
        enrolled=state == "enrolled", fleet_id=enrollment.get("fleet_id"),
        problems=list(cfg.problems))
    return report, line


def main() -> int:
    runner.configure_logging()
    cfg = settings.load()
    report, line = describe(cfg)
    runner.run(report, line, problems=tuple(p for p in cfg.problems
                                            if not p.startswith("FLEET_NODE_ID")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
