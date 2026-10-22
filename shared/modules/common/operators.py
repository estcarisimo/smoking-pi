"""Who delivers each target: its operator, kind and origin AS, cached.

The lookups are :mod:`common.asn`; this caches them per target, for the
modules that label targets (inference's shared detector, the exporter's
``target_labels``):

1. the host's address and CNAME chain, from DNS (the chain is what tells
   CloudFront from the rest of Amazon); an address is its own answer;
2. the address's origin AS and its name, from Team Cymru, per /24;
3. the operator and its kind from :func:`common.asn.classify`.

Addresses are kept a day, owners a week, a failed lookup an hour, so a
host that never resolves cannot hold the budget forever; at most
``BUDGET`` new lookups of each kind per pass, the oldest first. A lookup
that fails where an earlier one succeeded keeps the earlier answer and is
asked again in an hour: one DNS timeout must not relabel a target.

An address that is not global (the gateway, a host on the LAN) is
``local``, with no lookup; a host with no IPv4 address (IPv6-only, or one
that does not resolve) is ``unknown``. Neither is an error.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from common.asn import LOCAL, UNKNOWN, classify, origin, resolve

log = logging.getLogger("common.operators")

ADDRESS_TTL = 86400
FAILED_TTL = 3600
OWNER_TTL = 7 * 86400
BUDGET = 64  # new lookups per pass; the rest wait for the next one


def host_of(value: str | None) -> str:
    """The host in a target's ``host``: a URL's hostname, else the value."""
    value = (value or "").strip()
    if "://" in value:
        return urlsplit(value).hostname or ""
    return value


def _ipv4(text: str | None) -> ipaddress.IPv4Address | None:
    try:
        ip = ipaddress.ip_address(text or "")
    except ValueError:
        return None
    return ip if ip.version == 4 else None


class Operators:
    """Operator, kind and ASN per target, cached in a JSON file."""

    def __init__(self, path: Path, clock=time.time,
                 resolve_fn=resolve, origin_fn=origin) -> None:
        self.path = Path(path)
        self.clock = clock
        self._resolve, self._origin = resolve_fn, origin_fn
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            raw = {}
        self.addresses: dict[str, dict] = raw.get("addresses", {})
        self.owners: dict[str, dict] = raw.get("owners", {})

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"addresses": self.addresses, "owners": self.owners}))
            os.replace(tmp, self.path)
        except OSError:
            log.warning("could not save %s", self.path, exc_info=True)

    @staticmethod
    def _prefix(ip: str) -> str:
        return ".".join(ip.split(".")[:3])

    def refresh(self, hosts: dict[str, str]) -> None:
        """Look up what is missing or stale, at most BUDGET of each kind.
        ``hosts`` maps target -> host."""
        now = self.clock()

        def old(entry: dict | None, ttl: int) -> bool:
            if entry is None:
                return True
            return now - entry.get("at", now) > (FAILED_TTL if entry.get("failed") else ttl)

        def safe(fn):
            def call(arg):
                try:
                    return fn(arg)
                except Exception:  # noqa: BLE001 -- one bad lookup must not lose the others
                    log.debug("lookup failed for %s", arg, exc_info=True)
                    return None
            return call

        def failed(previous: dict | None, ttl: int) -> dict:
            # Keep a good answer through a failed re-lookup; ask again in an hour.
            if previous and not previous.get("failed"):
                return {**previous, "at": now - ttl + FAILED_TTL}
            return {"failed": True, "at": now}

        wanted = {h for h in (host_of(v) for v in hosts.values()) if h}
        stale = [h for h in sorted(wanted, key=lambda h: self.addresses.get(h, {}).get("at", 0))
                 if old(self.addresses.get(h), ADDRESS_TTL)][:BUDGET]
        if stale:
            with ThreadPoolExecutor(8) as pool:
                for host, found in zip(stale, pool.map(safe(self._resolve), stale)):
                    self.addresses[host] = (
                        {"ip": found[0], "chain": found[1], "at": now} if found
                        else failed(self.addresses.get(host), ADDRESS_TTL))
        ips = set()
        for host, address in self.addresses.items():
            ip = _ipv4(address.get("ip"))
            if host in wanted and ip is not None and ip.is_global:
                ips.add(str(ip))
        todo = sorted({self._prefix(ip): ip for ip in ips
                       if old(self.owners.get(self._prefix(ip)), OWNER_TTL)}.items(),
                      key=lambda kv: self.owners.get(kv[0], {}).get("at", 0))[:BUDGET]
        if todo:
            with ThreadPoolExecutor(8) as pool:
                for (prefix, _ip), found in zip(todo, pool.map(safe(self._origin),
                                                               [ip for _, ip in todo])):
                    self.owners[prefix] = (
                        {"asn": found[0], "name": found[1], "at": now} if found
                        else failed(self.owners.get(prefix), OWNER_TTL))
        if stale or todo:
            self.save()

    def owner(self, target: str, hosts: dict[str, str]) -> tuple[str, str, int]:
        """(operator, kind, ASN) of a target: ``unknown`` and 0 until its
        lookups succeed, ``local`` and 0 for an address that is not global."""
        host = host_of(hosts.get(target))
        address = self.addresses.get(host)
        ip = _ipv4((address or {}).get("ip"))
        if ip is None:
            return UNKNOWN, UNKNOWN, 0
        if not ip.is_global:
            return "local network", LOCAL, 0
        found = self.owners.get(self._prefix(str(ip)))
        if not found or not found.get("asn"):
            return UNKNOWN, UNKNOWN, 0
        operator, kind = classify(found["asn"], found.get("name", ""), host,
                                  address.get("chain", []))
        return operator, kind, int(found["asn"])

    def of(self, target: str, hosts: dict[str, str]) -> tuple[str, str]:
        """(operator, kind) of a target; ``unknown`` until its lookups succeed."""
        return self.owner(target, hosts)[:2]
