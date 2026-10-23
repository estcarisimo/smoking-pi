"""Reverse DNS (PTR) names of the routers on the paths, cached in ``ptr.json``.

A router's name is the one its operator gave its address, and it often says
where the router is and what it is (``ae-1.r02.chcgil09.us.bb...`` is a
backbone router in Chicago). That makes it the cheapest hint of location
along a path. It is also only a name: operators let them go stale, and some
lie. So a name is shown next to its hop and never used to judge a path: it
is not part of the path text, its hash or its changes.

Public addresses only (a home router's or an access network's private name
says nothing). One query per address a week; an address with no name is
asked again after a day, a failed query after an hour; at most ``BUDGET``
new queries a cycle, eight at a time, two seconds each, so the house's
resolver never sees a burst.
"""

from __future__ import annotations

import ipaddress
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import store

log = logging.getLogger("pathmeter.names")

FILE = "ptr.json"
TTL = 7 * 86400          # a name found
NONE_TTL = 86400         # no name (NXDOMAIN, no PTR record)
FAILED_TTL = 3600        # the query failed (timeout, SERVFAIL)
BUDGET = 100             # new queries per cycle
PARALLEL = 8
TIMEOUT = 2.0


class Failed(Exception):
    """The query did not get an answer (as opposed to "no name")."""


def reverse(ip: str) -> str:
    """The PTR name of ``ip``, "" when it has none; raises Failed when the
    resolver did not answer."""
    import dns.exception
    import dns.resolver
    import dns.reversename

    resolver = dns.resolver.Resolver()
    resolver.lifetime = TIMEOUT
    try:
        answer = resolver.resolve(dns.reversename.from_address(ip), "PTR")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return ""
    except dns.exception.DNSException as exc:
        raise Failed(type(exc).__name__) from None
    return str(answer[0].target).rstrip(".") if len(answer) else ""


def _public(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.version == 4 and ip.is_global


class Names:
    def __init__(self, clock=time.time, resolve=reverse) -> None:
        self.clock, self._resolve = clock, resolve
        self.cache: dict = store.load(FILE)

    def _due(self, entry: dict | None, now: float) -> bool:
        if entry is None:
            return True
        # A failure is asked again soon even when a name is kept from before.
        ttl = FAILED_TTL if entry.get("failed") else TTL if entry.get("name") else NONE_TTL
        return now - entry.get("at", 0) >= ttl

    def refresh(self, addresses: set[str]) -> int:
        """Ask for the names that are missing or due, oldest first; returns
        how many were asked."""
        now = self.clock()
        todo = sorted((a for a in addresses if _public(a) and self._due(self.cache.get(a), now)),
                      key=lambda a: self.cache.get(a, {}).get("at", 0))[:BUDGET]
        if not todo:
            return 0

        def ask(addr: str) -> dict:
            try:
                return {"name": self._resolve(addr), "at": now}
            except Failed:
                previous = self.cache.get(addr) or {}
                # A failure keeps a name already known: one timeout must not
                # blank a router's name.
                return {**previous, "failed": True, "at": now} if previous.get("name") \
                    else {"failed": True, "at": now}
            except Exception:  # noqa: BLE001 -- one bad answer must not lose the others
                log.debug("PTR lookup failed for %s", addr, exc_info=True)
                return {"failed": True, "at": now}

        with ThreadPoolExecutor(PARALLEL) as pool:
            for addr, entry in zip(todo, pool.map(ask, todo)):
                self.cache[addr] = entry
        store.save(FILE, self.cache)
        return len(todo)

    def of(self, addr: str | None) -> str | None:
        entry = self.cache.get(addr or "")
        return entry.get("name") or None if entry else None

    def prune(self, keep: set[str]) -> None:
        """Forget addresses no longer on any path."""
        gone = [a for a in self.cache if a not in keep]
        for a in gone:
            del self.cache[a]
        if gone:
            store.save(FILE, self.cache)
