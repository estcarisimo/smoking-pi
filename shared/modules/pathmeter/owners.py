"""Origin AS of the routers on the paths, cached per /24 in ``asn.json``.

Team Cymru answers per prefix (``common.asn.origin``), so one lookup covers
every router in a /24. Entries are kept a week, a failed lookup an hour;
at most ``BUDGET`` new lookups per cycle, the oldest first, so a first
cycle over hundreds of routers spreads over a few hours instead of a burst
of DNS queries. Until a lookup succeeds the hop has no AS and the AS path
skips it.
"""

from __future__ import annotations

import ipaddress
import time

from common import asn as lookup

import store

TTL = 7 * 86400
FAILED_TTL = 3600
BUDGET = 64
FILE = "asn.json"


def _prefix(ip: str) -> str:
    return ".".join(ip.split(".")[:3])


class Owners:
    def __init__(self, clock=time.time, origin_fn=lookup.origin) -> None:
        self.clock, self._origin = clock, origin_fn
        self.cache: dict = store.load(FILE)

    def _old(self, entry: dict | None, now: float) -> bool:
        if entry is None:
            return True
        return now - entry.get("at", 0) > (FAILED_TTL if entry.get("failed") else TTL)

    def refresh(self, addresses: set[str]) -> int:
        """Look up the /24s missing or stale; returns how many were asked."""
        now = self.clock()
        prefixes = {}
        for addr in addresses:
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            if ip.version == 4 and ip.is_global:
                prefixes.setdefault(_prefix(addr), addr)
        todo = sorted((p for p in prefixes if self._old(self.cache.get(p), now)),
                      key=lambda p: self.cache.get(p, {}).get("at", 0))[:BUDGET]
        for prefix in todo:
            try:
                found = self._origin(prefixes[prefix])
            except Exception:  # noqa: BLE001 -- one failed lookup must not stop the others
                found = None
            self.cache[prefix] = ({"asn": found[0], "name": found[1], "at": now}
                                  if found else {"failed": True, "at": now})
        if todo:
            store.save(FILE, self.cache)
        return len(todo)

    def of(self, addr: str | None) -> int | None:
        if not addr or "." not in addr:
            return None
        entry = self.cache.get(_prefix(addr))
        return entry.get("asn") if entry and not entry.get("failed") else None
