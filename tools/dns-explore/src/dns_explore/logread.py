"""Read AdGuard Home's query log (``querylog.json``, one JSON object per line).

AdGuard writes each entry with short keys: ``T`` (time), ``QH`` (question
host), ``QT`` (type), ``Answer`` (the DNS response, base64 wire format),
``Cached``, ``Upstream``, ``Elapsed`` (ns). The answer carries what the
aggregation levels need beyond the name: the CNAME chain (which CDN serves
it) and the addresses (which network, i.e. AS, they belong to).
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import dns.exception
import dns.message
import dns.rcode
import dns.rdatatype

logger = logging.getLogger(__name__)

CONTAINER = "pro-dns-observer-1"
CONTAINER_LOG_DIR = "/opt/adguardhome/work/data"


@dataclass(frozen=True)
class Query:
    """One query the observer answered.

    Parameters
    ----------
    ts : datetime
        When it was answered (timezone-aware).
    qname : str
        The question name, lowercase, no trailing dot.
    qtype : str
        ``A``, ``AAAA``, ``HTTPS``...
    cached : bool
        Answered from AdGuard's cache.
    rcode : str
        ``NOERROR``, ``NXDOMAIN``... (``UNKNOWN`` when the answer is missing).
    cnames : tuple[str, ...]
        The CNAME chain, in order, lowercase, no trailing dots.
    addrs : tuple[str, ...]
        A and AAAA addresses in the answer.
    """

    ts: datetime
    qname: str
    qtype: str
    cached: bool
    rcode: str
    cnames: tuple[str, ...] = field(default=())
    addrs: tuple[str, ...] = field(default=())


def _parse_time(value: str) -> datetime:
    # AdGuard writes nanoseconds; datetime takes microseconds.
    return datetime.fromisoformat(re.sub(r"\.(\d{1,6})\d*", r".\1", value.replace("Z", "+00:00")))


def _parse_answer(raw: str | None) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    if not raw:
        return "UNKNOWN", (), ()
    try:
        msg = dns.message.from_wire(base64.b64decode(raw))
    except (binascii.Error, dns.exception.DNSException, ValueError):
        return "UNKNOWN", (), ()
    cnames: list[str] = []
    addrs: list[str] = []
    for rrset in msg.answer:
        for rd in rrset:
            if rrset.rdtype == dns.rdatatype.CNAME:
                cnames.append(rd.target.to_text().rstrip(".").lower())
            elif rrset.rdtype in (dns.rdatatype.A, dns.rdatatype.AAAA):
                addrs.append(rd.address)
    return dns.rcode.to_text(msg.rcode()), tuple(cnames), tuple(addrs)


def parse_line(line: str) -> Query | None:
    """Parse one log line; ``None`` for a line that is not a query entry.

    Examples
    --------
    >>> parse_line('{"T":"2026-09-26T18:00:00Z","QH":"Example.COM","QT":"A"}').qname
    'example.com'
    """
    try:
        e = json.loads(line)
        ts = _parse_time(e["T"])
        qname = str(e["QH"]).rstrip(".").lower()
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    rcode, cnames, addrs = _parse_answer(e.get("Answer"))
    return Query(
        ts=ts,
        qname=qname,
        qtype=str(e.get("QT", "")),
        cached=bool(e.get("Cached", False)),
        rcode=rcode,
        cnames=cnames,
        addrs=addrs,
    )


def parse_lines(lines: Iterable[str]) -> Iterator[Query]:
    """Parse many lines, skipping (and counting) the unreadable ones."""
    bad = 0
    for line in lines:
        if not line.strip():
            continue
        q = parse_line(line)
        if q is None:
            bad += 1
            continue
        yield q
    if bad:
        logger.warning("skipped %d unreadable log lines", bad)


def read_files(paths: Iterable[Path]) -> Iterator[Query]:
    """Queries from local copies of the log (``querylog.json``, ``.1``...)."""
    for path in paths:
        with path.open(encoding="utf-8") as fh:
            yield from parse_lines(fh)


def read_container(container: str = CONTAINER) -> Iterator[Query]:
    """Queries straight from the running observer, via ``docker exec``.

    Reads the rotated file (``querylog.json.1``) first when it exists, then
    the current one. Entries still in AdGuard's memory buffer (up to
    ``size_memory``, 1000) are not on disk yet and are missed.
    """
    cmd = [
        "docker",
        "exec",
        container,
        "sh",
        "-c",
        f"cat {CONTAINER_LOG_DIR}/querylog.json.1 2>/dev/null; "
        f"cat {CONTAINER_LOG_DIR}/querylog.json",
    ]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    yield from parse_lines(out.splitlines())
