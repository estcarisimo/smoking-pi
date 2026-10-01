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
CurlHTTP1      Curl           3   300     5        180      51.8
CurlHTTP2      Curl           3   300     5        180      51.8
CurlHTTP3      Curl           3   300     5        180      51.8
FPing          FPing          6   300    10        720       2.9
DNS            DNS            3   300     5        180       1.3
TCPPing        TCPPing        3   300     5        180       0.9

21 targets: 1620 samples/h of 20000 (8.1%), ~161 MB/day of 1000 (16.1%).
Approximate: SmokePing measurements only, from the generated config.
```

Most expensive first: what to cut is at the top. `--json` prints the
same report as JSON, which is also what the API returns:
`GET /budget` on config-manager (token-protected like the rest of it).

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

The defaults are deliberately generous: the seed uses 16% of the bandwidth
and 8% of the samples. A DNS wizard adoption of 60 services over HTTP/1.1,
2 and 3 alone is about 3 GB a day, and shows as over budget.

## What it does not count yet

Measurements outside SmokePing, which do not grow with the target list:

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

1. The budget on the landing page and in Grafana: usage against a dashed
   ceiling line, broken down by probe type.
2. Admission: when the requested set exceeds the budget, reduce cadence,
   samples or targets, with deterministic rotation so the whole set is
   eventually covered, and report requested vs admitted vs deferred.
3. Per-destination limits (service, prefix, ASN) so a large discovered
   target set does not turn into concentrated probing of one operator.
4. The DNS wizard and future traceroute measurements asking this budget
   instead of keeping limits of their own.
