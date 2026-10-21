"""From traces to paths: the shared prefix, the AS path, and what changed.

**The shared prefix.** From the reference house every destination's first
five hops are the same (the home router, then the access network). Tracing
them for every destination every hour says the same thing seventy times, so
a full cycle learns the prefix (:func:`learn_prefix`) and the cycles after
it trace one destination in full (the reference) and every other one from
the first hop past the prefix. A reference that disagrees with the prefix
on a hop that answered means the access path moved: that cycle traces
everything in full again and learns it anew. A full cycle also runs once a
day whatever happens, so a destination whose path left the prefix earlier
than the others is noticed.

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

# A TTL is part of the shared prefix when every trace that got an answer
# there got the same address, and at least this share of traces got one.
# The home router answers about two traces in three (it rate-limits ICMP).
PREFIX_MIN_ANSWERED = 0.5
FULL_EVERY = 86400


def learn_prefix(paths: list[list]) -> list[str]:
    """The hops every path shares from TTL 1, ignoring silent hops; empty
    when there are too few paths to tell."""
    if len(paths) < 2:
        return []
    prefix = []
    for ttl in range(max(len(p) for p in paths)):
        answers = [p[ttl] for p in paths if ttl < len(p) and p[ttl]]
        if len(answers) < PREFIX_MIN_ANSWERED * len(paths) or len(set(answers)) != 1:
            break
        prefix.append(answers[0])
    return prefix


def agrees(path: list, prefix: list) -> bool:
    """Whether ``path`` matches ``prefix`` on every hop that answered."""
    return all(not path[i] or path[i] == prefix[i]
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


def differs(a: list, b: list, distance_a: int | None, distance_b: int | None) -> bool:
    if distance_a and distance_b and distance_a != distance_b:
        return True
    return any(a[i] and b[i] and a[i] != b[i] for i in range(min(len(a), len(b))))


def merged(old: list, new: list) -> list:
    """``new`` with its silent hops filled from ``old``: a hop that did not
    answer this time keeps the address it answered with before."""
    out = list(new)
    for i in range(min(len(old), len(new))):
        if out[i] is None:
            out[i] = old[i]
    return out


def track(state: dict, dst: str, hops: list, distance: int | None,
          asns: list[int]) -> dict:
    """Update one destination's ``state`` with a new trace; returns
    ``{"changed", "as_changed", "dst_changed"}``.

    ``state`` holds ``stable`` (the path in use) and ``candidate`` (a path
    seen once that differs from it), each ``{"dst", "hops", "distance",
    "as_path"}``.
    """
    now = {"dst": dst, "hops": hops, "distance": distance, "as_path": asns}
    stable = state.get("stable")
    result = {"changed": False, "as_changed": False, "dst_changed": False}
    if stable is None or stable.get("dst") != dst:
        result["dst_changed"] = stable is not None
        state["stable"], state["candidate"] = now, None
        return result
    if not differs(hops, stable["hops"], distance, stable.get("distance")):
        state["candidate"] = None
        stable["hops"] = merged(stable["hops"], hops)
        stable["distance"] = distance or stable.get("distance")
        if asns:
            stable["as_path"] = asns
        return result
    candidate = state.get("candidate")
    if candidate and not differs(hops, candidate["hops"], distance, candidate.get("distance")):
        result["changed"] = True
        result["as_changed"] = bool(asns) and asns != stable.get("as_path")
        state["stable"], state["candidate"] = {**now, "hops": merged(candidate["hops"], hops)}, None
        return result
    state["candidate"] = now
    return result
