"""The fleet's private CA, lead side (docs/fleet.md, "Security model").

Layout: a **root** (self-signed, path length 1) whose key signs only
intermediates and is meant to go offline once this lead's intermediate
exists; one **intermediate per lead** (path length 0) that signs node client
certificates and this lead's own TLS server certificate. Nodes pin the root
by the SHA-256 of its DER (the join string carries it), so a lead change
never changes what nodes trust; every certificate the lead hands out
travels as a chain, leaf then intermediate.

Every key is ECDSA P-256. Private keys are written once, mode 0600, through
:func:`write_private`, and never enter a message or a log. Validation
failures raise :class:`CsrRejected` with literal reason strings, never an
exception's text.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .protocol import CERT_LIFETIME_DAYS

ROOT_LIFETIME_DAYS = 20 * 365
INTERMEDIATE_LIFETIME_DAYS = 5 * 365
SERVER_LIFETIME_DAYS = 365
#: The lead re-issues its server certificate when less than this is left.
SERVER_RENEW_DAYS = 30
#: Certificates start this long before "now": a node's clock a little behind
#: the lead's must not see a certificate from the future.
BACKDATE = dt.timedelta(minutes=5)

ORG = "Smoking Pi Fleet"
NODE_URN_PREFIX = "urn:smoking-pi:node:"


class CsrRejected(ValueError):
    """A certificate request the lead will not sign. ``reasons`` are literal."""

    def __init__(self, *reasons: str):
        self.reasons = tuple(reasons)
        super().__init__("; ".join(reasons))


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def write_private(path: Path, data: bytes) -> None:
    """Write a secret: created 0600 (never wider, not even for a moment),
    then renamed into place so a reader sees the old file or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_public(path: Path, data: bytes) -> None:
    """A certificate or other non-secret file, 0644, replaced atomically."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Keys and certificates
# ---------------------------------------------------------------------------


def new_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def load_key(data: bytes) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve,
                                                                         ec.SECP256R1):
        raise ValueError("not a P-256 private key")
    return key


def cert_pem(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def load_cert(data: bytes | str) -> x509.Certificate:
    return x509.load_pem_x509_certificate(data.encode() if isinstance(data, str) else data)


def fingerprint(cert: x509.Certificate) -> str:
    """SHA-256 of the DER, lowercase hex: what the join string pins."""
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def der_fingerprint(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def same_public_key(a, b) -> bool:
    fmt = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return a.public_bytes(*fmt) == b.public_bytes(*fmt)


def not_after(cert: x509.Certificate) -> dt.datetime:
    return cert.not_valid_after_utc


def rfc3339(when: dt.datetime) -> str:
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> dt.datetime:
    # Whole seconds: the protocol's times have no fraction, and a not_after
    # that rounds differently on the wire than in the registry is a bug.
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _serial() -> int:
    # 159 random bits: positive, at most 20 octets (RFC 5280).
    return secrets.randbits(159) | 1


def _builder(subject: x509.Name, issuer: x509.Name, public_key, days: int,
             now: dt.datetime | None = None) -> x509.CertificateBuilder:
    now = now or _now()
    return (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(issuer).public_key(public_key)
            .serial_number(_serial())
            .not_valid_before(now - BACKDATE)
            .not_valid_after(now + dt.timedelta(days=days))
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(public_key),
                           critical=False))


def _aki(issuer_cert: x509.Certificate) -> x509.AuthorityKeyIdentifier:
    ski = issuer_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
    return x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ski)


def _ca_usage() -> x509.KeyUsage:
    return x509.KeyUsage(digital_signature=False, content_commitment=False,
                         key_encipherment=False, data_encipherment=False, key_agreement=False,
                         key_cert_sign=True, crl_sign=True, encipher_only=False,
                         decipher_only=False)


def _leaf_usage() -> x509.KeyUsage:
    return x509.KeyUsage(digital_signature=True, content_commitment=False,
                         key_encipherment=False, data_encipherment=False, key_agreement=False,
                         key_cert_sign=False, crl_sign=False, encipher_only=False,
                         decipher_only=False)


def make_root(fleet_id: str, key: ec.EllipticCurvePrivateKey) -> x509.Certificate:
    name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORG),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, f"fleet {fleet_id}"),
        x509.NameAttribute(NameOID.COMMON_NAME, f"Smoking Pi fleet root {fleet_id[:8]}"),
    ])
    return (_builder(name, name, key.public_key(), ROOT_LIFETIME_DAYS)
            .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
            .add_extension(_ca_usage(), critical=True)
            .sign(key, hashes.SHA256()))


def intermediate_name(fleet_id: str, lead_id: str) -> x509.Name:
    return x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORG),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, f"fleet {fleet_id}"),
        x509.NameAttribute(NameOID.COMMON_NAME, f"Smoking Pi fleet lead {lead_id}"),
    ])


def intermediate_csr(fleet_id: str, lead_id: str,
                     key: ec.EllipticCurvePrivateKey) -> x509.CertificateSigningRequest:
    """What a second (or moved) lead hands to whoever holds the root key."""
    return (x509.CertificateSigningRequestBuilder()
            .subject_name(intermediate_name(fleet_id, lead_id))
            .sign(key, hashes.SHA256()))


def sign_intermediate(csr: x509.CertificateSigningRequest, root_cert: x509.Certificate,
                      root_key: ec.EllipticCurvePrivateKey) -> x509.Certificate:
    """The root signs a lead's intermediate. The subject is taken from the
    request only after checking it names this fleet; the lifetime never
    outlives the root."""
    _check_p256_csr(csr)
    ou = root_cert.subject.get_attributes_for_oid(NameOID.ORGANIZATIONAL_UNIT_NAME)
    csr_ou = csr.subject.get_attributes_for_oid(NameOID.ORGANIZATIONAL_UNIT_NAME)
    if not ou or not csr_ou or csr_ou[0].value != ou[0].value:
        raise CsrRejected("intermediate request: not for this fleet")
    days = min(INTERMEDIATE_LIFETIME_DAYS,
               (not_after(root_cert) - _now()).days - 1)
    if days < 30:
        raise CsrRejected("root: expires too soon to sign an intermediate")
    return (_builder(csr.subject, root_cert.subject, csr.public_key(), days)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(_ca_usage(), critical=True)
            .add_extension(_aki(root_cert), critical=False)
            .sign(root_key, hashes.SHA256()))


def _check_p256_csr(csr: x509.CertificateSigningRequest) -> None:
    try:
        public = csr.public_key()
    except (ValueError, TypeError):
        raise CsrRejected("csr: unsupported key") from None
    if not isinstance(public, ec.EllipticCurvePublicKey) or not isinstance(public.curve,
                                                                           ec.SECP256R1):
        raise CsrRejected("csr: the key must be ECDSA P-256")
    try:
        valid = csr.is_signature_valid
    except (ValueError, InvalidSignature):
        valid = False
    if not valid:
        raise CsrRejected("csr: the signature does not verify")


def node_urn(node_id: str) -> str:
    return NODE_URN_PREFIX + node_id


def load_node_csr(pem: str, node_id: str) -> x509.CertificateSigningRequest:
    """Parse and validate a node's request: PEM, P-256, a signature that
    verifies (proof the node holds the key), and no other node's id in it.

    The lead issues its own subject (CN ``node-<id>``, SAN URN); what the
    request asks for is ignored, except that a request naming *another* node
    (by CN ``node-…`` or a node URN) is refused: that is a bug or a copy."""
    try:
        csr = x509.load_pem_x509_csr(pem.encode())
    except (ValueError, TypeError):
        raise CsrRejected("csr: not a PEM certificate request") from None
    _check_p256_csr(csr)
    names = [a.value for a in csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)]
    for cn in names:
        if isinstance(cn, str) and cn.startswith("node-") and cn != f"node-{node_id}":
            raise CsrRejected("csr: names another node")
    try:
        san = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        uris = san.get_values_for_type(x509.UniformResourceIdentifier)
    except x509.ExtensionNotFound:
        uris = []
    except ValueError:
        raise CsrRejected("csr: unreadable extensions") from None
    for uri in uris:
        if uri.startswith(NODE_URN_PREFIX) and uri != node_urn(node_id):
            raise CsrRejected("csr: names another node")
    return csr


def issue_node_cert(csr: x509.CertificateSigningRequest, node_id: str,
                    issuer_cert: x509.Certificate,
                    issuer_key: ec.EllipticCurvePrivateKey) -> x509.Certificate:
    """A client certificate for ``node_id``: 90 days, clientAuth only."""
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORG),
        x509.NameAttribute(NameOID.COMMON_NAME, f"node-{node_id}"),
    ])
    days = min(CERT_LIFETIME_DAYS, max(1, (not_after(issuer_cert) - _now()).days))
    return (_builder(subject, issuer_cert.subject, csr.public_key(), days)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(_leaf_usage(), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
                           critical=False)
            .add_extension(x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier(node_urn(node_id))]), critical=False)
            .add_extension(_aki(issuer_cert), critical=False)
            .sign(issuer_key, hashes.SHA256()))


def node_id_of(cert: x509.Certificate) -> str | None:
    """The node id a client certificate was issued for (its SAN URN)."""
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except (x509.ExtensionNotFound, ValueError):
        return None
    ids = [u[len(NODE_URN_PREFIX):] for u in san.get_values_for_type(
        x509.UniformResourceIdentifier) if u.startswith(NODE_URN_PREFIX)]
    return ids[0] if len(ids) == 1 else None


def url_hosts(urls) -> list[str]:
    """The host part of each lead URL, de-duplicated, in order."""
    out: list[str] = []
    for url in urls:
        host = urlsplit(url).hostname
        if host and host not in out:
            out.append(host)
    return out


def _san_entry(host: str) -> x509.GeneralName:
    try:
        return x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        return x509.DNSName(host.rstrip("."))


def issue_server_cert(hosts: list[str], lead_id: str, key: ec.EllipticCurvePrivateKey,
                      issuer_cert: x509.Certificate,
                      issuer_key: ec.EllipticCurvePrivateKey) -> x509.Certificate:
    """This lead's TLS certificate: one SAN per host nodes use to reach it."""
    if not hosts:
        raise ValueError("a server certificate needs at least one host")
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORG),
        x509.NameAttribute(NameOID.COMMON_NAME, f"lead-{lead_id[:8]}"),
    ])
    days = min(SERVER_LIFETIME_DAYS, max(1, (not_after(issuer_cert) - _now()).days))
    return (_builder(subject, issuer_cert.subject, key.public_key(), days)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(_leaf_usage(), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                           critical=False)
            .add_extension(x509.SubjectAlternativeName([_san_entry(h) for h in hosts]),
                           critical=False)
            .add_extension(_aki(issuer_cert), critical=False)
            .sign(issuer_key, hashes.SHA256()))


