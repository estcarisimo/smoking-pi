"""The lead's registry (SQLite, ``lead/registry.db``, mode 0600).

Nodes, the certificates issued to them, the instance ids seen per node (for
"Two installations, one node id"), sites, join tokens (SHA-256 only, single
use, expiring), failed-token attempts (lockout), and the lead list's epoch.

One connection per call, WAL, ``BEGIN IMMEDIATE`` around every
read-then-write: the HTTP service's threads and the admin command (run in
the same container or a one-off one on the same volume) share the file.
Times are integers, Unix seconds, UTC.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .protocol import INSTANCE_MEMORY_SECONDS

SCHEMA_VERSION = 1

#: Join tokens: default and longest life, in seconds.
TOKEN_TTL_DEFAULT = 15 * 60
TOKEN_TTL_MAX = 24 * 3600

#: Invalid join tokens: from the LOCKOUT_PER_SOURCE-th one from one address
#: within LOCKOUT_WINDOW, that address's invalid tokens are answered
#: rate_limited instead of token_invalid. A VALID token always enrolls,
#: whatever came before it from anywhere: the throttle slows a guesser down
#: and keeps the log readable, it never locks a real node out (behind
#: Docker's port publishing every client may share one address). Guessing a
#: 256-bit token is hopeless anyway.
LOCKOUT_WINDOW = 15 * 60
LOCKOUT_PER_SOURCE = 5

#: Node states. ``conflict``: two live instances (duplicate_node).
STATES = ("active", "revoked", "left", "conflict")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sites (
    site_id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    node_name TEXT NOT NULL,
    name_by_operator INTEGER NOT NULL DEFAULT 0,
    site_id TEXT NOT NULL REFERENCES sites(site_id),
    site_by_operator INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL,
    state_reason TEXT,
    state_at INTEGER NOT NULL,
    enrolled_at INTEGER NOT NULL,
    last_seen INTEGER,
    instance_id TEXT NOT NULL,
    version TEXT,
    edition TEXT,
    lead_epoch INTEGER,
    last_heartbeat TEXT
);
CREATE TABLE IF NOT EXISTS certs (
    fingerprint TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    serial TEXT NOT NULL,
    issuer TEXT NOT NULL,
    issued_at INTEGER NOT NULL,
    not_after INTEGER NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS certs_node ON certs(node_id);
CREATE TABLE IF NOT EXISTS instances (
    node_id TEXT NOT NULL, instance_id TEXT NOT NULL, replaced_at INTEGER NOT NULL,
    PRIMARY KEY (node_id, instance_id));
CREATE TABLE IF NOT EXISTS tokens (
    token_hash TEXT PRIMARY KEY,
    token_id TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER,
    used_by TEXT,
    node_name TEXT,
    site_name TEXT
);
CREATE TABLE IF NOT EXISTS token_failures (source TEXT NOT NULL, at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS token_failures_at ON token_failures(at);
"""


class RegistryError(ValueError):
    """The registry cannot be used; raised without a message."""


class RegistryMissing(RegistryError):
    pass


class RegistryUnreadable(RegistryError):
    pass


REGISTRY_PROBLEMS: dict[type, str] = {
    RegistryMissing: "the registry is missing",
    RegistryUnreadable: "the registry is unreadable or of an unknown schema",
}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_join_token() -> str:
    """43 base64url characters (256 bits): matches the protocol's pattern."""
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Token:
    token_id: str
    node_name: str | None
    site_name: str | None
    expires_at: int


@dataclass(frozen=True)
class Cert:
    fingerprint: str
    node_id: str
    serial: str
    issuer: str
    not_after: int
    revoked: bool


