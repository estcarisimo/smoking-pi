"""The Fleet protocol between a node's agent and a lead: the one contract.

Standard library only. Every message is a frozen dataclass whose fields carry
a :class:`Spec`; :func:`decode` validates a received JSON object against it
strictly, :func:`encode` produces the JSON object to send, and
:func:`reference_markdown` writes the reference in docs/fleet.md from the same
specs (a test fails when the two differ), so code and docs cannot drift.

Transport (HTTPS, JSON bodies, ``Content-Type: application/json``):

=========================  =====================  ============================
Route                      Authentication         Bodies
=========================  =====================  ============================
``GET  /fleet/root``       none; the node pins    → :class:`RootResponse`
                           the answer by the
                           join string's
                           fingerprint
``POST /fleet/enroll``     TLS (server verified   :class:`EnrollRequest` →
                           against the pinned     :class:`EnrollResponse`
                           root), join token
``POST /fleet/heartbeat``  mTLS                   :class:`HeartbeatRequest` →
                                                  :class:`HeartbeatResponse`
``POST /fleet/renew``      mTLS                   :class:`RenewRequest` →
                                                  :class:`RenewResponse`
``POST /fleet/leave``      mTLS                   :class:`LeaveRequest` →
                                                  :class:`LeaveResponse`
=========================  =====================  ============================

Any non-2xx answer carries an :class:`ErrorResponse`; :data:`ERROR_STATUS`
maps each error code to its HTTP status and :data:`ERROR_ACTION` says what
the agent does about it.

Compatibility rules (docs/fleet.md, "Protocol"):

* **Version negotiation is per request.** Every request carries
  ``protocol`` (the highest version the sender speaks) and ``protocol_min``
  (the lowest). The lead answers in :func:`negotiate`'s result, the highest
  version both speak, and says which in the response's ``protocol``; with no
  common version it answers ``protocol_unsupported`` with the range it
  speaks in ``supported_min``/``supported_max``. Nothing is stored, so a lead
  or an agent can be upgraded on its own.
* **Unknown fields are ignored**, at every level, by both sides: a newer peer
  may add an *optional* field without a version bump. Adding a required
  field, removing one, or changing a field's meaning or type is a new
  version.
* **Known fields are strict**: wrong type, out of range, malformed or missing
  (when required) rejects the whole message with ``bad_request``.
* **Open vocabularies** (``lead_mode``, ``role``, ``edition``, capability and
  profile names, error and directive ``type``/``code`` strings) accept any
  token matching :data:`TOKEN_PATTERN`; a value the receiver does not know
  is kept as-is and treated as "other". So ``lead_mode: "elected"`` needs no
  version bump.
* **Unknown directive types** are not applied. The agent reports each one in
  its next heartbeat's ``directive_results`` with status ``ignored``, and the
  lead decides (an older agent never guesses at a newer instruction). A
  directive of a known type whose arguments fail validation is reported
  ``invalid``; the other directives of the same response still apply.
* **Unknown error codes** are handled as ``internal`` (back off and retry).

Secrets (the join token, the store's write token) are :class:`Secret`
values: their ``repr`` and ``str`` are redacted, :func:`redacted` replaces
them in a dict meant for a log, and only :func:`encode` reveals them, for
the wire. Private keys never enter a message at all.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import hmac
import ipaddress
import json
import re
import secrets
import typing
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

#: The versions this code speaks. A lead or agent answers any request whose
#: [protocol_min, protocol] range overlaps this one.
PROTOCOL_MIN = 1
PROTOCOL_MAX = 1

#: Open vocabulary values: lowercase, a letter first, at most 32 characters.
TOKEN_PATTERN = r"^[a-z][a-z0-9_-]{0,31}$"

#: Heartbeat pacing, in seconds. The lead chooses ``next_heartbeat`` within
#: [HEARTBEAT_MIN, HEARTBEAT_MAX]; an agent that cannot reach any lead backs
#: off from BACKOFF_MIN, doubling with +-20 % jitter, up to BACKOFF_MAX.
HEARTBEAT_DEFAULT = 60
HEARTBEAT_MIN = 10
HEARTBEAT_MAX = 3600
BACKOFF_MIN = 30
BACKOFF_MAX = 1800

#: A lead treats an instance id it has already seen replaced as a second
#: live copy of the node (docs/fleet.md, "Two installations, one node id").
#: How long it remembers the replaced one:
INSTANCE_MEMORY_SECONDS = 7 * 24 * 3600

#: Client certificates last this long; the agent renews from 2/3 of it on.
CERT_LIFETIME_DAYS = 90

#: The join string's prefix, which is also its format version.
JOIN_PREFIX = "spfleet1_"

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_HEX16 = r"^[0-9a-f]{16}$"
_SHA256 = r"^[0-9a-f]{64}$"
_ID = r"^[A-Za-z0-9_-]{1,64}$"
# A human name: letters, digits, space and . _ - ' ( ), 1 to 64 characters.
_NAME = re.compile(r"^[\w .'()-]{1,64}$")
_JOIN_TOKEN = r"^[A-Za-z0-9_-]{32,128}$"
_WRITE_TOKEN = r"^[A-Za-z0-9_=+/-]{16,256}$"
_PEM_CERT = re.compile(
    r"^-----BEGIN CERTIFICATE-----\n[A-Za-z0-9+/=\n]+-----END CERTIFICATE-----\n?$")
_PEM_CSR = re.compile(
    r"^-----BEGIN CERTIFICATE REQUEST-----\n[A-Za-z0-9+/=\n]+"
    r"-----END CERTIFICATE REQUEST-----\n?$")
_PEM_MAX = 8192


class ValidationError(ValueError):
    """A received message did not pass its schema.

    ``reasons`` are literal strings built from the schema's own field paths
    and fixed phrases, never from the received values or an exception's
    text, so they are safe to log and to return in a ``bad_request``.
    """

    def __init__(self, reasons: list[str] | tuple[str, ...]):
        self.reasons = tuple(reasons)
        super().__init__("; ".join(self.reasons))


class Secret:
    """A credential that must not reach a log, a ``repr`` or a status file.

    ``reveal()`` is the one way to read it; :func:`encode` calls it to put the
    value on the wire. Comparison is constant-time.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str):
        if not isinstance(value, str):
            raise TypeError("Secret takes a str")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret('<redacted>')"

    def __str__(self) -> str:
        return "<redacted>"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Secret):
            return NotImplemented
        return hmac.compare_digest(self._value.encode(), other._value.encode())

    def __hash__(self) -> int:
        return hash(Secret)

    def __reduce__(self):
        raise TypeError("a Secret is not pickled")