def server_hosts(cert: x509.Certificate) -> list[str]:
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return []
    return ([str(v) for v in san.get_values_for_type(x509.IPAddress)]
            + list(san.get_values_for_type(x509.DNSName)))


def verify_issued_by(cert: x509.Certificate, issuer: x509.Certificate) -> bool:
    try:
        cert.verify_directly_issued_by(issuer)
    except (ValueError, TypeError, InvalidSignature):
        return False
    return True


# ---------------------------------------------------------------------------
# The CA on disk
# ---------------------------------------------------------------------------


@dataclass
class CA:
    """The lead's CA files, loaded. ``root_key`` is None once it went
    offline: the lead then cannot sign another intermediate, which it never
    needs in normal operation."""

    root: x509.Certificate
    intermediate: x509.Certificate
    intermediate_key: ec.EllipticCurvePrivateKey
    root_key: ec.EllipticCurvePrivateKey | None = None

    @property
    def root_sha256(self) -> str:
        return fingerprint(self.root)

    def chain(self, leaf: x509.Certificate) -> tuple[str, str]:
        return (cert_pem(leaf), cert_pem(self.intermediate))

    def sign_node(self, csr: x509.CertificateSigningRequest, node_id: str) -> x509.Certificate:
        return issue_node_cert(csr, node_id, self.intermediate, self.intermediate_key)


