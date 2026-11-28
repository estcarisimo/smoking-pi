"""A throwaway fleet CA and a fake lead, for the node's tests.

Nothing here is the lead (PR B's ``fleet/pki.py`` and ``fleet/lead.py``):
the CA is built with ``cryptography`` directly, and the "lead" is a
transport that answers with :mod:`fleet.protocol` types. No socket opens.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from fleet import chain, nodekeys, protocol
from fleet.transport import Tls, TransportError

NODE = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
FLEET = "0b1e2f3a-0000-4000-8000-000000000001"
SITE = "5e1e0000-0000-4000-8000-0000000000a1"
LEAD = "https://lead.example:8443"
LEAD2 = "https://lead2.example"
TOKEN = "TOKENsecretTOKENsecretTOKENsecret1234"


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def now() -> datetime:
    return datetime.now(timezone.utc)


class CA:
    """A root and one intermediate, as a lead's would be."""

    def __init__(self, name: str = "fleet"):
        self.root_key = ec.generate_private_key(ec.SECP256R1())
        self.root = self._ca(_name(f"{name} root"), self.root_key, _name(f"{name} root"),
                             self.root_key)
        self.inter_key = ec.generate_private_key(ec.SECP256R1())
        self.inter = self._ca(_name(f"{name} intermediate"), self.inter_key,
                              self.root.subject, self.root_key, path_length=0)

    @staticmethod
    def _ca(subject, key, issuer_name, issuer_key, path_length=None):
        t = now()
        return (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer_name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(t - timedelta(days=1)).not_valid_after(t + timedelta(days=3650))
                .add_extension(x509.BasicConstraints(ca=True, path_length=path_length),
                               critical=True)
                .add_extension(x509.KeyUsage(False, False, False, False, False, True, True,
                                             False, False), critical=True)
                .sign(issuer_key, hashes.SHA256()))

    @property
    def root_pem(self) -> str:
        return chain.pem(self.root)

    @property
    def fingerprint(self) -> str:
        return chain.fingerprint(self.root)

    def issue(self, csr_pem: str, node_id: str = NODE, *, days: float = 90,
              start: datetime | None = None, public_key=None) -> tuple[str, ...]:
        """(leaf, intermediate) for the CSR's key (or ``public_key``)."""
        csr = x509.load_pem_x509_csr(csr_pem.encode())
        start = start or now() - timedelta(minutes=5)
        leaf = (x509.CertificateBuilder().subject_name(_name(f"node-{node_id}"))
                .issuer_name(self.inter.subject)
                .public_key(public_key or csr.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(start).not_valid_after(start + timedelta(days=days))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName(
                    [x509.UniformResourceIdentifier(nodekeys.NODE_URN_PREFIX + node_id)]),
                    critical=False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
                               critical=False)
                .sign(self.inter_key, hashes.SHA256()))
        return (chain.pem(leaf), chain.pem(self.inter))


    def issue_server(self, dns: tuple[str, ...] = (), ips: tuple[str, ...] = ()):
        """A lead's TLS server certificate: (chain PEM leaf+intermediate,
        key PEM)."""
        import ipaddress

        from cryptography.hazmat.primitives import serialization
        key = ec.generate_private_key(ec.SECP256R1())
        names = [x509.DNSName(d) for d in dns] + [
            x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
        t = now()
        leaf = (x509.CertificateBuilder().subject_name(_name("lead"))
                .issuer_name(self.inter.subject).public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(t - timedelta(minutes=5))
                .not_valid_after(t + timedelta(days=30))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName(names), critical=False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                               critical=False)
                .sign(self.inter_key, hashes.SHA256()))
        key_pem = key.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
        return chain.pem(leaf) + chain.pem(self.inter), key_pem


def endpoints(*urls: str, epoch: int = 1, current: str | None = None):
    urls = urls or (LEAD,)
    return protocol.LeadEndpoints(epoch=epoch, current=current or urls[0], urls=tuple(urls),
                                  lead_mode="node")


def join_string(ca: CA, *urls: str, fingerprint: str | None = None) -> str:
    return protocol.JoinString(fleet_id=FLEET, lead_urls=tuple(urls or (LEAD,)),
                               token=protocol.Secret(TOKEN),
                               root_sha256=fingerprint or ca.fingerprint).to_string()


def error(code: str, **extra) -> tuple[int, protocol.ErrorResponse]:
    return protocol.error(code, error_id="0123456789abcdef", **extra)


@dataclass
class Call:
    method: str
    url: str
    body: dict | None
    tls: Tls


