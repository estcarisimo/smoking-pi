# Measurement budget

Every target you add costs something: packets on your uplink, load on the
server at the other end, CPU on the Pi. One target is nothing. The DNS
wizard adopting 60 services over ICMP, TCP and three HTTP versions is not
nothing. The measurement budget makes that cost visible before it is a
surprise on a metered connection.

This first version is **accounting, not admission**. It reports what the
configured measurements cost against two ceilings. It does not throttle,
defer or drop anything; that comes later, once the numbers have earned
trust (see [What comes next](#what-comes-next)).

## The command

```bash
sudo smoking-pi budget
```

Standard and Pro (it reads config-manager's generated SmokePing config;
Basic has no config-manager). On the shipped seed:

```
probe          class    targets  step pings  samples/h    MB/day
CurlHTTP1      Curl           3   300     3        108      31.1
CurlHTTP2      Curl           3   300     3        108      31.1
CurlHTTP3      Curl           3   300     3        108      31.1
FPing          FPing          6   300    10        720       2.9
DNS            DNS            3   300     5        180       1.3
TCPPing        TCPPing        3   300     5        180       0.9

21 targets: 1404 samples/h of 20000 (7.0%), ~98 MB/day of 1000 (9.8%).
Approximate: SmokePing measurements only, from the generated config.
```

Most expensive first: what to cut is at the top. `--json` prints the
same report as JSON, which is also what the API returns:
`GET /budget` on config-manager (token-protected like the rest of it).

## On the dashboard

The web admin's dashboard (Standard and Pro) shows the same report as the
*Measurement budget* card: how much of each ceiling is used, the headroom
left, and every probe, most expensive first. The bar turns yellow at 75%
of a ceiling and red over it, and an over-budget card says what brings it
back. The *Bandwidth Usage* card at the top shows the daily total and its
average rate, and the Probes page shows each probe's MB/day, both from
this report.

![The Measurement budget card on the shipped seed](img/budget-card.png)

Both figures are the configured cost of what SmokePing runs, not metered
traffic. When config-manager cannot be asked, the card says so instead of
guessing.

## In Grafana

On Pro with InfluxDB, the Overview dashboard (Grafana's home page) has a
*Measurement budget* row. It shows MB/day and samples per hour per probe,
stacked, with the ceiling as a dashed line, plus how much of each ceiling
is used now. A step in the stack is a change in targets or cadence, such
as a DNS wizard adoption, a new target or a shorter step. That is how you
see when the cost moved and why.

![The Measurement budget row on a staging Pi after a DNS wizard adoption](img/budget-overview.png)

The series comes from `measurement_budget.py` in the SmokePing container.
Every five minutes it reads config-manager's `/budget` (the same report as
the command and the card) and writes `measurement_budget` (the totals and
the ceilings) and `measurement_budget_probe` (one point per probe, tagged
`probe` and `probe_class`). A probe with no measured cost has no
`mb_per_day` field rather than a zero. ClickHouse installs have no
Overview dashboard and get the web admin card only.

## What it counts

What SmokePing runs, read from the same files SmokePing loads: the
generated `Targets` and `Probes`, the CPE targets `cpe_discovery.py`
writes, and the `Database` defaults. A probe's samples per hour are
`targets × pings × 3600 / step`. Disabled targets and IPv6 targets gated
out by [IPv6 gating](ipv6-gating.md) are not in the generated file, so
they cost nothing and are not counted.

The bytes are per sample, both directions, IP headers included, and were
measured rather than derived:

| class | bytes per sample | what one sample is on the wire |
| --- | --- | --- |
| `FPing` | 168 | an echo request and its reply (fping's default 56 data bytes) |
| `FPing6` | 208 | the same over IPv6 |
| `DNS` | 300 | one lookup: ~80 bytes up, 80–165 down with dig's EDNS cookie |
| `TCPPing` | 200 | SYN, SYN/ACK, and the kernel's RST |
| `Curl` | 12,000 | one HEAD over TLS 1.3: the certificate chain and the response headers (8–12 KB across eight popular sites) |

A probe of a class with no measured cost (a probe you added yourself) is
counted in samples and listed as unpriced, so the MB/day figure is not
read as complete.

Nearly all of the seed's bandwidth is TLS handshakes: an HTTP sample costs
70 times an ICMP one. That is why the HTTP probes send `HEAD`. Before
they did, each sample downloaded the whole home page, and the same seed
cost about 6 GB a day ([HTTP probes](http-probes.md#what-the-curl-sample-measures)).

## The ceilings

| setting | default | |
| --- | --- | --- |
| `MEASUREMENT_BUDGET_MB_PER_DAY` | 1000 | about 30 GB a month |
| `MEASUREMENT_BUDGET_SAMPLES_PER_HOUR` | 20000 | |

Both are in `.env.template`; set them with
`sudo smoking-pi config set MEASUREMENT_BUDGET_MB_PER_DAY 500`. An empty,
malformed or non-positive value falls back to the default: a typo does not
switch the check off. Over either ceiling, the report says so and what
brings it back: fewer targets, fewer pings, or a longer step
([Measurement frequency](measurement-frequency.md)).

The defaults are deliberately generous: the seed uses 10% of the bandwidth
and 7% of the samples. A DNS wizard adoption of 60 services over HTTP/1.1,
2 and 3 alone is about 1.9 GB a day, and shows as over budget.

## Measured traffic

Everything above is an **estimate**: the configured probes times a cost per
sample measured once. Pro also runs a **meter**. Every five minutes the
`uplink_traffic` exporter reads the kernel's byte counters
(`/proc/net/dev`) for the interface the default route leaves through, the
same uplink the Wi-Fi panels use, and keeps the last 24 hours.

- `smoking-pi budget` adds a line:
  `Measured on wlan0: ~1450 MB/day (1200 MB in, 250 MB out over the last 24.0 h), ...`
- The dashboard's budget card shows it as a bar against the same ceiling,
  and the top *Bandwidth Usage* card adds it under the estimate.
- Grafana's Overview draws it as the solid line on *Traffic against the
  budget (MB/day)*, over the stacked estimate and under the dashed
  ceiling. The series is `uplink_traffic` (tag `interface`; fields
  `rx_bytes`, `tx_bytes`, `seconds`, `mb_per_day`).

![The budget card with the measured line: about 410 MB/day estimated, about 813 MB/day measured on wlan0](img/budget-card-measured.png)

*A test Pi with the DNS wizard's HTTP targets: the estimate says 410
MB/day, and the meter (here only a minute old) says about twice that.*

The meter counts **everything on that interface**, not only the
measurements. That is its point: the gap between the two lines is what
the Pi spends on other things. It includes:

- LAN traffic on the same link: the microcut detector's pings to the
  router (about 50 MB a day), you opening Grafana or the web admin, and
  the DNS observer answering the house when the router forwards DNS to it;
- the stack's own downloads: image pulls on an upgrade, `apt`;
- anything else on the host: an assistant, the Cloudflare tunnels, a
  VPN's encapsulated traffic.

`mb_per_day` scales the hours covered to a day, so a meter that started an
hour ago already reads as a daily figure; the card and the command say how
many hours it rests on. Under an hour it is shown but not judged: no
percentage of the ceiling and a gray bar, since one image pull in the
first five minutes would read as hundreds of times the budget. When the
uplink changed interface during the day, both are named.

When a VPN owns the default route (a Tailscale exit node, WireGuard),
the interface metered is the tunnel: the figure is the traffic inside it,
not what leaves the radio. An interval is dropped, not guessed, when a
counter went backwards (a reboot), the uplink changed interface, or the
exporter was stopped for more than 15 minutes. A meter that stopped writing
for 15 minutes is shown as stale. Basic and Standard have no meter.
Which service sends what is the next section.

## By service

The meter above says how much; Pro also says **who**. The `netmeter`
container counts the same uplink per service, exactly, with nftables
counters keyed on each container:

- **Host-network services** (SmokePing and its exporters, the DNS
  observer, the alerter, the MCP server, mdns) by the cgroup of the socket
  that sends or receives. A connection's first packet also stamps it (one
  byte of the conntrack mark, bits 24-30, clear of Tailscale's), so the
  replies, ICMP echo replies included, count for the same service.
- **Bridged containers** (Grafana, the web admin, ai-insights, InfluxDB,
  PostgreSQL, config-manager) by their address, where their traffic is
  forwarded to the uplink.
- **host**: everything else on the uplink that no container sent or
  received (apt, an assistant, sshd, Docker pulling an image).
- **other_containers**: forwarded traffic of containers this stack does
  not name, such as a tunnel started by hand with `docker run`.

`smoking-pi budget` lists them after the measured line, most traffic
first. The dashboard's budget card has a *Measured by service* table,
and Grafana's Overview has *Measured traffic by service (MB/day)*,
stacked. The series is `service_traffic` (tags `service` and `kind`:
`host_network`, `bridge` or `rest`; fields `rx_bytes`, `tx_bytes`,
`seconds`, `mb_per_day`).

On a test Pi, two minutes of it read:

```text
smokeping          host_network  in    925642 B  out    384017 B  ~   943.0 MB/day
host               rest          in      3898 B  out     13103 B  ~    12.2 MB/day
grafana            bridge        in      5310 B  out      2417 B  ~     5.6 MB/day
dns-observer       host_network  in      3923 B  out      3382 B  ~     5.3 MB/day
```

**What it changes on the host.** It adds one table, `inet
smoking_pi_meter`, with three chains (output, input, forward) at
priority -150. They hold counters and a conntrack mark and nothing else:
no rule drops, accepts, rejects or rewrites a packet, so whatever the
host's firewall (Docker's, Tailscale's, yours) decided before, it still
decides. Stopping the container removes the table. `NETMETER=off` in the
env file loads nothing. To look at it: `sudo nft list table inet
smoking_pi_meter`.

The container runs with `CAP_NET_ADMIN` and no other capability, on the
host network and in the host's cgroup namespace (nft resolves a cgroup
by its path), on a read-only filesystem. It asks config-manager which
container is which (`GET /meter/containers`) rather than holding the
Docker socket. A container that restarts gets a new cgroup, even with
the same container id (`docker restart`), and nft keys a rule on the
cgroup itself, not its path; the meter watches each cgroup's identity
and reloads the table within a minute, keeping what the counters held.
Each host-network service keeps its own mark number across reloads, so a
long-lived connection is never credited to another service.

Limits worth knowing:

- **The uplink is a physical interface** (one with a device: `wlan0`,
  `eth0`) unless `NETMETER_INTERFACES` names it. An uplink that is a
  bridge, a bond, a VLAN or PPPoE needs that setting; without it the
  meter says so in its status and counts nothing.
- **A VPN's encrypted packets** leaving the uplink (tailscaled, WireGuard)
  are counted as `host`, not as the service whose traffic they carry.
- **Inbound multicast** (mDNS questions to the mdns service) is not tied
  to a socket on the way in and is counted as `host`; what mdns sends is
  counted as mdns.
- **The conntrack mark bits 24-30 stay** on the connections that carried
  them until those connections end, even after the table is removed. A
  firewall of your own that copies the whole conntrack mark into the
  packet mark without a mask (some multi-WAN or policy-routing setups)
  would see them; Docker and Tailscale do not do this. `NETMETER=off`
  if yours does.

## What it does not count yet

The estimate leaves out measurements outside SmokePing, which do not grow
with the target list (the meter above sees them):

- **The CPE microcut detector** (Pro, InfluxDB): 50 pings to the router
  every 30 seconds, per address family, about 144,000 a day each, or
  ~24 MB/day over IPv4 and ~30 MB/day over IPv6. It never leaves the LAN,
  so it costs nothing on the uplink; it is the largest single source of
  packets on the box.
- **Resolver identity and the public address** (Pro, InfluxDB): a few
  DNS lookups every 15 minutes, well under 1 MB a day.
- **The DNS observer's canary**: one lookup through the router every five
  minutes, and a self-test against itself on the Pi.
- **On-demand checks**: the DNS wizard's pre-flight, `smoking-pi dns test`,
  the OCA refresh.

## What comes next

From the roadmap, in order:

1. Admission: when the requested set exceeds the budget, reduce cadence,
   samples or targets, with deterministic rotation so the whole set is
   eventually covered, and report requested vs admitted vs deferred.
2. Per-destination limits (service, prefix, ASN) so a large discovered
   target set does not turn into concentrated probing of one operator.
3. The DNS wizard and future traceroute measurements asking this budget
   instead of keeping limits of their own.