# ---------------------------------------------------------------------------
# Field specs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    """How one field is validated, and how the reference describes it."""

    kind: str
    doc: str
    required: bool = True
    nullable: bool = False
    of: Any = None            # "object": the dataclass; "list": the item Spec
    min: int | None = None    # "int": bounds; "list"/"chain": item counts
    max: int | None = None
    max_len: int | None = None
    pattern: str | None = None
    values: tuple[str, ...] = ()  # "token": the values known today
    secret: bool = False


def spec(kind: str, doc: str, **kw: Any) -> Any:
    """A dataclass field carrying its Spec. Optional fields default to None,
    optional lists to an empty tuple."""
    s = Spec(kind, doc, **kw)
    default: Any = dataclasses.MISSING
    if not s.required:
        default = () if kind in ("list", "chain") else None
    return field(default=default, metadata={"spec": s})


def _specs(cls: type) -> list[tuple[str, Spec]]:
    return [(f.name, f.metadata["spec"]) for f in dataclasses.fields(cls)]


def now_rfc3339() -> str:
    """The protocol's time format: UTC, seconds, ``Z``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def valid_name(value: str) -> bool:
    return bool(_NAME.match(value)) and value.strip() == value


def valid_https_url(value: str) -> bool:
    """https://host[:port][/path], no credentials, query or fragment."""
    if not isinstance(value, str) or len(value) > 512 or any(c.isspace() for c in value):
        return False
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment:
        return False
    if parts.username is not None or parts.password is not None:
        return False
    if port is not None and not 0 < port < 65536:
        return False
    host = parts.hostname
    if ":" in host:  # an IPv6 literal
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return False
        return True
    return re.match(r"^[A-Za-z0-9.-]{1,253}$", host) is not None


def _check(s: Spec, value: Any, path: str, out: list[str]) -> Any:
    """Validate one value; append reasons to ``out``; return the decoded value."""
    k = s.kind
    if k == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            out.append(f"{path}: must be an integer")
            return None
        if (s.min is not None and value < s.min) or (s.max is not None and value > s.max):
            out.append(f"{path}: out of range")
        return value
    if k == "bool":
        if not isinstance(value, bool):
            out.append(f"{path}: must be true or false")
        return value
    if k == "object":
        if not isinstance(value, dict):
            out.append(f"{path}: must be an object")
            return None
        return _decode_into(s.of, value, path, out)
    if k in ("list", "chain"):
        if not isinstance(value, list):
            out.append(f"{path}: must be a list")
            return ()
        if (s.min is not None and len(value) < s.min) or (s.max is not None and len(value) > s.max):
            out.append(f"{path}: wrong number of items")
        item = s.of if k == "list" else Spec("pem", "")
        return tuple(_check(item, v, f"{path}[{i}]", out) for i, v in enumerate(value))
    if not isinstance(value, str):
        out.append(f"{path}: must be a string")
        return None
    if s.max_len is not None and len(value) > s.max_len:
        out.append(f"{path}: too long")
        return None
    ok = True
    if k == "str":
        ok = s.pattern is None or re.match(s.pattern, value) is not None
    elif k == "secret":
        ok = s.pattern is None or re.match(s.pattern, value) is not None
        return Secret(value) if ok else out.append(f"{path}: malformed")
    elif k == "token":
        ok = re.match(TOKEN_PATTERN, value) is not None
    elif k == "uuid":
        ok = _UUID.match(value) is not None
    elif k == "time":
        ok = _TIME.match(value) is not None
        if ok:
            try:
                parse_time(value)
            except ValueError:
                ok = False
    elif k == "name":
        ok = valid_name(value)
    elif k == "https_url":
        ok = valid_https_url(value)
    elif k == "pem":
        ok = len(value) <= _PEM_MAX and _PEM_CERT.match(value) is not None
    elif k == "csr":
        ok = len(value) <= _PEM_MAX and _PEM_CSR.match(value) is not None
    elif k == "text":
        ok = not any(ord(c) < 32 and c not in "\t" for c in value)
    else:  # pragma: no cover - a spec typo, caught by the tests
        raise AssertionError(f"unknown kind {k}")
    if not ok:
        out.append(f"{path}: malformed")
    return value