def create(paths, fleet_id: str, lead_id: str) -> CA:
    """A new root and this lead's intermediate, written under ``paths``."""
    root_key = new_key()
    root = make_root(fleet_id, root_key)
    inter_key = new_key()
    inter = sign_intermediate(intermediate_csr(fleet_id, lead_id, inter_key), root, root_key)
    write_private(paths.root_key, key_pem(root_key))
    write_private(paths.intermediate_key, key_pem(inter_key))
    write_public(paths.root_cert, cert_pem(root).encode())
    write_public(paths.intermediate_cert, cert_pem(inter).encode())
    return CA(root=root, intermediate=inter, intermediate_key=inter_key, root_key=root_key)


class CAError(ValueError):
    """The CA on disk cannot be used. Raised without a message: the type
    says what is wrong, and :data:`CA_PROBLEMS` is the sentence a person
    reads, so no output is ever built from the exception object."""


class CAMissing(CAError):
    pass


class CANotChained(CAError):
    pass


class CAKeyMismatch(CAError):
    pass


class RootKeyBroken(CAError):
    pass


#: Exception type -> the literal sentence for it.
CA_PROBLEMS: dict[type, str] = {
    CAMissing: "the CA files are missing or unreadable",
    CANotChained: "the intermediate is not signed by the root",
    CAKeyMismatch: "the intermediate key does not match its certificate",
    RootKeyBroken: "the root key is unreadable or not the root's",
}


