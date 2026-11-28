# Fleet (in progress)

One Smoking Pi measures one network from one place. Fleet is for several:
installations in different houses, offices or cities ("nodes") report to one
"lead", which shows them together. Each node keeps measuring on its own, with
its own history, whatever happens to the lead.

!!! warning "What this release has, and what it does not"
    This release gives every Pro installation its **identity** (a node id and
    the names a lead will show), adds the two services **switched off by
    default**, the **protocol** the node agent and the lead speak, **the
    lead** (creating a fleet and its CA, join strings, the node registry and
    the HTTPS endpoints: [Setting up a lead](#setting-up-a-lead)) and the
    **node's side**: `smoking-pi fleet join`, heartbeats, certificate
    renewal and `smoking-pi fleet leave` ([On a node](#on-a-node)). It does
    **not** replicate any data. See
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
Join a fleet: smoking-pi fleet join (paste the join string 'smoking-pi fleet token' printed on the lead).
```

The first `fleet status` (or `smoking-pi enable fleet`) creates the node id.
`--json` prints the same as one JSON object.

| Service | Profile | Turn on | What it is |
| --- | --- | --- | --- |
| `fleet-agent` | `fleet` | `smoking-pi enable fleet` | The node agent: this host reports to a lead. |
| `fleet-lead` | `fleet-lead` | `smoking-pi fleet init` ([Setting up a lead](#setting-up-a-lead)) | The lead: other hosts report here. Its CA, node registry and HTTPS endpoints (port `FLEET_LEAD_PORT`). |

Both run the same image (`fleet`), with no capability, a read-only root
filesystem and one volume each (`fleet-agent-state`, `fleet-lead-state`).

## Setting up a lead

On the host that leads (a measuring Pi, or a machine that only leads):

```bash
smoking-pi fleet init --url https://192.168.1.10:8443 --name "Home fleet"
```

```text
Created fleet 0b1e2f3a-5c6d-4e7f-8a9b-0c1d2e3f4a5b (Home fleet), led from here.
  lead URLs:   https://192.168.1.10:8443
  root SHA-256: 3f1c… (every join string carries it)
Next: 'smoking-pi fleet token' prints a join string for a node.
```

`--url` is where nodes reach the lead: an address or a name every node can
reach, on the port `FLEET_LEAD_PORT` (8443 unless you change it). Give
`--url` once per address (a LAN address and a public name, say); the lead's
TLS certificate names them all, and changing them later
(`smoking-pi config set FLEET_LEAD_URLS …`) re-issues it and tells every
node. Run without `--url`, the command suggests this host's address.

`fleet init` creates the fleet id, the fleet's CA (a root, and this lead's
intermediate signed by it), the node registry and the lead's server
certificate, all in the `fleet-lead-state` volume, then turns the
`fleet-lead` service on. From then on:

| Command | What it does |
| --- | --- |
| `smoking-pi fleet token [--name N] [--site S] [--ttl 15m]` | Prints a join string for one node, once. It is single-use and expires (15 minutes unless `--ttl`, at most 24 h); the lead keeps only its SHA-256. `--name`/`--site` file the node under these names, whatever it calls itself. |
| `smoking-pi fleet nodes [--json]` | The enrolled nodes: state (`active`, `revoked`, `left`, `conflict`, "silent" when not heard from in 5 minutes), last heartbeat, version, certificate expiry. |
| `smoking-pi fleet revoke NODE` | Cuts a node off (its id, 8 or more characters of it, or its name). Its next heartbeat is told to stop; only a new join string brings it back. |
| `smoking-pi fleet ca status` | The CA: root fingerprint, whether the root key is on this lead, the intermediate's expiry. |
| `smoking-pi fleet status` | Also shows what the lead serves, and whether its root key is still online. |

`FLEET_LEAD_MODE` says whether the lead also measures as a node of its own
fleet (`node`, the default) or only leads (`dedicated`). The lead never
opens a connection to a node: nodes behind NAT need nothing forwarded, and
only the lead's port must be reachable from them.

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

## On a node

Everything here runs on the node, and none of it is in the measurement path:
the node measures exactly the same whether it is in a fleet or not, whether
the lead answers or not.

### Joining a fleet

On the lead, `smoking-pi fleet token` prints a join string (`spfleet1_…`).
On the node:

```bash
smoking-pi fleet join
```

```text
Paste the join string (from smoking-pi fleet token on the lead; it is not shown):
Joining: the lead's root is checked against the join string, then this node enrolls (its key is made here and never leaves it).
Joined fleet 0b1e2f3a-0000-4000-8000-000000000001 as node 6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b, through https://lead.example:8443.
Its certificate is valid until 2027-02-25T12:00:00Z and renews by itself.
FLEET_LEAD_URLS=https://lead.example:8443
```

The join string holds a single-use token, so it is **never an argument**:
paste it at the prompt (it is not echoed), or pipe it in
(`smoking-pi fleet join < file`). `smoking-pi fleet join spfleet1_…` is
refused, because every user of the host sees a command's arguments in `ps`,
and the shell keeps them in its history.

What happens:

1. The command checks this installation has a node id and is not in a fleet
   already (joining twice, or over a corrupt or revoked enrollment, is
   refused: `fleet leave` first).
2. It runs the enrollment **in the fleet image** (`docker compose run` of the
   `fleet-agent` service, the join string on its stdin): that is where the
   key is made and kept, in the agent's volume as the agent's user, and the
   host needs no Python package. The token is held in memory, sent once, and
   never written, logged or printed.
3. The enrollment fetches `/fleet/root` from the leads in order, and refuses,
   **before sending anything**, a root whose SHA-256 is not the join
   string's. From then on that root is the only certificate it trusts (never
   the system's store).
4. It generates the P-256 key, sends a certificate request with the token,
   and checks the answer: the same fleet and node id, a chain that leads to
   the pinned root, a certificate for this key and this node id.
5. It writes the root, key (0600), chain and enrollment, the enrollment
   last. Then the command records the lead list in `FLEET_LEAD_URLS` and turns
   the agent on (`enable fleet`).

A join that fails keeps nothing: the node is as it was. The messages say
what to do:

| What went wrong | What the node says |
| --- | --- |
| Not a join string | copy it again from `fleet token` |
| No lead answered | nothing changed; the same join string works again until it expires |
| The root does not match the fingerprint | **REFUSED**, nothing sent: another fleet's string, or something between the node and the lead that is not the lead |
| Token unknown, used or expired | ask for a new join string |
| The lead already has this node id (`node_exists`) | it holds a live certificate there (an earlier join whose answer was lost, for instance): on the lead, `smoking-pi fleet revoke NODE`, then a new join string |
| Two installations with this id (`duplicate_node`) | see [Two installations, one node id](#two-installations-one-node-id) |
| The certificate does not check out | **REFUSED**, nothing kept; the lead has probably recorded the node: `smoking-pi fleet revoke NODE` there, and ask for a new string |

### What a node reports

The agent sends a heartbeat every `next_heartbeat` seconds (the lead
chooses), over mutual TLS. Today it reports what it knows without any new
privilege, and leaves the rest absent rather than guessing:

| Field | Reported | From |
| --- | --- | --- |
| node and site names | yes | `FLEET_NODE_NAME`, `FLEET_SITE_NAME` |
| software version | yes | the release the command runs (`SMOKING_PI_VERSION`; `dev` from a source checkout) |
| edition | yes | `pro` (the only edition with Fleet) |
| profiles, capabilities | yes | `COMPOSE_PROFILES`; `influxdb` or `clickhouse` as capability |
| last doctor's counts | not yet | needs the doctor's results where the agent can read them |
| measuring (freshness) | not yet | needs config-manager's answer or the RRD files |
| uplink interface | not yet | needs the host's routing table |
| replication, seed | not yet | the data plane is not built |
| results of the last directives | yes | the agent |

### Directives

| Directive | What this release does |
| --- | --- |
| `revoked` | stops for good (below). Overrides the rest of the response. |
| `lead_endpoints` | replaces the lead list, kept in the agent's volume, if its epoch is higher; reported `done`, or `ignored` when not higher. |
| `renew_now` | renews the certificate at once: `done` or `failed`. Within 15 minutes of a failed renewal it answers `failed` without asking the lead again, so a lead that keeps asking cannot make the node generate a key on every heartbeat. |
| `repoint_store`, `start_seed` | keeps what it received (the store's token in its own 0600 file; nothing is rewritten when the lead repeats the same one) and reports `ignored`: the data plane is a later release, and the lead decides. |
| a type it does not know | `ignored`; one whose arguments fail validation, `invalid`; one without an id is dropped and logged. |

### Certificates

The node's certificate lives 90 days. From two thirds of its life on
(60 days) the agent renews it: a **new key**, made on the node, a request
over mTLS with the current certificate, and the answer checked like the
first one. The new key and certificate replace the old pair only once both
are written, each write and rename synced to the SD card with its directory
(a power cut mid-swap is finished at the next start, whichever rename the
card kept); until then the old pair stays in use, and a renewal that fails
is retried every 15 minutes, which leaves a month of margin.

### When the lead is away

Nothing on the node changes. The agent tries each lead it holds, in order
(the one that answered last first, then the list the lead gave, then any
`FLEET_LEAD_URLS` entry missing from that list: an operator's way in when
every lead moved), and when none answers it waits 30 s, doubling with
±20 % jitter up to 30 minutes, and tries again, for as long as it takes. It
logs a failure once (and once an hour while it lasts), not every attempt.

After 10 minutes (or three heartbeat intervals) without an answer, the
node's **link to the lead** is `failing` (`lead_link` in
`fleet status --json`): `fleet status` shows the failures, and the node
keeps measuring as a standalone one. The container's Docker health says
only whether the agent process is alive: a node whose lead is away, whose
certificate expired or that was revoked is measuring fine, and its
container stays `healthy` in `docker ps`.

**A certificate that expired while the lead was away** cannot be renewed
(renewal is authenticated with it). The agent stops sending and says so; the
way back is a new join: `smoking-pi fleet leave --force`, a new join string
from the lead, `smoking-pi fleet join`. The lead lets a node id whose
certificates all expired join again; it refuses (`node_exists`) only one
that still holds a live certificate, which `smoking-pi fleet revoke NODE` on
the lead releases.

### States that wait for a person

`smoking-pi fleet status` shows the agent's state; none of these makes the container unhealthy or the
agent exit or restart in a loop:

| State | Means | What to do |
| --- | --- | --- |
| `not_enrolled` | not in a fleet (idle) | `fleet join` |
| `enrolled` | heartbeats going (or retrying while the lead is away) | nothing |
| `stopped` | the lead answered something the protocol says to stop on (`unauthenticated`, `duplicate_node`, `bad_request`…); the agent tries again after 6 hours or when it restarts | look at the lead |
| `revoked` | the lead revoked the node; nothing is sent again, not even after a restart | `fleet leave` |
| `cert_expired` | above | `fleet leave --force`, then a new join |
| `state_corrupt` | the enrollment or its credentials cannot be used | `fleet leave --force`, then a new join |
| `identity_mismatch` | the agent's volume holds another node id's enrollment | [Two installations, one node id](#two-installations-one-node-id) |

### Leaving a fleet

```bash
smoking-pi fleet leave
```

The command stops the agent, tells the lead (`/fleet/leave`, over mTLS),
removes the key, certificate, root and enrollment from the agent's volume,
turns the agent off and clears `FLEET_LEAD_URLS` (kept on a host that is
also the lead). If no lead answers, nothing is removed and the agent goes
back to work; `--force` leaves anyway, and the lead keeps listing the node
as active until its operator revokes it (`smoking-pi fleet revoke NODE`;
revoked and left nodes stay in `fleet nodes`). A revoked node leaves without asking the
lead. Leaving never touches the measurements or their history, and the node
id stays: it belongs to the installation.

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

### On the lead

- **TLS.** The lead serves HTTPS with its own certificate, issued by its
  intermediate for the hosts of `FLEET_LEAD_URLS` and sent with the
  intermediate; it re-issues it, with a new key, 30 days before it expires
  or when the URLs change. Client certificates are verified in the
  handshake against the **root**, so a certificate from any of the fleet's
  intermediates is accepted; enrollment is the one route that needs none.
- **Every request is looked up.** On heartbeat, renew and leave the lead
  finds the client certificate by its SHA-256 in the registry: one it did
  not issue, one for another node id than the request's, an expired one or
  a revoked one is refused (`unauthenticated`, or for a revoked node a
  `revoked` directive / error). Revoking a node revokes every certificate it
  ever held, so a node that joins again does not bring its old ones back.
- **Join tokens** are 256 random bits, stored as SHA-256 only, single-use,
  and consumed in the same transaction that issues the certificate. A
  request with a valid token and a bad certificate request is refused
  without spending the token. The token is always looked up first, so **a
  valid token always enrolls**, whatever else came from anywhere. Only
  invalid ones are throttled: from the fifth invalid token one address sent
  within 15 minutes, its invalid tokens are answered `rate_limited` (with
  `retry_after`) instead of `token_invalid`, and the lead logs one line
  saying so. Nothing locks enrollment out: behind Docker's port publishing
  every client may appear as one address, and a lockout would let anyone
  who can reach the port keep real nodes out. The tokens' size is what
  makes guessing hopeless.
- **Certificate requests** must be P-256, with a signature that verifies
  (proof the node holds the key), and must not name another node (a CN
  `node-…` or a node URN for a different id). The lead writes the subject
  itself. A renewal must be for a new key.
- **Errors** are the protocol's `ErrorResponse`: a fixed sentence per code
  and an `error_id`; what went wrong is in the lead's log under that id,
  never in the answer.

### The root key

`fleet init` leaves the root key on the lead because the lead needs it once
more only when another lead joins or the role moves. Taking it offline
removes the key that could mint a whole new set of leads from the one
machine that is online:

```bash
smoking-pi fleet ca offline-root /media/usb/fleet-root-key.pem
```

The command refuses a RAM-backed place (`/tmp`, `/dev/shm`, `/run`, any
tmpfs), writes the key to the file (mode 0600), flushes the file and its
directory to the disk, has the lead check that the copy is its root key, and
only then deletes it from the lead. The serving lead never loads the root
key, so once the file is gone no process on the lead holds it, not even in
memory. Keep the file offline (a USB stick in a drawer, a password
manager): it is the only copy. The lead keeps working without it: its
intermediate signs every node certificate. `smoking-pi fleet ca
restore-root FILE` puts it back, after checking it is the same key; a file
that is not the root key changes nothing either way.

**A second lead, or moving the role** (not built yet; the layout is ready
for it). The procedure will be: the new lead creates its own intermediate
key and a request for it; whoever holds the root key signs it with the root
(`fleet/pki.py` has both steps, `intermediate_csr` and `sign_intermediate`,
tested); the new lead installs the root and its intermediate, takes a copy
of the registry, and nodes find it through the lead list (a new epoch).
Nodes keep trusting the same root, their certificates from the old lead
stay valid until they renew, and the new lead asks them to renew from its
own intermediate (`renew_now` for a certificate another intermediate
issued). The commands for this (`fleet ca sign-lead`, `fleet promote`) do
not exist yet.

### Where the secrets live

| Secret | Where | Never in |
| --- | --- | --- |
| Join token | nowhere on the node: read by `fleet join` from the terminal or stdin (never an argument), passed on stdin to the enrollment, sent once; the lead keeps its SHA-256 only | arguments, the environment, the env file, logs, `repr`, status |
| Node private key | `fleet-agent-state`: `node/key.pem`, mode 0600 | any message |
| Store write token | `fleet-agent-state`: `node/store-token`, 0600; and InfluxDB's remote | logs, status, heartbeat |
| Root key | the lead that ran the fleet's creation, `lead/ca/root-key.pem`, 0600, until `fleet ca offline-root` moves it to a file you keep | any message |
| Intermediate key | `fleet-lead-state`: `lead/ca/intermediate-key.pem`, 0600 | any message |
| Lead TLS key | `fleet-lead-state`: `lead/tls/server-key.pem`, 0600 | any message |
| Join tokens | the lead's registry (`lead/registry.db`, 0600), as SHA-256 only | logs, status |

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
| `FLEET_LEAD_URLS` | empty | The leads' https base URLs, comma-separated, in order. On a node, `fleet join` writes it and `fleet leave` clears it; once joined, the agent follows the list the lead gives and tries these after it. On the lead: where nodes reach it (`fleet init --url`; `fleet leave` keeps it there); its certificate names these hosts. |
| `FLEET_LEAD_MODE` | `node` | On the lead: `node` (it measures too) or `dedicated`. |
| `FLEET_LEAD_PORT` | `8443` | On the lead: the host port nodes connect to. |

## What is not built yet

Stated plainly, so nothing here is mistaken for a feature:

- **On the node**: reporting the doctor's counts, measurement freshness
  and the uplink interface in heartbeats.
- **Moving the lead role and a second lead**: the commands to sign another
  lead's intermediate with the root and to hand the registry over
  (`fleet ca sign-lead`, `fleet promote`). The CA layout and the signing
  step are built and tested ([The root key](#the-root-key)).
- **Renewing the intermediate**: it lasts five years, and there is no
  command to renew it, nor to replace one that expired; with the root key
  offline that will mean restoring the root key first.
- **Rotating the root** (it lasts twenty years): no command, and nodes pin
  it, so a new root means every node joins again.
- **Resolving a `conflict` node** other than by revoking it, and deleting
  nodes from the registry (revoked and left nodes stay listed).
- **The data plane**: the lead's store, per-node buckets and tokens,
  replication, the history seed, and showing the nodes' data on the lead.
  Until then enrollment answers `store: null` ("no store yet", as the
  protocol defines), and no heartbeat directive concerns replication.
- **Election** (topology 3).