class Registry:
    def __init__(self, path: Path, clock=time.time):
        self.path = Path(path)
        self.clock = clock

    def now(self) -> int:
        return int(self.clock())

    # -- connection ---------------------------------------------------------

    @contextlib.contextmanager
    def _db(self, write: bool = False):
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 15000")
            if write:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    yield conn
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
            else:
                yield conn
        finally:
            conn.close()

    def create(self) -> None:
        """Create the file (0600) and its schema. Idempotent."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        os.chmod(self.path, 0o600)
        with self._db() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(_SCHEMA)
            conn.execute("INSERT OR IGNORE INTO meta VALUES ('schema', ?)",
                         (str(SCHEMA_VERSION),))

    def check(self) -> None:
        """Raise a :class:`RegistryError` subclass when the file is not this
        registry (no message: :data:`REGISTRY_PROBLEMS` has the sentence)."""
        if not self.path.exists():
            raise RegistryMissing()
        try:
            with self._db() as conn:
                row = conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        except sqlite3.DatabaseError:
            raise RegistryUnreadable() from None
        if row is None or row["value"] != str(SCHEMA_VERSION):
            raise RegistryUnreadable()

    # -- lead list epoch ----------------------------------------------------

    def lead_epoch(self, urls: tuple[str, ...], mode: str) -> int:
        """The epoch of (urls, mode): unchanged while they are, one higher
        when they changed since the last call."""
        current = json.dumps({"urls": list(urls), "mode": mode}, sort_keys=True)
        with self._db(write=True) as conn:
            rows = dict(conn.execute(
                "SELECT key, value FROM meta WHERE key IN ('epoch', 'endpoints')").fetchall())
            epoch = int(rows.get("epoch", "0"))
            if rows.get("endpoints") != current:
                epoch += 1
                conn.execute("INSERT OR REPLACE INTO meta VALUES ('epoch', ?)", (str(epoch),))
                conn.execute("INSERT OR REPLACE INTO meta VALUES ('endpoints', ?)", (current,))
        return epoch

    # -- tokens -------------------------------------------------------------

    def add_token(self, ttl: int, node_name: str | None = None,
                  site_name: str | None = None) -> tuple[str, Token]:
        """A new single-use token: (the token, its record). Only the hash is
        stored; the token itself exists only in what the caller prints."""
        if not 60 <= ttl <= TOKEN_TTL_MAX:
            raise ValueError("ttl out of range")
        token = new_join_token()
        record = Token(token_id=secrets.token_hex(4), node_name=node_name,
                       site_name=site_name, expires_at=self.now() + ttl)
        with self._db(write=True) as conn:
            conn.execute("DELETE FROM tokens WHERE expires_at < ? AND used_at IS NULL",
                         (self.now() - 7 * 86400,))
            conn.execute(
                "INSERT INTO tokens (token_hash, token_id, created_at, expires_at, node_name,"
                " site_name) VALUES (?, ?, ?, ?, ?, ?)",
                (token_hash(token), record.token_id, self.now(), record.expires_at,
                 node_name, site_name))
        return token, record

    def tokens(self) -> list[dict]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT token_id, created_at, expires_at, used_at, used_by, node_name,"
                " site_name FROM tokens ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def failures_from(self, source: str) -> tuple[int, int]:
        """(invalid tokens from ``source`` in the window, seconds until the
        oldest of them leaves it)."""
        since = self.now() - LOCKOUT_WINDOW
        with self._db() as conn:
            count, oldest = conn.execute(
                "SELECT COUNT(*), MIN(at) FROM token_failures WHERE source = ? AND at > ?",
                (source, since)).fetchone()
        return count, max(1, (oldest or self.now()) + LOCKOUT_WINDOW - self.now())

    def record_failure(self, source: str) -> None:
        with self._db(write=True) as conn:
            conn.execute("DELETE FROM token_failures WHERE at <= ?",
                         (self.now() - LOCKOUT_WINDOW,))
            conn.execute("INSERT INTO token_failures VALUES (?, ?)", (source, self.now()))

    # -- enrollment ---------------------------------------------------------

    def enroll(self, token: str, node_id: str, instance_id: str, node_name: str,
               site_name: str, version: str, edition: str, issue) -> tuple[str, dict]:
        """Consume ``token`` and enroll ``node_id``, all or nothing.

        ``issue()`` is called inside the transaction once the token and the
        node id are accepted, and returns (fingerprint, serial, issuer
        fingerprint, not_after) of the certificate it signed; it may raise
        (a bad CSR), which rolls everything back, token included.

        Returns ("ok", node row) or (error code, {}): ``token_invalid`` for
        an unknown, used or expired token; ``node_exists`` for a node id
        enrolled here that still holds an unexpired certificate (a clone with
        a fresh token), or one in conflict. A revoked or left node, or one
        whose certificates all expired, may join again: its old
        certificates are revoked."""
        now = self.now()
        with self._db(write=True) as conn:
            tok = conn.execute(
                "SELECT token_id, node_name, site_name FROM tokens WHERE token_hash = ?"
                " AND used_at IS NULL AND expires_at > ?", (token_hash(token), now)).fetchone()
            if tok is None:
                return "token_invalid", {}
            node = conn.execute("SELECT state FROM nodes WHERE node_id = ?",
                                (node_id,)).fetchone()
            if node is not None and node["state"] == "conflict":
                return "node_exists", {}
            if node is not None and node["state"] == "active" and conn.execute(
                    "SELECT 1 FROM certs WHERE node_id = ? AND revoked = 0 AND not_after > ?",
                    (node_id, now)).fetchone():
                # It holds a live certificate: another installation with this
                # id, or an earlier join whose answer was lost (the operator
                # revokes it first). One whose certificates all expired (the
                # lead was away too long) may simply join again.
                return "node_exists", {}
            fp, serial, issuer, cert_not_after = issue()
            name_by_op = tok["node_name"] is not None
            site_by_op = tok["site_name"] is not None
            name = tok["node_name"] if name_by_op else node_name
            site_id = self._site(conn, tok["site_name"] if site_by_op else site_name)
            conn.execute("UPDATE tokens SET used_at = ?, used_by = ? WHERE token_hash = ?",
                         (now, node_id, token_hash(token)))
            values = (name, int(name_by_op), site_id, int(site_by_op), now, now, now,
                      instance_id, version, edition, node_id)
            if node is not None:
                # Joining again (revoked, left, or every certificate expired):
                # a fresh start, and every
                # certificate it held before stays revoked.
                conn.execute("UPDATE certs SET revoked = 1 WHERE node_id = ?", (node_id,))
                conn.execute("DELETE FROM instances WHERE node_id = ?", (node_id,))
                conn.execute(
                    "UPDATE nodes SET node_name = ?, name_by_operator = ?, site_id = ?,"
                    " site_by_operator = ?, state = 'active', state_reason = NULL,"
                    " state_at = ?, enrolled_at = ?, last_seen = ?, instance_id = ?,"
                    " version = ?, edition = ?, lead_epoch = 0, last_heartbeat = NULL"
                    " WHERE node_id = ?", values)
            else:
                conn.execute(
                    "INSERT INTO nodes (node_name, name_by_operator, site_id,"
                    " site_by_operator, state, state_at, enrolled_at, last_seen,"
                    " instance_id, version, edition, lead_epoch, node_id)"
                    " VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, 0, ?)", values)
            self._add_cert(conn, node_id, fp, serial, issuer, cert_not_after)
            row = dict(conn.execute("SELECT * FROM nodes WHERE node_id = ?",
                                    (node_id,)).fetchone())
        return "ok", row

    def _site(self, conn, name: str) -> str:
        row = conn.execute("SELECT site_id FROM sites WHERE name = ?", (name,)).fetchone()
        if row:
            return row["site_id"]
        site_id = str(uuid.uuid4())
        conn.execute("INSERT INTO sites VALUES (?, ?, ?)", (site_id, name, self.now()))
        return site_id

    def _add_cert(self, conn, node_id, fp, serial, issuer, cert_not_after) -> None:
        conn.execute("INSERT INTO certs (fingerprint, node_id, serial, issuer, issued_at,"
                     " not_after) VALUES (?, ?, ?, ?, ?, ?)",
                     (fp, node_id, serial, issuer, self.now(), cert_not_after))

    def add_cert(self, node_id: str, fp: str, serial: str, issuer: str,
                 cert_not_after: int, presented: str | None = None) -> bool:
        """Record a renewed certificate, in one transaction with the check
        that the node is still active and the certificate it presented
        (``presented``, a fingerprint) still unrevoked: a revoke that lands
        between the request's checks and this write wins. False: nothing
        recorded (the caller answers ``revoked``)."""
        with self._db(write=True) as conn:
            node = conn.execute("SELECT state FROM nodes WHERE node_id = ?",
                                (node_id,)).fetchone()
            if node is None or node["state"] != "active":
                return False
            if presented is not None and conn.execute(
                    "SELECT 1 FROM certs WHERE fingerprint = ? AND revoked = 0",
                    (presented,)).fetchone() is None:
                return False
            self._add_cert(conn, node_id, fp, serial, issuer, cert_not_after)
        return True

    # -- lookups ------------------------------------------------------------

    def cert(self, fp: str) -> Cert | None:
        with self._db() as conn:
            row = conn.execute("SELECT * FROM certs WHERE fingerprint = ?", (fp,)).fetchone()
        if row is None:
            return None
        return Cert(row["fingerprint"], row["node_id"], row["serial"], row["issuer"],
                    row["not_after"], bool(row["revoked"]))

    def node(self, node_id: str) -> dict | None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT nodes.*, sites.name AS site_name FROM nodes JOIN sites USING (site_id)"
                " WHERE node_id = ?", (node_id,)).fetchone()
        return dict(row) if row else None

    def nodes(self) -> list[dict]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT nodes.*, sites.name AS site_name,"
                " (SELECT MAX(not_after) FROM certs WHERE certs.node_id = nodes.node_id"
                "  AND revoked = 0) AS cert_not_after"
                " FROM nodes JOIN sites USING (site_id) ORDER BY sites.name, node_name"
            ).fetchall()
        return [dict(r) for r in rows]

    def counts(self) -> dict[str, int]:
        with self._db() as conn:
            rows = conn.execute("SELECT state, COUNT(*) FROM nodes GROUP BY state").fetchall()
        return {r[0]: r[1] for r in rows}

    # -- instances ----------------------------------------------------------

    def claim_instance(self, node_id: str, instance_id: str) -> str:
        """Record that ``instance_id`` speaks for ``node_id``.

        Returns ``ok`` (the current instance, or a new one that replaces
        it: a restart) or ``duplicate_node`` (a replaced instance spoke
        again, or the node is already in conflict). A replaced instance is
        remembered INSTANCE_MEMORY_SECONDS."""
        now = self.now()
        with self._db(write=True) as conn:
            node = conn.execute("SELECT state, instance_id FROM nodes WHERE node_id = ?",
                                (node_id,)).fetchone()
            if node is None:
                return "unknown"
            if node["state"] == "conflict":
                return "duplicate_node"
            if node["instance_id"] == instance_id:
                return "ok"
            conn.execute("DELETE FROM instances WHERE replaced_at < ?",
                         (now - INSTANCE_MEMORY_SECONDS,))
            seen = conn.execute("SELECT 1 FROM instances WHERE node_id = ? AND instance_id = ?",
                                (node_id, instance_id)).fetchone()
            if seen:
                conn.execute("UPDATE nodes SET state = 'conflict', state_reason ="
                             " 'duplicate_instance', state_at = ? WHERE node_id = ?",
                             (now, node_id))
                return "duplicate_node"
            conn.execute("INSERT OR REPLACE INTO instances VALUES (?, ?, ?)",
                         (node_id, node["instance_id"], now))
            conn.execute("UPDATE nodes SET instance_id = ? WHERE node_id = ?",
                         (instance_id, node_id))
        return "ok"

    # -- heartbeat, leave, revoke ------------------------------------------

    def heartbeat(self, node_id: str, *, node_name: str, site_name: str, version: str,
                  edition: str, lead_epoch: int, summary: dict) -> bool:
        """Record a heartbeat; False (nothing recorded) when the node is no
        longer active: revoked or left since the request was checked."""
        now = self.now()
        with self._db(write=True) as conn:
            node = conn.execute("SELECT name_by_operator, site_by_operator, state FROM nodes"
                                " WHERE node_id = ?", (node_id,)).fetchone()
            if node is None or node["state"] != "active":
                return False
            conn.execute("UPDATE nodes SET last_seen = ?, version = ?, edition = ?,"
                         " lead_epoch = ?, last_heartbeat = ? WHERE node_id = ?",
                         (now, version, edition, lead_epoch,
                          json.dumps(summary, sort_keys=True), node_id))
            if not node["name_by_operator"]:
                conn.execute("UPDATE nodes SET node_name = ? WHERE node_id = ?",
                             (node_name, node_id))
            if not node["site_by_operator"]:
                conn.execute("UPDATE nodes SET site_id = ? WHERE node_id = ?",
                             (self._site(conn, site_name), node_id))
        return True

    def seen(self, node_id: str) -> None:
        with self._db(write=True) as conn:
            conn.execute("UPDATE nodes SET last_seen = ? WHERE node_id = ?",
                         (self.now(), node_id))

    def set_state(self, node_id: str, state: str, reason: str) -> bool:
        """revoked (from any state) or left (only from active): the node and
        every certificate it holds. False when nothing changed."""
        assert state in ("revoked", "left")
        only = " AND state = 'active'" if state == "left" else ""
        with self._db(write=True) as conn:
            cur = conn.execute("UPDATE nodes SET state = ?, state_reason = ?, state_at = ?"
                               " WHERE node_id = ?" + only,
                               (state, reason, self.now(), node_id))
            if cur.rowcount != 1:
                return False
            conn.execute("UPDATE certs SET revoked = 1 WHERE node_id = ?", (node_id,))
        return cur.rowcount == 1

    def find(self, ref: str) -> list[dict]:
        """Nodes matching a reference: the whole id, a prefix of at least 8
        of its characters, or the exact name."""
        ref = ref.strip()
        out = []
        for node in self.nodes():
            if (node["node_id"] == ref or node["node_name"] == ref
                    or (len(ref) >= 8 and node["node_id"].startswith(ref.lower()))):
                out.append(node)
        return out
