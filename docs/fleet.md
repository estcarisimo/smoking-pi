# Fleet (in progress)

One Smoking Pi measures one network from one place. Fleet is for several:
installations in different houses, offices or cities ("nodes") report to one
"lead", which shows them together. Each node keeps measuring on its own, with
its own history, whatever happens to the lead.

!!! warning "What this release has, and what it does not"
    This release gives every Pro installation its **identity** (a node id and
    the names a lead will show), adds the two services **switched off by
    default**, the `smoking-pi fleet status` command, and the **protocol** the
    node agent and the lead will speak. It does **not** create a fleet, join
    one, send anything, or replicate any data. With the services on, they log
    their state ("not enrolled", "no fleet initialized") and idle, without
    opening a port or making a network call. See
    [What is not built yet](#what-is-not-built-yet).

Fleet is part of the **Pro** edition. Basic and Standard have no fleet agent:
they have no InfluxDB to replicate from, and Fleet's data plane is InfluxDB
replication.

## Turning it on

```bash
smoking-pi fleet status
```

```text
Fleet (docs/fleet.md)
  node id:    6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b
  node name:  node-6f1c2a3b
  site:       default
  role:       none (Fleet is off: smoking-pi enable fleet)
  leads:      none set
  enrolled:   not known (the fleet agent is off)
Joining a fleet is not built yet: this release gives the installation its identity.
```

The first `fleet status` (or `smoking-pi enable fleet`) creates the node id.
`--json` prints the same as one JSON object.

| Service | Profile | Turn on | What it is |
| --- | --- | --- | --- |
| `fleet-agent` | `fleet` | `smoking-pi enable fleet` | The node agent: this host reports to a lead. |
| `fleet-lead` | `fleet-lead` | `smoking-pi enable fleet-lead` | The lead: other hosts report here. Its CA and node registry. |

Both run the same image (`fleet`), with no capability, a read-only root
filesystem and one volume each (`fleet-agent-state`, `fleet-lead-state`).

## Three topologies

Which one is a matter of configuration, not of a different install:

1. **A dedicated lead** that does not measure: a host running the
   `fleet-lead` service with `FLEET_LEAD_MODE=dedicated`. Every other
   installation runs `fleet-agent`.
2. **A measuring node that holds the lead role**: one installation runs both
   services (`FLEET_LEAD_MODE=node`, the default) and is a node of its own
   fleet. The role can move to another node by command (later).
3. **A lead elected among candidate nodes** (later, with etcd): needs at
   least three candidates that reach each other directly.

This release lets you choose 1 or 2 (`FLEET_LEAD_MODE` offers `dedicated` and
`node`). The protocol already serves 3: the lead mode travels as an open
value, agents never assume a single lead (they hold a list of lead endpoints,
follow the current one, and take a newer list from any lead), and the lead
tells each node where to replicate, so the store can move with the role.

## Identity

| Id | Created by | Where it lives |
| --- | --- | --- |
| `node_id` | `smoking-pi`, once per installation (a random UUID) | `FLEET_NODE_ID` in the env file |
| node name | you: `smoking-pi config set FLEET_NODE_NAME "Kitchen Pi"` | the env file |
| site name | you: `smoking-pi config set FLEET_SITE_NAME Home` | the env file |
| `site_id` | the lead, when the node enrolls | the agent's volume |
| `fleet_id` | the lead, when the fleet is created | the lead's volume; the join string |
| `instance_id` | the agent, at every start (never stored) | memory only |

The node id is in the env file because that is the one file every path
keeps: upgrades never rewrite it, `smoking-pi backup` copies it, `restore`
puts it back, and the command can read it with every service off.
`smoking-pi config set FLEET_NODE_ID` is refused: the id is created once and
never set. Names may change at any time; the lead shows the current ones.

Measurements are **not** tagged with any of these ids. A node's data lands in
its own bucket on the lead's store (`node-<node_id>`), which is the
attribution; nothing local changes, and no dashboard or exporter does.

### Two installations, one node id

Restoring a backup onto a second machine while the first keeps running gives
two installations the same node id, and the same key and certificate if the
agent's volume came along. Nothing cryptographic tells them apart, so the
protocol does it with the **instance id**:

- Each agent process makes a random `instance_id` when it starts and sends it
  with every request. It is never written to disk, so a backup cannot carry
  it.
- The lead remembers, per node, the instance it is talking to and the ones it
  saw replaced. A restart (or a restore onto a new card while the old one is
  gone) is a new instance that replaces the old one: accepted.
- A replaced instance that speaks again means two copies are alive. The lead
  answers `duplicate_node` (409) to **every** instance of that node from then
  on and marks the node as in conflict; the agents stop sending and stop
  replicating, and say so in `fleet status`. It is not guessed which copy is
  the original: a person resolves it by revoking the node and joining the
  installation to keep (the other needs a new node id first).
- Enrolling a node id the lead already has (a clone joining with a fresh
  token) is refused with `node_exists`.

The opposite mismatch is caught on the node itself. The agent's enrollment
file records the node id it was enrolled under; if `FLEET_NODE_ID` says
something else (an older env file restored beside a newer agent volume, or
the reverse), the agent reports `identity_mismatch` and sends nothing, and
`fleet status` shows it. An enrollment file that cannot be read or is not
what it should be is `state_corrupt`: never treated as "not enrolled", so
the agent will not enroll over it. Both wait for a person.

What it does not catch: two copies of which only one ever talks to the lead.
That one is simply the node.

## Security model

- **Private CA, offline root plus one intermediate per lead.** Nodes trust
  only the root, which never changes when the lead does; each lead signs with
  its own intermediate, so every message that carries a certificate carries a
  chain (leaf, then intermediate), and the node stores and presents that
  chain.
- **Joining** takes a join string, `spfleet1_…`: the fleet id, the lead URLs,
  a single-use token that expires in minutes, and the SHA-256 of the root
  certificate. The node fetches the root, refuses it unless the fingerprint
  matches, and from then on trusts nothing else. It generates its P-256 key
  locally (the key never leaves the node), sends a certificate request with
  the token, and receives a 90-day client certificate for its node id.
- **Every certificate the sender or the node presents travels as a chain**
  (leaf, then intermediate). The exceptions are trust anchors, which are
  single certificates: the fleet root a node pins (`root_pem`) and the
  anchor of the store's TLS (`ca_pem`).
- **Every later request is mutual TLS.** Renewal uses the current
  certificate; revocation is a flag in the lead's registry, checked on every
  request (no CRL), and a revoked node is told to stop.
- **The agent always opens the connection** (outbound HTTPS), so a node
  behind NAT needs nothing opened. The lead never changes a node's
  configuration or triggers an upgrade: version 1 is read-only toward nodes.
- **Data plane** (later): InfluxDB replication to the lead's store over TLS
  with a certificate from the fleet CA; the node authenticates with a
  write-only token scoped to its own bucket.

### Where the secrets live

| Secret | Where | Never in |
| --- | --- | --- |
| Join token | nowhere on the node: read by the join command, sent once; the lead keeps its SHA-256 only | logs, `repr`, status |
| Node private key | `fleet-agent-state`: `node/key.pem`, mode 0600 | any message |
| Store write token | `fleet-agent-state`: `node/store-token`, 0600; and InfluxDB's remote | logs, status, heartbeat |
| Root key | the lead that ran the fleet's creation, `lead/ca/root-key.pem`, 0600, meant to be moved offline | any message |
| Intermediate key | `fleet-lead-state`: `lead/ca/intermediate-key.pem`, 0600 | any message |

`fleet/layout.py` is the one list of these paths. The protocol types the two
secrets that do travel (`Secret`): their `repr` and `str` are redacted, a
log gets `redacted()`, and only the encoder for the wire reveals them. A
`Secret` refuses to be pickled, so `copy.deepcopy` and `dataclasses.asdict`
fail on any message that holds one: use `encode()`, `redacted()` or
`dataclasses.replace()`. The
tests fill every secret file with a marker and check that no status file,
log line or `repr` ever shows it.

`smoking-pi backup` copies the volumes of the services that are on: with
the fleet agent on, a backup holds the node's key (which is how a replaced
SD card keeps its enrollment). Keep backups as private as the env file they
also contain.

