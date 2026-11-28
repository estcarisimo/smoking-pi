"""HTTPS to a lead, and one protocol exchange over it.

The transport is injected everywhere (:class:`Transport`): tests hand the
agent a fake one and nothing here opens a socket in a test. The real one,
:class:`HttpsTransport`, is :mod:`http.client` over an :class:`ssl.SSLContext`
whose only trust anchor is the pinned fleet root (never the system store),
with hostname checking on, presenting the node's chain for mTLS.

The one unverified request is ``GET /fleet/root`` during a join, before
anything is pinned: its answer is accepted only if its SHA-256 matches the
join string's (:func:`fleet.chain.pinned_root`), so an attacker on the path
can make a join fail, never succeed.
"""

from __future__ import annotations

import http.client
import socket
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from . import protocol


@dataclass(frozen=True)
class Tls:
    """How to talk to a lead. ``root`` None: unverified (the root fetch only)."""

    root: Path | None
    chain: Path | None = None
    key: Path | None = None


#: Why no HTTP answer came, as a literal kind (:func:`failure_kind`).
FAILURE_KINDS = ("hostname", "certificate", "tls", "timeout", "refused", "dns",
                 "local_credentials", "network")

#: OpenSSL's X509_V_ERR_HOSTNAME_MISMATCH and X509_V_ERR_IP_ADDRESS_MISMATCH.
_NAME_MISMATCH = (62, 64)


class TransportError(Exception):
    """No HTTP answer: refused, timed out, TLS failed. ``reason`` is one of
    FAILURE_KINDS, chosen from the exception's class (and, for a failed
    certificate check, its numeric code), never its text."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def failure_kind(err: BaseException) -> str:
    """A literal from FAILURE_KINDS for a connection failure."""
    if isinstance(err, ssl.SSLCertVerificationError):
        return "hostname" if getattr(err, "verify_code", None) in _NAME_MISMATCH else "certificate"
    if isinstance(err, ssl.SSLError):
        return "tls"
    if isinstance(err, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(err, ConnectionRefusedError):
        return "refused"
    if isinstance(err, socket.gaierror):
        return "dns"
    return "network"


class Transport(Protocol):
    def request(self, method: str, url: str, body: bytes | None,
                tls: Tls) -> tuple[int, bytes]:
        """(HTTP status, at most MAX_BODY_BYTES + 1 bytes of body)."""


def context(tls: Tls) -> ssl.SSLContext:
    """The TLS client context. With a root: CERT_REQUIRED, hostname checked,
    that root the only CA (a missing or unreadable root file raises; there
    is no fallback to any other store). ``root=None`` (the join's root fetch
    alone) is the one way to an unverified context."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # CERT_REQUIRED + hostname check
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if tls.root is None:
        # The join's root fetch: nothing is pinned yet, and the answer is
        # checked against the join string's fingerprint (module docstring).
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    else:
        ctx.load_verify_locations(cafile=str(tls.root))
    if tls.chain is not None:
        ctx.load_cert_chain(certfile=str(tls.chain), keyfile=str(tls.key))
    return ctx


class HttpsTransport:
    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout

    def request(self, method: str, url: str, body: bytes | None,
                tls: Tls) -> tuple[int, bytes]:
        parts = urlsplit(url)
        try:
            ctx = context(tls)
            conn = http.client.HTTPSConnection(parts.hostname, parts.port or 443,
                                               context=ctx, timeout=self.timeout)
        except (OSError, ValueError, ssl.SSLError):
            raise TransportError("local_credentials") from None
        headers = {"User-Agent": "smoking-pi-fleet-agent", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, parts.path or "/", body=body, headers=headers)
            resp = conn.getresponse()
            return resp.status, resp.read(protocol.MAX_BODY_BYTES + 1)
        except (OSError, ssl.SSLError, http.client.HTTPException) as err:
            raise TransportError(failure_kind(err)) from None
        finally:
            conn.close()


class LeadError(Exception):
    """A non-2xx answer. ``code`` is the ErrorResponse's (``internal`` when
    the body was not one); ``body`` the decoded ErrorResponse, if any."""

    def __init__(self, status: int, body: protocol.ErrorResponse | None):
        self.status = status
        self.body = body
        self.code = body.code if body is not None else "internal"
        super().__init__(self.code)


class BadAnswer(Exception):
    """A 2xx answer that is not the message the route promises. ``reasons``
    are the protocol's literal validation reasons."""

    def __init__(self, reasons: tuple[str, ...]):
        self.reasons = tuple(reasons)
        super().__init__("; ".join(self.reasons))


def route_url(base: str, route: str) -> str:
    return base.rstrip("/") + route


def call(transport: Transport, base: str, method: str, route: str, message: Any,
         response_cls: type, tls: Tls) -> Any:
    """Send ``message`` (or nothing), return the decoded ``response_cls``.
    Raises TransportError, LeadError or BadAnswer."""
    body = protocol.encode_json(message).encode() if message is not None else None
    status, data = transport.request(method, route_url(base, route), body, tls)
    if len(data) > protocol.MAX_BODY_BYTES:
        raise BadAnswer(("body: too large",))
    if 200 <= status < 300:
        try:
            return protocol.decode_json(response_cls, data)
        except protocol.ValidationError as err:
            raise BadAnswer(err.reasons) from None
    try:
        error = protocol.decode_json(protocol.ErrorResponse, data)
    except protocol.ValidationError:
        error = None
    raise LeadError(status, error)
