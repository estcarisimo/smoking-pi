# DNS observer

Which names does this house actually resolve? That is the first step toward
measuring the services that matter here, not a generic list of sites. The DNS
observer answers it from the house's own DNS queries. Nobody's traffic is
sniffed or intercepted: the router is configured, on purpose, to forward DNS
to the Pi.

It runs as one container with two pieces:

- **[AdGuard Home](https://github.com/AdguardTeam/AdGuardHome)** (v0.107.79,
  filtering off) is the DNS server. It answers on port 53, forwards to
  encrypted upstreams (DNS over HTTPS by default), and keeps the query log.
  We reuse it rather than writing a DNS server. It already has what the
  design needs: DoH, DoT, DoQ and DoH3 upstreams, a plain-DNS fallback,
  serve-stale caching, and a query log with the upstream, the latency, the
  response code and whether the answer came from the cache.
- **The supervisor** is ours. It writes AdGuard's settings, restarts AdGuard
  when it stops answering, and says whether observations are arriving. When
  they are not, it says why (see
  [When the observer is not getting information](#when-the-observer-is-not-getting-information)).

```
LAN devices ──DNS──▶ router ──forwards──▶ Pi :53 (AdGuard Home) ──DoH──▶ 1.1.1.1 / 8.8.8.8
                                               ▲                  └─plain DNS if DoH fails
                     canary every 5 min ───────┘ (supervisor → router → Pi)
```

Pro edition, opt-in (`dns` profile). It is off unless you enable it, and
until the router points at it, it changes nothing about the house's DNS.

## Setup guide

About 20 minutes, most of it waiting for the check in step 6. You need the
Pro edition running and the router's admin app or page. Keep a phone with
mobile data at hand: if something goes wrong, it is how you reach the
router's settings to undo step 5, if your router is managed from an app.

The model is **router → Pi → encrypted upstream**. Your devices keep asking
the router for DNS, as they do today, and only the router's own upstream
changes. That is one setting, in one place, and one tap undoes it. This
guide never changes the DNS server your devices get by DHCP, and never turns
on AdGuard's DHCP server.

### 1. Give the Pi a fixed address

The router is about to send the whole house's DNS to one address. If the
Pi's DHCP lease then hands it a new address, the router forwards to nothing.
In the router, reserve the Pi's current address; `hostname -I` on the Pi
shows it. Routers call this "DHCP reservation", "static lease" or "address
reservation", and put it next to the DHCP settings.

### 2. Start the observer

```bash
smoking-pi dns enable
```

This command:

- refuses if something else already listens on port 53 here, such as a
  Pi-hole or dnsmasq, and changes nothing in that case;
- generates `DNS_ADMIN_PASSWORD` if the install predates it;
- adds `dns` to `COMPOSE_PROFILES` and starts `dns-observer`, and nothing
  else;
- prints the address to give the router.

`smoking-pi dns status` now reads `starting` and then `not_receiving`. That
is correct: nothing sends queries here yet.

### 3. Check that the Pi answers, before touching the router

From a laptop on the same network, ask the Pi directly. Use the Pi's
address from step 1; `192.168.1.10` here is only an example:

```bash
dig @192.168.1.10 example.com
```

`status: NOERROR` and an address in the ANSWER SECTION mean the Pi
resolves. A timeout means the router
must not point at it yet; see
[When the observer is not getting information](#when-the-observer-is-not-getting-information)
and `smoking-pi logs dns-observer`.

### 4. Decide on a secondary

| | Value | Why |
|---|---|---|
| Primary | the Pi's address | Queries reach the observer |
| Secondary | `1.1.1.1` (or your ISP's resolver) | **The house stays online if the Pi is down** |

Many routers spread queries across primary and secondary, or switch to the
secondary after one slow answer. The observer then sees only part of the
house's DNS, and its state says so (`partial`). Without a secondary it sees
everything, but when the Pi is off the house has no DNS until the router
gives up on it, which some routers never do. We recommend keeping the
secondary: an incomplete record beats a house without DNS.

### 5. Point the router at the Pi

Find the setting for **the DNS servers the router itself uses**. It is
often called "custom DNS", "DNS server" under the WAN or Internet settings,
or "upstream" or "forwarders". Enter the primary and secondary from step 4
and save.

This works on routers that answer the house's DNS themselves (a DNS proxy
or forwarder), which is what most home routers do: their devices get the
router's own address as DNS server, and `dig example.com` on a laptop shows
the router's address on its `SERVER:` line. If yours names another address, the router
is handing devices a resolver directly. Changing that is the DHCP-direct
mode, which this guide does not cover.

**IPv6.** Routers with IPv6 often have a second pair of DNS fields for it.
With IPv6 on, the router may send some queries to those servers, which
bypass the Pi, and the state shows `partial`. The observer listens on IPv4
only by default. Pointing the IPv6 fields at the Pi takes
`DNS_BIND=0.0.0.0,::` and a Pi IPv6 address that does not change. Most ISP
prefixes do change, so the simplest choice is to leave the IPv6 fields as
they are.

### 6. Confirm it took effect

```bash
smoking-pi dns status
```

It reads `observing` as soon as the first query arrives, usually within
seconds. The canary line shows one seen within about 5 minutes. Only the canary proves the router forwards here: the observer asks the
router for a unique name every 5 minutes, and seeing that name arrive is the
evidence. The busiest names appear under the status as the house uses the
network.

If it reads `not_receiving` after 20 minutes, the router is not forwarding
to the Pi. Check the setting saved, and that the address matches step 1.

### Undo

Change the router's DNS back to automatic **first**, then:

```bash
smoking-pi dns disable
```

In the other order, a router that forwards only to the Pi leaves the house
without DNS for as long as the observer is gone.

## When the observer is not getting information

Observations stop for three reasons, and all three look the same from
inside the Pi: no queries. Each has its own evidence and its own fallback.

**The container, or Docker, is down.** Nothing on the Pi answers.

- *The house*: the router's secondary DNS answers. Without one, the house
  has no DNS.
- *Recovery*:
  - `restart: unless-stopped` restarts the container;
  - the systemd unit brings the stack back when Docker starts;
  - inside the container, the supervisor restarts AdGuard if it exits, and
    kills and restarts it if it stops answering its self-test (every 10 s,
    3 misses, about 30 s).

  The self-test is an `ANY` query, which AdGuard refuses locally without
  asking an upstream. A test that went upstream would fail during an
  internet outage and get a working server killed.
- *Detection*: `status.json` carries a heartbeat. A reader that finds it
  older than 90 s reports `down`, with `observed_until` (the last real query),
  so the gap in the data has a start. A stop on purpose (`docker stop`,
  `compose down`, an upgrade) is written at once as `stopped`.

**Nobody sends queries here: the router was never set, or it reverted.**
Routers lose custom DNS settings on firmware updates, factory resets and
ISP-pushed configurations.

- *The problem*: a quiet house at 4 a.m. and a router that went back to its
  ISP's DNS look identical by query count alone.
- *The canary*: every `DNS_CANARY_INTERVAL` (5 min), the supervisor asks
  **the router** for a unique name, `<random>.canary.smoking-pi.invalid`. If
  the router forwards to the Pi, the name shows up in the query log here.
  - Canaries arriving and no queries means `quiet`, a quiet house.
  - `DNS_CANARY_MISSES` (3) canaries in a row not arriving, and no queries,
    means `not_receiving`: the router does not send here.
  - Canaries missing while queries arrive means `partial`: the router splits
    between the Pi and its secondary.

  The name is under `.invalid`, which by RFC 6761 exists nowhere. A canary
  that leaks to a public resolver, after the router reverted, is answered
  NXDOMAIN and reaches nobody's authoritative server.
- *With the canary off* (`DNS_CANARY_VIA=off`), silence reads as `idle`: "I
  cannot tell". It never reads as `quiet`.

**Queries arrive but the upstreams fail.** AdGuard answers through
`fallback_dns` (plain DNS to `1.1.1.1` and `8.8.8.8`; state
`upstream_fallback`). If that fails too, recently asked names come from the
stale cache for up to 12 h, and new names fail (state `upstream_failing`).

### States

| State | Live? | Meaning | What to do |
|---|---|---|---|
| `observing` | yes | Queries are arriving | — |
| `quiet` | yes | No queries for `DNS_QUIET_AFTER` (30 min), but the canary still arrives | — |
| `partial` | yes | Queries arrive, canaries do not: the router splits across a secondary | Expected with a secondary DNS |
| `upstream_fallback` | yes | DoH failing; answering over plain DNS | Check the Pi's internet |
| `upstream_failing` | yes | Upstreams failing; stale cache or SERVFAIL | Check the Pi's internet |
| `idle` | no | Silence, canary off: cannot tell why | Turn the canary on |
| `not_receiving` | no | Canaries not forwarded and no queries; or never any query | Check the router's DNS setting |
| `server_down` | no | AdGuard not answering; being restarted | `smoking-pi logs dns-observer` if it persists |
| `starting` | no | Just started | Wait for the first canary |
| `stopped` | no | Stopped on purpose | `smoking-pi up` |
| `down` | no | No heartbeat (container or Docker down) | `smoking-pi up`; `smoking-pi logs dns-observer` |

**For whatever uses the observations** (target recommendations, next):
`status.json`'s `live` says whether the data is current. When it is not,
fall back to the last known ranking (still in the query log, marked stale
since `observed_until`), then to the curated default targets. Never read
silence as "nothing matters in this house".

## What it measures, and what it does not

It records **DNS activity observed at the Pi**, not traffic. That
`foo.example` was resolved at 10:02 does not mean an app transferred
anything from it. Several things break that inference:

- caches in the router, the OS and the app;
- prefetching and speculative resolution;
- CDNs and shared endpoints behind CNAMEs;
- many apps behind one name;
- SVCB/HTTPS records.

**Coverage is partial, by design.** Apps and systems that bring their own
encrypted DNS never ask the router, so they bypass the observer entirely.
Examples:

- browsers with DoH to a provider;
- Android Private DNS;
- iCloud Private Relay;
- VPNs;
- devices with a hard-coded `8.8.8.8`.

The observer does not block port 853 or known DoH endpoints to force them
through. Status says "network DNS only", and that is what it is.

## Privacy

- Names stay on the Pi, in AdGuard's query log inside the `dns-observer-work`
  volume, for `DNS_RETENTION_DAYS` (7). They are never written to InfluxDB,
  sent to the AI reports or put in alerts.
- Client addresses are masked (`DNS_ANONYMIZE_CLIENTS=1`, AdGuard's
  `anonymize_client_ip`). In the router model every query comes from the
  router anyway.
- The admin UI and API listen on `127.0.0.1:3053` only. To look at the query
  log: `ssh -L 3053:localhost:3053 pi@<the Pi>`, then open
  http://localhost:3053 and sign in as `smokingpi` with `DNS_ADMIN_PASSWORD`
  (`smoking-pi passwords --show-secrets`).

## Safety rules the configuration enforces

The supervisor merges these into AdGuard's `AdGuardHome.yaml` on every start.
Change them in the env file, not in the UI; the UI's other settings are kept.

- **No loops.** Upstreams, fallback and bootstrap resolvers must be public
  addresses. The router forwards to the Pi, so the router as upstream sends
  every query around in a circle. AdGuard's reverse lookups of private
  addresses and its rDNS client names would also go to the router, so both
  are turned off. `DNS_ALLOW_PRIVATE_UPSTREAM=1` lifts the rule, for an
  internal resolver that does not forward back here.
- **No open resolver.** Only private, loopback, link-local, ULA and CGNAT
  (Tailscale) clients are answered. An empty `DNS_ALLOW_CLIENTS` means the
  default. It never means "everyone", which is what AdGuard would read into
  an empty list.
- **No rate limit.** Every query comes from the router's single address.
  AdGuard's default of 20 queries/s per /24 would throttle the whole house.
- **Filtering off** on first start (no block lists). If you turn blocking on
  in the UI, it stays on; that is your choice, not the observer's.

## Settings

All optional except the password; in the env file (`smoking-pi config set`).

| Key | Default | |
|---|---|---|
| `DNS_ADMIN_PASSWORD` | generated | AdGuard admin UI password; re-hashed on each start, so it can be rotated |
| `DNS_UPSTREAMS` | `https://1.1.1.1/dns-query,https://8.8.8.8/dns-query` | Any AdGuard upstream spec (`tls://`, `quic://`, `h3://`) |
| `DNS_FALLBACK` | `1.1.1.1,8.8.8.8` | Plain DNS when every upstream fails; `none` for no fallback |
| `DNS_BOOTSTRAP` | `1.1.1.1,8.8.8.8` | Resolves upstream hostnames; never the router |
| `DNS_UPSTREAM_TIMEOUT_MS` | `2000` | Per upstream, before the next one |
| `DNS_ALLOW_CLIENTS` | private ranges | Comma-separated CIDRs answered |
| `DNS_BIND` / `DNS_PORT` | `0.0.0.0` / `53` | Where it listens |
| `DNS_RETENTION_DAYS` | `7` | Query log and statistics |
| `DNS_ANONYMIZE_CLIENTS` | `1` | Mask client addresses |
| `DNS_CANARY_VIA` | `auto` | Where the canary is asked: `auto` (default gateway), `off`, or an address |
| `DNS_CANARY_DOMAIN` | `canary.smoking-pi.invalid` | Change it if the router answers `.invalid` itself |
| `DNS_CANARY_INTERVAL` / `DNS_CANARY_MISSES` | `300` / `3` | ~15 min to `not_receiving` |
| `DNS_QUIET_AFTER` | `1800` | Silence before `quiet`/`idle` |
| `DNS_ADMIN_ADDRESS` | `127.0.0.1:3053` | Admin UI/API listen address |

## Turning it off

See [Undo](#undo): point the router back first, then `smoking-pi dns
disable`. The query log stays in the volumes until `smoking-pi purge`.