def load(paths, with_root_key: bool = False) -> CA:
    """Load the CA, or raise a :class:`CAError` subclass.

    The root key is read only when ``with_root_key``: the serving lead never
    holds it in memory, so taking it offline (deleting the file) takes it
    off the lead at once, without a restart."""
    try:
        root = load_cert(paths.root_cert.read_bytes())
        inter = load_cert(paths.intermediate_cert.read_bytes())
        inter_key = load_key(paths.intermediate_key.read_bytes())
    except (OSError, ValueError, TypeError):
        raise CAMissing() from None
    if not verify_issued_by(inter, root):
        raise CANotChained()
    if not same_public_key(inter.public_key(), inter_key.public_key()):
        raise CAKeyMismatch()
    root_key = None
    if with_root_key and paths.root_key.exists():
        try:
            root_key = load_key(paths.root_key.read_bytes())
        except (OSError, ValueError, TypeError):
            raise RootKeyBroken() from None
        if not same_public_key(root_key.public_key(), root.public_key()):
            raise RootKeyBroken()
    return CA(root=root, intermediate=inter, intermediate_key=inter_key, root_key=root_key)


def root_key_matches(ca: CA, pem: bytes) -> bool:
    """Whether ``pem`` is the private key of this fleet's root."""
    try:
        key = load_key(pem)
    except (ValueError, TypeError):
        return False
    return same_public_key(key.public_key(), ca.root.public_key())


_PEM_BLOCK = re.compile(rb"-----BEGIN ([A-Z ]+)-----\r?\n.*?-----END \1-----\r?\n?", re.S)


def read_server_pair(path: Path):
    """(key, leaf, intermediate) from the one file that holds all three, or
    ValueError when it is incomplete or the key is not the leaf's."""
    blocks = [(m.group(1), m.group(0)) for m in _PEM_BLOCK.finditer(path.read_bytes())]
    keys = [b for kind, b in blocks if kind == b"PRIVATE KEY"]
    certs = [b for kind, b in blocks if kind == b"CERTIFICATE"]
    if len(keys) != 1 or len(certs) != 2:
        raise ValueError("server key file: not one key and two certificates")
    key, leaf, inter = load_key(keys[0]), load_cert(certs[0]), load_cert(certs[1])
    if not same_public_key(key.public_key(), leaf.public_key()):
        raise ValueError("server key file: the key is not the certificate's")
    return key, leaf, inter


def ensure_server_cert(paths, ca: CA, lead_id: str, hosts: list[str],
                       now: dt.datetime | None = None) -> bool:
    """Make sure this lead's TLS key and certificate cover ``hosts``, belong
    together and have more than SERVER_RENEW_DAYS left; re-issue both (a
    new key) otherwise. Returns True when it wrote a new pair.

    The key, the leaf and the intermediate live in ONE file
    (``server_key``, 0600), which gunicorn reads as both its certfile and
    its keyfile: replacing it is a single rename, so a power cut leaves the
    old pair or the new one, never a key beside a certificate it does not
    match (which gunicorn cannot load: a crash loop). ``server_cert`` is a
    public copy of the chain, for people and the status, written after."""
    now = now or _now()
    try:
        _key, current, inter = read_server_pair(paths.server_key)
        fresh = (not_after(current) - now > dt.timedelta(days=SERVER_RENEW_DAYS)
                 and sorted(server_hosts(current)) == sorted(hosts_normalized(hosts))
                 and inter == ca.intermediate
                 and verify_issued_by(current, ca.intermediate))
    except (OSError, ValueError, TypeError):
        fresh = False
    if fresh:
        if not paths.server_cert.exists():
            write_public(paths.server_cert, "".join(ca.chain(current)).encode())
        return False
    key = new_key()
    cert = issue_server_cert(hosts, lead_id, key, ca.intermediate, ca.intermediate_key)
    chain = "".join(ca.chain(cert)).encode()
    write_private(paths.server_key, key_pem(key) + chain)
    write_public(paths.server_cert, chain)
    return True


def hosts_normalized(hosts: list[str]) -> list[str]:
    out = []
    for h in hosts:
        entry = _san_entry(h)
        out.append(str(entry.value))
    return out
