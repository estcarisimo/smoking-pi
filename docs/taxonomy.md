# Service taxonomy

Every target has a `category`: `top_sites`, `netflix_oca`, `dns_resolvers`,
`custom`, `http`, `tcp`, `dns_wizard`, `cpe`. That is a technical bucket.
It decides which SmokePing section the target goes in and which tag its
points carry in InfluxDB, and it never said what the service is. A
dashboard, the DNS wizard or the assistant could call a target `topsites`,
but not "video streaming" or "an AI assistant".

The service taxonomy adds that, beside the category, which does not change:

- **service**: the name of what is measured (`Netflix`, `Claude`,
  `Google Public DNS`).
- **categories**: one or more ids from the vocabulary below (YouTube is
  `video` and `social`).
- **label_source**, **label_confidence**, **label_rule**: where the label
  came from (see [Resolution](#resolution)).

## Two axes

A measured target answers two different questions:

1. **What service is it?** The service axis, this page: a vocabulary of our
   own and rules that map targets onto it.
2. **Who delivers it?** The delivery axis: the network that actually
   answers (Netflix's own Open Connect, Akamai, Cloudflare, an ISP cache).
   `common/asn.py` already computes this from the answering address's AS.
   It is not part of the labels yet.

The two are independent. `www.nytimes.com` is a news site (service)
delivered by Fastly (delivery); `uba.ar` is a university delivered by
Imperva's CDN.

## The vocabulary

Fifteen categories and `unknown`. Each answers one network question.

| id | Label | The question |
| --- | --- | --- |
| `video` | Video streaming | Can the line hold a video stream without rebuffering? |
| `audio` | Audio streaming | Does music or podcast playback start and keep playing? |
| `gaming` | Gaming | Is latency low and steady enough for online play and game stores? |
| `realtime_comms` | Calls and conferencing | Are voice and video calls free of loss and jitter? |
| `messaging` | Messaging | Do messages and their notifications get through promptly? |
| `social` | Social media | Do feeds, images and short videos load quickly? |
| `web_search` | Web search | Does search answer fast, the first step of most browsing? |
| `ai_assistant` | AI assistant | Can an AI assistant (chat, code, voice) be reached and answer? |
| `cloud_saas` | Cloud and SaaS | Are cloud storage, office suites and work tools reachable? |
| `software_update` | Software updates | Can operating system, app and game updates download? |
| `cdn` | Content delivery network | How close and how steady is the nearest CDN edge? |
| `dns` | DNS | Do name lookups answer, and how fast? |
| `connectivity` | Connectivity | Is the local network and the way out to the Internet up? |
| `ads_analytics` | Ads and analytics | Are ad and analytics endpoints slowing pages down? |
| `academic` | Academic and research | Are universities and research networks reachable? |
| `unknown` | Unknown | No rule says what this service is yet. |

The vocabulary and the rules live in one file,
`shared/modules/config-manager/templates/taxonomy.yaml`, which ships in the
config-manager image. It has a `version`. It is not copied into your
config directory and is not edited per install: a correction for your
install is an [override](#overrides).

## Resolution

Each rule matches on exactly one thing, and the most specific match wins:

1. **`name`**: the target's name, or its base name without the probe
   suffix (`Google_h2`, `Google_h3` and `Google_tcp443` all match a
   `Google` rule). This is how every seed target is labeled.
2. **`host_suffix`**: the longest suffix of the host, on label boundaries.
   `meet.google.com` beats `google.com`; `notnetflix.com` does not match
   `netflix.com`. An IP address matches only itself, so a `1.1.1.1` rule
   never claims `11.1.1.1`.
3. **`category`**: the technical bucket as a last resort. Every
   `netflix_oca` target is Netflix video, every `dns_resolvers` target is
   DNS, the `cpe` target is the gateway.

Nothing matched: the target is `unknown`, and its service is its title.
Unknown is allowed. `sudo smoking-pi taxonomy unknown` lists those
targets, and the doctor's `targets-labeled` check warns about them (it
never fails on them).

Each rule records:

| Field | What it says |
| --- | --- |
| `service` | The service name. Absent means the target's title (the resolver bucket uses this). |
| `categories` | One or more vocabulary ids. Never `unknown`. |
| `source` | The evidence the maintainer used: `manual`, `adguard`, `ndpi` or `radar`. |
| `confidence` | `high`, `medium` or `low`. |
| `since` | The date the rule was written or last checked. |
| `note` | Optional: why, when it is not obvious (`uba.ar` measures Imperva's edge). |

## Overrides

A label the rules get wrong for your install (a target you added for a
home server, a service the vocabulary has no rule for) is an override. An
override replaces the resolved label entirely and is reported as
`label_source: manual`, `label_rule: override`. Overrides are stored in
PostgreSQL (the `target_labels` table, keyed by target name), so they need
database mode, which is how Pro runs.

An override follows its target when you rename it and goes away when you
delete it. Netflix OCA targets keep theirs across an OCA refresh, because
their names are stable.

In the web admin, open a target's edit dialog on the **Targets** page:
the **Service label** section shows the current label and where it came
from, and offers **Save label** and **Reset to default**. The list has
**Service** and **Service categories** columns, a filter by service
category, and both in the CSV export.

## The command

```bash
sudo smoking-pi taxonomy list
sudo smoking-pi taxonomy list --json
sudo smoking-pi taxonomy unknown
sudo smoking-pi taxonomy set home_nas "Home NAS" cloud_saas --note "the Synology"
sudo smoking-pi taxonomy reset home_nas
```

Standard and Pro (it asks config-manager; Basic has none). `list` and
`unknown` work in either mode; `set` and `reset` need the database.
`set TARGET SERVICE CATEGORY[,CATEGORY...]` takes the categories as
comma-separated vocabulary ids.

The API behind it, for scripts:

| Endpoint | What it does |
| --- | --- |
| `GET /taxonomy` | The version and the vocabulary. |
| `GET /taxonomy/targets` | Every target with its label. |
| `GET /taxonomy/unknown` | The active targets that resolve to `unknown`. |
| `PUT /targets/<id>/labels` | Override: `{"service": ..., "categories": [...], "note": ...}`. |
| `DELETE /targets/<id>/labels` | Back to the resolved label. |

`GET /targets` carries `service`, `categories`, `label_source`,
`label_confidence`, `label_rule` and `label_note` on every target. `PUT
/targets/<id>` does not write labels.

## Licensing: the labels are ours

No external source gives hostname-to-service categories under a license a
package can ship: Cloudflare Radar's categories are CC BY-NC, AdGuard's
HostlistsRegistry is GPL-3.0, nDPI's host table is LGPL, and the tracker
datasets are non-commercial. So the vocabulary and the rules are the
project's own. Those sources are evidence a maintainer consults when
writing or checking a rule (which hostnames belong to which service), and
`source` on each rule records which one. None of their files is copied
into the repository.

## In InfluxDB, Grafana and the assistants

The labels never become tags on `latency`, `dns_latency`, `http_latency`,
`tcp_latency` or `cpe_latency`: a new tag splits a series, so retagging
would cut every target's history in two, and again at every relabel.
Instead the InfluxDB exporter (`rrd2influx.py`, Pro) writes them to a
measurement of their own, the way it writes `target_host`:

| Measurement `target_labels` | |
| --- | --- |
| tag `target` | The target's name, the same tag the latency series carry |
| field `service` | The service name |
| field `categories` | The vocabulary ids, sorted and comma-joined (`social,video`) |
| field `label_source` | `manual` (an override, or a rule from the maintainer's own check), `adguard`, `ndpi` or `radar`; empty for `unknown` |
| field `taxonomy_version` | The rules' `version` the label came from |

A point is written when the exporter first sees a target and whenever any
of those changes (an override, a renamed or retitled target, a new
taxonomy version), never per cycle: a few hundred points per install, and
one more per change. The exporter asks config-manager's
`GET /taxonomy/targets` every five minutes and never resolves a label
itself; when config-manager does not answer, it logs one line and tries
again on the next round. Inactive targets get none. The last point per
target is its current label, and the points before it say what it was
called when.

**Grafana.** Connectivity & Quality and Side by Side have a **Service
category** variable (several at once; All by default), listing the
categories the targets have. On Connectivity & Quality it narrows every
panel that reads targets' measurements (the tiles, the category lines, the
targets table, the heatmaps and the per-target episodes) to the targets in
the chosen categories; the whole-connection rows stay whole. On Side by
Side it narrows the Target list within the probe bucket (the variable
**Category (one at a time)**, unchanged). With All, every target counts,
labeled or not, and the panels' queries are the ones they were (one more
`target =~ /^(.*)$/` predicate, which the storage engine answers from its
index). Target Detail shows the service and its categories in a **Service**
tile on the first row.

**The assistants.** The MCP server's `list_targets` returns each target's
`service` and `categories` and takes a `service_category` filter;
`get_latency_stats` and `get_loss_events` carry the two fields on each
target they report when they have read the target list for the links. The
web admin's chat does the same in its `list_targets`. The answering guide
(`common/guide.py`, the one every assistant gets) tells them to speak of a
target by its service, not its probe bucket, and that `unknown` is not a
broken target.

ClickHouse mode has no `target_labels` yet ([ClickHouse](clickhouse.md)).

## Not yet

- TODO (PR 3): the maintainer's refresh tooling (checking rules against the
  sources and proposing new ones), and the delivery axis from
  `common/asn.py` next to the service.
