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

About 10 minutes. You need the
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
- generates `DNS_ADMIN_PASSWORD` if the install predates it (the admin UI's
  password; `smoking-pi passwords --show-secrets` shows it);
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
resolves. `smoking-pi dns test` on the Pi checks the same thing from the Pi
side (its first three lines; the router lines fail until step 5, which is
expected here). A timeout means the router
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
and save. Then **leave the settings and open them again**: some router apps
and pages keep an edit on screen that was never applied, and the first sign
is the check in step 6 failing.

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

### 6. Test it

Right after saving the router setting, on the Pi:

```bash
smoking-pi dns test
```

It checks each link of the path in a few seconds, in order:

```text
OK    Pi DNS server answers: 127.0.0.1:53
OK    answers on the LAN: 192.168.1.10:53
OK    resolves through the upstreams: example.com in 16 ms
OK    router resolves: example.com via 192.168.1.1 in 8 ms
OK    router forwards to the Pi: 10/10 test names asked of 192.168.1.1 arrived here

The DNS path works: devices -> router -> Pi -> upstreams.
```

The last line is the one that matters. The Pi asks the router for ten
unique names that nobody else could ask, and counts how many arrive in its
own query log. A router that forwards here passes them all on; one that does
not, passes none. The command exits 1 when a check failed, so a script can
use it.

| Failing line | What it means | What to do |
|---|---|---|
| `Pi DNS server answers` | AdGuard is not answering on the Pi | `smoking-pi dns status`; `smoking-pi logs dns-observer` |
| `answers on the LAN` | It answers on loopback only: the router cannot reach it | `DNS_BIND` must include the LAN address or `0.0.0.0`; check the Pi's firewall allows port 53 |
| `resolves through the upstreams` | The Pi cannot reach its encrypted resolvers | `smoking-pi doctor`; check the Pi's internet and `DNS_UPSTREAMS` |
| `router resolves` | Devices asking the router get no answer right now | Fix the lines above; if the router points only at the Pi, set it back to automatic meanwhile |
| `router forwards`, **0/10** | The router is not using the Pi | Open the router's DNS setting again: it did not save, or it points at another address. Check the address matches step 1 |
| `router forwards`, 0/10, *answers itself* | The router answers the test names' suffix itself and never forwards it | `smoking-pi config set DNS_CANARY_DOMAIN <a name it forwards>`; the default under `home.arpa` works on most routers |
| `router forwards`, **some**/10 (warning) | The router also sends queries elsewhere: the secondary from step 4, or IPv6 DNS | Expected with a secondary; the status reads `partial` |

Two things it does not need: rebooting the router, and renewing the DHCP
leases of your devices. Devices keep asking the router, as they did before;
only the router's own upstream changed, and it applies at once.

If it ends with a note that the Pi's address comes from DHCP, make sure you
reserved it (step 1). The router cannot tell the Pi whether it did.

After that, `smoking-pi dns status` keeps checking on its own: it reads
`observing` as soon as the house's queries arrive, and its canary asks the
router for one unique name every 5 minutes, so a router that reverts its
setting later is caught within about 15 minutes. The busiest names appear
under the status as the house uses the network.

### Checking the path by hand

From a laptop on the same network, with `192.168.1.10` standing for the Pi
and `192.168.1.1` for the router:

| Command | Expected | It shows |
|---|---|---|
| `dig example.com` | `SERVER:` is the router's address | Devices ask the router, as the guide assumes |
| `dig @192.168.1.10 example.com` | `status: NOERROR`, an address | The Pi answers on the LAN |
| `dig @192.168.1.1 example.com` | `status: NOERROR`, an address | The router answers |
| `dig +short whoami.akamai.net` | Address(es) of the public resolver's egress | Which public resolver the house ends up at: with the router pointed at the Pi, those of the Pi's upstreams ([Which resolver answers](public-resolver.md)) |
| `dig test-$RANDOM.canary.smoking-pi.home.arpa` | `NXDOMAIN`, and the name in the Pi's query log (admin UI) within seconds | The router forwards to the Pi: a name nobody else asks, so not cached anywhere. The by-hand version of the test's last line |

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
  **the router** for a unique name, `<random>.canary.smoking-pi.home.arpa`. If
  the router forwards to the Pi, the name shows up in the query log here.
  - Canaries arriving and no queries means `quiet`, a quiet house.
  - `DNS_CANARY_MISSES` (3) canaries in a row not arriving, and no queries,
    means `not_receiving`: the router does not send here.
  - Canaries missing while queries arrive means `partial`: the router splits
    between the Pi and its secondary.

  The name is under `home.arpa`, which RFC 8375 reserves for home networks:
  it exists nowhere publicly. A canary that leaks to a public resolver,
  after the router reverted, is answered NXDOMAIN and reaches nobody's
  authoritative server. It is not under `.invalid` or `.test`: routers that
  follow RFC 6761 answer those themselves and never forward them, so a
  canary there could never arrive and the state would stay `partial` or
  `not_receiving` on a router that works. `smoking-pi dns test` says when a
  router does that with the name in use.
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
| `partial` | yes | Queries arrive, canaries do not: the router splits across a secondary, or answers the canary name itself | Expected with a secondary DNS; otherwise `smoking-pi dns test` |
| `upstream_fallback` | yes | DoH failing; answering over plain DNS | Check the Pi's internet |
| `upstream_failing` | yes | Upstreams failing; stale cache or SERVFAIL | Check the Pi's internet |
| `idle` | no | Silence, canary off: cannot tell why | Turn the canary on |
| `not_receiving` | no | Canaries not forwarded and no queries; or never any query | Check the router's DNS setting; `smoking-pi dns test` |
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
- The admin UI (AdGuard Home's own, with the query log and statistics)
  listens on `127.0.0.1:3053` by default, this machine only. Sign in as
  `smokingpi`; `smoking-pi passwords --show-secrets` shows the password and
  where to open it. Two ways in from another computer:
  - an SSH tunnel: `ssh -L 3053:localhost:3053 pi@192.168.1.10` (the Pi's
    address), then open http://localhost:3053;
  - open it to the network, like the web admin and Grafana:
    `smoking-pi config set DNS_ADMIN_ADDRESS 0.0.0.0:3053`, then
    http://192.168.1.10:3053. Anyone on the network who has the password
    then sees every name the house resolves. `smoking-pi config unset
    DNS_ADMIN_ADDRESS` closes it again.

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
| `DNS_CANARY_DOMAIN` | `canary.smoking-pi.home.arpa` | Change it if the router answers it itself (`smoking-pi dns test` says so) |
| `DNS_CANARY_INTERVAL` / `DNS_CANARY_MISSES` | `300` / `3` | ~15 min to `not_receiving` |
| `DNS_QUIET_AFTER` | `1800` | Silence before `quiet`/`idle` |
| `DNS_ADMIN_ADDRESS` | `127.0.0.1:3053` | Admin UI listen address; `0.0.0.0:3053` opens it to the network |

## Turning it off

See [Undo](#undo): point the router back first, then `smoking-pi dns
disable`. The query log stays in the volumes until `smoking-pi purge`.
