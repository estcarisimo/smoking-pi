"""``python -m fleet.leave [--force]``: take this node out of its fleet.

Run by ``smoking-pi fleet leave`` inside the fleet image, after the command
has stopped the agent (so the two never write the same files). Best effort
toward the lead: ``POST /fleet/leave`` over mTLS to each lead in order; if
none answers, nothing is removed unless ``--force`` (the lead then keeps
listing the node until its operator revokes it with ``smoking-pi fleet
revoke`` on the lead). Then every file of the enrollment is removed: key,
chain, root, store token, ``enrollment.json``. The node's measurements live
elsewhere and are never touched.

A revoked node is not announced (the lead already has it out). A corrupt
enrollment, another node's, or an expired certificate cannot speak to the
lead: they are cleared with ``--force``.

Output: sentences on stderr, ``{"left": bool, "told_lead": bool}`` on
stdout. Exit 0 left (or nothing to leave), 1 refused.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from . import nodestate, protocol, settings
from .agent import Agent, assess
from .layout import NodePaths, node_paths
from .transport import HttpsTransport, Transport


def leave(cfg: settings.Settings, paths: NodePaths, transport: Transport, force: bool,
          say) -> tuple[int, dict]:
    local = assess(cfg, paths, datetime.now(timezone.utc))
    if local.state in ("no_identity", "not_enrolled"):
        nodestate.remove_all(paths)  # leftovers of an interrupted join, if any
        say("This node is not in a fleet: nothing to leave.")
        return 0, {"left": True, "told_lead": False}
    if local.state == "revoked":
        nodestate.remove_all(paths)
        say("This node had been revoked; its enrollment is removed here.")
        return 0, {"left": True, "told_lead": False}
    if local.state != "enrolled":
        why = {"state_corrupt": "its enrollment or credentials are corrupt",
               "identity_mismatch": "the enrollment here belongs to another node id",
               "cert_expired": "its certificate has expired"}.get(local.state, local.state)
        if not force:
            say(f"This node cannot tell the lead it leaves: {why}. Nothing was removed. "
                "'smoking-pi fleet leave --force' removes the enrollment here anyway; the "
                "lead keeps listing the node until its operator revokes it ('smoking-pi "
                "fleet revoke' on the lead).")
            return 1, {"left": False, "told_lead": False}
        nodestate.remove_all(paths)
        say(f"Enrollment removed here ({why}); the lead was not told.")
        return 0, {"left": True, "told_lead": False}

    agent = Agent(cfg, paths, transport)
    agent.local = local
    # drained/remaining: this release does not replicate, so there is no
    # queue to wait for (docs/fleet.md, "What is not built yet").
    message = protocol.LeaveRequest(
        protocol=protocol.PROTOCOL_MAX, protocol_min=protocol.PROTOCOL_MIN,
        node_id=cfg.node_id, instance_id=agent.instance_id, drained=True, remaining_bytes=0)
    out = agent.exchange("/fleet/leave", message, protocol.LeaveResponse)
    told = out.kind in ("ok", "revoked")
    if not told and not force:
        what = (f"the lead answered '{out.code}'" if out.code else "no lead answered")
        say(f"The node could not tell the lead it leaves ({what}). Nothing was removed. "
            "Try again, or 'smoking-pi fleet leave --force' to leave anyway: the lead then "
            "keeps listing this node until its operator revokes it ('smoking-pi fleet "
            "revoke' on the lead).")
        return 1, {"left": False, "told_lead": False}
    nodestate.remove_all(paths)
    fleet = local.enrollment.get("fleet_id", "?")
    if told:
        say(f"Left fleet {fleet}: the lead was told, and the key and certificate are removed.")
    else:
        say(f"Left fleet {fleet} without telling the lead (--force): the key and certificate "
            "are removed here; the lead keeps listing this node until its operator revokes "
            "it ('smoking-pi fleet revoke' on the lead).")
    return 0, {"left": True, "told_lead": told}


def main(argv: list[str] | None = None, *, stdout=None, stderr=None,
         transport: Transport | None = None, paths: NodePaths | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    stdout, stderr = stdout or sys.stdout, stderr or sys.stderr
    force = "--force" in argv

    def say(text: str) -> None:
        print(text, file=stderr)

    try:
        code, result = leave(settings.load(), paths or node_paths(),
                             transport or HttpsTransport(), force, say)
    except OSError as err:
        say(f"Leaving failed: the enrollment files could not be removed "
            f"({type(err).__name__}).")
        code, result = 1, {"left": False, "told_lead": False}
    print(json.dumps(result), file=stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
