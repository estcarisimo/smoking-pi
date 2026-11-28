"""The lead's HTTP service: the five routes of :mod:`fleet.protocol`, as WSGI.

``GET /fleet/root`` and ``POST /fleet/enroll`` need no client certificate
(enrollment is authenticated by the join token); heartbeat, renew and leave
need one: verified against the fleet root in the TLS handshake (gunicorn,
``ssl.CERT_OPTIONAL``, see :mod:`fleet.server`), then looked up here, on
every request, by its SHA-256 in the registry, where revocation lives.

Every answer that is not 2xx is a protocol ``ErrorResponse`` whose message
is the code's fixed sentence (``protocol.error``); what went wrong is logged
under the response's ``error_id``, as a literal string. Nothing a node sent
is echoed into a log except ids the protocol validated, and the join token
only ever exists here as a ``Secret``.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable

from cryptography import x509

from . import pki, protocol
from . import registry as registry_mod
from .registry import Registry

log = logging.getLogger("fleet.lead")

#: The lead nudges a node to renew (``renew_now``) when the certificate it
#: presents has less than this left; the agent renews on its own from 2/3
#: of the lifetime (30 days left), so this is the backstop.
RENEW_NUDGE = dt.timedelta(days=20)

_REASONS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
            409: "Conflict", 413: "Content Too Large", 421: "Misdirected Request",
            429: "Too Many Requests", 500: "Internal Server Error",
            503: "Service Unavailable"}


class Refused(Exception):
    """An error answer: (code, literal detail for the log, protocol extras)."""

    def __init__(self, code: str, detail: str, **extra: Any):
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.extra = extra


def peer_cert_der(environ: dict) -> bytes | None:
    """The client certificate the TLS handshake verified, DER, or None.

    gunicorn puts the connection's socket in the environ; with
    ``CERT_OPTIONAL`` a client may present none (enrollment)."""
    sock = environ.get("gunicorn.socket")
    getpeercert = getattr(sock, "getpeercert", None)
    if getpeercert is None:
        return None
    try:
        der = getpeercert(binary_form=True)
    except (ValueError, OSError):
        return None
    return der or None


def directive_id() -> str:
    return "d-" + secrets.token_hex(6)


@dataclass
class Fleet:
    fleet_id: str
    lead_id: str
    name: str


class LeadService:
    """The WSGI application. Built once per process from the loaded CA, the
    registry and the settings; the lead list's epoch is fixed at start (a
    change of FLEET_LEAD_URLS or FLEET_LEAD_MODE recreates the container)."""

    def __init__(self, fleet: Fleet, ca: pki.CA, registry: Registry,
                 lead_urls: tuple[str, ...], lead_mode: str,
                 heartbeat_interval: int = protocol.HEARTBEAT_DEFAULT,
                 clock: Callable[[], float] = time.time):
        if not lead_urls:
            raise ValueError("a lead needs at least one URL")
        self.fleet = fleet
        self.ca = ca
        self.registry = registry
        self.clock = clock
        self.heartbeat_interval = heartbeat_interval
        self.intermediate_sha256 = pki.fingerprint(ca.intermediate)
        epoch = registry.lead_epoch(tuple(lead_urls), lead_mode)
        self.endpoints = protocol.LeadEndpoints(
            epoch=epoch, current=lead_urls[0], urls=tuple(lead_urls), lead_mode=lead_mode)
        self.routes = {
            ("GET", "/fleet/root"): self.root,
            ("POST", "/fleet/enroll"): self.enroll,
            ("POST", "/fleet/heartbeat"): self.heartbeat,
            ("POST", "/fleet/renew"): self.renew,
            ("POST", "/fleet/leave"): self.leave,
        }

    # -- WSGI ---------------------------------------------------------------

    def __call__(self, environ: dict, start_response):
        method = environ.get("REQUEST_METHOD", "")
        path = environ.get("PATH_INFO", "")
        handler = self.routes.get((method, path))
        route = path if handler else "?"
        version = protocol.PROTOCOL_MAX
        try:
            if handler is None:
                raise Refused("bad_request", "no such route or method")
            status, body = handler(environ)
        except Refused as r:
            version = r.extra.pop("_version", version)
            status, body = self._refuse(route, r, version)
        except Exception:
            error_id = protocol.new_error_id()
            # The traceback goes to the lead's log only; the answer is the
            # fixed "internal" sentence with the id to find it.
            log.exception("%s: internal error (error_id %s)", route, error_id)
            status, body = protocol.error("internal", error_id=error_id)
        payload = protocol.encode_json(body).encode()
        start_response(f"{status} {_REASONS.get(status, 'Error')}", [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(payload))),
            ("Cache-Control", "no-store"),
        ])
        return [payload]

    def _refuse(self, route: str, r: Refused, version: int):
        status, body = protocol.error(r.code, protocol=version, **r.extra)
        level = logging.ERROR if status >= 500 else logging.WARNING
        log.log(level, "%s: %s, %s (error_id %s)", route, r.code, r.detail, body.error_id)
        return status, body

    # -- request reading ----------------------------------------------------

    def _request(self, environ: dict, cls: type) -> tuple[Any, int]:
        """The decoded request and the protocol version to answer in."""
        try:
            size = int(environ.get("CONTENT_LENGTH") or "")
        except ValueError:
            raise Refused("bad_request", "no Content-Length",
                          reasons=("body: length required",)) from None
        if size < 0:
            raise Refused("bad_request", "negative Content-Length",
                          reasons=("body: length required",))
        if size > protocol.MAX_BODY_BYTES:
            raise Refused("too_large", "body over the limit, not read")
        raw = environ["wsgi.input"].read(size)
        if len(raw) != size:
            raise Refused("bad_request", "body shorter than its Content-Length",
                          reasons=("body: truncated",))
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise Refused("bad_request", "body is not JSON", reasons=("body: not JSON",)) from None
        version = self._negotiate(data)
        try:
            return protocol.decode(cls, data), version
        except protocol.ValidationError as err:
            raise Refused("bad_request", "request did not pass the schema",
                          reasons=tuple(r[:200] for r in err.reasons[:32]),
                          _version=version) from None

    @staticmethod
    def _negotiate(data: Any) -> int:
        """The version to answer in, read before the full decode: a newer
        agent's request may not decode as this version's message."""
        if not isinstance(data, dict):
            raise Refused("bad_request", "body is not an object",
                          reasons=("body: must be a JSON object",))
        high, low = data.get("protocol"), data.get("protocol_min")
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (high, low)):
            raise Refused("bad_request", "no protocol range",
                          reasons=("protocol: must be an integer",))
        version = protocol.negotiate(low, high)
        if version is None:
            raise Refused("protocol_unsupported", "no version in common",
                          supported_min=protocol.PROTOCOL_MIN,
                          supported_max=protocol.PROTOCOL_MAX)
        return version

    def _client(self, environ: dict, node_id: str, version: int):
        """The verified client certificate's registry record and the
        certificate, for ``node_id``; refuses ``unauthenticated`` otherwise.
        Revocation is the caller's: heartbeat answers it with a directive."""
        der = peer_cert_der(environ)
        if der is None:
            raise Refused("unauthenticated", "no client certificate", _version=version)
        record = self.registry.cert(pki.der_fingerprint(der))
        if record is None:
            raise Refused("unauthenticated", "client certificate not issued here",
                          _version=version)
        try:
            cert = x509.load_der_x509_certificate(der)
        except ValueError:
            raise Refused("unauthenticated", "client certificate unreadable",
                          _version=version) from None
        if record.node_id != node_id or pki.node_id_of(cert) != node_id:
            raise Refused("unauthenticated", "client certificate is another node's",
                          _version=version)
        if record.not_after <= self.clock():
            raise Refused("unauthenticated", "client certificate expired", _version=version)
        return record, cert

    def _now(self) -> dt.datetime:
        return dt.datetime.fromtimestamp(int(self.clock()), dt.timezone.utc)

    def _server_time(self) -> str:
        return pki.rfc3339(self._now())

    # -- routes -------------------------------------------------------------

    def root(self, environ: dict):
        return 200, protocol.RootResponse(
            protocol=protocol.PROTOCOL_MAX, protocol_min=protocol.PROTOCOL_MIN,
            protocol_max=protocol.PROTOCOL_MAX, root_pem=pki.cert_pem(self.ca.root))

    def enroll(self, environ: dict):
        source = str(environ.get("REMOTE_ADDR") or "unknown")
        req, version = self._request(environ, protocol.EnrollRequest)
        if req.fleet_id != self.fleet.fleet_id:
            self._invalid_token(source, version, "join string for another fleet")
        issued: list[x509.Certificate] = []

        def issue():
            csr = pki.load_node_csr(req.csr_pem, req.node_id)
            leaf = self.ca.sign_node(csr, req.node_id)
            issued.append(leaf)
            return (pki.fingerprint(leaf), format(leaf.serial_number, "x"),
                    self.intermediate_sha256, int(pki.not_after(leaf).timestamp()))

        try:
            code, row = self.registry.enroll(
                req.join_token.reveal(), req.node_id, req.instance_id, req.node_name,
                req.site_name, req.software.version, req.software.edition, issue)
        except pki.CsrRejected as err:
            raise Refused("bad_request", "certificate request refused",
                          reasons=tuple(f"csr_pem: {r}" for r in err.reasons),
                          _version=version) from None
        if code == "token_invalid":
            self._invalid_token(source, version, "unknown, used or expired join token")
        if code == "node_exists":
            raise Refused("node_exists", f"node {req.node_id} is already enrolled",
                          _version=version)
        leaf = issued[0]
        log.info("enrolled node %s in site %s (certificate until %s)", req.node_id,
                 row["site_id"], pki.rfc3339(pki.not_after(leaf)))
        return 200, protocol.EnrollResponse(
            protocol=version, fleet_id=self.fleet.fleet_id, node_id=req.node_id,
            site_id=row["site_id"], cert_chain=self.ca.chain(leaf),
            not_after=pki.rfc3339(pki.not_after(leaf)), lead_endpoints=self.endpoints,
            heartbeat_interval=self.heartbeat_interval,
            # The data plane is not built yet: "null until the lead has a
            # store" (protocol.EnrollResponse.store).
            store=None)

    def _invalid_token(self, source: str, version: int, detail: str):
        """Refuse an invalid token: token_invalid, or rate_limited once this
        source sent LOCKOUT_PER_SOURCE of them in the window. Only invalid
        tokens are ever throttled; the token is always looked up first."""
        self.registry.record_failure(source)
        count, wait = self.registry.failures_from(source)
        if count == registry_mod.LOCKOUT_PER_SOURCE:
            log.warning("enrollment: %s sent %d invalid join tokens in %d minutes; its "
                        "invalid tokens are answered rate_limited until the window slides "
                        "(a valid token still enrolls)", source, count,
                        registry_mod.LOCKOUT_WINDOW // 60)
        if count >= registry_mod.LOCKOUT_PER_SOURCE:
            raise Refused("rate_limited", detail + ", source throttled",
                          retry_after=min(wait, 86400), _version=version)
        raise Refused("token_invalid", detail, _version=version)

    def heartbeat(self, environ: dict):
        req, version = self._request(environ, protocol.HeartbeatRequest)
        record, cert = self._client(environ, req.node_id, version)
        node = self.registry.node(req.node_id)
        if node is None:  # pragma: no cover - a cert row always has its node
            raise Refused("unauthenticated", "certificate for an unknown node", _version=version)
        if record.revoked or node["state"] in ("revoked", "left"):
            reason = "left" if node["state"] == "left" else "operator"
            log.info("heartbeat from revoked node %s: told to stop", req.node_id)
            return 200, protocol.HeartbeatResponse(
                protocol=version, server_time=self._server_time(),
                next_heartbeat=protocol.HEARTBEAT_MAX,
                directives=(protocol.Revoked(id=directive_id(), reason=reason),))
        if self.registry.claim_instance(req.node_id, req.instance_id) != "ok":
            raise Refused("duplicate_node", f"node {req.node_id}: a replaced instance spoke "
                          "again, two installations share this node id", _version=version)
        inv = req.inventory
        summary = protocol.redacted(req)
        if not self.registry.heartbeat(
                req.node_id, node_name=inv.node_name, site_name=inv.site_name,
                version=inv.version, edition=inv.edition, lead_epoch=req.lead_epoch,
                summary=summary):
            # Revoked (or left) between the checks above and this write.
            now = self.registry.node(req.node_id) or {}
            return 200, protocol.HeartbeatResponse(
                protocol=version, server_time=self._server_time(),
                next_heartbeat=protocol.HEARTBEAT_MAX,
                directives=(protocol.Revoked(
                    id=directive_id(),
                    reason="left" if now.get("state") == "left" else "operator"),))
        directives: list[Any] = []
        if req.lead_epoch < self.endpoints.epoch:
            directives.append(protocol.SetLeadEndpoints(id=directive_id(),
                                                        lead_endpoints=self.endpoints))
        if (pki.not_after(cert) - self._now() < RENEW_NUDGE
                or record.issuer != self.intermediate_sha256):
            # Near expiry, or issued by another lead's intermediate (the
            # role moved): a certificate from this lead's own.
            directives.append(protocol.RenewNow(id=directive_id()))
        return 200, protocol.HeartbeatResponse(
            protocol=version, server_time=self._server_time(),
            next_heartbeat=self.heartbeat_interval, directives=tuple(directives))

    def renew(self, environ: dict):
        req, version = self._request(environ, protocol.RenewRequest)
        record, cert = self._client(environ, req.node_id, version)
        node = self.registry.node(req.node_id)
        if record.revoked or node is None or node["state"] in ("revoked", "left"):
            raise Refused("revoked", f"renew from revoked node {req.node_id}", _version=version)
        if self.registry.claim_instance(req.node_id, req.instance_id) != "ok":
            raise Refused("duplicate_node", f"node {req.node_id}: renew from a second "
                          "installation", _version=version)
        try:
            csr = pki.load_node_csr(req.csr_pem, req.node_id)
        except pki.CsrRejected as err:
            raise Refused("bad_request", "certificate request refused",
                          reasons=tuple(f"csr_pem: {r}" for r in err.reasons),
                          _version=version) from None
        if pki.same_public_key(csr.public_key(), cert.public_key()):
            raise Refused("bad_request", "renewal reuses the current key",
                          reasons=("csr_pem: must be for a new key",), _version=version)
        leaf = self.ca.sign_node(csr, req.node_id)
        if not self.registry.add_cert(req.node_id, pki.fingerprint(leaf),
                                      format(leaf.serial_number, "x"), self.intermediate_sha256,
                                      int(pki.not_after(leaf).timestamp()),
                                      presented=record.fingerprint):
            # Revoked between the checks above and this write: the
            # certificate just signed is never recorded, so never valid here.
            raise Refused("revoked", f"renew from node {req.node_id}, revoked meanwhile",
                          _version=version)
        self.registry.seen(req.node_id)
        log.info("renewed node %s (certificate until %s)", req.node_id,
                 pki.rfc3339(pki.not_after(leaf)))
        return 200, protocol.RenewResponse(protocol=version, cert_chain=self.ca.chain(leaf),
                                           not_after=pki.rfc3339(pki.not_after(leaf)))

    def leave(self, environ: dict):
        req, version = self._request(environ, protocol.LeaveRequest)
        der = peer_cert_der(environ)
        record = self.registry.cert(pki.der_fingerprint(der)) if der else None
        node = self.registry.node(req.node_id)
        if (record is not None and node is not None and record.node_id == req.node_id
                and node["state"] == "left"):
            # Asked again (the first answer was lost): the same answer.
            return 200, protocol.LeaveResponse(protocol=version, state="left")
        record, _cert = self._client(environ, req.node_id, version)
        if record.revoked or node is None or node["state"] == "revoked":
            raise Refused("revoked", f"leave from revoked node {req.node_id}", _version=version)
        if self.registry.claim_instance(req.node_id, req.instance_id) != "ok":
            raise Refused("duplicate_node", f"node {req.node_id}: leave from a second "
                          "installation", _version=version)
        if not self.registry.set_state(req.node_id, "left",
                                       "drained" if req.drained else "not_drained"):
            raise Refused("revoked", f"leave from node {req.node_id}, revoked meanwhile",
                          _version=version)
        log.info("node %s left the fleet (queue drained: %s, %d bytes left)", req.node_id,
                 "yes" if req.drained else "no", req.remaining_bytes)
        return 200, protocol.LeaveResponse(protocol=version, state="left")
