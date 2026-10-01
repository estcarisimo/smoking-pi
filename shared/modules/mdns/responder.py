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

import random
from collections import deque
from dataclasses import dataclass

import wire

PROBE_INTERVAL = 0.25
PROBES = 3
ANNOUNCEMENTS = 2
ANNOUNCE_INTERVAL = 1.0
# §8.1: after 15 conflicts in 10 s, wait 5 s before probing again.
RATE_CONFLICTS = 15
RATE_WINDOW = 10.0
RATE_PAUSE = 5.0
# After this many names, stop and say so in the status; start again from
# the base name this much later (the hosts holding them may have left).
MAX_SUFFIX = 20
GIVE_UP_RETRY = 300.0
# §8.1: the first probe waits a random 0-250 ms, so hosts powered on
# together do not probe in lockstep.
PROBE_JITTER = 0.25
# §6: a record is multicast at most once a second; once per 250 ms when
# defending the name against a probe.
ANSWER_INTERVAL = 1.0
DEFEND_INTERVAL = 0.25
# §6.7: answers to a legacy (one-shot, not port 5353) query live <= 10 s.
LEGACY_TTL = 10
MDNS_PORT = 5353


@dataclass(frozen=True)
class Outgoing:
    """A message to send: multicast when ``to`` is None, else unicast."""
    message: wire.Message
    to: tuple[str, int] | None = None