def _decode_into(cls: type, data: dict, path: str, out: list[str]) -> Any:
    kwargs: dict[str, Any] = {}
    for name, s in _specs(cls):
        where = f"{path}.{name}" if path else name
        if name not in data:
            if s.required:
                out.append(f"{where}: required")
            continue
        value = data[name]
        if value is None:
            if not s.nullable:
                out.append(f"{where}: must not be null")
            kwargs[name] = None
            continue
        kwargs[name] = _check(s, value, where, out)
    # Unknown keys in `data` are ignored on purpose (module docstring).
    if out:
        return None
    hook = getattr(cls, "_validate", None)
    obj = cls(**kwargs)
    if hook is not None:
        for reason in hook(obj):
            out.append(f"{path}: {reason}" if path else reason)
    return obj


def decode(cls: type, data: Any) -> Any:
    """A received JSON object (already parsed) as ``cls``, or ValidationError."""
    from_wire = getattr(cls, "from_wire", None)
    if from_wire is not None:
        return from_wire(data)
    if not isinstance(data, dict):
        raise ValidationError(["body: must be a JSON object"])
    out: list[str] = []
    obj = _decode_into(cls, data, "", out)
    if out:
        raise ValidationError(out)
    return obj


def decode_json(cls: type, text: str | bytes) -> Any:
    try:
        data = json.loads(text)
    except (ValueError, UnicodeDecodeError):
        raise ValidationError(["body: not JSON"]) from None
    return decode(cls, data)


def _plain(value: Any, reveal: bool) -> Any:
    if isinstance(value, Secret):
        return value.reveal() if reveal else "<redacted>"
    if dataclasses.is_dataclass(value):
        return _as_dict(value, reveal)
    if isinstance(value, (list, tuple)):
        return [_plain(v, reveal) for v in value]
    return value


def _as_dict(obj: Any, reveal: bool) -> dict:
    out: dict[str, Any] = {}
    for f in dataclasses.fields(obj):
        value = getattr(obj, f.name)
        s: Spec = f.metadata["spec"]
        if value is None and not s.required:
            continue
        out[f.name] = _plain(value, reveal)
    extra = getattr(obj, "_wire_extra", None)
    if extra is not None:
        out.update(extra())
    return out


def encode(obj: Any) -> dict:
    """The JSON object to send, secrets revealed. Never log it."""
    return _as_dict(obj, reveal=True)


def encode_json(obj: Any) -> str:
    return json.dumps(encode(obj), separators=(",", ":"), sort_keys=True)


def redacted(obj: Any) -> dict:
    """The same object with every Secret replaced: safe for a log."""
    return _as_dict(obj, reveal=False)


def negotiate(peer_min: int, peer_max: int,
              ours: tuple[int, int] = (PROTOCOL_MIN, PROTOCOL_MAX)) -> int | None:
    """The highest version both sides speak, or None."""
    low, high = max(peer_min, ours[0]), min(peer_max, ours[1])
    return high if low <= high else None


def new_error_id() -> str:
    return secrets.token_hex(8)


def new_instance_id() -> str:
    """A fresh instance id: one per agent process start, never persisted."""
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Shared fields
# ---------------------------------------------------------------------------

def _protocol_field() -> Any:
    return spec("int", "Highest protocol version the sender speaks.", min=1, max=65535)


def _protocol_min_field() -> Any:
    return spec("int", "Lowest protocol version the sender speaks.", min=1, max=65535)


def _check_range(obj: Any) -> list[str]:
    return ["protocol_min: above protocol"] if obj.protocol_min > obj.protocol else []


# ---------------------------------------------------------------------------
# Join string
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class JoinString:
    """What ``smoking-pi fleet token`` prints on the lead and ``fleet join``
    takes on the node: ``spfleet1_`` + base64url (unpadded) of a compact JSON
    object with these fields. The token is single-use and short-lived; the
    lead stores only its SHA-256. ``root_sha256`` is the fingerprint the node
    pins the root certificate to before it trusts anything the lead says."""

    fleet_id: str = spec("uuid", "The fleet's id, created by `fleet init` on the lead.")
    lead_urls: tuple = spec(
        "list", "Where to enroll: the lead's base URLs, in order of preference.",
        of=Spec("https_url", ""), min=1, max=8)
    token: Secret = spec(
        "secret", "Single-use enrollment token (32-128 base64url characters).",
        pattern=_JOIN_TOKEN, max_len=128, secret=True)
    root_sha256: str = spec(
        "str", "SHA-256 of the fleet root certificate's DER, lowercase hex.",
        pattern=_SHA256)
    expires_at: str | None = spec(
        "time", "When the token expires (informative: the lead decides).",
        required=False)

    def to_string(self) -> str:
        raw = json.dumps(encode(self), separators=(",", ":"), sort_keys=True).encode()
        return JOIN_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def parse(cls, text: str) -> "JoinString":
        if not isinstance(text, str):
            raise ValidationError(["join string: must be text"])
        text = text.strip()
        if not text.startswith(JOIN_PREFIX):
            raise ValidationError(["join string: unknown format (expected spfleet1_...)"])
        body = text[len(JOIN_PREFIX):]
        if len(body) > 4096 or not re.match(r"^[A-Za-z0-9_-]+$", body):
            raise ValidationError(["join string: malformed"])
        try:
            raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
            data = json.loads(raw)
        except (binascii.Error, ValueError, UnicodeDecodeError):
            raise ValidationError(["join string: malformed"]) from None
        return decode(cls, data)

    def __str__(self) -> str:  # a join string printed by accident stays secret
        return f"JoinString(fleet_id={self.fleet_id}, token=<redacted>)"


