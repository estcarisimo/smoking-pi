"""The host's LAN interfaces and the mDNS socket.

The container runs on the host network, so /proc/net and the interface
ioctls describe the Pi itself. Docker's bridges and veths, VPNs and the
loopback are never where a laptop on the LAN asks for a name.
"""

from __future__ import annotations

import errno
import fcntl
import ipaddress
import logging
import socket
import struct

log = logging.getLogger("mdns")

GROUP = "224.0.0.251"
PORT = 5353

# Interfaces nobody on the LAN asks through.
VIRTUAL_PREFIXES = ("docker", "br-", "veth", "tailscale", "tun", "tap",
                    "wg", "virbr", "zt", "cni", "flannel", "podman")

# Linux socket options the socket module may not name.
IP_MULTICAST_ALL = getattr(socket, "IP_MULTICAST_ALL", 49)
IP_RECVTTL = getattr(socket, "IP_RECVTTL", 12)
IP_TTL_CMSG = getattr(socket, "IP_TTL", 2)

SIOCGIFFLAGS = 0x8913
SIOCGIFADDR = 0x8915
IFF_UP = 0x1
IFF_LOOPBACK = 0x8
IFF_MULTICAST = 0x1000
# /proc/net/if_inet6 flags: an address not (yet) usable as a source.
IFA_F_TEMPORARY = 0x01
IFA_F_DADFAILED = 0x08
IFA_F_DEPRECATED = 0x20
IFA_F_TENTATIVE = 0x40

IF_INET6 = "/proc/net/if_inet6"


def _ioctl(sock: socket.socket, request: int, name: str) -> bytes | None:
    try:
        return fcntl.ioctl(sock.fileno(), request, struct.pack("256s", name.encode()[:15]))
    except OSError:
        return None