## Protocol

HTTPS with JSON bodies. The routes:

| Route | Authentication | Request → response |
| --- | --- | --- |
| `GET /fleet/root` | none; the answer is pinned by the join string's fingerprint | → `RootResponse` |
| `POST /fleet/enroll` | TLS against the pinned root, plus the join token | `EnrollRequest` → `EnrollResponse` |
| `POST /fleet/heartbeat` | mTLS | `HeartbeatRequest` → `HeartbeatResponse` |
| `POST /fleet/renew` | mTLS | `RenewRequest` → `RenewResponse` |
| `POST /fleet/leave` | mTLS | `LeaveRequest` → `LeaveResponse` |

Any other answer is an `ErrorResponse`.

### Compatibility rules

- **Versions are negotiated on every request.** A request says the range it
  speaks (`protocol_min` to `protocol`); the lead answers in the highest
  version both speak and says which in the response's `protocol`. With none
  in common it answers `protocol_unsupported` with its own range. Nothing is
  stored, so a lead and its agents upgrade independently: an older agent gets
  answers in its version, a newer agent gets the lead's newest.
- **Unknown fields are ignored**, at every level, by both sides. A newer
  version may add an optional field without a version bump; adding a
  required field, removing one or changing a meaning is a new version.
- **Known fields are strict**: a wrong type, a value out of range, a
  malformed id or a missing required field rejects the whole message
  (`bad_request`, with the failing fields' paths).
- **Open vocabularies** (lead mode, edition, profiles, capabilities, the
  `type` of a directive, the `code` of an error) accept any lowercase token;
  a value the receiver does not know is kept as-is and treated as "other".
  `lead_mode: elected` will need no version bump.
- **Unknown directives are not applied.** The agent reports each one in its
  next heartbeat as `ignored`, and the lead decides what to do about an agent
  too old for it. A known directive whose arguments fail validation is
  reported `invalid`; the rest of the response still applies.
- **A directive without a usable `id`** cannot be reported back: the agent
  drops it and logs why, and the rest of the response still applies
  (`revoked` first). The lead never sends one; seeing one is a lead bug.
- **Unknown error codes** are handled as `internal`: back off and retry.
- **Optional fields** may be omitted or `null`, both meaning absent; a
  required field is never `null`.
- **Patterns are matched whole, in ASCII**: an id with a trailing newline or
  another script's digits is malformed. Names are the exception: node and
  site names may use any script ("Zürich", "東京"), in NFC form.
- **Bodies are at most 256 KiB**, both ways; a larger one is refused unread
  (`too_large`).

### Heartbeats and directives

The agent sends a heartbeat every `next_heartbeat` seconds (10 to 3600; the
lead chooses), one at a time. It carries what the lead needs to see a node
from afar: the inventory (version, edition, profiles and capabilities, the
last doctor's counts, whether it is measuring, the uplink interface), the
replication's state as InfluxDB reports it (bytes waiting, last response
code and message) plus whether the log said "queue is full" (a full queue
drops the newest points, silently otherwise), the history seed's progress,
and what it did with the previous response's directives.

When no lead answers, the agent backs off from 30 s, doubling with jitter, up
to 30 minutes, and nothing else on the node changes: the agent is never in
the measurement path.

The response may carry directives, applied in order:

| Directive | The agent |
| --- | --- |
| `revoked` | deletes its replication and remote (a revoked node otherwise retries forever), stops sending, keeps its local data. Overrides the rest. |
| `repoint_store` | updates its remote and replication in place, which keeps the queue (delete and re-create would discard it). |
| `lead_endpoints` | takes the new lead list if its epoch is higher than the one it holds. |
| `renew_now` | renews its certificate now. |
| `start_seed` | copies its local history in a time range to the store, rate-limited (replication only carries new writes). |

`leave` waits, for a bounded time, until the replication queue is empty
before deleting anything, and says whether it got there.

### Reference

Generated from `shared/modules/fleet/fleet/protocol.py`; a test fails when
this section and the code differ.

<!-- protocol-reference:start (generated by shared/modules/fleet/write_reference.py) -->

Protocol versions spoken by this release: 1 to 1.
Types: *time* is UTC `YYYY-MM-DDTHH:MM:SSZ`; *token* is an open vocabulary value matching `[a-z][a-z0-9_-]{0,31}` (whole, ASCII); *name* is 1 to 64 letters and digits of any script, spaces and `. _ - ' ( )`, in NFC form; *secret* never appears in a log, a status or `repr`. Optional fields may be omitted or null, both meaning absent; required fields are never null. Bodies are at most 262144 bytes.

#### JoinString

What ``smoking-pi fleet token`` prints on the lead and ``fleet join`` takes on the node: ``spfleet1_`` + base64url (unpadded) of a compact JSON object with these fields. The token is single-use and short-lived; the lead stores only its SHA-256. ``root_sha256`` is the fingerprint the node pins the root certificate to before it trusts anything the lead says.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `fleet_id` | UUID | yes | The fleet's id, created by `fleet init` on the lead. |
| `lead_urls` | list of https URL | yes | Where to enroll: the lead's base URLs, in order of preference. (1..8 items) |
| `token` | string, secret | yes | Single-use enrollment token (32-128 base64url characters). (≤ 128 chars) |
| `root_sha256` | string | yes | SHA-256 of the fleet root certificate's DER, lowercase hex. |
| `expires_at` | time | no | When the token expires (informative: the lead decides). |

#### RootResponse

``GET /fleet/root``: the root certificate. The node accepts it only if its DER's SHA-256 equals the join string's ``root_sha256``.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `protocol` | integer | yes | Version of this answer. (1..65535) |
| `protocol_min` | integer | yes | Lowest protocol version the sender speaks. (1..65535) |
| `protocol_max` | integer | yes | Highest version the lead speaks. (1..65535) |
| `root_pem` | PEM certificate | yes | The fleet root certificate. |

#### EnrollRequest

``POST /fleet/enroll``. The CSR's key was generated on the node and never leaves it; the lead ignores the CSR's subject and issues a certificate for ``node_id`` (CN ``node-<id>``, SAN ``urn:smoking-pi:node:<id>``, EKU clientAuth).

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `protocol` | integer | yes | Highest protocol version the sender speaks. (1..65535) |
| `protocol_min` | integer | yes | Lowest protocol version the sender speaks. (1..65535) |
| `join_token` | string, secret | yes | The join string's token. (≤ 128 chars) |
| `fleet_id` | UUID | yes | The fleet the join string names. |
| `node_id` | UUID | yes | This installation's id (FLEET_NODE_ID). |
| `instance_id` | UUID | yes | This agent process's id (see Heartbeat). |
| `node_name` | name | yes | FLEET_NODE_NAME. |
| `site_name` | name | yes | FLEET_SITE_NAME. |
| `csr_pem` | PEM CSR | yes | P-256 certificate signing request. |
| `software` | `Software` | yes | What the node runs. |

#### Software

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `version` | string | yes | Smoking Pi version. |
| `edition` | token | yes | Edition. (known: `basic`, `standard`, `pro`) |

#### EnrollResponse

The node stores ``cert_chain`` (leaf first, then the issuing intermediate: the trust anchor it pinned is the root, which a lead change never changes) and presents the whole chain on every mTLS request.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `protocol` | integer | yes | Version of this answer. (1..65535) |
| `fleet_id` | UUID | yes | The fleet. |
| `node_id` | UUID | yes | Echoes the request. |
| `site_id` | UUID | yes | The site the lead filed this node under. |
| `cert_chain` | list of PEM certificates | yes | Client certificate, then its issuing intermediate(s). (2..4 items) |
| `not_after` | time | yes | When the leaf expires. |
| `lead_endpoints` | `LeadEndpoints` | yes | The lead list. |
| `heartbeat_interval` | integer | yes | Seconds to the first heartbeat. (10..3600) |
| `store` | `StoreDescriptor` | no | Where to replicate; null until the lead has a store. |

#### LeadEndpoints

Where the leads are. Agents never assume a single lead: they hold this list, send to ``current``, try the others in order when it does not answer, and replace their copy only with a list of a higher ``epoch``. A lead change (mode 2 move, mode 3 election) is a new epoch.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `epoch` | integer | yes | Version of this list; only a higher one replaces it. (1..9007199254740992) |
| `current` | https URL | yes | The lead to send to now. |
| `urls` | list of https URL | yes | Every lead endpoint, in order of preference; contains `current`. (1..16 items) |
| `lead_mode` | token | yes | How the lead role is held (open: an unknown value means 'other'). (known: `dedicated`, `node`, `elected`) |

#### StoreDescriptor

Where a node replicates (InfluxDB 2 native replication) and with what. The write token is scoped to this node's bucket only, write-only, and is shown by InfluxDB 2.9 only when created: this message is the one place it travels, and the node keeps it in ``store-token`` (layout.py).

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `store_id` | UUID | yes | Identifies this store; a different id means repoint. |
| `url` | https URL | yes | The store's InfluxDB base URL (TLS, fleet CA or `ca_pem`). |
| `org_id` | string | yes | Remote organization ID (16 hex). |
| `bucket_id` | string | yes | This node's bucket on the store (16 hex). |
| `bucket_name` | string | yes | Its name, `node-<node_id>`, for display. |
| `write_token` | string, secret | yes | Write-only token for that bucket alone. (≤ 256 chars) |
| `ca_pem` | PEM certificate | yes | The certificate that anchors the store's TLS chain. |
| `max_queue_bytes` | integer | no | Replication queue size the node should set (InfluxDB --max-queue-bytes). (1048576..1099511627776) |

#### HeartbeatRequest

``POST /fleet/heartbeat``, every ``next_heartbeat`` seconds. ``instance_id`` is random per agent process start and never stored: a restart is a new instance that replaces the old one, and an instance the lead saw replaced that speaks again means two live installations share this ``node_id`` (a backup restored onto a second machine): the lead answers ``duplicate_node`` to every instance until an operator resolves it. The agent never sends two heartbeats at once.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `protocol` | integer | yes | Highest protocol version the sender speaks. (1..65535) |
| `protocol_min` | integer | yes | Lowest protocol version the sender speaks. (1..65535) |
| `node_id` | UUID | yes | This installation's id. |
| `instance_id` | UUID | yes | This agent process's id. |
| `sent_at` | time | yes | When the agent sent it. |
| `lead_epoch` | integer | yes | Epoch of the lead list the agent holds. (0..9007199254740992) |
| `store_id` | UUID | no | Store the agent replicates to, if any. |
| `inventory` | `Inventory` | yes | What the node is. |
| `replication` | `ReplicationState` | no | Replication state; null when not replicating. |
| `seed` | `SeedProgress` | no | History seed; null when none. |
| `directive_results` | list of `DirectiveResult` | no | Outcomes of the previous response's directives. (0..64 items) |

#### Inventory

What the node is, sent with every heartbeat.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `node_name` | name | yes | The node's human name (FLEET_NODE_NAME). |
| `site_name` | name | yes | The site's human name (FLEET_SITE_NAME). |
| `version` | string | yes | Smoking Pi version. |
| `edition` | token | yes | Edition. (known: `basic`, `standard`, `pro`) |
| `profiles` | list of token | yes | Compose profiles enabled. (0..64 items) |
| `capabilities` | list of token | yes | What the node can do (open vocabulary). (0..64 items; known: `influxdb`, `clickhouse`, `replication`, `seed`) |
| `doctor` | `DoctorSummary` | no | Last doctor run. |
| `measuring` | `Freshness` | no | Measurement freshness. |
| `uplink` | `Uplink` | no | Uplink interface. |

#### DoctorSummary

The last `smoking-pi doctor --live` on the node, counted.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `ok` | integer | yes | Checks that passed. (0..10000) |
| `warn` | integer | yes | Checks that warned. (0..10000) |
| `fail` | integer | yes | Checks that failed. (0..10000) |
| `skipped` | integer | yes | Checks skipped. (0..10000) |
| `at` | time | yes | When the doctor ran. |

#### Freshness

"Is it measuring": what the node's targets last produced.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `state` | token | yes | Overall answer. (known: `measuring`, `stale`, `unknown`) |
| `targets` | integer | yes | Targets in the generated configuration. (0..100000) |
| `stale` | integer | yes | Of those, how many produced nothing for two steps. (0..100000) |
| `newest_age_seconds` | integer | no | Age of the newest measurement. (0..2147483648) |

#### Uplink

The interface the node measures through.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `interface` | string | yes | Interface name. |
| `kind` | token | yes | Link type. (known: `wifi`, `ethernet`, `other`) |

#### ReplicationState

The node's replication as InfluxDB reports it, plus what the agent saw in the log. ``latest_response_code`` 0 means a network or TLS failure (InfluxDB's convention); a full queue drops the *newest* points, and only the log says so, hence ``queue_full_seen``.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `store_id` | UUID | yes | The store this replication points at. |
| `remaining_bytes` | integer | yes | remainingBytesToBeSynced. (0..9007199254740992) |
| `latest_response_code` | integer | yes | Last HTTP status from the store (0: network). (0..599) |
| `latest_error` | string | no | Last error message InfluxDB recorded, truncated. (≤ 512 chars) |
| `queue_full_seen` | boolean | yes | A 'queue is full' log line since the last heartbeat. |
| `queue_full_last_at` | time | no | When the last one was seen. |
| `max_queue_bytes` | integer | yes | The queue's size limit. (0..9007199254740992) |