# ---------------------------------------------------------------------------
# Pieces
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class LeadEndpoints:
    """Where the leads are. Agents never assume a single lead: they hold this
    list, send to ``current``, try the others in order when it does not
    answer, and replace their copy only with a list of a higher ``epoch``.
    A lead change (mode 2 move, mode 3 election) is a new epoch."""

    epoch: int = spec("int", "Version of this list; only a higher one replaces it.",
                      min=1, max=2**53)
    current: str = spec("https_url", "The lead to send to now.")
    urls: tuple = spec("list", "Every lead endpoint, in order of preference; contains `current`.",
                       of=Spec("https_url", ""), min=1, max=16)
    lead_mode: str = spec(
        "token", "How the lead role is held (open: an unknown value means 'other').",
        values=("dedicated", "node", "elected"))

    def _validate(self) -> list[str]:
        return [] if self.current in self.urls else ["current: not in urls"]


@dataclass(frozen=True, kw_only=True)
class StoreDescriptor:
    """Where a node replicates (InfluxDB 2 native replication) and with what.

    The write token is scoped to this node's bucket only, write-only, and is
    shown by InfluxDB 2.9 only when created: this message is the one place
    it travels, and the node keeps it in ``store-token`` (layout.py)."""

    store_id: str = spec("uuid", "Identifies this store; a different id means repoint.")
    url: str = spec("https_url", "The store's InfluxDB base URL (TLS, fleet CA or `ca_pem`).")
    org_id: str = spec("str", "Remote organization ID (16 hex).", pattern=_HEX16)
    bucket_id: str = spec("str", "This node's bucket on the store (16 hex).", pattern=_HEX16)
    bucket_name: str = spec("str", "Its name, `node-<node_id>`, for display.",
                            pattern=r"^node-[0-9a-f-]{36}$")
    write_token: Secret = spec("secret", "Write-only token for that bucket alone.",
                               pattern=_WRITE_TOKEN, max_len=256, secret=True)
    ca_pem: str = spec("pem", "The certificate that anchors the store's TLS chain.")
    max_queue_bytes: int | None = spec(
        "int", "Replication queue size the node should set (InfluxDB --max-queue-bytes).",
        required=False, min=1 << 20, max=1 << 40)


@dataclass(frozen=True, kw_only=True)
class DoctorSummary:
    """The last `smoking-pi doctor --live` on the node, counted."""

    ok: int = spec("int", "Checks that passed.", min=0, max=10000)
    warn: int = spec("int", "Checks that warned.", min=0, max=10000)
    fail: int = spec("int", "Checks that failed.", min=0, max=10000)
    skipped: int = spec("int", "Checks skipped.", min=0, max=10000)
    at: str = spec("time", "When the doctor ran.")


@dataclass(frozen=True, kw_only=True)
class Freshness:
    """"Is it measuring": what the node's targets last produced."""

    state: str = spec("token", "Overall answer.", values=("measuring", "stale", "unknown"))
    targets: int = spec("int", "Targets in the generated configuration.", min=0, max=100000)
    stale: int = spec("int", "Of those, how many produced nothing for two steps.",
                      min=0, max=100000)
    newest_age_seconds: int | None = spec(
        "int", "Age of the newest measurement.", required=False, nullable=True,
        min=0, max=2**31)


@dataclass(frozen=True, kw_only=True)
class Uplink:
    """The interface the node measures through."""

    interface: str = spec("str", "Interface name.", pattern=r"^[A-Za-z0-9._@:-]{1,32}$")
    kind: str = spec("token", "Link type.", values=("wifi", "ethernet", "other"))


@dataclass(frozen=True, kw_only=True)
class Inventory:
    """What the node is, sent with every heartbeat."""

    node_name: str = spec("name", "The node's human name (FLEET_NODE_NAME).")
    site_name: str = spec("name", "The site's human name (FLEET_SITE_NAME).")
    version: str = spec("str", "Smoking Pi version.", pattern=r"^[0-9A-Za-z.+~-]{1,40}$")
    edition: str = spec("token", "Edition.", values=("basic", "standard", "pro"))
    profiles: tuple = spec("list", "Compose profiles enabled.", of=Spec("token", ""), max=64)
    capabilities: tuple = spec("list", "What the node can do (open vocabulary).",
                               of=Spec("token", ""), max=64,
                               values=("influxdb", "clickhouse", "replication", "seed"))
    doctor: DoctorSummary | None = spec("object", "Last doctor run.", of=DoctorSummary,
                                        required=False, nullable=True)
    measuring: Freshness | None = spec("object", "Measurement freshness.", of=Freshness,
                                       required=False, nullable=True)
    uplink: Uplink | None = spec("object", "Uplink interface.", of=Uplink,
                                 required=False, nullable=True)


