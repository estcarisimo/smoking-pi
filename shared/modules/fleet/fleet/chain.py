"""Checking what a lead hands a node against the root the node pinned.

Node side only (the lead's CA is ``fleet/pki.py``). The node trusts one
certificate: the fleet root whose SHA-256 the join string carried. Every
certificate chain a lead sends (the node's own, at enrollment and at each
renewal) must lead up to that root, signature by signature; the system's
trust store is never consulted. TLS to the lead is checked the same way by
:mod:`fleet.transport`, with the pinned root as the only anchor.

Every :class:`ChainError` reason is a literal string, safe to log and print.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from typing import Sequence

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization

from .nodekeys import NODE_URN_PREFIX


class ChainError(ValueError):
    """A certificate or chain was refused; ``reason`` is literal."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def load(pem: str | bytes) -> x509.Certificate:
    try:
        return x509.load_pem_x509_certificate(pem.encode() if isinstance(pem, str) else pem)
    except ValueError:
        raise ChainError("a certificate is not a readable PEM certificate") from None


def der(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.DER)


def pem(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def fingerprint(cert: x509.Certificate) -> str:
    """SHA-256 of the DER, lowercase hex: what the join string pins."""
    return hashlib.sha256(der(cert)).hexdigest()


def _is_ca(cert: x509.Certificate) -> bool:
    try:
        return bool(cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
    except x509.ExtensionNotFound:
        return False


def _issued_by(cert: x509.Certificate, issuer: x509.Certificate) -> bool:
    try:
        cert.verify_directly_issued_by(issuer)
    except (ValueError, TypeError, InvalidSignature):
        return False
    return True


def pinned_root(root_pem: str, sha256: str) -> x509.Certificate:
    """The root a lead served, accepted only if it is the pinned one."""
    root = load(root_pem)
    if not hmac.compare_digest(fingerprint(root), sha256.lower()):
        raise ChainError("the lead's root certificate does not match the fingerprint in "
                         "the join string")
    if not _is_ca(root) or not _issued_by(root, root):
        raise ChainError("the pinned root is not a self-signed CA certificate")
    return root


def verify(chain_pems: Sequence[str], root: x509.Certificate,
           now: datetime | None) -> list[x509.Certificate]:
    """The chain, leaf first, checked up to ``root``: each certificate signed
    by the next, the last by the root, every issuer a CA, the leaf not one.
    With ``now``, every certificate must also be valid at that time (None
    skips the dates: the caller judges expiry itself). Returns the
    certificates without the root, should the lead have appended it."""
    certs = [load(p) for p in chain_pems]
    if certs and der(certs[-1]) == der(root):
        certs = certs[:-1]
    if not certs:
        raise ChainError("the chain holds no certificate for the node")
    if _is_ca(certs[0]):
        raise ChainError("the chain's first certificate is a CA, not a node certificate")
    for i, cert in enumerate(certs):
        issuer = certs[i + 1] if i + 1 < len(certs) else root
        if not _is_ca(issuer) or not _issued_by(cert, issuer):
            raise ChainError("the certificate chain does not lead to the pinned root")
    if now is not None:
        for cert in [*certs, root]:
            if not cert.not_valid_before_utc <= now <= cert.not_valid_after_utc:
                raise ChainError("a certificate in the chain is not valid at this time")
    return certs


def node_identity_ok(leaf: x509.Certificate, node_id: str) -> bool:
    """The leaf names this node: its ``urn:smoking-pi:node:`` URIs and its
    CN, whichever are there (at least one), all name ``node_id``."""
    names: list[str] = []
    try:
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names += [u[len(NODE_URN_PREFIX):] for u in san.get_values_for_type(
            x509.UniformResourceIdentifier) if u.startswith(NODE_URN_PREFIX)]
    except x509.ExtensionNotFound:
        pass
    for attr in leaf.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME):
        value = attr.value if isinstance(attr.value, str) else ""
        names.append(value[len("node-"):] if value.startswith("node-") else value)
    return bool(names) and all(n == node_id for n in names)
