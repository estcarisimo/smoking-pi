"""The few pieces of the DNS wire format a host-name responder needs.

Multicast DNS (RFC 6762) is DNS messages on 224.0.0.251:5353. A responder
for one host name reads questions and the A/AAAA records other hosts send,
and writes probes, answers and goodbyes. Nothing here touches a socket.
"""

from __future__ import annotations

import ipaddress
import struct
from dataclasses import dataclass, field

TYPE_A = 1
TYPE_AAAA = 28
TYPE_NSEC = 47
TYPE_ANY = 255
CLASS_IN = 1
# The top bit of a record's class is "cache flush" (the record set is
# complete: forget older ones); of a question's, "unicast response wanted".
CLASS_TOP_BIT = 0x8000

FLAG_RESPONSE = 0x8000
FLAG_AUTHORITATIVE = 0x0400

# RFC 6762 §10: host-name records live 120 s in caches.
HOST_TTL = 120


class WireError(ValueError):
    """A message that cannot be parsed."""


@dataclass(frozen=True)
class Question:
    name: str
    qtype: int
    unicast: bool = False


@dataclass(frozen=True)
class Record:
    name: str
    rtype: int
    ttl: int
    rdata: bytes
    flush: bool = False

    def address(self) -> str | None:
        """The record's address, for A and AAAA."""
        if self.rtype == TYPE_A and len(self.rdata) == 4:
            return str(ipaddress.IPv4Address(self.rdata))
        if self.rtype == TYPE_AAAA and len(self.rdata) == 16:
            return str(ipaddress.IPv6Address(self.rdata))
        return None


@dataclass
class Message:
    id: int = 0
    flags: int = 0
    questions: list[Question] = field(default_factory=list)
    answers: list[Record] = field(default_factory=list)
    authority: list[Record] = field(default_factory=list)
    additional: list[Record] = field(default_factory=list)

    @property
    def is_response(self) -> bool:
        return bool(self.flags & FLAG_RESPONSE)


def same_name(a: str, b: str) -> bool:
    """DNS names compare without case and without the trailing dot."""
    return a.rstrip(".").lower() == b.rstrip(".").lower()


def _read_name(data: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    end = None
    jumps = 0
    while True:
        if offset >= len(data):
            raise WireError("name runs past the end")
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                raise WireError("truncated pointer")
            jumps += 1
            if jumps > 32:
                raise WireError("pointer loop")
            if end is None:
                end = offset + 2
            offset = ((length & 0x3F) << 8) | data[offset + 1]
            continue
        if length & 0xC0:
            raise WireError("unknown label type")
        offset += 1
        if offset + length > len(data):
            raise WireError("label runs past the end")
        labels.append(data[offset:offset + length].decode("utf-8", "replace"))
        offset += length
    return ".".join(labels), (end if end is not None else offset)


def _read_record(data: bytes, offset: int) -> tuple[Record, int]:
    name, offset = _read_name(data, offset)
    if offset + 10 > len(data):
        raise WireError("truncated record")
    rtype, rclass, ttl, rdlen = struct.unpack_from("!HHIH", data, offset)
    offset += 10
    if offset + rdlen > len(data):
        raise WireError("record data runs past the end")
    rdata = data[offset:offset + rdlen]
    return Record(name, rtype, ttl, rdata, bool(rclass & CLASS_TOP_BIT)), offset + rdlen


def parse(data: bytes) -> Message:
    if len(data) < 12:
        raise WireError("shorter than a header")
    msg_id, flags, qd, an, ns, ar = struct.unpack_from("!HHHHHH", data, 0)
    msg = Message(id=msg_id, flags=flags)
    offset = 12
    for _ in range(qd):
        name, offset = _read_name(data, offset)
        if offset + 4 > len(data):
            raise WireError("truncated question")
        qtype, qclass = struct.unpack_from("!HH", data, offset)
        offset += 4
        msg.questions.append(Question(name, qtype, bool(qclass & CLASS_TOP_BIT)))
    for count, section in ((an, msg.answers), (ns, msg.authority), (ar, msg.additional)):
        for _ in range(count):
            record, offset = _read_record(data, offset)
            section.append(record)
    return msg


def _name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode("utf-8")
        if not 0 < len(raw) < 64:
            raise WireError(f"bad label in {name!r}")
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def _record(r: Record) -> bytes:
    rclass = CLASS_IN | (CLASS_TOP_BIT if r.flush else 0)
    return _name(r.name) + struct.pack("!HHIH", r.rtype, rclass, r.ttl, len(r.rdata)) + r.rdata


def build(msg: Message) -> bytes:
    out = struct.pack(
        "!HHHHHH", msg.id, msg.flags, len(msg.questions), len(msg.answers),
        len(msg.authority), len(msg.additional),
    )
    for q in msg.questions:
        qclass = CLASS_IN | (CLASS_TOP_BIT if q.unicast else 0)
        out += _name(q.name) + struct.pack("!HH", q.qtype, qclass)
    for section in (msg.answers, msg.authority, msg.additional):
        for r in section:
            out += _record(r)
    return out


def address_records(name: str, addresses: list[str], ttl: int = HOST_TTL,
                    flush: bool = True) -> list[Record]:
    """A and AAAA records for ``name``, IPv4 first."""
    records = []
    for text in addresses:
        ip = ipaddress.ip_address(text)
        rtype = TYPE_A if ip.version == 4 else TYPE_AAAA
        records.append(Record(name, rtype, ttl, ip.packed, flush))
    records.sort(key=lambda r: (r.rtype, r.rdata))
    return records


def nsec_record(name: str, present: set[int], ttl: int = HOST_TTL) -> Record:
    """§6.1: "this name has these types and no others", so an asker stops
    waiting for an AAAA (or A) that does not exist. The restricted form:
    the next name is the name itself, one bitmap window for types < 256."""
    bitmap = bytearray(32)
    for t in present:
        if 0 < t < 256:
            bitmap[t // 8] |= 0x80 >> (t % 8)
    length = max((i + 1 for i, b in enumerate(bitmap) if b), default=1)
    rdata = _name(name) + bytes([0, length]) + bytes(bitmap[:length])
    return Record(name, TYPE_NSEC, ttl, rdata, True)