class Responder:
    def __init__(self, base: str, addresses: list[str], jitter=None):
        self.base = base.strip().rstrip(".").lower()
        if self.base.endswith(".local"):
            self.base = self.base[: -len(".local")]
        self.suffix = 1
        self.addresses = sorted(set(addresses))
        self.state = "probing"
        self.conflicts = 0
        self._jitter = jitter or (lambda: random.uniform(0, PROBE_JITTER))
        self._recent_conflicts: deque[float] = deque()
        self._last_multicast: dict[int, float] = {}
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
        self._next = now + self._jitter()

    # --- timers ---------------------------------------------------------------

    def tick(self, now: float) -> list[Outgoing]:
        """What is due at ``now``: a probe, the claim, an announcement."""
        if now < self._next:
            return []
        if self.state == "gave_up":
            # Start over from the base name: the squatters may be gone.
            self.suffix = 1
            self._recent_conflicts.clear()
            self.start(now)
            return []
        if self.state == "probing":
            if not self.addresses:
                # No address yet (DHCP at boot): a probe would go nowhere,
                # and silence must not be mistaken for the name being free.
                self._probes_sent = 0
                self._next = now + 1.0
                return []
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
        return self._next if self.state in ("probing", "announcing", "gave_up") else None

    @property
    def answering(self) -> bool:
        """The name is ours: claimed, and announced at least once."""
        return self.state == "announced" or (self.state == "announcing" and self._announced > 0)

    # --- messages ---------------------------------------------------------------

    def records(self, ttl: int = wire.HOST_TTL, flush: bool = True) -> list[wire.Record]:
        return wire.address_records(self.name, self.addresses, ttl, flush)

    def probe(self) -> wire.Message:
        # §8.2: the proposed records go in the authority section, so two
        # hosts probing at once can tell who wins.
        # QU (unicast response) left clear: a unicast reply to port 5353
        # reaches only one of the sockets bound there, and the host's Avahi
        # may be the one that gets it. A multicast reply reaches all.
        return wire.Message(
            questions=[wire.Question(self.name, wire.TYPE_ANY)],
            authority=self.records(flush=False),
        )

    def announcement(self, ttl: int = wire.HOST_TTL) -> wire.Message:
        return wire.Message(
            flags=wire.FLAG_RESPONSE | wire.FLAG_AUTHORITATIVE,
            answers=self.records(ttl),
        )

    def goodbye(self) -> list[Outgoing]:
        """§10.1: TTL 0 tells caches to drop the name now, not in 120 s."""
        if not self.answering or not self.addresses:
            return []
        return [Outgoing(self.announcement(ttl=0))]

    # --- addresses ----------------------------------------------------------------

    def set_addresses(self, addresses: list[str], now: float) -> list[Outgoing]:
        """The host's addresses changed (DHCP, an interface came up, another
        network). §8: probe again, since a host on the new network may hold
        the name; the claim then announces cache-flush records that replace
        the old addresses in every cache."""
        new = sorted(set(addresses))
        if new == self.addresses:
            return []
        self.addresses = new
        if self.state != "gave_up":
            self.start(now)
        return []

    # --- incoming -------------------------------------------------------------------

    def handle(self, msg: wire.Message, source: tuple[str, int], own: set[str],
               now: float) -> list[Outgoing]:
        """React to one received message; return what to send back."""
        if source[0] in own:
            return []
        if msg.is_response:
            # §6: responses come from port 5353; anything else is not an
            # mDNS responder, and no reason to give up the name.
            if source[1] == MDNS_PORT and self._conflicts_with(msg):
                self._rename(now)
            return []
        if self._conflicts_with(msg):
            self._rename(now)
            return []
        if not self.answering:
            return []
        return self._answer(msg, source, now)

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
        self._recent_conflicts.append(now)
        while self._recent_conflicts and now - self._recent_conflicts[0] > RATE_WINDOW:
            self._recent_conflicts.popleft()
        if self.suffix >= MAX_SUFFIX:
            self.state = "gave_up"
            self._next = now + GIVE_UP_RETRY
            return
        self.suffix += 1
        self.start(now)
        if len(self._recent_conflicts) >= RATE_CONFLICTS:
            self._next = now + RATE_PAUSE

    def _answer(self, msg: wire.Message, source: tuple[str, int],
                 now: float) -> list[Outgoing]:
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
        if not wanted or not self.addresses:
            return []
        legacy = source[1] != MDNS_PORT
        ttl = LEGACY_TTL if legacy else wire.HOST_TTL
        records = [r for r in self.records(ttl, flush=not legacy) if r.rtype in wanted]
        present = {r.rtype for r in self.records()}
        # §6.1: say which of the asked types do not exist, so the asker
        # does not wait out a timeout for them.
        missing = (wanted & {wire.TYPE_A, wire.TYPE_AAAA}) - present
        if not legacy:
            # §7.1: the asker already holds these with at least half their
            # life left; repeating them is noise.
            known = {(r.rtype, r.rdata) for r in self._ours(msg.answers)
                     if r.ttl >= wire.HOST_TTL // 2}
            records = [r for r in records if (r.rtype, r.rdata) not in known]
            # §6: at most one multicast of a record a second (a quarter
            # second when defending against a probe).
            gap = DEFEND_INTERVAL if msg.authority else ANSWER_INTERVAL
            records = [r for r in records
                       if now - self._last_multicast.get(r.rtype, -gap) >= gap]
            for t in {r.rtype for r in records}:
                self._last_multicast[t] = now
            if missing and now - self._last_multicast.get(wire.TYPE_NSEC, -gap) < gap:
                missing = set()
            if missing:
                self._last_multicast[wire.TYPE_NSEC] = now
        nsec = [wire.nsec_record(self.name, present, ttl)] if missing else []
        if not records and not nsec:
            return []
        flags = wire.FLAG_RESPONSE | wire.FLAG_AUTHORITATIVE
        # The NSEC answers on its own when nothing else does, else it rides
        # along in the additional section.
        answers, additional = (records, nsec) if records else (nsec, [])
        if legacy:
            reply = wire.Message(
                id=msg.id, flags=flags,
                questions=[wire.Question(q.name, q.qtype) for q in asked],
                answers=answers, additional=additional,
            )
            return [Outgoing(reply, to=source)]
        return [Outgoing(wire.Message(flags=flags, answers=answers, additional=additional))]

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
