"""scamper output and InfluxDB rows, built by hand. Addresses: 192.168.1.1
is the home router, 23.0.0.x the access network, 34.0.0.x and on beyond."""

import json

import trace

ROUTER = "192.168.1.1"
ACCESS = ["23.0.0.1", "23.0.0.2", "23.0.0.3", "23.0.0.4"]
PREFIX = [ROUTER, *ACCESS]


def trace_json(dst: str, hops: list, first_hop: int = 1, reached: bool = True) -> str:
    """One scamper trace object: ``hops[i]`` answers TTL ``first_hop + i``
    (None: silent)."""
    entries = [{"addr": addr, "probe_ttl": first_hop + i, "rtt": 1.0 + i}
               for i, addr in enumerate(hops) if addr]
    return json.dumps({
        "type": "trace", "dst": dst, "firsthop": first_hop,
        "stop_reason": "COMPLETED" if reached else "GAPLIMIT",
        "hop_count": first_hop + len(hops) - 1, "probe_count": len(hops),
        "hops": entries,
    })


class Network:
    """A fake scamper run: destination -> its full path (TTL 1 first)."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls = []

    def __call__(self, dsts, rate, first_hop=1):
        self.calls.append((sorted(dsts), first_hop))
        lines = ['{"type":"cycle-start"}']
        for dst in dsts:
            full = self.routes[dst]
            lines.append(trace_json(dst, full[first_hop - 1:], first_hop,
                                    reached=full[-1] == dst))
        return trace.parse("\n".join(lines))


def query_for(targets: dict):
    """A fake query_influx: ``targets`` maps a target name to its host;
    every one of them is active."""
    def query(flux):
        if "schema.tagValues" in flux:
            return [{"_value": name} for name in targets]
        return [{"target": name, "category": "custom", "_value": host}
                for name, host in targets.items()]
    return query