@dataclass(frozen=True, kw_only=True)
class ReplicationState:
    """The node's replication as InfluxDB reports it, plus what the agent saw
    in the log. ``latest_response_code`` 0 means a network or TLS failure
    (InfluxDB's convention); a full queue drops the *newest* points, and only
    the log says so, hence ``queue_full_seen``."""

    store_id: str = spec("uuid", "The store this replication points at.")
    remaining_bytes: int = spec("int", "remainingBytesToBeSynced.", min=0, max=2**53)
    latest_response_code: int = spec("int", "Last HTTP status from the store (0: network).",
                                     min=0, max=599)
    latest_error: str | None = spec(
        "text", "Last error message InfluxDB recorded, truncated.", required=False,
        nullable=True, max_len=512)
    queue_full_seen: bool = spec("bool", "A 'queue is full' log line since the last heartbeat.")
    queue_full_last_at: str | None = spec("time", "When the last one was seen.",
                                          required=False, nullable=True)
    max_queue_bytes: int = spec("int", "The queue's size limit.", min=0, max=2**53)


@dataclass(frozen=True, kw_only=True)
class SeedProgress:
    """Copying pre-existing history to the store (replication only carries
    new writes)."""

    state: str = spec("token", "Where the seed is.",
                      values=("pending", "running", "done", "failed", "paused"))
    since: str = spec("time", "Oldest point to copy.")
    until: str = spec("time", "Newest point to copy (the replication's start).")
    cursor: str | None = spec("time", "Copied up to here.", required=False, nullable=True)
    bytes_sent: int = spec("int", "Bytes sent so far.", min=0, max=2**53)
    error_id: str | None = spec("str", "Set when state is failed: the node's log id.",
                                required=False, nullable=True, pattern=_HEX16)


@dataclass(frozen=True, kw_only=True)
class DirectiveResult:
    """What the agent did with a directive of the previous response."""

    id: str = spec("str", "The directive's id.", pattern=_ID)
    status: str = spec("token", "Outcome.", values=("done", "failed", "ignored", "invalid"))
    error_id: str | None = spec("str", "For failed: the node's log id.", required=False,
                                nullable=True, pattern=_HEX16)


# ---------------------------------------------------------------------------
# Directives (in a heartbeat response)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Revoked:
    """Stop: this node is no longer in the fleet. The agent deletes its
    replication and remote (a revoked node otherwise retries forever),
    stops heartbeating and keeps its local data. Overrides every other
    directive of the same response. Only a new join undoes it."""

    id: str = spec("str", "Directive id.", pattern=_ID)
    reason: str = spec("token", "Why (open).", values=("operator", "left"))

    TYPE: typing.ClassVar[str] = "revoked"


@dataclass(frozen=True, kw_only=True)
class RepointStore:
    """Replicate to this store from now on. The agent updates its remote and
    replication in place (never delete + create, which discards the queue)."""

    id: str = spec("str", "Directive id.", pattern=_ID)
    store: StoreDescriptor = spec("object", "The new store.", of=StoreDescriptor)

    TYPE: typing.ClassVar[str] = "repoint_store"


@dataclass(frozen=True, kw_only=True)
class SetLeadEndpoints:
    """A new lead list; applied only when its epoch is higher than held."""

    id: str = spec("str", "Directive id.", pattern=_ID)
    lead_endpoints: LeadEndpoints = spec("object", "The list.", of=LeadEndpoints)

    TYPE: typing.ClassVar[str] = "lead_endpoints"


@dataclass(frozen=True, kw_only=True)
class RenewNow:
    """Renew the client certificate now, whatever its age."""

    id: str = spec("str", "Directive id.", pattern=_ID)

    TYPE: typing.ClassVar[str] = "renew_now"


@dataclass(frozen=True, kw_only=True)
class StartSeed:
    """Copy local history in [since, until) to the store, at most
    ``max_bytes_per_second`` and within the measurement budget."""

    id: str = spec("str", "Directive id.", pattern=_ID)
    since: str = spec("time", "Oldest point.")
    until: str = spec("time", "Newest point.")
    max_bytes_per_second: int = spec("int", "Rate limit.", min=1024, max=1 << 30)

    TYPE: typing.ClassVar[str] = "start_seed"

    def _validate(self) -> list[str]:
        return [] if self.since < self.until else ["since: not before until"]


#: Directive type -> class. A type not in here is unknown to this version.
DIRECTIVES: dict[str, type] = {c.TYPE: c for c in (Revoked, RepointStore, SetLeadEndpoints,
                                                    RenewNow, StartSeed)}


@dataclass(frozen=True)
class UnknownDirective:
    """A directive this version cannot apply: reported ``ignored``."""

    id: str
    type: str


@dataclass(frozen=True)
class InvalidDirective:
    """A known type whose arguments failed validation: reported ``invalid``."""

    id: str
    type: str
    reasons: tuple[str, ...]


def decode_directive(data: Any) -> Any:
    """One directive object -> its class, UnknownDirective or InvalidDirective.

    Raises ValidationError only when it has no usable ``id`` and ``type``
    (nothing could be reported back about it)."""
    if not isinstance(data, dict):
        raise ValidationError(["directive: must be an object"])
    did, dtype = data.get("id"), data.get("type")
    if not (isinstance(did, str) and re.match(_ID, did)):
        raise ValidationError(["directive.id: malformed"])
    if not (isinstance(dtype, str) and re.match(TOKEN_PATTERN, dtype)):
        raise ValidationError(["directive.type: malformed"])
    cls = DIRECTIVES.get(dtype)
    if cls is None:
        return UnknownDirective(did, dtype)
    try:
        return decode(cls, data)
    except ValidationError as err:
        return InvalidDirective(did, dtype, err.reasons)


