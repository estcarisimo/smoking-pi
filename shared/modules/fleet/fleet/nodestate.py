"""The node's enrollment on disk: what ``fleet join`` writes, the agent keeps
up to date, and ``fleet leave`` removes. Paths are :mod:`fleet.layout`'s.

``enrollment.json`` holds the non-secret facts, and its presence is what
"enrolled" means, so it is always written last and removed first:

* ``node_id``, ``fleet_id``, ``site_id``, ``root_sha256``, ``enrolled_at``
* ``not_after``: the current certificate's end (rewritten at each renewal)
* ``lead_endpoints``: the protocol's LeadEndpoints (epoch, current, urls,
  lead_mode), replaced by a ``lead_endpoints`` directive of a higher epoch
* ``heartbeat_interval``: the enroll response's, for the first heartbeat
* ``store``: the StoreDescriptor received, without its token (that one is
  in ``store-token``), and ``applied: false``: this release does not
  replicate (docs/fleet.md)
* ``seed_requested``: the last ``start_seed`` received, not applied either
* ``revoked``: ``{"at", "reason"}`` once the lead said so; only a new join
  (after ``fleet leave``) undoes it

The key and the chain are replaced as a pair at each renewal: both new
files are written beside the old ones, then swapped. A crash between the
two swaps is finished by :func:`finish_renewal` at the next start; a crash
before both new files exist leaves the old pair, which is still valid.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cryptography import x509

from . import chain, nodekeys, protocol, statefile
from .layout import NodePaths

_PEM_BLOCK = re.compile(
    r"-----BEGIN CERTIFICATE-----\r?\n[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----\r?\n?",
    re.ASCII)


class CredentialError(ValueError):
    """The node's credentials on disk cannot be used; ``reason`` is literal."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Credentials:
    root: x509.Certificate
    leaf: x509.Certificate

    @property
    def not_before(self) -> datetime:
        return self.leaf.not_valid_before_utc

    @property
    def not_after(self) -> datetime:
        return self.leaf.not_valid_after_utc

    def renew_after(self) -> datetime:
        """2/3 of the certificate's life (protocol.CERT_LIFETIME_DAYS)."""
        return self.not_before + (self.not_after - self.not_before) * 2 / 3


def split_pems(text: str) -> list[str]:
    return [m.group(0).replace("\r\n", "\n") for m in _PEM_BLOCK.finditer(text)]


def chain_text(pems) -> str:
    return "".join(p if p.endswith("\n") else p + "\n" for p in pems)


def load_credentials(paths: NodePaths, node_id: str, root_sha256: str) -> Credentials:
    """The root (checked against the pinned fingerprint), the chain (checked
    up to it), the key (matching the leaf) and the leaf naming this node.
    Dates are not judged here: an expired certificate is the agent's
    ``cert_expired``, not a broken file."""
    try:
        root_pem = paths.root_cert.read_text()
        chain_pem = paths.chain.read_text()
    except (OSError, UnicodeDecodeError):
        raise CredentialError("the fleet root or the node certificate file is missing "
                              "or unreadable") from None
    try:
        root = chain.pinned_root(root_pem, root_sha256)
        certs = chain.verify(split_pems(chain_pem), root, now=None)
    except chain.ChainError as err:
        raise CredentialError(err.reason) from None
    try:
        key = nodekeys.load_key(paths.key)
    except nodekeys.NodeKeyError as err:
        raise CredentialError(str(err)) from None
    if not nodekeys.matches(certs[0], key):
        raise CredentialError("the node key does not match the node certificate")
    if not chain.node_identity_ok(certs[0], node_id):
        raise CredentialError("the node certificate names another node")
    return Credentials(root=root, leaf=certs[0])


def lead_endpoints(enrollment: dict) -> protocol.LeadEndpoints:
    """The enrollment's lead list, validated as the protocol's."""
    return protocol.decode(protocol.LeadEndpoints, enrollment.get("lead_endpoints"))


def endpoints_dict(e: protocol.LeadEndpoints) -> dict:
    return protocol.encode(e)


def store_dict(store: protocol.StoreDescriptor, received_at: str) -> dict:
    """The descriptor without its token, marked not applied."""
    data = protocol.redacted(store)
    data.pop("write_token", None)
    return dict(data, applied=False, received_at=received_at)


def save_store(paths: NodePaths, enrollment: dict, store: protocol.StoreDescriptor,
               received_at: str) -> dict:
    """Keep a store descriptor for the data plane (a later release): the
    token in its 0600 file, the rest in the enrollment."""
    statefile.write_bytes(paths.store_token, store.write_token.reveal().encode(), 0o600)
    return dict(enrollment, store=store_dict(store, received_at))


def write_enrollment(paths: NodePaths, enrollment: dict) -> None:
    statefile.write_object(paths.enrollment, enrollment)


def _new(path: Path) -> Path:
    return path.with_name(path.name + ".new")


def store_renewal(paths: NodePaths, key_pem: bytes, chain_pem: str) -> None:
    """Write the new key and chain beside the old ones, then swap them."""
    statefile.write_bytes(_new(paths.key), key_pem, 0o600)
    statefile.write_bytes(_new(paths.chain), chain_pem.encode(), 0o644)
    os.replace(_new(paths.key), paths.key)
    os.replace(_new(paths.chain), paths.chain)


def finish_renewal(paths: NodePaths) -> str | None:
    """After a crash in :func:`store_renewal`: ``completed`` (the new pair
    is now in place), ``discarded`` (the new key had no certificate yet; the
    old pair stays), or None (nothing was pending)."""
    key_new, chain_new = _new(paths.key), _new(paths.chain)
    if chain_new.exists():
        # The chain is written after the key: if it exists, the key is
        # either beside it or already swapped in.
        if key_new.exists():
            os.replace(key_new, paths.key)
        os.replace(chain_new, paths.chain)
        return "completed"
    if key_new.exists():
        statefile.remove(key_new)
        return "discarded"
    return None


def remove_all(paths: NodePaths) -> None:
    """Forget the enrollment: ``enrollment.json`` first (so nothing reads a
    half-removed one as enrolled), then every other file of the node
    directory, secrets and leftovers of interrupted writes included. The
    measurement data is elsewhere and untouched."""
    statefile.remove(paths.enrollment)
    node_dir = paths.enrollment.parent
    if node_dir.is_dir():
        for f in node_dir.iterdir():
            if f.is_file() or f.is_symlink():
                statefile.remove(f)
