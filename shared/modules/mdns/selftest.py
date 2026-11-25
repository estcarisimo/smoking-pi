"""Does this responder still hear the LAN's multicast?

``healthy`` used to mean "the loop is writing its status", which a deaf
responder does perfectly well: after the Wi-Fi driver reloaded (2026-11-15)
the container stayed "healthy" for nine days while no one on the LAN could
resolve ``smoking-pi.local``.

Once a minute the responder multicasts a query for a name nobody holds,
from its own socket, and expects to receive it back (multicast loopback is
on). The kernel hands it back only through a live group membership on the
interface it went out of, which is the path a real query takes in. A
packet that never returns means the membership went stale. What this
cannot see is a radio that drops multicast before the kernel does.
"""

from __future__ import annotations

import secrets

import wire

INTERVAL = 60.0
# The loopback is immediate; this is slack for a busy loop, not for the air.
WAIT = 5.0
# Misses before the status says "deaf" (and the healthcheck with it), before
# the socket is rebuilt in-process, and before the process gives up and
# lets Docker restart it.
DEAF_AFTER = 3
REBUILD_AFTER = 3
EXIT_AFTER = 6


class SelfTest:
    def __init__(self) -> None:
        self.misses = 0
        self.token = ""
        self._sent_at: float | None = None
        self._heard = False
        self._next = 0.0
        self._rebuilt = False
        # A host whose loopback never returns a packet is not deaf, the test
        # just does not work there: say nothing and do nothing.
        self.ever_heard = False

    @property
    def deaf(self) -> bool:
        return self.ever_heard and self.misses >= DEAF_AFTER

    def should_rebuild(self) -> bool:
        """True once per run of misses, at the threshold."""
        if not self.ever_heard or self._rebuilt or self.misses < REBUILD_AFTER:
            return False
        self._rebuilt = True
        return True

    @property
    def give_up(self) -> bool:
        return self.ever_heard and self.misses >= EXIT_AFTER

    def due(self, now: float) -> bool:
        return self._sent_at is None and now >= self._next

    def query(self, now: float) -> bytes:
        """The packet to multicast on every interface."""
        self.token = "selftest-" + secrets.token_hex(4)
        self._sent_at, self._heard = now, False
        msg = wire.Message(id=0, flags=0,
                           questions=[wire.Question(f"{self.token}.local", wire.TYPE_A)])
        return wire.build(msg)

    def observe(self, msg: wire.Message) -> bool:
        """Whether this packet is our own query coming back (and note it)."""
        if self._sent_at is None or msg.is_response:
            return False
        if any(wire.same_name(q.name, f"{self.token}.local") for q in msg.questions):
            self._heard = True
            return True
        return False

    def settle(self, now: float) -> None:
        """Call every pass of the loop: close the test once it was heard or
        the wait is over."""
        if self._sent_at is None:
            return
        if self._heard:
            self.misses = 0
            self._rebuilt = False
            self.ever_heard = True
        elif now - self._sent_at >= WAIT:
            self.misses += 1
        else:
            return
        self._sent_at = None
        self._next = now + INTERVAL

    def reset(self) -> None:
        """The responder is not announcing (probing again, a name conflict):
        there is nothing to hear yet, and a stale verdict must not outlive it."""
        self.misses = 0
        self._sent_at = None
        self._rebuilt = False

    def status(self) -> dict:
        return {"selftest_misses": self.misses}