def _directive_wire(d: Any) -> dict:
    out = encode(d)
    out["type"] = d.TYPE
    return out


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class RootResponse:
    """``GET /fleet/root``: the root certificate. The node accepts it only if
    its DER's SHA-256 equals the join string's ``root_sha256``."""

    protocol: int = spec("int", "Version of this answer.", min=1, max=65535)
    protocol_min: int = _protocol_min_field()
    protocol_max: int = spec("int", "Highest version the lead speaks.", min=1, max=65535)
    root_pem: str = spec("pem", "The fleet root certificate.")


@dataclass(frozen=True, kw_only=True)
class Software:
    version: str = spec("str", "Smoking Pi version.", pattern=r"^[0-9A-Za-z.+~-]{1,40}$")
    edition: str = spec("token", "Edition.", values=("basic", "standard", "pro"))


@dataclass(frozen=True, kw_only=True)
class EnrollRequest:
    """``POST /fleet/enroll``. The CSR's key was generated on the node and
    never leaves it; the lead ignores the CSR's subject and issues a
    certificate for ``node_id`` (CN ``node-<id>``, SAN
    ``urn:smoking-pi:node:<id>``, EKU clientAuth)."""

    protocol: int = _protocol_field()
    protocol_min: int = _protocol_min_field()
    join_token: Secret = spec("secret", "The join string's token.", pattern=_JOIN_TOKEN,
                              max_len=128, secret=True)
    fleet_id: str = spec("uuid", "The fleet the join string names.")
    node_id: str = spec("uuid", "This installation's id (FLEET_NODE_ID).")
    instance_id: str = spec("uuid", "This agent process's id (see Heartbeat).")
    node_name: str = spec("name", "FLEET_NODE_NAME.")
    site_name: str = spec("name", "FLEET_SITE_NAME.")
    csr_pem: str = spec("csr", "P-256 certificate signing request.")
    software: Software = spec("object", "What the node runs.", of=Software)

    _validate = _check_range


@dataclass(frozen=True, kw_only=True)
class EnrollResponse:
    """The node stores ``cert_chain`` (leaf first, then the issuing
    intermediate: the trust anchor it pinned is the root, which a lead change
    never changes) and presents the whole chain on every mTLS request."""

    protocol: int = spec("int", "Version of this answer.", min=1, max=65535)
    fleet_id: str = spec("uuid", "The fleet.")
    node_id: str = spec("uuid", "Echoes the request.")
    site_id: str = spec("uuid", "The site the lead filed this node under.")
    cert_chain: tuple = spec("chain", "Client certificate, then its issuing intermediate(s).",
                             min=2, max=4)
    not_after: str = spec("time", "When the leaf expires.")
    lead_endpoints: LeadEndpoints = spec("object", "The lead list.", of=LeadEndpoints)
    heartbeat_interval: int = spec("int", "Seconds to the first heartbeat.",
                                   min=HEARTBEAT_MIN, max=HEARTBEAT_MAX)
    store: StoreDescriptor | None = spec(
        "object", "Where to replicate; null until the lead has a store.", of=StoreDescriptor,
        required=False, nullable=True)


@dataclass(frozen=True, kw_only=True)
class HeartbeatRequest:
    """``POST /fleet/heartbeat``, every ``next_heartbeat`` seconds.

    ``instance_id`` is random per agent process start and never stored: a
    restart is a new instance that replaces the old one, and an instance the
    lead saw replaced that speaks again means two live installations share
    this ``node_id`` (a backup restored onto a second machine): the lead
    answers ``duplicate_node`` to every instance until an operator resolves
    it. The agent never sends two heartbeats at once."""

    protocol: int = _protocol_field()
    protocol_min: int = _protocol_min_field()
    node_id: str = spec("uuid", "This installation's id.")
    instance_id: str = spec("uuid", "This agent process's id.")
    sent_at: str = spec("time", "When the agent sent it.")
    lead_epoch: int = spec("int", "Epoch of the lead list the agent holds.", min=0, max=2**53)
    store_id: str | None = spec("uuid", "Store the agent replicates to, if any.",
                                required=False, nullable=True)
    inventory: Inventory = spec("object", "What the node is.", of=Inventory)
    replication: ReplicationState | None = spec(
        "object", "Replication state; null when not replicating.", of=ReplicationState,
        required=False, nullable=True)
    seed: SeedProgress | None = spec("object", "History seed; null when none.",
                                     of=SeedProgress, required=False, nullable=True)
    directive_results: tuple = spec("list", "Outcomes of the previous response's directives.",
                                    of=Spec("object", "", of=DirectiveResult),
                                    required=False, max=64)

    _validate = _check_range


@dataclass(frozen=True, kw_only=True)
class HeartbeatResponse:
    protocol: int = spec("int", "Version of this answer.", min=1, max=65535)
    server_time: str = spec("time", "The lead's clock (the agent logs a skew over 60 s).")
    next_heartbeat: int = spec("int", "Seconds until the next heartbeat.",
                               min=HEARTBEAT_MIN, max=HEARTBEAT_MAX)
    directives: tuple = spec(
        "list", "Instructions, applied in order (types below). Read-only toward the "
        "node's configuration: none changes what or how the node measures.",
        of=Spec("object", "", of=dict), required=False, max=32)

    @classmethod
    def from_wire(cls, data: Any) -> "HeartbeatResponse":
        """decode() for the envelope, decode_directive() for each directive."""
        if not isinstance(data, dict):
            raise ValidationError(["body: must be a JSON object"])
        raw = data.get("directives", [])
        if not isinstance(raw, list) or len(raw) > 32:
            raise ValidationError(["directives: must be a list of at most 32"])
        envelope = decode(_HeartbeatEnvelope, {k: v for k, v in data.items()
                                               if k != "directives"})
        return cls(protocol=envelope.protocol, server_time=envelope.server_time,
                   next_heartbeat=envelope.next_heartbeat,
                   directives=tuple(decode_directive(d) for d in raw))

    def _wire_extra(self) -> dict:
        return {"directives": [_directive_wire(d) for d in self.directives
                               if type(d) in DIRECTIVES.values()]}


