"""The entrypoints' status file, and what the healthcheck and `fleet status` read.

``python -m fleet.status`` prints it as JSON; ``--check`` exits 1 when the
loop that rewrites it stopped (what the Docker healthcheck runs). The
container's health is the process's: a node that is revoked, whose
certificate expired, or whose lead is away is measuring fine, and its
agent is alive; that is ``lead_link``'s to say, in `fleet status`.
The file holds only the fields :func:`snapshot` builds: never a secret.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from .layout import state_dir

#: The idle loop rewrites the file this often; the healthcheck allows three.
TICK_SECONDS = 60

#: The fields a status may carry, all non-secret.
FIELDS = ("role", "state", "detail", "node_id", "node_name", "site_name", "lead_mode",
          "lead_urls", "enrolled", "fleet_id", "problems", "started", "at", "protocol",
          # The agent's, once enrolled (fleet/agent.py):
          "lead_link", "site_id", "lead_in_use", "last_heartbeat", "next_heartbeat",
          "failures", "last_error", "cert_not_after", "renew_after", "revoked_at",
          "store_received")

#: ``lead_link``, the node's link to its lead: ``idle`` (not in a fleet),
#: ``ok`` (enrolled, and a lead answered recently or the agent just
#: started), ``failing`` (enrolled, and either no lead answered for a while
#: or a state waits for a person: revoked, certificate expired, stopped,
#: corrupt, another node's id). Shown by `fleet status`; the healthcheck
#: does not read it.
LEAD_LINK = ("idle", "ok", "failing")


def path(root: Path | None = None) -> Path:
    return (root or state_dir()) / "status.json"


def snapshot(**values) -> dict:
    unknown = set(values) - set(FIELDS)
    if unknown:
        raise KeyError(f"not a status field: {sorted(unknown)}")
    return dict(values)


def write(report: dict, root: Path | None = None) -> None:
    p = path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=1, sort_keys=True))
    os.replace(tmp, p)


def read(root: Path | None = None) -> dict:
    try:
        return json.loads(path(root).read_text())
    except (OSError, ValueError):
        return {}


def healthy(report: dict, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    at = report.get("at")
    return isinstance(at, (int, float)) and now - at <= 3 * TICK_SECONDS


def main(argv: list[str]) -> int:
    current = read()
    if "--check" in argv:
        return 0 if healthy(current) else 1
    print(json.dumps(current, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