def ipv4_of(name: str) -> str | None:
    """The interface's IPv4 address, if it is up, multicast and has one."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        flags = _ioctl(s, SIOCGIFFLAGS, name)
        if flags is None:
            return None
        value = struct.unpack_from("H", flags, 16)[0]
        if not value & IFF_UP or not value & IFF_MULTICAST or value & IFF_LOOPBACK:
            return None
        raw = _ioctl(s, SIOCGIFADDR, name)
        if raw is None:
            return None
        return socket.inet_ntoa(raw[20:24])


def ipv6_addresses(path: str = IF_INET6) -> dict[str, list[str]]:
    """Global and unique-local IPv6 addresses per interface. Link-local ones
    are left out: without a zone they are useless to the asker."""
    out: dict[str, list[str]] = {}
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    for line in lines:
        parts = line.split()
        if len(parts) != 6:
            continue
        hexaddr, _index, _plen, scope, flags, name = parts
        try:
            ip = ipaddress.IPv6Address(bytes.fromhex(hexaddr))
            scope_v, flags_v = int(scope, 16), int(flags, 16)
        except ValueError:
            continue
        if scope_v != 0 or ip.is_link_local or ip.is_loopback:
            continue
        if flags_v & (IFA_F_TEMPORARY | IFA_F_DADFAILED | IFA_F_DEPRECATED | IFA_F_TENTATIVE):
            continue
        out.setdefault(name, []).append(str(ip))
    return out


def is_virtual(name: str) -> bool:
    return name == "lo" or name.startswith(VIRTUAL_PREFIXES)


def lan_interfaces(only: list[str] | None = None) -> dict[str, dict]:
    """``{name: {"index": n, "ipv4": addr, "ipv6": [...]}}`` for every LAN
    interface with an IPv4 address (the transport this responder uses).
    ``only`` (MDNS_INTERFACES) replaces the automatic choice."""
    v6 = ipv6_addresses()
    found: dict[str, dict] = {}
    for index, name in socket.if_nameindex():
        if only:
            if name not in only:
                continue
        elif is_virtual(name):
            continue
        v4 = ipv4_of(name)
        if not v4:
            continue
        found[name] = {"index": index, "ipv4": v4, "ipv6": v6.get(name, [])}
    return found


def addresses(interfaces: dict[str, dict]) -> list[str]:
    out: list[str] = []
    for info in interfaces.values():
        out.append(info["ipv4"])
        out.extend(info["ipv6"])
    return out


def own_addresses() -> set[str]:
    """Every address of this host, virtual interfaces included: a packet
    from any of them is this host talking to itself."""
    own: set[str] = set()
    for _index, name in socket.if_nameindex():
        v4 = ipv4_of(name)
        if v4:
            own.add(v4)
    for addrs in ipv6_addresses().values():
        own.update(addrs)
    return own


class MulticastSocket:
    """One IPv4 socket on 5353, shared with Avahi (both set SO_REUSEPORT,
    and every listener gets its own copy of each multicast packet)."""

    def __init__(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_PKTINFO, 1)
        # §11: a genuine mDNS packet arrives with IP TTL 255, which no
        # router can forward; the TTL is checked on every packet.
        s.setsockopt(socket.IPPROTO_IP, IP_RECVTTL, 1)
        # Only the groups joined on this socket's own interfaces: by default
        # Linux delivers 224.0.0.251 from every interface any socket on the
        # host joined (Docker bridges, VPNs, Avahi's choices).
        try:
            s.setsockopt(socket.IPPROTO_IP, IP_MULTICAST_ALL, 0)
        except OSError:
            pass
        s.bind(("", PORT))
        s.setblocking(False)
        self.sock = s
        self.joined: dict[str, str] = {}

    def fileno(self) -> int:
        return self.sock.fileno()

    def sync(self, interfaces: dict[str, dict]) -> None:
        """Join the group on new interfaces, leave it on gone ones."""
        for name, addr in list(self.joined.items()):
            if interfaces.get(name, {}).get("ipv4") != addr:
                self._membership(socket.IP_DROP_MEMBERSHIP, addr)
                del self.joined[name]
        for name, info in interfaces.items():
            if name not in self.joined:
                if self._membership(socket.IP_ADD_MEMBERSHIP, info["ipv4"]):
                    self.joined[name] = info["ipv4"]
                    log.info("listening on %s (%s)", name, info["ipv4"])

    def _membership(self, option: int, addr: str) -> bool:
        mreq = socket.inet_aton(GROUP) + socket.inet_aton(addr)
        try:
            self.sock.setsockopt(socket.IPPROTO_IP, option, mreq)
            return True
        except OSError as e:
            # Already a member is fine; the rest is worth a line in the
            # log, never a crash.
            if e.errno == errno.EADDRINUSE:
                return True
            log.warning("multicast membership on %s failed: errno %s", addr, e.errno)
            return False

    def receive(self) -> tuple[bytes, tuple[str, int], int | None, int | None] | None:
        """One packet, its source, the index of the interface it came in on
        and its IP TTL; None when there is nothing (more) to read."""
        try:
            data, ancdata, _flags, source = self.sock.recvmsg(
                9000, socket.CMSG_SPACE(12) + socket.CMSG_SPACE(4))
        except BlockingIOError:
            return None
        except OSError as e:
            log.warning("receive failed: errno %s", e.errno)
            return None
        index = ttl = None
        for level, ctype, cdata in ancdata:
            if level != socket.IPPROTO_IP:
                continue
            if ctype == socket.IP_PKTINFO and len(cdata) >= 4:
                index = struct.unpack_from("i", cdata, 0)[0]
            elif ctype == IP_TTL_CMSG and len(cdata) >= 4:
                ttl = struct.unpack_from("i", cdata, 0)[0]
        return data, source, index, ttl

    def send_multicast(self, data: bytes, interface_addr: str) -> None:
        try:
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                                 socket.inet_aton(interface_addr))
            self.sock.sendto(data, (GROUP, PORT))
        except OSError as e:
            log.warning("send on %s failed: errno %s", interface_addr, e.errno)

    def send_unicast(self, data: bytes, to: tuple[str, int]) -> None:
        try:
            self.sock.sendto(data, to)
        except OSError as e:
            log.warning("reply to %s failed: errno %s", to[0], e.errno)