@dataclass(frozen=True, kw_only=True)
class _HeartbeatEnvelope:
    protocol: int = spec("int", "", min=1, max=65535)
    server_time: str = spec("time", "")
    next_heartbeat: int = spec("int", "", min=HEARTBEAT_MIN, max=HEARTBEAT_MAX)


@dataclass(frozen=True, kw_only=True)
class RenewRequest:
    """``POST /fleet/renew``, with the current certificate. The old one stays
    valid until it expires, so a lost response costs nothing."""

    protocol: int = _protocol_field()
    protocol_min: int = _protocol_min_field()
    node_id: str = spec("uuid", "This installation's id.")
    instance_id: str = spec("uuid", "This agent process's id.")
    csr_pem: str = spec("csr", "A new CSR (a new key, generated on the node).")

    _validate = _check_range


@dataclass(frozen=True, kw_only=True)
class RenewResponse:
    protocol: int = spec("int", "Version of this answer.", min=1, max=65535)
    cert_chain: tuple = spec("chain", "The new leaf, then its issuing intermediate(s).",
                             min=2, max=4)
    not_after: str = spec("time", "When the new leaf expires.")


@dataclass(frozen=True, kw_only=True)
class LeaveRequest:
    """``POST /fleet/leave``. The agent first waits (bounded) for the
    replication queue to drain, because deleting a replication discards its
    queue; it says whether it did."""

    protocol: int = _protocol_field()
    protocol_min: int = _protocol_min_field()
    node_id: str = spec("uuid", "This installation's id.")
    instance_id: str = spec("uuid", "This agent process's id.")
    drained: bool = spec("bool", "The queue reached 0 before the agent gave up waiting.")
    remaining_bytes: int = spec("int", "What was left when it stopped waiting.",
                                min=0, max=2**53)

    _validate = _check_range


@dataclass(frozen=True, kw_only=True)
class LeaveResponse:
    protocol: int = spec("int", "Version of this answer.", min=1, max=65535)
    state: str = spec("token", "The node's state on the lead now.", values=("left",))


#: Error code -> HTTP status.
ERROR_STATUS: dict[str, int] = {
    "bad_request": 400,
    "protocol_unsupported": 400,
    "unauthenticated": 401,
    "token_invalid": 403,
    "revoked": 403,
    "node_exists": 409,
    "duplicate_node": 409,
    "not_current_lead": 421,
    "rate_limited": 429,
    "internal": 500,
    "unavailable": 503,
}

#: Error code -> the fixed message a lead sends with it.
ERROR_MESSAGE: dict[str, str] = {
    "bad_request": "The request did not match the protocol.",
    "protocol_unsupported": "No protocol version in common.",
    "unauthenticated": "A valid client certificate is required.",
    "token_invalid": "The join token is unknown, used or expired.",
    "revoked": "This node is revoked.",
    "node_exists": "A node with this id is already enrolled.",
    "duplicate_node": "Two installations are using this node id.",
    "not_current_lead": "This lead is not the current one.",
    "rate_limited": "Too many requests.",
    "internal": "The lead failed; see its log.",
    "unavailable": "The lead is not ready.",
}

#: Error code -> what the agent does. ``backoff``: retry the same lead with
#: backoff; ``next_lead``: try the next endpoint; ``stop``: stop sending,
#: stop replicating, report the state in `fleet status` and wait for a
#: person; ``renegotiate``: retry once within the advertised range, else
#: stop; ``fix_request``: a bug or a bad join string: stop and log.
ERROR_ACTION: dict[str, str] = {
    "bad_request": "fix_request",
    "protocol_unsupported": "renegotiate",
    "unauthenticated": "stop",
    "token_invalid": "stop",
    "revoked": "stop",
    "node_exists": "stop",
    "duplicate_node": "stop",
    "not_current_lead": "next_lead",
    "rate_limited": "backoff",
    "internal": "backoff",
    "unavailable": "backoff",
}

@dataclass(frozen=True, kw_only=True)
class ErrorResponse:
    """Every non-2xx body. ``message`` is a fixed sentence chosen by
    ``code``, never derived from an exception; ``error_id`` finds the detail
    in the sender's log. The optional fields belong to specific codes."""

    protocol: int = spec("int", "Version of this answer.", min=1, max=65535)
    code: str = spec("token", "What went wrong (open; unknown = internal).",
                     values=tuple(ERROR_STATUS))
    message: str = spec("text", "A fixed, human sentence.", max_len=300)
    error_id: str = spec("str", "Find the detail in the sender's log.", pattern=_HEX16)
    reasons: tuple = spec("list", "bad_request: which fields failed (schema paths).",
                          of=Spec("text", "", max_len=200), required=False, max=32)
    supported_min: int | None = spec("int", "protocol_unsupported: lowest the lead speaks.",
                                     required=False, min=1, max=65535)
    supported_max: int | None = spec("int", "protocol_unsupported: highest the lead speaks.",
                                     required=False, min=1, max=65535)
    retry_after: int | None = spec("int", "rate_limited, unavailable: seconds to wait.",
                                   required=False, min=1, max=86400)
    lead_endpoints: LeadEndpoints | None = spec(
        "object", "not_current_lead: where the lead is.", of=LeadEndpoints, required=False)


