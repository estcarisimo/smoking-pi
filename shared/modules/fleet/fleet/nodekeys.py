"""The node's key and certificate request: node side only.

The key is P-256, generated on the node (by ``fleet join``, and again at
every renewal), written mode 0600 in the agent's volume and never sent:
only the certificate request built from it leaves the node. Nothing here
logs, and no function returns the key's bytes except :func:`key_pem`, whose
one caller writes them to the key file.
"""

from __future__ import annotations

import hmac
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

#: The identity the lead puts in a node's certificate (protocol.EnrollRequest).
NODE_URN_PREFIX = "urn:smoking-pi:node:"


class NodeKeyError(ValueError):
    """The key file is not a P-256 private key. The message is literal."""


def new_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    """PKCS#8 PEM, unencrypted: the volume's mode is its protection, and the
    agent must start unattended."""
    return key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def load_key(path: Path) -> ec.EllipticCurvePrivateKey:
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError):
        raise NodeKeyError("the node key is unreadable or not a PEM private key") from None
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise NodeKeyError("the node key is not a P-256 key")
    return key


def csr_pem(key: ec.EllipticCurvePrivateKey, node_id: str) -> str:
    """A request for ``node-<id>`` / ``urn:smoking-pi:node:<id>``. The lead
    ignores the subject and issues for the node id it was sent; it is set
    anyway so the request reads as what it is."""
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"node-{node_id}")]))
           .add_extension(x509.SubjectAlternativeName(
               [x509.UniformResourceIdentifier(NODE_URN_PREFIX + node_id)]), critical=False)
           .sign(key, hashes.SHA256()))
    return csr.public_bytes(serialization.Encoding.PEM).decode()


def public_der(public_key) -> bytes:
    return public_key.public_bytes(serialization.Encoding.DER,
                                   serialization.PublicFormat.SubjectPublicKeyInfo)


def matches(cert: x509.Certificate, key: ec.EllipticCurvePrivateKey) -> bool:
    """Whether the certificate was issued for this key."""
    try:
        return hmac.compare_digest(public_der(cert.public_key()), public_der(key.public_key()))
    except (ValueError, TypeError):
        return False
