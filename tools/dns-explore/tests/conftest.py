"""Synthetic AdGuard query-log lines. No test touches the network or Docker."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import dns.message
import dns.rrset
import pytest

T0 = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)


def wire_answer(qname: str, qtype: str, cnames: tuple[str, ...], addrs: tuple[str, ...]) -> str:
    """A response in DNS wire format, base64, as AdGuard stores it."""
    q = dns.message.make_query(qname, qtype)
    r = dns.message.make_response(q)
    owner = qname.rstrip(".") + "."
    for target in cnames:
        r.answer.append(dns.rrset.from_text(owner, 60, "IN", "CNAME", target + "."))
        owner = target + "."
    for a in addrs:
        rtype = "AAAA" if ":" in a else "A"
        r.answer.append(dns.rrset.from_text(owner, 60, "IN", rtype, a))
    return base64.b64encode(r.to_wire()).decode()


@pytest.fixture
def line() -> Callable[..., str]:
    def make(
        qname: str,
        minutes: float = 0,
        qtype: str = "A",
        cnames: tuple[str, ...] = (),
        addrs: tuple[str, ...] = ("192.0.2.1",),
        cached: bool = False,
    ) -> str:
        ts = (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", ".123456789Z")
        return json.dumps(
            {
                "T": ts,
                "QH": qname,
                "QT": qtype,
                "Answer": wire_answer(qname, qtype, cnames, addrs),
                "Cached": cached,
            }
        )

    return make
