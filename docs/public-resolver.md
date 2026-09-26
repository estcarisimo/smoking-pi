# Which resolver answers

The DNS server your devices use is usually the router, but the router rarely
resolves anything itself. It forwards each query to someone: the ISP, Google,
Cloudflare, or the Pi's own [DNS observer](dns-observer.md). That resolver's
**public address** is what every website's DNS server sees. CDNs choose which
of their servers to send you to from it, and from the *client subnet* it may
pass on (EDNS Client Subnet, ECS). So a change of resolver can move every
CDN-backed measurement at the same moment, with nothing wrong on the line.

Smoking Pi (Pro) records it over time.

## What is measured

Every `RESOLVER_INTERVAL` (15 minutes), from the SmokePing container, for
each path:

| Path | Asked through | Shows |
|---|---|---|
| `router` | the default gateway (`RESOLVER_VIA`) | what the house uses today |
| `observer` | the DNS observer on this host, when it runs | which upstream AdGuard Home really reaches |

A few public names answer with the address of whoever asked them:

- `whoami.akamai.net` (A record): the resolver's egress address, as Akamai
  sees it;
- `o-o.myaddr.l.google.com` (TXT): the same from Google, plus
  `edns0-client-subnet <prefix>` when the resolver passed one on.

Big resolvers are anycast pools, so the egress address changes from query to
query. Three queries through one router gave three different Google
addresses. What stays the same is who owns them. So every address's owner,
an ASN and its registry name (for example `AS15169 GOOGLE - Google LLC, US`),
is looked up from [Team Cymru](https://www.team-cymru.com/ip-asn-mapping)'s
DNS service. That lookup goes to `1.1.1.1` directly, never through the path
being measured.

Some resolvers spread queries across providers. The DNS observer, with its
default upstreams `1.1.1.1` and `8.8.8.8`, shows Cloudflare *and* Google. So
every owner seen is kept, not only the most frequent one.

## Where it shows

- **The web admin's dashboard**, on the *Your connection* card: "DNS through
  your router is answered by Google LLC (AS15169), which passes your subnet
  … on to websites", with the addresses seen.
- **Grafana**: a *Resolver changed* annotation on the same dashboards as
  *Uplink changed*.
- **The daily digest**: a line under *Local link* on a day the resolver
  changed hands.
- **The assistant**: `system_status` has a `resolver` block with the owner
  and client subnet per path, and the last change within a week.
- **InfluxDB**: measurement `dns_resolver`, tag `path`. Fields:
  - `owner`: the owners, joined with ` + ` when there are several;
  - `asn`: the owner of most addresses;
  - `egress`: the addresses seen;
  - `ecs`: the client subnet passed on;
  - `via`: the server asked;
  - `ok`: 0 when the path gave no answer;
  - `previous`: only on a change point.

## What counts as a change

A change is marked when the owners seen **share none** with the owners seen
since the last change: Google, then Quad9, for example. A varying mix of the
same providers is not a change. A pool seen as Cloudflare and Google in one
cycle and only Cloudflare in the next is the same resolver, and marking it
would put a false change on every other cycle.

The price: a resolver that *adds* a provider is not marked. Pointing the
router at the DNS observer does exactly that when Google was the router's
resolver before, since the observer also uses Google. The `owner` field
still shows the new mix. The observer's own canary is what proves that the
router forwards to it.

A path that did not answer is written with `ok=0` and never counts as a
change. The DNS observer path is skipped entirely while nothing listens on
the Pi's port 53.

## Settings

In the env file (`smoking-pi config set`):

| Key | Default | |
|---|---|---|
| `RESOLVER_INTERVAL` | `900` | Seconds between probes (60 at least) |
| `RESOLVER_VIA` | `auto` | Where the `router` path asks: `auto` (the default gateway), `off`, or an address |
| `RESOLVER_OBSERVER` | `auto` | `off` skips the DNS observer path |

Each cycle sends four small queries per path: three for
`whoami.akamai.net` and one for Google's name. On top of those come Team
Cymru lookups for any egress address or ASN not seen in the last day. Those servers learn the resolver's address, which
they see on every lookup anyway. They learn nothing about your devices.
