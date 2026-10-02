"""The counter-only nftables table that attributes uplink bytes to services.

One table, ``inet smoking_pi_meter``, three base chains, nothing but
counters and a conntrack mark. No rule drops, accepts or rewrites a
packet: every chain's policy is accept and every rule ends in ``return``
or falls through, so the host's own firewall (Docker's, Tailscale's,
anything else's) decides exactly what it decided before.

Only the uplink interfaces count (``oifname``/``iifname`` in the uplink
set). Traffic between containers, to the loopback or over Docker's
bridges never touches the uplink and is not counted.

* **Host-network services** (SmokePing, the DNS observer, the alerter, the
  MCP server, mdns) share the host's network namespace, so their packets
  are told apart by the cgroup of the socket that sends them
  (``socket cgroupv2``). The first outgoing packet of a connection also
  stamps the connection (one byte of ``ct mark``), so its replies,
  ICMP echo replies included, count for the same service on the way in.
  A connection that starts inbound (a LAN client asking the DNS observer)
  is matched by the receiving socket's cgroup.
* **Bridged containers** (Grafana, the web admin, ai-insights...) reach
  the uplink through the forward hook, by their own address.
* **Internet only**: the same uplink, without the traffic whose other end
  is on the local network (private, link-local and multicast addresses:
  LOCAL_V4/LOCAL_V6). Counted as totals (``internet_tx``/``_rx``, and
  ``fwd_internet_*`` for bridged containers), not per service. That is
  what an ISP's data cap sees; the difference is the LAN: you opening
  Grafana, the DNS observer answering the house, the router's pings.
* What is left: ``other_containers`` (forwarded traffic of containers this
  stack does not name, such as a Cloudflare tunnel started by hand) and
  ``host`` (everything else on the uplink: apt, an assistant, sshd), as
  the totals minus the named services.

Pure: builds text and names; nft.py runs it.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

TABLE = "smoking_pi_meter"
FAMILY = "inet"
# The ct mark byte this table owns: bits 24-30. Tailscale uses 0xff0000
# for its fwmarks; Kubernetes 0x4000/0x8000. The other bits are kept.
MARK_MASK = 0x7F000000
MARK_SHIFT = 24
MAX_MARKED = 0x7F
PRIORITY = -150
# Marks are fixed per service, never positional: a conntrack entry keeps
# its mark for as long as the connection lives (hours, for TCP), so a
# service appearing or leaving must not renumber the others.
FIXED_MARKS = {"smokeping": 1, "dns-observer": 2, "alerter": 3, "mcp-server": 4,
               "mdns": 5, "netmeter": 6}
HASHED_MARKS = range(16, MAX_MARKED)  # for a host-network service not named above
# The other end of a packet on the local network, not the Internet: the
# private ranges (RFC 1918, RFC 4193 ULA), link-local, multicast, the
# IPv4 broadcast and the unspecified addresses (a DHCP discover, IPv6
# duplicate address detection). Static, so an address change never reloads the table. A
# LAN numbered from public space (a global IPv6 prefix on the LAN) counts
# as Internet; carrier-grade NAT space (100.64/10) is the ISP's, so it does.
LOCAL_V4 = ("0.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
            "169.254.0.0/16", "224.0.0.0/4", "255.255.255.255")
LOCAL_V6 = ("::", "fc00::/7", "fe80::/10", "ff00::/8")


@dataclass(frozen=True)
class Service:
    """One container of the stack, as config-manager's /meter/containers
    describes it."""
    name: str
    host_network: bool
    cgroup: str | None = None          # e.g. system.slice/docker-<id>.scope
    cgroup_level: int = 2
    ipv4: tuple[str, ...] = ()
    ipv6: tuple[str, ...] = ()


def counter_name(service: str) -> str:
    """A service name as an nft object name: lowercase, [a-z0-9_]."""
    return "s_" + re.sub(r"[^a-z0-9_]", "_", service.lower())


def mark_of(service: str) -> int:
    """The service's mark byte: fixed for the stack's own services, else
    derived from its name, so it is the same on every reload."""
    if service in FIXED_MARKS:
        return FIXED_MARKS[service]
    import zlib
    return HASHED_MARKS[zlib.crc32(service.encode()) % len(HASHED_MARKS)]


def _quoted(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_./:@-]+", value):
        raise ValueError(f"refusing to quote {value!r} into a ruleset")
    return f'"{value}"'


def _ifset(names: list[str]) -> str:
    return "{ " + ", ".join(_quoted(n) for n in names) + " }"


def services_counted(services: list[Service]) -> list[Service]:
    """The services that get counters: host-network ones need a cgroup,
    bridged ones an address. Sorted, so the same stack makes the same
    ruleset (a changed ruleset is reloaded; an unchanged one is not)."""
    out = []
    for s in services:
        if s.host_network and s.cgroup:
            out.append(s)
        elif not s.host_network and (s.ipv4 or s.ipv6):
            out.append(s)
    return sorted(out, key=lambda s: s.name)


def build(services: list[Service], uplinks: list[str]) -> str:
    """The whole table as an atomic nft script: create it if missing,
    delete it, create it again, so a reload never leaves a moment with
    no table or two."""
    if not uplinks:
        raise ValueError("no uplink interface to count on")
    counted = []
    names: set[str] = set()
    marks: set[int] = set()
    for s in services_counted(services):
        # Two services may not share a counter (web-admin and web_admin)
        # or a mark: nft would refuse the whole table for one clash.
        if counter_name(s.name) in names or (s.host_network and mark_of(s.name) in marks):
            continue
        names.add(counter_name(s.name))
        if s.host_network:
            marks.add(mark_of(s.name))
        counted.append(s)
    host_net = [s for s in counted if s.host_network]
    bridged = [s for s in counted if not s.host_network]
    up = _ifset(sorted(uplinks))

    counters = ["total_tx", "total_rx", "fwd_tx", "fwd_rx", "internet_tx", "internet_rx",
                "fwd_internet_tx", "fwd_internet_rx"]
    for s in counted:
        counters += [counter_name(s.name) + "_tx", counter_name(s.name) + "_rx"]

    out_rules, in_mark_rules, in_sock_rules, fwd_rules = [], [], [], []
    for s in host_net:
        mark = mark_of(s.name) << MARK_SHIFT
        c = counter_name(s.name)
        sock = f"socket cgroupv2 level {int(s.cgroup_level)} {_quoted(s.cgroup)}"
        out_rules.append(
            f"{sock} ct mark set ct mark and {hex(~MARK_MASK & 0xFFFFFFFF)} or {hex(mark)} "
            f"counter name {c}_tx return")
        in_mark_rules.append(
            f"ct mark and {hex(MARK_MASK)} == {hex(mark)} counter name {c}_rx return")
        in_sock_rules.append(
            f"{sock} ct mark set ct mark and {hex(~MARK_MASK & 0xFFFFFFFF)} or {hex(mark)} "
            f"counter name {c}_rx return")
    for s in bridged:
        c = counter_name(s.name)
        for addr in s.ipv4:
            a = str(ipaddress.IPv4Address(addr))
            fwd_rules.append(f"oifname {up} ip saddr {a} counter name {c}_tx return")
            fwd_rules.append(f"iifname {up} ip daddr {a} counter name {c}_rx return")
        for addr in s.ipv6:
            a = str(ipaddress.IPv6Address(addr))
            fwd_rules.append(f"oifname {up} ip6 saddr {a} counter name {c}_tx return")
            fwd_rules.append(f"iifname {up} ip6 daddr {a} counter name {c}_rx return")

    def chain(name: str, hook: str, rules: list[str]) -> str:
        body = "\n".join(f"\t\t{r}" for r in rules)
        return (f"\tchain {name} {{\n\t\ttype filter hook {hook} priority {PRIORITY}; "
                f"policy accept;\n{body}\n\t}}\n")

    lines = [f"table {FAMILY} {TABLE} {{}}", f"delete table {FAMILY} {TABLE}",
             f"table {FAMILY} {TABLE} {{"]
    lines += [f"\tcounter {c} {{}}" for c in counters]
    lines += [f"\tset local_v4 {{ type ipv4_addr; flags interval; "
              f"elements = {{ {', '.join(LOCAL_V4)} }} }}",
              f"\tset local_v6 {{ type ipv6_addr; flags interval; "
              f"elements = {{ {', '.join(LOCAL_V6)} }} }}"]
    text = "\n".join(lines) + "\n"
    # The Internet-only counters come before the per-service rules, which
    # end in return.
    text += chain("meter_out", "output", [
        f"oifname != {up} return", "counter name total_tx",
        "ip daddr != @local_v4 counter name internet_tx",
        "ip6 daddr != @local_v6 counter name internet_tx",
        *out_rules])
    text += chain("meter_in", "input", [
        f"iifname != {up} return", "counter name total_rx",
        "ip saddr != @local_v4 counter name internet_rx",
        "ip6 saddr != @local_v6 counter name internet_rx",
        *in_mark_rules, *in_sock_rules])
    text += chain("meter_forward", "forward", [
        f"oifname {up} iifname != {up} counter name fwd_tx",
        f"iifname {up} oifname != {up} counter name fwd_rx",
        f"oifname {up} iifname != {up} ip daddr != @local_v4 counter name fwd_internet_tx",
        f"oifname {up} iifname != {up} ip6 daddr != @local_v6 counter name fwd_internet_tx",
        f"iifname {up} oifname != {up} ip saddr != @local_v4 counter name fwd_internet_rx",
        f"iifname {up} oifname != {up} ip6 saddr != @local_v6 counter name fwd_internet_rx",
        *fwd_rules])
    return text + "}\n"


def teardown() -> str:
    return f"table {FAMILY} {TABLE} {{}}\ndelete table {FAMILY} {TABLE}\n"


def internet(counts: dict[str, tuple[int, int]]) -> dict[str, int]:
    """Bytes to and from the Internet (not the local network), host and
    forwarded together, from one reset of the counters."""
    def b(name):
        return counts.get(name, (0, 0))[1]
    return {"rx": b("internet_rx") + b("fwd_internet_rx"),
            "tx": b("internet_tx") + b("fwd_internet_tx")}


def attribute(counts: dict[str, tuple[int, int]],
              services: list[Service]) -> dict[str, dict]:
    """Per service ``{"rx": bytes, "tx": bytes, "rx_packets", "tx_packets"}``
    from one reset of the counters, plus ``other_containers`` (forwarded,
    not named) and ``host`` (on the uplink, not forwarded, not named).
    ``counts`` maps a counter name to (packets, bytes); ``services`` are
    the ones the ruleset counted (services_counted)."""
    def get(name):
        return counts.get(name, (0, 0))

    def add(acc, rxp, rxb, txp, txb):
        return [acc[0] + rxp, acc[1] + rxb, acc[2] + txp, acc[3] + txb]

    out: dict[str, dict] = {}
    named_host = [0, 0, 0, 0]
    named_fwd = [0, 0, 0, 0]
    for s in services:
        c = counter_name(s.name)
        rxp, rxb = get(c + "_rx")
        txp, txb = get(c + "_tx")
        out[s.name] = {"rx": rxb, "tx": txb, "rx_packets": rxp, "tx_packets": txp}
        if s.host_network:
            named_host = add(named_host, rxp, rxb, txp, txb)
        else:
            named_fwd = add(named_fwd, rxp, rxb, txp, txb)

    for label, rx_total, tx_total, named in (
        ("host", get("total_rx"), get("total_tx"), named_host),
        ("other_containers", get("fwd_rx"), get("fwd_tx"), named_fwd),
    ):
        out[label] = {
            "rx": max(rx_total[1] - named[1], 0), "tx": max(tx_total[1] - named[3], 0),
            "rx_packets": max(rx_total[0] - named[0], 0),
            "tx_packets": max(tx_total[0] - named[2], 0),
        }
    return out
