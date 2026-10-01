"""Print the name the responder holds: ``python status.py [--json]``.

Also the container's healthcheck (``--check``): exit 0 while the status is
fresh and the name is answered (or the responder is off on purpose), 1
otherwise. ``smoking-pi url`` prints the name from ``--json``.
"""

from __future__ import annotations

import json
import os
import sys
import time

import main as mdns

# Written every 15 s; three missed writes is a stuck loop.
STALE_SECONDS = 60


def read(path: str, now: float | None = None) -> dict:
    try:
        with open(path) as f:
            body = json.load(f)
    except (OSError, ValueError):
        return {"state": "down", "reason": "no status yet"}
    if not isinstance(body, dict):
        return {"state": "down", "reason": "unreadable status"}
    now = time.time() if now is None else now
    if now - float(body.get("updated") or 0) > STALE_SECONDS:
        body = dict(body, state="down", reason="the responder stopped writing its status")
    return body


def healthy(body: dict) -> bool:
    return body.get("state") in ("announced", "announcing", "probing", "off")


def main(argv: list[str]) -> int:
    body = read(mdns.status_path(dict(os.environ)))
    if "--check" in argv:
        return 0 if healthy(body) else 1
    if "--json" in argv:
        json.dump(body, sys.stdout, indent=1, sort_keys=True)
        print()
        return 0
    state = body.get("state")
    if state == "off":
        print("mDNS name: off (MDNS_NAME=off)")
        return 0
    print(f"name:       {body.get('name', '-')}")
    print(f"state:      {state}" + (f"  ({body['reason']})" if body.get("reason") else ""))
    if body.get("name") and body.get("base") and body["name"] != body["base"]:
        print(f"note:       {body['base']} is held by another host on this network")
    print(f"addresses:  {', '.join(body.get('addresses') or []) or '-'}")
    print(f"interfaces: {', '.join(body.get('interfaces') or []) or '-'}")
    return 0 if state == "announced" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
