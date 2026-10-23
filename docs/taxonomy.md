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
   answers (Netflix's own Open Connect, Akamai, Cloudflare, an ISP), its
   **operator** and its **kind**, see [Delivery](#delivery).

The two are independent. `www.nytimes.com` is a news site (service)
delivered by Fastly (delivery); `uba.ar` is a university delivered by
Imperva's CDN.

## The vocabulary

Sixteen categories and `unknown`. Each answers one network question.

| id | Label | The question |
| --- | --- | --- |
| `video` | Video streaming | Can the line hold a video stream without rebuffering? |
| `audio` | Audio streaming | Does music or podcast playback start and keep playing? |
| `gaming` | Gaming | Is latency low and steady enough for online play and game stores? |
| `realtime_comms` | Calls and conferencing | Are voice and video calls free of loss and jitter? |
| `messaging` | Messaging | Do messages and their notifications get through promptly? |
| `social` | Social media | Do feeds, images and short videos load quickly? |
| `web_search` | Web search | Does search answer fast, the first step of most browsing? |
| `web` | Websites | Does ordinary browsing work (news, reference, shopping, company sites)? |
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
   `Google` rule). The seed has none (see below); a name rule is for a
   target whose host says nothing.
2. **`host_suffix`**: the longest suffix of the host, on label boundaries.
   `meet.google.com` beats `google.com`; `notnetflix.com` does not match
   `netflix.com`. An IP address matches only itself, so a `1.1.1.1` rule
   never claims `11.1.1.1`.
3. **`category`**: the technical bucket as a last resort. Every
   `netflix_oca` target is Netflix video, every `dns_resolvers` target is
   DNS, the `cpe` target is the gateway.

### Label by host, not by name

The seed labels every public target by its host. The DNS wizard adopts
the hosts a house uses under names of its own (`W_amazon_com_h2` for
`www.amazon.com`), so a rule on the template's name, `Amazon`, labeled
the shipped copy and left the wizard's copy of the same host unknown. A
host rule labels both, and every probe of either, identically. A `name`
rule is the right key only when the host is not: a private address, or a
host shared by unrelated services. A service's own longer suffixes still
win over its website, so `unagi-na.amazon.com` (Amazon's telemetry) is
`ads_analytics` while `www.amazon.com` is `web` and `cdn`.

A website's suffix rule is a catch-all: any subdomain no longer rule
claims gets the website's label, so `aws.amazon.com` is Amazon `[web,
cdn]` too. When an adopted host is not what its domain's rule says, an
[override](#overrides) corrects it for your install, and a rule for it
here corrects it for everyone. A target you created with a seed name
(`Google`, `Amazon`) on another host is labeled by that host now, not by
its name.

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

## Delivery

The service axis is what someone wrote down; the delivery axis is looked
up. For each active target, the InfluxDB exporter resolves the host (an IP
address is its own answer; a URL's hostname for HTTP targets), asks Team
Cymru's DNS service for the address's origin AS (cached a week per /24, a
failed lookup an hour, at most 64 new lookups per round), and classifies
that AS (`shared/modules/common/asn.py`):

1. **`KNOWN`**, a short hand table of well-known networks in `asn.py`. It
   always wins: it is where a kind that matters for measurement is decided
   on purpose (Netflix's AS is an `embedded_cache`, whatever else it is).
   Amazon's AS is split by the CNAME chain: CloudFront is a `cdn`, the rest
   `cloud_origin`.
2. **`common/data/asn_roles.csv`**, one kind for each of about 880 ASes,
   derived from [Linnaeus](https://github.com/NU-AquaLab/linnaeus)'s
   hand-labeled ASes (Piotto et al., arXiv 2603.13649; MIT, its license
   next to the file). Linnaeus gives an AS several roles; the derivation
   keeps the first that says who delivers, in this order: Access, Mobile
   and Satellite → `access`; Content Provider CDN → `cdn`, Cloud and
   Hosting → `cloud_origin`, other content → `content_network`; Transit →
   `transit`; DNS → `dns_infrastructure`; Educational & Research →
   `academic`. Roles that say who owns an AS rather than what it delivers
   (government, finance, enterprise, ...) are left out.
3. The AS name: a university's is `academic`; anything else is its own
   operator, of kind `other`.

| Kind | What it means for a measurement |
| --- | --- |
| `cdn` | A multi-tenant CDN edge: many sites share it, so its trouble shows on all of them at once. |
| `content_network` | A content provider's own network (Google, Meta, Apple, Microsoft). |
| `embedded_cache` | A cache inside or next to your ISP (Netflix Open Connect): close, and not on the general Internet path. |
| `cloud_origin` | A cloud region, a hosting provider or the service's own servers: often far, one location. |
| `access` | An access (home, mobile or satellite) ISP: the target answers from inside a carrier, often its own cache or service. |
| `transit` | A transit carrier's network. |
| `dns_infrastructure` | A root, TLD or authoritative DNS operator. |
| `academic` | A university or research network. |
| `local` | An address on your own network (the gateway); not looked up. |
| `other` | An AS neither table classifies: its operator is the AS name. |
| `unknown` | No IPv4 answer (an IPv6-only host, one that does not resolve) or the lookup has not answered yet. Not an error. |

The same lookups and kinds feed the experimental shared-congestion
detector (`inference/tags.py`), which asks whether loss reached several
kinds of infrastructure at once, and the path meter records the same
origin AS for each hop ([Path](path.md)). The per-target cache is
`common/operators.py`, shared by the exporter and the detector; four older
Team Cymru readers (the resolver probe, the public-IP check, the DNS
wizard, `tools/dns-explore`) predate it and are left as they are.

Refreshing the roles file is a maintainer step:
`python3 shared/scripts/build-asn-roles.py hierarchical.csv --release <release>`
on a downloaded Linnaeus `labels/hierarchical.csv`; `--check` says whether
the committed file is current. ASdb (no license) and PeeringDB (its terms
forbid storing its data) are not used, and nothing is asked of a
commercial geolocation or BGP service.

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
| field `operator` | Who runs the network the host answers from ([Delivery](#delivery)), or `unknown` |
| field `kind` | That network's kind (`cdn`, `access`, ...), or `unknown` |
| field `asn` | Its origin AS number; 0 when unknown or local |

A point is written when the exporter first sees a target and whenever any
of those changes (an override, a renamed or retitled target, a new
taxonomy version, a host that moved to another network or whose lookup
first answered), never per cycle: a few hundred points per install, and
one more per change. The exporter asks config-manager's
`GET /taxonomy/targets` every five minutes and never resolves a label
itself; when config-manager does not answer, it logs one line and tries
again on the next round. Inactive targets get none. The last point per
target is its current label, and the points before it say what it was
called when. The dashboards read the last point over all time, which
holds because the bucket keeps everything (no retention period is set);
a bucket given one would need the exporter to rewrite the labels before
they expire.

**Grafana.** Connectivity & Quality and Side by Side have a **Service
category** variable (several at once; All by default), listing the
categories the targets have. On Connectivity & Quality it narrows every
panel that reads targets' measurements (the tiles, the category lines, the
targets table, the heatmaps and the per-target episodes) to the targets in
the chosen categories; the whole-connection rows stay whole. On Side by
Side it narrows the Target list within the probe bucket (the variable
**Category (one at a time)**, unchanged). With All, every target counts,
labeled or not; the panels' queries gain one predicate, `target =~
/^(.*)$/`, which the storage engine evaluates on the tag (no
`contains(set:)`, which it would not push down). Target Detail shows the service and its categories in a **Service**
tile on the first row, with the operator and kind next to it.

**The assistants.** The MCP server's `list_targets` returns each target's
`service` and `categories` and takes a `service_category` filter;
`get_latency_stats` and `get_loss_events` carry the two fields on each
target they report when they have read the target list for the links.
The delivery axis is not in the MCP tools yet: it lives in InfluxDB, and
no tool reads `target_labels` there; `list_targets` reads config-manager. The
web admin's chat does the same in its `list_targets`. The answering guide
(`common/guide.py`, the one every assistant gets) tells them to speak of a
target by its service, not its probe bucket, and that `unknown` is not a
broken target.

ClickHouse mode has no `target_labels` yet ([ClickHouse](clickhouse.md)).

The doctor's `targets-labeled` check also counts the active targets whose
kind is `unknown` and names them, as a warning (never a failure): the
lookups have not answered, or the host has no IPv4 address. A target whose
host is an IPv6 address is not counted, since the IPv4-only lookup can
never give it a kind; a hostname with only IPv6 addresses is, and stays
named.

## Refreshing the rules

Rules go stale: a service moves to new hostnames, a source recategorizes
one, an install adds targets no rule knows. `shared/scripts/build-taxonomy.py`
checks the shipped rules against the sources and proposes rules for
unlabeled targets. It is a maintainer tool, not a `smoking-pi` command: it
needs the network and third-party sources that never reach a Pi, and what
it produces is a report a person turns into a pull request. It never edits
`taxonomy.yaml`, because a relabel renames a target's service on every
dashboard and splits what Grafana shows by service; that is a decision for
a reviewer, not a script.

```bash
smoking-pi taxonomy list --json > targets.json          # on an install
python3 shared/scripts/build-taxonomy.py --adguard --ndpi --targets targets.json --out report.md
python3 shared/scripts/build-taxonomy.py --adguard --ndpi --check   # before a release
```

Run it before a release, and after adopting new services from the DNS
wizard (their targets arrive unlabeled). Each source is off unless its flag
is given, is fetched over HTTPS at run time into `--cache-dir` (a temporary
directory by default), and is never written to the repository:

| Flag | Source | License | Use |
| --- | --- | --- | --- |
| `--adguard [FILE]` | AdGuard HostlistsRegistry `assets/services.json` | GPL-3.0 | Hostnames per service and the service's group |
| `--ndpi [FILE]` | nDPI `src/lib/ndpi_content_match.c.inc` (`host_match`) | LGPL-3.0 | Hostnames per protocol and its category |
| `--radar` | Cloudflare Radar `ranking/domain/{domain}` (`CLOUDFLARE_API_TOKEN`) | CC BY-NC 4.0 | A domain's content categories, one request per second |

Each source's categories map onto the vocabulary through a small table in
the script (AdGuard `messenger` → `messaging`, nDPI `VOIP` →
`realtime_comms`, Radar 164 → `video`, ...); AdGuard's `streaming` covers
both `video` and `audio`. What a source says is evidence for a fact we
write ourselves, so no license follows it into the repository.

The report has three sections:

1. **Unlabeled targets** from `--targets`, each with every source's
   proposal as a rule to paste (`source` set, `confidence: low`, `since`
   today, a note saying where it came from).
2. **Disagreements**: a shipped rule whose own source lists none of its
   hosts any more, or puts one in a category the rule does not have. What
   the other sources say, and names that differ, are listed after it for
   information.
3. **Stale rules**: `since` older than `--max-age` days (180).

`--check` exits 1 when section 2 or 3 is not empty. A proposal becomes a
rule when a maintainer checks it (does the service use those hosts? is the
category the question it answers?), raises `confidence` to what the check
supports, keeps `source`, sets `since` to the day of the check, and opens a
pull request. A disagreement is resolved the same way: correct the rule,
or confirm it and move its `since`, or change its `source` to `manual`
when the maintainer's own check is the evidence.
