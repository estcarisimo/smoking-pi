"""The meter's state: ``python status.py [--json]``; ``--check`` is the
container's healthcheck (0 while counting, or off on purpose)."""

from __future__ import annotations

import json
import os
import sys
import time

STALE_SECONDS = 120


def read(path: str, now: float | None = None) -> dict:
    try:
        with open(path) as f:
            body = json.load(f)
    except (OSError, ValueError):
        return {"state": "down", "reason": "no status yet"}
    if not isinstance(body, dict):
        return {"state": "down", "reason": "unreadable status"}
    try:
        updated = float(body.get("updated") or 0)
    except (TypeError, ValueError):
        updated = 0.0
    if (time.time() if now is None else now) - updated > STALE_SECONDS:
        body = dict(body, state="down", reason="the meter stopped writing its status")
    return body


def main(argv: list[str]) -> int:
    path = os.path.join(os.environ.get("NETMETER_STATE_DIR") or "/var/lib/netmeter",
                        "status.json")
    body = read(path)
    if "--check" in argv:
        return 0 if body.get("state") in ("counting", "starting", "off") else 1
    if "--json" in argv:
        json.dump(body, sys.stdout, indent=1, sort_keys=True)
        print()
        return 0
    reason = f"  ({body['reason']})" if body.get("reason") else ""
    print(f"state:    {body.get('state')}{reason}")
    print(f"uplinks:  {', '.join(body.get('uplinks') or []) or '-'}")
    print(f"services: {', '.join(body.get('services') or []) or '-'}")
    return 0 if body.get("state") == "counting" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