Handler = Callable[[dict | None, Tls], tuple[int, Any]]


@dataclass
class FakeLead:
    """Answers per (method, URL); a URL in ``down`` refuses the connection."""

    ca: CA = field(default_factory=CA)
    routes: dict[tuple[str, str], Handler] = field(default_factory=dict)
    down: set[str] = field(default_factory=set)
    calls: list[Call] = field(default_factory=list)
    directives: list[list[dict]] = field(default_factory=list)

    def request(self, method: str, url: str, body: bytes | None, tls: Tls):
        self.calls.append(Call(method, url, json.loads(body) if body else None, tls))
        if any(url.startswith(base + "/") for base in self.down):
            raise TransportError("refused")
        handler = self.routes.get((method, url))
        if handler is None:
            raise TransportError("refused")
        status_code, payload = handler(self.calls[-1].body, tls)
        if isinstance(payload, bytes):
            return status_code, payload
        if not isinstance(payload, dict):
            payload = protocol.encode(payload)
        return status_code, json.dumps(payload).encode()

    def to(self, route: str) -> list[Call]:
        return [c for c in self.calls if c.url.endswith(route)]

    # -- the routes a lead serves ---------------------------------------------

    def serve_root(self, base: str = LEAD, root_pem: str | None = None) -> None:
        self.routes[("GET", base + "/fleet/root")] = lambda b, t: (200, protocol.RootResponse(
            protocol=1, protocol_min=1, protocol_max=1, root_pem=root_pem or self.ca.root_pem))

    def serve_enroll(self, base: str = LEAD, *, issuer: CA | None = None, lead_urls=None,
                     store=None, **issue_kw) -> None:
        def enroll(body, tls):
            leaf_chain = (issuer or self.ca).issue(body["csr_pem"], body["node_id"], **issue_kw)
            return 200, protocol.EnrollResponse(
                protocol=1, fleet_id=body["fleet_id"], node_id=body["node_id"], site_id=SITE,
                cert_chain=leaf_chain, not_after=protocol.now_rfc3339(),
                lead_endpoints=endpoints(*(lead_urls or (base,))), heartbeat_interval=60,
                store=store)
        self.routes[("POST", base + "/fleet/enroll")] = enroll

    def serve_heartbeat(self, base: str = LEAD, next_heartbeat: int = 60) -> None:
        """Each answer carries the next list queued in ``directives``."""
        def beat(body, tls):
            out = {"protocol": 1, "server_time": protocol.now_rfc3339(),
                   "next_heartbeat": next_heartbeat}
            if self.directives:
                out["directives"] = self.directives.pop(0)
            return 200, out
        self.routes[("POST", base + "/fleet/heartbeat")] = beat

    def serve_renew(self, base: str = LEAD, *, issuer: CA | None = None, **issue_kw) -> None:
        def renew(body, tls):
            leaf_chain = (issuer or self.ca).issue(body["csr_pem"], body["node_id"], **issue_kw)
            return 200, protocol.RenewResponse(protocol=1, cert_chain=leaf_chain,
                                               not_after=protocol.now_rfc3339())
        self.routes[("POST", base + "/fleet/renew")] = renew

    def serve_leave(self, base: str = LEAD) -> None:
        self.routes[("POST", base + "/fleet/leave")] = lambda b, t: (
            200, protocol.LeaveResponse(protocol=1, state="left"))

    def answer(self, method: str, url: str, status_code: int, payload: Any) -> None:
        self.routes[(method, url)] = lambda b, t: (status_code, payload)

    def serve_all(self, base: str = LEAD) -> None:
        self.serve_root(base)
        self.serve_enroll(base)
        self.serve_heartbeat(base)
        self.serve_renew(base)
        self.serve_leave(base)


def private_key_marker(paths) -> str:
    """A distinctive line of the node's private key PEM (its body)."""
    lines = paths.key.read_text().splitlines()
    return max(lines[1:-1], key=len)


def enrolled(paths, lead: FakeLead | None = None, *urls: str, **env) -> FakeLead:
    """A node joined to ``lead`` (a new FakeLead serving every route at
    each of ``urls`` by default), through the real join code."""
    from fleet import join, settings
    urls = urls or (LEAD,)
    if lead is None:
        lead = FakeLead()
        for url in urls:
            lead.serve_all(url)
            lead.serve_enroll(url, lead_urls=urls)
    cfg = settings.load({"FLEET_NODE_ID": NODE, **env})
    join.enroll(protocol.JoinString.parse(join_string(lead.ca, *urls)), cfg, paths, lead)
    lead.calls.clear()
    return lead
