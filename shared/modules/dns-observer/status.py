"""Print the observer's state: ``python status.py [--json]``.

Also the container's healthcheck (``--check``): exit 0 while the status
file is fresh and AdGuard answers, 1 otherwise. Healthy means *serving*,
not *observing* -- a router that stopped forwarding is not a reason for
Docker to restart a working server.
"""

from __future__ import annotations

import json
import os
import sys
import time

import health

STATUS = os.path.join(os.environ.get("DNS_STATE_DIR", "/var/lib/dns-observer"), "status.json")


def _when(ts: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "never"


def main(argv: list[str]) -> int:
    status = health.read_status(STATUS)
    if "--check" in argv:
        answering = (status.get("server") or {}).get("answering", False)
        return 0 if status["state"] != "down" and answering else 1
    if "--json" in argv:
        json.dump(status, sys.stdout, indent=1, sort_keys=True)
        print()
        return 0
    print(f"state:          {status['state']}{'' if status.get('live') else '  (not live)'}")
    print(f"reason:         {status['reason']}")
    if status.get("fix"):
        print(f"fix:            {status['fix']}")
    print(f"observed until: {_when(status.get('observed_until'))}")
    canary = status.get("canary") or {}
    if canary.get("enabled"):
        print(
            f"canary:         via {canary['via']}, last seen {_when(canary.get('last_seen'))}, "
            f"{canary.get('seen_24h', 0)}/{canary.get('sent_24h', 0)} seen in 24 h"
        )
    elif canary:
        print("canary:         off")
    for d in (status.get("top_domains") or [])[:10]:
        print(f"  {d['queries']:>7}  {d['name']}")
    return 0 if status.get("live") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
