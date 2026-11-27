"""The node agent (profile ``fleet``): ``python -m fleet.agent``.

This release only reads its settings and identity, says whether the node is
enrolled, and idles. Enrollment, heartbeats and replication come in later
releases (docs/fleet.md, "What is not built yet"). Whatever it does, the
agent is never in the measurement path: if it stops, nothing else changes.
"""

from __future__ import annotations

import sys

from . import runner, settings, statefile, status
from .layout import node_paths


#: The agent's states. Only ``not_enrolled`` may lead to enrolling (PR C):
#: ``state_corrupt`` and ``identity_mismatch`` wait for a person.
STATES = ("no_identity", "not_enrolled", "enrolled", "state_corrupt", "identity_mismatch")


def describe(cfg: settings.Settings, paths=None) -> tuple[dict, str]:
    """The status report and the one log line, from settings and the state dir."""
    paths = paths or node_paths()
    read = statefile.read_object(paths.enrollment)
    enrollment: dict = read if isinstance(read, dict) else {}
    problems = list(cfg.problems)
    present = read != statefile.ABSENT
    if read == statefile.CORRUPT or (
            present and not settings.valid_node_id(str(enrollment.get("node_id", "")))):
        state = "state_corrupt"
        problems.append("enrollment.json is unreadable, not a JSON object, or has no "
                        "valid node_id")
        line = ("fleet agent: the enrollment file is corrupt; nothing is sent, and the "
                "node will not enroll over it. Idle.")
        enrollment = {}
    elif present and enrollment["node_id"] != cfg.node_id:
        state = "identity_mismatch"
        problems.append("enrollment.json was made for another node id than FLEET_NODE_ID")
        line = (f"fleet agent: enrolled as node {enrollment['node_id']}, but FLEET_NODE_ID "
                f"is {cfg.node_id or 'empty'} (an older env file restored beside this "
                "agent's volume?); nothing is sent. Idle.")
    elif not cfg.has_identity:
        state = "no_identity"
        line = ("fleet agent: no node id (FLEET_NODE_ID is empty or invalid); "
                "run 'smoking-pi fleet status' on the host to create one. Idle.")
    elif present:
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
        enrolled=state == "enrolled",
        fleet_id=enrollment.get("fleet_id") if state == "enrolled" else None,
        problems=problems)
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
