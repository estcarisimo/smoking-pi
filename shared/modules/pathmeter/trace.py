"""Paris traceroute with scamper: the command, and its JSON read into paths.

ICMP-Paris (``trace -P icmp-paris``): every probe to one destination keeps
the same flow identifier, so a load balancer on the way sends them all down
one path and the hops read as one path, not a mix of several. Measured from
the reference house on 2026-10-21 over 76 destinations, it reached 60; UDP
Paris reached 34 and TCP to port 443 reached 7 (docs/path.md).

One probe per hop (``-q 1``), one second to wait for each (``-w 1``), and
the trace gives up after five silent hops in a row (``-g 5``). scamper
paces every probe of a run together at ``PATHMETER_PPS`` packets per second,
so a run of many destinations never bursts.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field

SCAMPER = "scamper"
METHOD = "icmp-paris"
GAP_LIMIT = 5
# Bytes on the wire per probe, both directions: scamper's 44-byte ICMP echo
# and a reply of about the same size (an echo reply is 44, a TTL-exceeded
# quotes the probe and is 56-72). An unanswered probe costs only its half.
BYTES_PER_PROBE = 110


@dataclass
class Trace:
    """One destination's trace. ``hops[i]`` is the address that answered
    TTL ``i + 1`` (None when nothing did), ``rtts`` the same in ms."""

    dst: str
    reached: bool
    first_hop: int
    probes: int
    hops: list = field(default_factory=list)
    rtts: list = field(default_factory=list)
    stop: str = ""

    @property
    def distance(self) -> int | None:
        """The destination's TTL, when it answered."""
        if not self.reached:
            return None
        for i in range(len(self.hops) - 1, -1, -1):
            if self.hops[i] == self.dst:
                return i + 1
        return None


def command(pps: int, first_hop: int, list_path: str) -> list[str]:
    """scamper over the addresses in ``list_path`` (one per line)."""
    trace = f"trace -P {METHOD} -q 1 -w 1 -g {GAP_LIMIT}"
    if first_hop > 1:
        trace += f" -f {int(first_hop)}"
    return [SCAMPER, "-p", str(int(pps)), "-O", "json", "-c", trace, "-f", list_path]


def parse(text: str) -> list[Trace]:
    """scamper's JSON output, one object per line, into traces. Lines that
    are not a trace (the cycle markers) or do not parse are skipped."""
    out = []
    for line in text.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "trace" or not obj.get("dst"):
            continue
        replies = {}
        for hop in obj.get("hops") or []:
            ttl = hop.get("probe_ttl")
            if isinstance(ttl, int) and ttl > 0 and ttl not in replies and hop.get("addr"):
                replies[ttl] = (hop["addr"], hop.get("rtt"))
        last = max([obj.get("hop_count") or 0, *replies.keys()])
        out.append(Trace(
            dst=obj["dst"],
            reached=obj.get("stop_reason") == "COMPLETED",
            first_hop=int(obj.get("firsthop") or 1),
            probes=int(obj.get("probe_count") or 0),
            hops=[replies[t][0] if t in replies else None for t in range(1, last + 1)],
            rtts=[replies[t][1] if t in replies else None for t in range(1, last + 1)],
            stop=str(obj.get("stop_reason") or ""),
        ))
    return out


def run(addresses: list[str], pps: int, first_hop: int = 1, timeout: int = 1800,
        runner=subprocess.run) -> list[Trace]:
    """Trace every address, starting at ``first_hop``. Raises when scamper
    fails or times out; the caller counts it as a failed pass."""
    if not addresses:
        return []
    with tempfile.NamedTemporaryFile("w", suffix=".txt") as listing:
        listing.write("\n".join(addresses) + "\n")
        listing.flush()
        done = runner(command(pps, first_hop, listing.name), capture_output=True,
                      text=True, timeout=timeout, check=True)
    return parse(done.stdout)
