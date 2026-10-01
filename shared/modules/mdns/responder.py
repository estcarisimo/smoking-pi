"""One host name on Multicast DNS: probe for it, claim it, answer for it.

RFC 6762 in the small: before using ``<base>.local`` the responder asks
three times, 250 ms apart, whether anyone holds it (§8.1). Silence means
it is ours: two announcements (§8.3), then an answer to every question for
it. An answer from another host with other addresses is a conflict (§9):
take the next name, ``<base>-2.local``, and probe again.

Two things keep a host from fighting itself, which is how Avahi on the
reference Pi ended up as ``smokingpi-2.local`` eight seconds after boot:

* packets from this host's own addresses are ignored (multicast loopback,
  or a Wi-Fi access point that echoes multicast back);
* records identical to ours are never a conflict (§9: "identical data").

Time is passed in, and messages are returned rather than sent, so the
whole protocol is tested without a network.
"""

from __future__ import annotations

from dataclasses import dataclass

import wire

PROBE_INTERVAL = 0.25
PROBES = 3
ANNOUNCEMENTS = 2
ANNOUNCE_INTERVAL = 1.0
# §8.1: after 15 conflicts in 10 s, wait 5 s between probes. Simpler and
# stricter: stop after this many names and say so in the status.
MAX_SUFFIX = 20
# §6.7: answers to a legacy (one-shot, not port 5353) query live <= 10 s.
LEGACY_TTL = 10
MDNS_PORT = 5353


@dataclass(frozen=True)
class Outgoing:
    """A message to send: multicast when ``to`` is None, else unicast."""
    message: wire.Message
    to: tuple[str, int] | None = None


