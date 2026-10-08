# DNS resolver probes

How long a public resolver takes to answer one query, and how often it does
not answer at all. SmokePing's `DNS` probe sends the query with `dig` to the
resolver's address and records the time to the answer. This is the Pi
asking a resolver directly; it is not the house's own lookups (that is the
[DNS observer](dns-observer.md)) and not which resolver the house's queries
actually reach on the Internet (that is
[Which resolver answers](public-resolver.md)).

## What is measured

The install seeds three resolvers, in the `dns_resolvers` category:

| Target | Resolver | Name looked up |
| --- | --- | --- |
| `GoogleDNS` | 8.8.8.8 | `google.com` |
| `CloudflareDNS` | 1.1.1.1 | `cloudflare.com` |
| `Quad9DNS` | 9.9.9.9 | `quad9.net` |

The probe (`DNS` in `probes.yaml`) runs `/usr/bin/dig` and sends **5
queries per target every 300 seconds**: each query is one sample, and a
query that gets no answer counts as lost. The name looked up is the
target's `lookup`; it stays the same from cycle to cycle, so after the
first query the resolver usually answers from its cache: the time is
mostly the path to the resolver, not a full recursive resolution.

On a Pi the probe's timer is the kernel tick (4 ms on a 250 Hz kernel), so
resolution times sit on a few fixed steps; a change smaller than one tick
does not show.

To measure another resolver, add it in the web admin (*Targets → Add*) with
the probe **DNS**, the category **DNS Resolvers** and a name to look up.
On Pro, the web admin's *Your connection* card suggests the resolvers
your own network hands out (see [Getting started](getting-started.md), step 6).

## Where the data lands

SmokePing writes these targets under the `DNS_Resolvers` section, so their
RRDs are in `DNS_Resolvers/` (for example `DNS_Resolvers/GoogleDNS.rrd`).
Both exporters classify by that top-level directory: the section name is
what `config_generator.CATEGORY_PRESENTATION` emits, and renaming one means
renaming the other. RRDs under the older directory name `resolvers/` still
export the same way, so old trees keep their history.

In InfluxDB:

| | |
| --- | --- |
| measurement | `dns_latency` |
| tags | `target` (`GoogleDNS`…), `category` = `dns`, `probe_type` = `dns` |
| `ping1`..`ping5`, `median` | resolution time, **seconds** |
| `loss` | **0–1 ratio**: the RRD's count of lost queries divided by `pings` |
| `pings`, `step` | queries per cycle (5) and the step in seconds (300) |
| `uptime` | SmokePing's own data source, written only when the RRD holds a value |

Dashboards multiply the times by 1000 to show milliseconds. In ClickHouse
the rows carry `measurement_type` = `dns_latency` and `category` = `dns`,
and loss is `packet_loss` in percent (0–100); see
[ClickHouse backend](clickhouse.md). The `category` tag is `dns`, not the
directory name and not the database's `dns_resolvers`: the two
vocabularies are mapped in `common/links.py`, and both have consumers.

## Dashboards

- **DNS Resolvers – Side-by-Side (Resolution Time & Packet Loss)**, in the
  `dns-resolution-times` folder: one row per resolver, repeated over the
  `target` variable (every target with `dns_latency` data in the last 24
  hours). The left panel is the resolution time in milliseconds — median,
  mean, p10/p20/p80/p90, min and max of the five samples — and the right
  one the share of queries lost.
- **SmokePing DNS Resolvers – Resolution Time & Loss (Percentiles /
  Mean)**, in the `overview` folder: the same data, one resolver at a time.
- [Target Detail](target-detail.md) shows a resolver's DNS time next to the
  other layers measured for the same target.

## When the dashboards are empty

1. **Is SmokePing measuring?** The web admin's **Measurements** card (or
   `GET /measurements` on the config API) lists every target that has
   stopped updating or never wrote data. A resolver added a moment ago is
   *waiting* until its first 300-second step.
2. **Does the instrumentation agree with itself?**

    ```bash
    smoking-pi doctor --live
    ```

    It checks, among other things, that every panel queries a measurement
    and tags the exporters actually write, and that the running containers
    run the code in the repository.

3. **What does SmokePing say?**

    ```bash
    smoking-pi logs smokeping
    ```

    The InfluxDB exporter runs in the same container and logs each cycle
    (`Cycle complete: N points exported`); a container that keeps
    restarting, or a probe error, shows here too.

4. **Data arrives but every InfluxDB panel is empty.** Grafana's InfluxDB
   token and InfluxDB have diverged. A whole-stack restart checks the token
   and repairs it when it is the cause:

    ```bash
    smoking-pi restart
    ```

A resolver missing from the dashboard's dropdown has written no
`dns_latency` point in the last 24 hours: check it on the Measurements
card first. A time range shorter than one step (5 minutes) can also show
nothing.

## Reading the numbers

- **Loss on one resolver only** is that resolver or the path to it; loss on
  all three at once is the connection, and the ICMP targets will show it
  too.
