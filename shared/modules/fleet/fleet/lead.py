"""The lead (profile ``fleet-lead``): ``python -m fleet.lead``.

This release only reads its settings, says whether a fleet is initialized
here, and idles: it does not listen on any port yet. The CA, the registry
and the HTTPS endpoints come in a later release (docs/fleet.md).
"""

from __future__ import annotations

import json
import sys

from . import runner, settings, status
from .layout import lead_paths


def describe(cfg: settings.Settings, paths=None) -> tuple[dict, str]:
    paths = paths or lead_paths()
    fleet: dict = {}
    if paths.fleet.is_file():
        try:
            fleet = json.loads(paths.fleet.read_text())
        except (OSError, ValueError):
            fleet = {}
    if fleet:
        state = "initialized"
        line = (f"fleet lead: fleet {fleet.get('fleet_id', '?')} is initialized here "
                f"(mode {cfg.lead_mode}); this release does not serve nodes yet. Idle.")
    else:
        state = "no_fleet"
        line = (f"fleet lead: no fleet initialized (mode {cfg.lead_mode}); "
                "nothing is served. Idle.")
    report = status.snapshot(
        role="lead", state=state, lead_mode=cfg.lead_mode, node_id=cfg.node_id or None,
        node_name=cfg.node_name, fleet_id=fleet.get("fleet_id"),
        lead_urls=list(cfg.lead_urls), problems=list(cfg.problems))
    return report, line


def main() -> int:
    runner.configure_logging()
    cfg = settings.load()
    report, line = describe(cfg)
    # A dedicated lead may have no node id of its own: not a problem here.
    runner.run(report, line, problems=tuple(p for p in cfg.problems
                                            if not p.startswith("FLEET_NODE_ID")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