class Responder:
    def __init__(self, base: str, addresses: list[str]):
        self.base = base.strip().rstrip(".").lower()
        if self.base.endswith(".local"):
            self.base = self.base[: -len(".local")]
        self.suffix = 1
        self.addresses = sorted(set(addresses))
        self.state = "probing"
        self.conflicts = 0
        self._probes_sent = 0
        self._announced = 0
        self._next = 0.0

    @property
    def name(self) -> str:
        label = self.base if self.suffix == 1 else f"{self.base}-{self.suffix}"
        return f"{label}.local"

    def start(self, now: float) -> None:
        self.state = "probing"
        self._probes_sent = 0
        self._announced = 0
        self._next = now

    # --- timers ---------------------------------------------------------------

    def tick(self, now: float) -> list[Outgoing]:
        """What is due at ``now``: a probe, the claim, an announcement."""
        if self.state in ("gave_up", "idle") or now < self._next:
            return []
        if self.state == "probing":
            if self._probes_sent < PROBES:
                self._probes_sent += 1
                self._next = now + PROBE_INTERVAL
                return [Outgoing(self.probe())]
            # The last probe's 250 ms passed in silence: the name is ours.
            self.state = "announcing"
        if self.state == "announcing":
            self._announced += 1
            if self._announced >= ANNOUNCEMENTS:
                self.state = "announced"
            self._next = now + ANNOUNCE_INTERVAL
            return [Outgoing(self.announcement())]
        return []

    def next_due(self) -> float | None:
        return self._next if self.state in ("probing", "announcing") else None

    # --- messages ---------------------------------------------------------------

    def records(self, ttl: int = wire.HOST_TTL, flush: bool = True) -> list[wire.Record]:
        return wire.address_records(self.name, self.addresses, ttl, flush)

    def probe(self) -> wire.Message:
        # §8.2: the proposed records go in the authority section, so two
        # hosts probing at once can tell who wins.
        return wire.Message(
            questions=[wire.Question(self.name, wire.TYPE_ANY, unicast=True)],
            authority=self.records(flush=False),
        )

    def announcement(self, ttl: int = wire.HOST_TTL) -> wire.Message:
        return wire.Message(
            flags=wire.FLAG_RESPONSE | wire.FLAG_AUTHORITATIVE,
            answers=self.records(ttl),
        )

    def goodbye(self) -> list[Outgoing]:
        """§10.1: TTL 0 tells caches to drop the name now, not in 120 s."""
        if self.state not in ("announcing", "announced") or not self.addresses:
            return []
        return [Outgoing(self.announcement(ttl=0))]

    # --- addresses ----------------------------------------------------------------

    def set_addresses(self, addresses: list[str], now: float) -> list[Outgoing]:
        """The host's addresses changed (DHCP, an interface came up)."""
        new = sorted(set(addresses))
        if new == self.addresses:
            return []
        self.addresses = new
        if self.state == "announced":
            # Cache-flush records replace the old set in every cache.
            self.state = "announcing"
            self._announced = 0
            self._next = now
        return []

    # --- incoming -------------------------------------------------------------------

    def handle(self, msg: wire.Message, source: tuple[str, int], own: set[str],
               now: float) -> list[Outgoing]:
        """React to one received message; return what to send back."""
        if source[0] in own:
            return []
        if self._conflicts_with(msg):
            self._rename(now)
            return []
        if msg.is_response or self.state != "announced":
            return []
        return self._answer(msg, source)

    def _ours(self, records: list[wire.Record]) -> list[wire.Record]:
        return [r for r in records
                if wire.same_name(r.name, self.name) and r.rtype in (wire.TYPE_A, wire.TYPE_AAAA)]

    def _conflicts_with(self, msg: wire.Message) -> bool:
        if self.state == "gave_up":
            return False
        if msg.is_response:
            mine = set(self.addresses)
            for r in self._ours(msg.answers + msg.additional):
                if r.ttl == 0:
                    continue  # a goodbye: the other host is letting it go
                if r.address() not in mine:
                    return True
            return False
        # A query: another host probing for the same name at the same time.
        if self.state != "probing":
            return False
        theirs = self._ours(msg.authority)
        if not theirs or not any(wire.same_name(q.name, self.name) for q in msg.questions):
            return False
        return _tiebreak_lost(self._ours(self.probe().authority), theirs)

    def _rename(self, now: float) -> None:
        self.conflicts += 1
        if self.suffix >= MAX_SUFFIX:
            self.state = "gave_up"
            return
        self.suffix += 1
        self.start(now)

    def _answer(self, msg: wire.Message, source: tuple[str, int]) -> list[Outgoing]:
        wanted: set[int] = set()
        asked: list[wire.Question] = []
        for q in msg.questions:
            if not wire.same_name(q.name, self.name):
                continue
            if q.qtype == wire.TYPE_ANY:
                wanted |= {wire.TYPE_A, wire.TYPE_AAAA}
            elif q.qtype in (wire.TYPE_A, wire.TYPE_AAAA):
                wanted.add(q.qtype)
            else:
                continue
            asked.append(q)
        if not wanted:
            return []
        legacy = source[1] != MDNS_PORT
        records = [r for r in self.records(LEGACY_TTL if legacy else wire.HOST_TTL,
                                           flush=not legacy)
                   if r.rtype in wanted]
        if not legacy:
            # §7.1: the asker already holds these with at least half their
            # life left; repeating them is noise.
            known = {(r.rtype, r.rdata) for r in self._ours(msg.answers)
                     if r.ttl >= wire.HOST_TTL // 2}
            records = [r for r in records if (r.rtype, r.rdata) not in known]
        if not records:
            return []
        if legacy:
            reply = wire.Message(
                id=msg.id, flags=wire.FLAG_RESPONSE | wire.FLAG_AUTHORITATIVE,
                questions=[wire.Question(q.name, q.qtype) for q in asked], answers=records,
            )
            return [Outgoing(reply, to=source)]
        return [Outgoing(wire.Message(
            flags=wire.FLAG_RESPONSE | wire.FLAG_AUTHORITATIVE, answers=records))]

    def status(self) -> dict:
        return {
            "name": self.name,
            "base": f"{self.base}.local",
            "state": self.state,
            "addresses": list(self.addresses),
            "conflicts": self.conflicts,
        }


def _tiebreak_lost(ours: list[wire.Record], theirs: list[wire.Record]) -> bool:
    """§8.2: compare the sorted record sets; the lexicographically later
    one wins. Identical sets are the same host heard twice: no conflict."""
    def key(records):
        return sorted((wire.CLASS_IN, r.rtype, r.rdata) for r in records)
    a, b = key(ours), key(theirs)
    return a != b and a < b