def error(code: str, *, error_id: str | None = None, protocol: int = PROTOCOL_MAX,
          **extra: Any) -> tuple[int, ErrorResponse]:
    """(HTTP status, body) for a known code. The message is the code's fixed
    sentence; detail belongs in the log under ``error_id``."""
    if code not in ERROR_STATUS:
        raise KeyError(code)
    body = ErrorResponse(protocol=protocol, code=code, message=ERROR_MESSAGE[code],
                         error_id=error_id or new_error_id(), **extra)
    return ERROR_STATUS[code], body


def agent_action(code: str) -> str:
    """What the agent does about an error code; unknown codes are internal."""
    return ERROR_ACTION.get(code, ERROR_ACTION["internal"])


# ---------------------------------------------------------------------------
# The reference (docs/fleet.md)
# ---------------------------------------------------------------------------

#: The messages and objects the reference documents, in order.
REFERENCE: tuple[type, ...] = (
    JoinString, RootResponse, EnrollRequest, Software, EnrollResponse,
    LeadEndpoints, StoreDescriptor,
    HeartbeatRequest, Inventory, DoctorSummary, Freshness, Uplink, ReplicationState,
    SeedProgress, DirectiveResult, HeartbeatResponse,
    Revoked, RepointStore, SetLeadEndpoints, RenewNow, StartSeed,
    RenewRequest, RenewResponse, LeaveRequest, LeaveResponse, ErrorResponse,
)

_KIND_TEXT = {
    "int": "integer", "bool": "boolean", "str": "string", "secret": "string, secret",
    "token": "token", "uuid": "UUID", "time": "time", "name": "name",
    "https_url": "https URL", "pem": "PEM certificate", "csr": "PEM CSR",
    "text": "string", "chain": "list of PEM certificates",
}


def _type_text(s: Spec) -> str:
    if s.kind == "object":
        return "object" if s.of is dict else f"`{s.of.__name__}`"
    if s.kind == "list":
        item = s.of
        inner = (f"`{item.of.__name__}`" if item.kind == "object" and item.of is not dict
                 else "object" if item.kind == "object" else _KIND_TEXT[item.kind])
        return f"list of {inner}"
    return _KIND_TEXT[s.kind]


def _bounds_text(s: Spec) -> str:
    parts = []
    if s.kind == "int" and (s.min is not None or s.max is not None):
        parts.append(f"{s.min if s.min is not None else ''}..{s.max if s.max is not None else ''}")
    if s.kind in ("list", "chain") and (s.min or s.max):
        parts.append(f"{s.min or 0}..{s.max} items")
    if s.max_len:
        parts.append(f"≤ {s.max_len} chars")
    if s.values:
        parts.append("known: " + ", ".join(f"`{v}`" for v in s.values))
    return "; ".join(parts)


def _doc(cls: type) -> str:
    text = (cls.__doc__ or "").strip()
    if text.startswith(cls.__name__ + "("):  # dataclass's generated docstring
        return ""
    return " ".join(text.split())


def reference_markdown() -> str:
    """The protocol reference, generated from the specs above."""
    lines = [
        f"Protocol versions spoken by this release: {PROTOCOL_MIN} to {PROTOCOL_MAX}.",
        "Types: *time* is UTC `YYYY-MM-DDTHH:MM:SSZ`; *token* is an open "
        f"vocabulary value matching `{TOKEN_PATTERN}`; *name* is 1 to 64 letters, "
        "digits, spaces and `. _ - ' ( )`; *secret* never appears in a log, a status "
        "or `repr`. Optional fields may be omitted; *null* is allowed only where it says so.",
        "",
    ]
    for cls in REFERENCE:
        title = cls.__name__
        if cls in DIRECTIVES.values():
            title += f" — directive `{cls.TYPE}`"
        lines += [f"#### {title}", ""]
        doc = _doc(cls)
        if doc:
            lines += [doc, ""]
        lines += ["| Field | Type | Required | Notes |", "| --- | --- | --- | --- |"]
        if cls in DIRECTIVES.values():
            lines.append(f"| `type` | token | yes | `{cls.TYPE}` |")
        for name, s in _specs(cls):
            req = "yes" if s.required else "no"
            if s.nullable:
                req += ", nullable"
            notes = s.doc
            bounds = _bounds_text(s)
            if bounds:
                notes = f"{notes} ({bounds})" if notes else bounds
            lines.append(f"| `{name}` | {_type_text(s)} | {req} | {notes} |")
        lines.append("")
    lines += ["#### Error codes", "",
              "| Code | HTTP | Agent action | Message |", "| --- | --- | --- | --- |"]
    for code, status in ERROR_STATUS.items():
        lines.append(f"| `{code}` | {status} | {ERROR_ACTION[code]} | {ERROR_MESSAGE[code]} |")
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    print(reference_markdown(), end="")
