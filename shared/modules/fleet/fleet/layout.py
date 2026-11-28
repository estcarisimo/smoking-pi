"""Where Fleet keeps its files: the one list, for both entrypoints.

Each service has its own volume at ``/var/lib/fleet`` (``fleet-agent-state``,
``fleet-lead-state``), so a host that both measures and leads keeps the two
apart. ``smoking-pi backup`` copies the volumes of the services that are on,
which is how a replaced SD card keeps its enrollment.

Secrets are files of mode 0600 named in :data:`NODE_SECRETS` and
:data:`LEAD_SECRETS`. Nothing else may hold them: not ``status.json``, not
a log line, not a heartbeat (tests/test_secrets.py reads every writer).
The join token is never written anywhere on the node: ``fleet join`` reads
it, sends it once, and drops it; the lead keeps only its SHA-256.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_STATE_DIR = "/var/lib/fleet"


def state_dir() -> Path:
    return Path(os.environ.get("FLEET_STATE_DIR") or DEFAULT_STATE_DIR)


@dataclass(frozen=True)
class NodePaths:
    """The agent's files (PR C writes them; PR A only reads ``enrollment``)."""

    root: Path

    @property
    def status(self) -> Path:
        """What the agent is doing; read by the healthcheck and `fleet status`."""
        return self.root / "status.json"

    @property
    def enrollment(self) -> Path:
        """Non-secret facts of the enrollment, a JSON object: ``node_id``
        (required: the id it was enrolled under), ``fleet_id``, ``site_id``,
        ``not_after``, the lead list and the store descriptor without its
        token. Its presence is what "enrolled" means. A file that is not
        such an object is ``state_corrupt``, never "not enrolled" (the
        agent must not enroll over it); a ``node_id`` other than
        FLEET_NODE_ID is ``identity_mismatch`` (an older env file restored
        beside a newer agent volume), and the agent sends nothing."""
        return self.root / "node" / "enrollment.json"

    @property
    def key(self) -> Path:
        """The node's P-256 private key, generated here, never sent. 0600."""
        return self.root / "node" / "key.pem"

    @property
    def chain(self) -> Path:
        """Client certificate then its issuing intermediate (PEM)."""
        return self.root / "node" / "chain.pem"

    @property
    def root_cert(self) -> Path:
        """The fleet root, pinned by the join string's fingerprint."""
        return self.root / "node" / "root.pem"

    @property
    def store_token(self) -> Path:
        """The store's write token, kept to re-create the remote if InfluxDB
        loses it. 0600."""
        return self.root / "node" / "store-token"


@dataclass(frozen=True)
class LeadPaths:
    """The lead's files (PR B writes them; PR A only reads ``fleet``)."""

    root: Path

    @property
    def status(self) -> Path:
        return self.root / "status.json"

    @property
    def fleet(self) -> Path:
        """A JSON object: fleet_id, name, lead mode, the lead list. Its
        presence is what "a fleet is initialized here" means; a file that is
        not such an object is ``state_corrupt``."""
        return self.root / "lead" / "fleet.json"

    @property
    def root_cert(self) -> Path:
        return self.root / "lead" / "ca" / "root.pem"

    @property
    def root_key(self) -> Path:
        """The root's key: only where `fleet init` was run, and meant to be
        moved offline once the intermediate is signed. 0600."""
        return self.root / "lead" / "ca" / "root-key.pem"

    @property
    def intermediate_cert(self) -> Path:
        return self.root / "lead" / "ca" / "intermediate.pem"

    @property
    def intermediate_key(self) -> Path:
        """This lead's issuing key. 0600."""
        return self.root / "lead" / "ca" / "intermediate-key.pem"

    @property
    def registry(self) -> Path:
        """Nodes, their certificates' serials, revocations, instance ids,
        and join tokens as SHA-256 only (SQLite)."""
        return self.root / "lead" / "registry.db"

    @property
    def server_cert(self) -> Path:
        """A public copy of this lead's TLS certificate then the
        intermediate (PEM), issued for the hosts of FLEET_LEAD_URLS. The
        server reads ``server_key``, never this file."""
        return self.root / "lead" / "tls" / "server.pem"

    @property
    def server_key(self) -> Path:
        """The TLS server key, its certificate and the intermediate, in one
        file so the pair is replaced in one rename (pki.ensure_server_cert);
        gunicorn's certfile and keyfile. 0600."""
        return self.root / "lead" / "tls" / "server-key.pem"


def node_paths(root: Path | None = None) -> NodePaths:
    return NodePaths(root or state_dir())


def lead_paths(root: Path | None = None) -> LeadPaths:
    return LeadPaths(root or state_dir())


def _props(cls: type) -> list[str]:
    return [n for n, v in vars(cls).items() if isinstance(v, property)]


#: Files that hold a secret: mode 0600, never read into a status or a log.
NODE_SECRETS = ("key", "store_token")
LEAD_SECRETS = ("root_key", "intermediate_key", "registry", "server_key")
assert set(NODE_SECRETS) <= set(_props(NodePaths))
assert set(LEAD_SECRETS) <= set(_props(LeadPaths))
