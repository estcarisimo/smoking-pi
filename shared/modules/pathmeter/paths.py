"""From traces to paths: the shared prefix, the AS path, and what changed.

**The shared prefix.** From the reference house every destination's first
five hops are the same (the home router, then the access network). Tracing
them for every destination every hour says the same thing seventy times,
and every hour a burst of probes would reach the router at once. So each
cycle traces a few destinations from hop 1, in turn (main.FULL_PER_CYCLE),
learns the prefix from them (:func:`learn_prefix`), and traces every other
destination from the first hop past it. A trace from hop 1 that disagrees
with the prefix on a hop that answered, or ends inside it, means the access
path moved: the prefix is learned again from that cycle's traces.

**A change** is a destination whose path differs from the one it had on a
hop that answered both times (or whose distance changed while it answered
both times), in two traces in a row: one trace that differs is a
candidate, which the next either confirms or drops. That keeps a load
balancer that moved one trace, or a router answering from another of its
addresses once, from reading as a change. The AS path (the sequence of
origin ASes over the hops that answered) changing is flagged on its own:
it is the change that usually matters.

A destination whose address changed (its name resolved elsewhere, as CDN
names do) starts a new path: that is not a path change.
"""

from __future__ import annotations

import hashlib
import ipaddress

# A TTL is shared when at least this share of the paths reaching it got an
# answer there, all the same address.
PREFIX_MIN_ANSWERED = 0.5
# Silent TTLs in a row a prefix may span (the home router rate-limits).
MAX_SILENT_GAP = 1

def learn_prefix(paths: list[list]) -> list:
    """The hops the paths share from TTL 1.

    A TTL is shared when at least half of the paths that reach it got an
    answer there, and every answer is the same address. Silent hops make
    agreement cheap: one router that answered among twenty silent ones is
    not evidence that the twenty pass through it. A TTL answered by fewer
    than half may still sit inside the prefix as unknown (None), once in a
    row and only before a shared TTL: the home router rate-limits its
    replies, and a quiet first hop says nothing about the paths parting. A
    path that ends earlier (a destination inside the prefix, like the
    ISP's first hop) stops counting there. Empty when too few paths."""
    prefix: list = []
    gap = 0
    for ttl in range(max((len(p) for p in paths), default=0)):
        reaching = [p for p in paths if ttl < len(p)]
        answered = [p[ttl] for p in reaching if p[ttl]]
        if len(reaching) < 2 or len(set(answered)) > 1:
            break
        if answered and len(answered) >= PREFIX_MIN_ANSWERED * len(reaching):
            prefix += [None] * gap + [answered[0]]
            gap = 0
        elif gap < MAX_SILENT_GAP:
            gap += 1
        else:
            break
    return prefix


def agrees(path: list, prefix: list) -> bool:
    """Whether ``path`` matches ``prefix`` on every hop that answered."""
    return all(not path[i] or not prefix[i] or path[i] == prefix[i]
               for i in range(min(len(path), len(prefix))))


def joined(prefix: list, own: list, first_hop: int) -> list:
    """A trace started past the prefix, completed with it."""
    head = list(prefix[: first_hop - 1])
    head += [None] * (first_hop - 1 - len(head))
    return head + list(own[first_hop - 1:])


def is_public(addr: str | None) -> bool:
    try:
        return bool(addr) and ipaddress.ip_address(addr).is_global
    except ValueError:
        return False


def as_path(hops: list, asn_of) -> list[int]:
    """Origin ASes along the hops that answered, repeats collapsed. Private
    and unknown hops are skipped (they say nothing about who carried it)."""
    seq: list[int] = []
    for addr in hops:
        if not is_public(addr):
            continue
        asn = asn_of(addr)
        if asn and (not seq or seq[-1] != asn):
            seq.append(asn)
    return seq


def text(hops: list) -> str:
    return "|".join(h or "*" for h in hops)


def digest(hops: list) -> str:
    """A short name for the path, over the hops that answered."""
    return hashlib.sha256(text(hops).encode()).hexdigest()[:12]


def differs(a: dict, b: dict) -> bool:
    """Whether two traces of one destination disagree, judged only on the
    hops both measured themselves (from the later of their first hops): the
    shared prefix a trace borrowed is not its own measurement, and a prefix
    that moved must not read as this destination's path changing."""
    if a.get("distance") and b.get("distance") and a["distance"] != b["distance"]:
        return True
    start = max(a.get("own_from", 1), b.get("own_from", 1)) - 1
    ha, hb = a["hops"], b["hops"]
    return any(ha[i] and hb[i] and ha[i] != hb[i] for i in range(start, min(len(ha), len(hb))))


def merged(old: list, new: list) -> list:
    """``new`` with its silent hops filled from ``old``: a hop that did not
    answer this time keeps the address it answered with before."""
    out = list(new)
    for i in range(min(len(old), len(new))):
        if out[i] is None:
            out[i] = old[i]
    return out


def _as_changed(old: dict, new: dict, asn_of) -> bool:
    """Whether the AS path changed, over the hops both measured; never when
    a public hop's owner is not known yet (a pending lookup is not a change
    of network)."""
    start = max(old.get("own_from", 1), new.get("own_from", 1)) - 1
    a, b = old["hops"][start:], new["hops"][start:]
    if not all(asn_of(h) for h in a + b if is_public(h)):
        return False
    return as_path(a, asn_of) != as_path(b, asn_of)


def track(state: dict, dst: str, hops: list, distance: int | None, asn_of,
          own_from: int = 1) -> dict:
    """Update one destination's ``state`` with a new trace; returns
    ``{"changed", "as_changed", "dst_changed"}``.

    ``state`` holds ``stable`` (the path in use) and ``candidate`` (a path
    seen once that differs from it), each ``{"dst", "hops", "distance",
    "own_from"}``; ``own_from`` is the first hop the trace measured itself.
    """
    now = {"dst": dst, "hops": hops, "distance": distance, "own_from": own_from}
    stable = state.get("stable")
    result = {"changed": False, "as_changed": False, "dst_changed": False}
    if stable is None or stable.get("dst") != dst:
        result["dst_changed"] = stable is not None
        state["stable"], state["candidate"] = now, None
        return result
    if not differs(now, stable):
        state["candidate"] = None
        stable["hops"] = merged(stable["hops"], hops)
        stable["distance"] = distance or stable.get("distance")
        stable["own_from"] = min(stable.get("own_from", 1), own_from)
        return result
    candidate = state.get("candidate")
    if candidate and not differs(now, candidate):
        result["changed"] = True
        result["as_changed"] = _as_changed(stable, now, asn_of)
        state["stable"] = {**now, "hops": merged(candidate["hops"], hops),
                           "own_from": min(candidate.get("own_from", 1), own_from)}
        state["candidate"] = None
        return result
    state["candidate"] = now
    return result