#### SeedProgress

Copying pre-existing history to the store (replication only carries new writes).

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `state` | token | yes | Where the seed is. (known: `pending`, `running`, `done`, `failed`, `paused`) |
| `since` | time | yes | Oldest point to copy. |
| `until` | time | yes | Newest point to copy (the replication's start). |
| `cursor` | time | no | Copied up to here. |
| `bytes_sent` | integer | yes | Bytes sent so far. (0..9007199254740992) |
| `error_id` | string | no | Set when state is failed: the node's log id. |

#### DirectiveResult

What the agent did with a directive of the previous response.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string | yes | The directive's id. |
| `status` | token | yes | Outcome. (known: `done`, `failed`, `ignored`, `invalid`) |
| `error_id` | string | no | For failed: the node's log id. |

#### HeartbeatResponse

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `protocol` | integer | yes | Version of this answer. (1..65535) |
| `server_time` | time | yes | The lead's clock (the agent logs a skew over 60 s). |
| `next_heartbeat` | integer | yes | Seconds until the next heartbeat. (10..3600) |
| `directives` | list of object | no | Instructions, applied in order (types below); `revoked` overrides the rest. Read-only toward the node's configuration: none changes what or how the node measures. One without a usable `id` is dropped and logged by the agent (the lead never sends one); the others still apply. (0..32 items) |

#### Revoked — directive `revoked`

Stop: this node is no longer in the fleet. The agent deletes its replication and remote (a revoked node otherwise retries forever), stops heartbeating and keeps its local data. Overrides every other directive of the same response. Only a new join undoes it.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `type` | token | yes | `revoked` |
| `id` | string | yes | Directive id. |
| `reason` | token | yes | Why (open). (known: `operator`, `left`) |

#### RepointStore — directive `repoint_store`

Replicate to this store from now on. The agent updates its remote and replication in place (never delete + create, which discards the queue).

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `type` | token | yes | `repoint_store` |
| `id` | string | yes | Directive id. |
| `store` | `StoreDescriptor` | yes | The new store. |

#### SetLeadEndpoints — directive `lead_endpoints`

A new lead list; applied only when its epoch is higher than held.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `type` | token | yes | `lead_endpoints` |
| `id` | string | yes | Directive id. |
| `lead_endpoints` | `LeadEndpoints` | yes | The list. |

#### RenewNow — directive `renew_now`

Renew the client certificate now, whatever its age.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `type` | token | yes | `renew_now` |
| `id` | string | yes | Directive id. |

#### StartSeed — directive `start_seed`

Copy local history in [since, until) to the store, at most ``max_bytes_per_second`` and within the measurement budget.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `type` | token | yes | `start_seed` |
| `id` | string | yes | Directive id. |
| `since` | time | yes | Oldest point. |
| `until` | time | yes | Newest point. |
| `max_bytes_per_second` | integer | yes | Rate limit. (1024..1073741824) |

#### RenewRequest

``POST /fleet/renew``, with the current certificate. The old one stays valid until it expires, so a lost response costs nothing.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `protocol` | integer | yes | Highest protocol version the sender speaks. (1..65535) |
| `protocol_min` | integer | yes | Lowest protocol version the sender speaks. (1..65535) |
| `node_id` | UUID | yes | This installation's id. |
| `instance_id` | UUID | yes | This agent process's id. |
| `csr_pem` | PEM CSR | yes | A new CSR (a new key, generated on the node). |

#### RenewResponse

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `protocol` | integer | yes | Version of this answer. (1..65535) |
| `cert_chain` | list of PEM certificates | yes | The new leaf, then its issuing intermediate(s). (2..4 items) |
| `not_after` | time | yes | When the new leaf expires. |

#### LeaveRequest

``POST /fleet/leave``. The agent first waits (bounded) for the replication queue to drain, because deleting a replication discards its queue; it says whether it did.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `protocol` | integer | yes | Highest protocol version the sender speaks. (1..65535) |
| `protocol_min` | integer | yes | Lowest protocol version the sender speaks. (1..65535) |
| `node_id` | UUID | yes | This installation's id. |
| `instance_id` | UUID | yes | This agent process's id. |
| `drained` | boolean | yes | The queue reached 0 before the agent gave up waiting. |
| `remaining_bytes` | integer | yes | What was left when it stopped waiting. (0..9007199254740992) |

#### LeaveResponse

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `protocol` | integer | yes | Version of this answer. (1..65535) |
| `state` | token | yes | The node's state on the lead now. (known: `left`) |

#### ErrorResponse

Every non-2xx body. ``message`` is a fixed sentence chosen by ``code``, never derived from an exception; ``error_id`` finds the detail in the sender's log. The optional fields belong to specific codes.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `protocol` | integer | yes | Version of this answer. (1..65535) |
| `code` | token | yes | What went wrong (open; unknown = internal). (known: `bad_request`, `protocol_unsupported`, `unauthenticated`, `token_invalid`, `revoked`, `node_exists`, `duplicate_node`, `not_current_lead`, `too_large`, `rate_limited`, `internal`, `unavailable`) |
| `message` | string | yes | A fixed, human sentence. (≤ 300 chars) |
| `error_id` | string | yes | Find the detail in the sender's log. |
| `reasons` | list of string | no | bad_request: which fields failed (schema paths). (0..32 items) |
| `supported_min` | integer | no | protocol_unsupported: lowest the lead speaks. (1..65535) |
| `supported_max` | integer | no | protocol_unsupported: highest the lead speaks. (1..65535) |
| `retry_after` | integer | no | rate_limited, unavailable: seconds to wait. (1..86400) |
| `lead_endpoints` | `LeadEndpoints` | no | not_current_lead: where the lead is. |

#### Error codes

| Code | HTTP | Agent action | Message |
| --- | --- | --- | --- |
| `bad_request` | 400 | fix_request | The request did not match the protocol. |
| `protocol_unsupported` | 400 | renegotiate | No protocol version in common. |
| `unauthenticated` | 401 | stop | A valid client certificate is required. |
| `token_invalid` | 403 | stop | The join token is unknown, used or expired. |
| `revoked` | 403 | stop | This node is revoked. |
| `node_exists` | 409 | stop | A node with this id is already enrolled. |
| `duplicate_node` | 409 | stop | Two installations are using this node id. |
| `not_current_lead` | 421 | next_lead | This lead is not the current one. |
| `too_large` | 413 | fix_request | The request body is too large. |
| `rate_limited` | 429 | backoff | Too many requests. |
| `internal` | 500 | backoff | The lead failed; see its log. |
| `unavailable` | 503 | backoff | The lead is not ready. |

<!-- protocol-reference:end -->

## Settings

In the env file's `## Fleet` section; `smoking-pi config describe KEY`
explains each.

| Setting | Default | Meaning |
| --- | --- | --- |
| `FLEET_NODE_ID` | created once | This installation's id. Never set by hand. |
| `FLEET_NODE_NAME` | `node-<first 8 of the id>` | The name the lead shows. |
| `FLEET_SITE_NAME` | `default` | The site's name; the lead gives it an id. |
| `FLEET_LEAD_URLS` | empty | The leads' https base URLs, comma-separated, in order. |
| `FLEET_LEAD_MODE` | `node` | On the lead: `node` (it measures too) or `dedicated`. |

## What is not built yet

Stated plainly, so nothing here is mistaken for a feature:

- **The lead**: creating a fleet and its CA, join tokens, the registry,
  the HTTPS endpoints, listing and revoking nodes, moving the role. Its
  commands (`fleet init`, `fleet token`, `fleet nodes`, `fleet revoke`,
  `fleet promote`) do not exist yet.
- **The agent**: joining (`fleet join`), heartbeats, renewal, `fleet leave`.
- **The data plane**: the lead's store, per-node buckets and tokens,
  replication, the history seed, and showing the nodes' data on the lead.
- **Election** (topology 3).

Until then the two services only report their state, and
`smoking-pi fleet status` is the one Fleet command.
