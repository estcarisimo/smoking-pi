# Targets by service

The **Targets by service** dashboard (Targets folder) answers "what does
this Pi measure?": how many targets it measures of each service category
and of each kind of delivery network, and which services they are. It
reads the service labels described in [Service taxonomy](taxonomy.md).
Open it from the dashboard list, the Overview's *Where to look next*
list, or the header of Connectivity & Quality.

| Panel | What it shows |
| --- | --- |
| Targets per service category | One bar per category of the [vocabulary](taxonomy.md#the-vocabulary), zero included, in its order. A target with two categories (YouTube is video and social) counts in both, so the bars add up to more than the targets. |
| Targets per delivery kind | One bar per [kind of network](taxonomy.md#delivery) the targets answer from (CDN, content network, embedded cache...); one kind each. |
| Services | One row per service, with its categories, its operator, its kind and how many targets measure it, most first. A service delivered by two networks has a row for each. |

The panels read the last label of each active target, one a probe wrote
for in the last 24 hours, so a removed or renamed target, which keeps its
last label in InfluxDB forever, is not counted. The time range does not
change them. Each probe counts as a target: a website the DNS wizard probes
by ICMP, TCP and three HTTP versions counts five times. An active target
the exporter has not labeled yet is not counted. The queries read one point
per target.

*Unknown* in the categories means no rule labels the target yet
(`sudo smoking-pi taxonomy unknown` lists them); in the kinds, that the
host had no IPv4 answer or the AS lookup has not answered yet. Neither is
a broken target.

The dashboard is part of the InfluxDB set: ClickHouse mode has no service
labels yet ([ClickHouse](clickhouse.md)), and the panels say "no labels
yet".
