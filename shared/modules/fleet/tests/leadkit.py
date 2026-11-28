"""Test helpers for the lead: a lead in a temporary directory, node keys and
CSRs, and WSGI calls made in-process (no socket: the TLS handshake's result
is a fake ``gunicorn.socket`` whose ``getpeercert`` returns the DER)."""

from __future__ import annotations

import io
import json
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from fleet import admin, layout, protocol, settings
from fleet.lead import build

LEAD_URL = "https://lead.example:8443"
NODE = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
NODE2 = "7a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
INSTANCE = "11111111-2222-4333-8444-555555555555"
INSTANCE2 = "22222222-3333-4444-8555-666666666666"


class Clock:
    """Starts at the real time: certificates are issued with the real clock."""

    def __init__(self, start: float | None = None):
        self.t = time.time() if start is None else start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def cfg(urls=(LEAD_URL,), mode="node"):
    return settings.load({"FLEET_LEAD_URLS": ",".join(urls), "FLEET_LEAD_MODE": mode})


def make_lead(root, urls=(LEAD_URL,), mode="node", clock=None, name="Home fleet"):
    """Initialize a fleet under ``root`` and return (paths, service)."""
    paths = layout.lead_paths(root)
    admin.init(paths, cfg(urls, mode), name)
    service, *_ = build(cfg(urls, mode), paths)
    if clock is not None:
        service.clock = clock
        service.registry.clock = clock
    return paths, service


def rebuild(paths, urls=(LEAD_URL,), mode="node", clock=None):
    service, *_ = build(cfg(urls, mode), paths)
    if clock is not None:
        service.clock = clock
        service.registry.clock = clock
    return service


def node_key():
    return ec.generate_private_key(ec.SECP256R1())


def csr_pem(key=None, cn: str | None = None, uri: str | None = None) -> tuple[object, str]:
    key = key or node_key()
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, cn)] if cn else []
    builder = x509.CertificateSigningRequestBuilder().subject_name(x509.Name(attrs))
    if uri:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(uri)]), critical=False)
    csr = builder.sign(key, hashes.SHA256())
    return key, csr.public_bytes(serialization.Encoding.PEM).decode()


class FakeTLSSocket:
    def __init__(self, der: bytes | None):
        self.der = der

    def getpeercert(self, binary_form=False):
        assert binary_form
        return self.der


def der_of(pem: str) -> bytes:
    return x509.load_pem_x509_certificate(pem.encode()).public_bytes(serialization.Encoding.DER)


def call(service, method: str, path: str, body=None, cert_pem: str | None = None,
         remote: str = "192.0.2.5", raw: bytes | None = None, length: str | None = None):
    """(status, JSON body) of one request."""
    data = raw if raw is not None else (b"" if body is None else json.dumps(body).encode())
    environ = {
        "REQUEST_METHOD": method, "PATH_INFO": path, "REMOTE_ADDR": remote,
        "CONTENT_LENGTH": length if length is not None else str(len(data)),
        "wsgi.input": io.BytesIO(data),
        "gunicorn.socket": FakeTLSSocket(der_of(cert_pem) if cert_pem else None),
    }
    seen = {}

    def start_response(status, headers):
        seen["status"] = int(status.split()[0])
        seen["headers"] = dict(headers)

    out = b"".join(service(environ, start_response))
    assert seen["headers"]["Content-Type"] == "application/json"
    return seen["status"], json.loads(out)


def new_join(paths, ttl=900, name=None, site=None, urls=(LEAD_URL,)) -> protocol.JoinString:
    text, _about = admin.token(paths, cfg(urls), ttl, name, site)
    return protocol.JoinString.parse(text)


def enroll_body(join: protocol.JoinString, node_id=NODE, instance=INSTANCE, key=None,
                name="Kitchen Pi", site="Home", csr: str | None = None, fleet_id=None):
    key = key or node_key()
    if csr is None:
        _k, csr = csr_pem(key)
    return key, {
        "protocol": 1, "protocol_min": 1, "join_token": join.token.reveal(),
        "fleet_id": fleet_id or join.fleet_id, "node_id": node_id, "instance_id": instance,
        "node_name": name, "site_name": site, "csr_pem": csr,
        "software": {"version": "2.56.0", "edition": "pro"},
    }


def enroll(service, paths, node_id=NODE, instance=INSTANCE, **kw):
    """Enroll a node; returns (key, decoded EnrollResponse)."""
    join = new_join(paths)
    key, body = enroll_body(join, node_id=node_id, instance=instance, **kw)
    status, out = call(service, "POST", "/fleet/enroll", body)
    assert status == 200, out
    return key, protocol.decode(protocol.EnrollResponse, out)


def heartbeat_body(node_id=NODE, instance=INSTANCE, epoch=1, **inventory):
    inv = {"node_name": "Kitchen Pi", "site_name": "Home", "version": "2.56.0",
           "edition": "pro", "profiles": ["influxdb", "fleet"], "capabilities": ["influxdb"]}
    inv.update(inventory)
    return {"protocol": 1, "protocol_min": 1, "node_id": node_id, "instance_id": instance,
            "sent_at": "2027-01-15T08:00:00Z", "lead_epoch": epoch, "inventory": inv}
