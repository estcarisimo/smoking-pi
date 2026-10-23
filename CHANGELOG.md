# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses semantic-ish versioning: minor bumps per completed
modernization sprint (or batch), patch bumps for hotfixes. Each released
version gets a matching GitHub release and git tag.

## [Unreleased]

### Fixed
- **The six websites the staging Pi's wizard had adopted had no service
  label.** The v2.47.0 seed covered the hosts the reference Pi measured,
  so on the staging Pi every probe of `www.microsoft.com`,
  `www.mozilla.org`, `www.python.org`, `www.wikipedia.org`,
  `www.bbc.co.uk` and `acm.org` (28 targets) stayed `unknown` in the
  Service category filter and the doctor's `targets-labeled` check. Six
  `host_suffix` rules label them (`web`; ACM also `academic`), 114 rules
  in all; the `microsoft.com` catch-all yields to the Teams, Copilot,
  update and telemetry rules that already exist.
- **Target Detail's Service tile was unreadable.** It drew the category
  as the big value and the service's name as a tiny caption, so on a
  phone "academic" was what you could read and "Universidad de Buenos
  Aires" was not; the operator and its kind shared the same tile, just as
  small. The tile is now two: **Service** shows the name large with the
  categories over it, and **Delivered by** the operator large with its
  kind in words ("CDN", "embedded cache"); both stack the small text over
  the value at any width, and "unknown (no rule yet)" stays orange.

### Added
- **Connectivity & Quality says what is measured.** Nothing showed how
  many targets of each kind the Pi measures: the labels existed, but only
  one target at a time. A collapsed row, *What is measured (service
  labels)*, counts the active targets per service category and per
  delivery kind (every id of each vocabulary, zero included) and lists
  the services with their operator, kind and number of targets. It reads
  one label point per target and ignores the Service category variable.

## [2.47.0] — 2026-10-23

The paths say where they go: each router on Target Detail's path table now
has its reverse DNS name next to its address and network, and the networks
come from [originet](https://github.com/estcarisimo/originet), the Rust
rewrite of map-ip-to-asn, which loads CAIDA's daily routing snapshot in
seconds instead of the minute the previous table took on a Pi. The service
labels reach the targets the DNS wizard adopted, which were half of a real
install, and an assistant can find the MCP server without being pointed at
this repository. Nothing changes unless the path meter or inference is on,
except the labels, which are rewritten once.

### Added

- **Each router on a path has its name.** The path table showed a hop's
  address, network and reply time, and nothing of where the router was:
  `184.104.193.47` says nothing, its reverse DNS name
  `be1.core1.hou2.he.net` says Hurricane Electric's core in Houston. The
  path meter now asks for the PTR name of every public hop and Target
  Detail's path table shows it as **name (PTR)**. Names are hints, so they
  are kept out of everything that judges a path (the path text, its hash,
  its changes): a renamed router is not a path change. Each address is
  asked once a week through the Pi's resolver, at most 100 new queries a
  cycle, eight at a time, so the resolver never sees a burst: about 250
  queries the first day on the reference house, almost none after. On the
  staging Pi, 43 of 66 routers had a name.

- **An agent can find the MCP server without already knowing this
  repository.** The docs site had no `/llms.txt`, the published image
  carried no registry name, and the repository had no `server.json`, so a
  registry or an assistant that had not been pointed here could not
  discover the server or tell a real image from one that only reused the
  name. The docs build now writes `/llms.txt` (linked from the README).
  The mcp-server image is labeled
  `io.modelcontextprotocol.server.name=io.github.estcarisimo/smoking-pi`.
  `server.json` at the repository root describes that OCI image and is
  not published by CI. The image is the one `release.yml` already pushes
  (`mcp-server:<version>`, and `:latest` only after the install tests);
  that build repeats the registry name label. There is no second image
  workflow.

### Changed

- **The routing table is originet.** v2.46.0 looked hops' networks up in a
  table this project built itself from CAIDA's RouteViews snapshot: 43 s of
  a Pi's half core to build, a parser of its own to keep right, and the
  last origin of a multi-origin prefix where the tool it imitated has moved
  on. [originet](https://github.com/estcarisimo/originet), the Rust rewrite
  of map-ip-to-asn, does the same job: the path meter and inference now
  install its wheel and let it fetch, cache and load the snapshot (8 s on
  the staging Pi's half core, including the download; 2 s from its cache;
  100 000 lookups in 0.1 s), keeping the two newest snapshots (its cache
  keeps every day otherwise). A prefix several networks announce now
  reports the first of them, as originet does. The files of the old table
  are removed on the first start. PeeringDB's exchange LANs and Team Cymru
  (names, and whatever no table covers) are unchanged. On the staging Pi,
  27 of 28 public hops got their network in the first cycle.

### Fixed

- **Half of a real install's targets had no service label.** The seed
  taxonomy labeled the shipped targets by name (`Amazon`, `Apple`, `NYT`,
  ...), so the DNS wizard's copies of the same hosts (`W_amazon_com_h2`
  for `www.amazon.com`) matched nothing, and the ordinary websites and
  device endpoints the wizard adopts had no class in the vocabulary. On
  the reference Pi, right after the v2.46.0 upgrade, the Service category
  filter and the doctor's `targets-labeled` check called 124 of 247
  active targets unknown. The seed now labels by host: the name rules are
  `host_suffix` rules (the shipped targets keep their labels), a new
  category `web` ("Websites": does ordinary browsing work?) makes Amazon,
  The New York Times and Apple `[web, cdn]`, and rules cover the 37 hosts
  that were unlabeled there (Alexa, Cursor, Grammarly, Notion, Samsung TV,
  iCloud Private Relay, NTP, telemetry endpoints, ...). One,
  `tzm.protechts.net`, stays unknown: no source names its owner. The
  taxonomy `version` is now 2, so the exporter rewrites every target's
  labels once (108 rules). A target someone created with a seed name
  (`Google`, `Amazon`) on another host is now labeled by its host, and the
  shipped Google targets' confidence is `medium`, the host rule's.

## [2.46.0] — 2026-10-22

The release that gives every target a service label, carries it to the
time series, the dashboards and the assistants, and says who delivers each
service. Every target now has a `service` and `categories` from a
vocabulary of 15 categories, resolved by 78 shipped rules and correctable
per install (web admin or `smoking-pi taxonomy`). The InfluxDB exporter
writes a `target_labels` point per target (service, categories, and the
operator, kind and AS of the network that answers), only when a label
changes and without adding a tag to any latency series, so no history
splits. Connectivity & Quality and Side by Side filter by **Service
category**, Target Detail shows a **Service** tile, and `list_targets`
names targets by service. The path meter and inference name every hop's
network from the first cycle with a local prefix-to-AS table the Pi
downloads once a day (about 4 MB; not shipped in the package). It adds no
probe; the exporter makes a few cached Team Cymru lookups (a week per
/24). A maintainer tool checks the shipped rules against their sources.
Also fixed: the doctor no longer reports a stopped Wi-Fi collector after
the radio reconnects.

### Added

- **What kind of service each target is.** A target's `category`
  (`top_sites`, `netflix_oca`, `dns_resolvers`, `dns_wizard`...) says which
  SmokePing section and InfluxDB tag it gets, never what the service is:
  the Quality dashboard, the DNS wizard and the assistant could only call
  Netflix, Claude or a game store "topsites" or "dns_wizard", and nothing
  could ask how video, calls or AI assistants were doing as a group. Every
  target now carries a service label beside its category, which is
  unchanged: a `service` name and one or more `categories` from a
  vocabulary of the project's own (15 categories and `unknown`: video,
  audio, gaming, calls, messaging, social, search, AI assistant, cloud,
  software updates, CDN, DNS, connectivity, ads, academic), with the rule
  that produced it, its evidence and a confidence. The rules
  (`config-manager/templates/taxonomy.yaml`, versioned, 78 rules) match a
  target's name, then the longest suffix of its host, then its technical
  bucket; every seed target and the services the DNS wizard usually adopts
  resolve. No external hostname-to-category list can be shipped under its
  license (AdGuard's registry is GPL, nDPI's LGPL, Cloudflare Radar's
  categories non-commercial), so they are only evidence a maintainer
  consults, recorded as `source` on each rule. A wrong label is corrected
  per install with an override in PostgreSQL (`target_labels`), from the
  web admin's target dialog or `smoking-pi taxonomy set|reset`;
  `smoking-pi taxonomy list|unknown` shows them. `GET /targets` carries the
  label; `GET /taxonomy`, `/taxonomy/targets` and `/taxonomy/unknown` are
  new. The doctor validates the file and the seed's labels
  (`service-taxonomy`) and warns about unlabeled targets on a running stack
  (`targets-labeled`). `docs/taxonomy.md`.

- **The service labels reach the time series, the dashboards and the
  assistants.** On their own, the labels above would be known only to
  config-manager's API, the web admin and `smoking-pi taxonomy`: the
  dashboards filtered by probe bucket, and the
  assistants listed "topsites" and "dns_wizard" targets, so nobody could
  look at how video or AI assistants were doing as a group, or hear
  "Netflix, video streaming" instead of a bucket name. The InfluxDB
  exporter now writes a `target_labels` measurement (tag `target`; fields
  `service`, `categories`, `label_source`, `taxonomy_version`), read from
  config-manager's `GET /taxonomy/targets` and written only when a label
  changes. No tag was added to any latency series, which would have split
  every target's history. Connectivity & Quality and Side by Side have a
  **Service category** variable (All by default, which adds one
  `target =~ /^(.*)$/` predicate the storage engine evaluates on the tag); Target Detail has a
  **Service** tile. The MCP server's `list_targets` and the web chat's
  return `service` and `categories` and filter by `service_category`;
  `get_latency_stats` and `get_loss_events` carry them when they read the
  target list for links; the shared answering guide says to name targets
  by service. The live doctor's dashboard check now sends a variable's
  custom all value when it is saved on All, as Grafana does. ClickHouse
  mode has no `target_labels` yet. `docs/taxonomy.md`.

- **Every target says who delivers it, and the rules can be checked
  against their sources.** A target's label said what the service is for
  (`video`, `ai_assistant`), never whose network answers: Netflix from an
  Open Connect box inside the ISP and a news site from a Fastly edge looked
  alike, though trouble on a shared CDN edge or inside the carrier means
  something else than trouble at one origin. And the rules could only be
  checked by hand, with nothing to say a source had
  moved a hostname or recategorized a service. The InfluxDB exporter now
  adds `operator`, `kind` and `asn` to each target's `target_labels` point
  (written on change, as before): the host's origin AS from Team Cymru,
  cached a week per /24, classified by `common/asn.py`. Its hand table of
  well-known networks still decides first; behind it, a vendored
  `common/data/asn_roles.csv` gives a kind to about 880 more ASes, derived
  from Linnaeus's hand-labeled ASes (MIT), which adds the kinds `access`,
  `transit` and `dns_infrastructure`, and `local` for a LAN address. The
  per-target cache moved from the inference module to
  `common/operators.py`; the experimental shared-congestion detector reads
  the same kinds, so an AS it called `other` may now count as one of
  these. Target Detail's Service tile shows the operator and kind; the
  doctor's `targets-labeled` check warns about targets whose network is not
  identified. The smokeping image now carries `shared/modules/common` and
  dnspython for this. A maintainer tool, `shared/scripts/build-taxonomy.py`,
  reads AdGuard's and nDPI's host lists and Cloudflare Radar at run time
  (never vendored), proposes rules for an install's unlabeled targets, and
  reports shipped rules their source no longer supports and rules older
  than 180 days; `--check` exits 1 on those, and it never edits the rules
  ([Service taxonomy](taxonomy.md#refreshing-the-rules)).

### Changed

- **The path meter and inference know every hop's network from the first
  cycle.** The origin AS came from Team Cymru's DNS service, one query per
  /24, at most 64 new ones a cycle so as not to send a burst of DNS: on the
  reference Pi the first cycle named 61 of the /24s on its paths and left
  the rest, the ISP's own first hop among them, as "–" for hours. The
  service now uses the data behind
  [map-ip-to-asn](https://github.com/estcarisimo/map-ip-to-asn): CAIDA's
  daily RouteViews prefix-to-AS snapshot, downloaded once a day into its
  state volume and looked up locally, by the longest announced prefix and
  for every hop at once. Routers answering from an exchange point's LAN,
  which nobody announces, are named by the exchange (PeeringDB), in a new
  **exchange (IXP)** column of Target Detail's path table. Team Cymru
  stays for AS names and for what neither covers. On the staging Pi, 30 of
  the 32 public hops got their network in the first cycle; the other two
  are in nobody's table. About 4 MB a day of download per service; a
  table of a few MB built from it (43 s on the staging Pi's half core once
  a day, a restart reads it in 0.06 s). A failed download keeps the
  previous table. The exporter's service labels keep Team Cymru for now.

### Fixed

- **The doctor no longer says the Wi-Fi collector stopped after a reconnect.**
  When the radio reconnects (after a reboot, or a lost association), the
  samples written while it was disconnected carry no network tags and form
  a series of their own. The `monitor-radio` check grouped the series and
  took the last row, which is the end of whichever series came last, not
  the newest sample: on the reference Pi, after a reboot on 2026-10-22, it
  warned for hours that "the newest Wi-Fi sample is 154 min old: the
  collector stopped" while the collector wrote every 10 s. A warning that
  is wrong teaches people to ignore the one that is right. The check now
  sorts by time before taking the newest sample and before measuring the
  spacing; replayed on that Pi's data it reads the sample from the same
  minute. The same shape read which interface carries the route in the
  alerter (its Wi-Fi verdict, for ten minutes after a reconnect), in the
  digest and AI report's Wi-Fi line, and in the assistant's Wi-Fi answers:
  sorted too. The alerter's deaf-radio detection already sorted and was
  right.

## [2.45.0] — 2026-10-22

The paths, not only the times: an experimental `pathmeter` service traces
the route to every measured destination once an hour with a Paris
traceroute, shows the last path, its networks and its changes on Target
Detail, and counts what it sends in `smoking-pi budget`. It is off unless
you run `smoking-pi enable pathmeter` (Pro with InfluxDB), stays out of the
alert path, and is built not to load the home router or the provider's
first routers: at most four probes an hour reach the hops every path
shares, however many destinations there are, and no router on the way gets
more than five probes a second.

### Added

- **The path to each destination (experimental).** The measurements said how
  long a destination took to answer, never which way the packets went: a
  latency step could not be read next to the route it happened on, and
  whether an evening of loss sat in the access network or beyond could only
  be guessed from how many destinations lost at once. A new `pathmeter`
  service (Pro with InfluxDB, its own profile, off by default: `smoking-pi
  enable pathmeter`) traces every measured destination once an hour with a
  Paris traceroute over ICMP (scamper), so a load balancer on the way does
  not draw a path that does not exist. ICMP because it is the method that
  gets there: from the reference house it reached 60 of 76 destinations,
  against 34 over UDP and 7 over TCP. The hops every path shares (the home
  router and the access network, five on the reference house) are never
  crossed by a burst. Traced from hop 1, every destination would send its
  probe at the home router within seconds of the others (76 here, more as
  the list grows), and the router, which rate-limits, dropped a third of
  them in the test. Each cycle traces only four destinations from hop 1, in
  turn, and the rest from the first hop past the shared ones, learned from
  every destination's latest full trace; a new gateway is followed in the
  cycle that sees it. The router gets four probes an hour however many
  destinations there are, and a cycle on the staging Pi sent 193 probes
  instead of 299. A change counts after two traces in a row show it, and an
  AS-path change, from Team Cymru's origin data, is flagged on its own.
  Target Detail shows the last path in the selected range, the changes and
  the distance per trace in a collapsed *Path (experimental)* row, and marks
  confirmed changes with an annotation. `smoking-pi budget` counts what its
  cycles actually sent (the day's mean) as a `Traceroute` row (about 600
  probes an hour at the defaults, 3 % of the default samples ceiling). Paced
  at `PATHMETER_PPS` (5 per second, so no router on the way gets more);
  `PATHMETER_INTERVAL` sets the cycle. Host network, every capability
  dropped but `NET_RAW` and the three scamper's own privilege separation
  needs. The AS lookup moved from the inference module to `common/asn.py`,
  shared by both. `docs/path.md`.

## [2.44.0] — 2026-10-20

The experimental inference module now answers for the whole connection, not
only per destination: once a pass, it runs its loss detector and Jitterbug,
both unchanged, on series built across every steady destination, and says
whether a period was shared across operators and kinds of infrastructure or
carried by one target, and who lost in it. Connectivity & Quality draws the
results as *Whole connection* rows and a who-lost table. It changes nothing
unless the `inference` profile is on, stays out of the alert path, adds no
probe, and adds a few cached DNS lookups per target (Team Cymru's origin-AS
service) plus a few files in the inference state volume; the first pass after
the upgrade takes about 30 s longer while it fills the aggregate stream.
On Pro with InfluxDB, `smoking-pi upgrade` now records its own window,
which Grafana shades and the inference module leaves out, so an upgrade no
longer reads as the network losing. Target Detail's percentiles on long ranges are
now each window's own instead of an average of five-minute percentiles, and
its DNS charts say when a target has no DNS probe.

### Added

- **`smoking-pi upgrade` records its own window.** An upgrade recreates
  the containers and showed up in the measurements as a cut on every
  destination at once, the ISP gateway included (reference Pi, 2026-10-15
  17:30Z: 4–6 % loss everywhere; 2026-10-18 18:30Z: 66 %), and nothing
  recorded that an upgrade had happened, so the detectors and whoever read
  the charts took it for the network. `upgrade` now writes a
  `phase=start` point (measurement `upgrade`, tags `from_version` and
  `to_version`, field `value=1`) before the first container changes, and a
  `phase=end` point with `result=ok` once InfluxDB takes writes again, or
  `result=failed` from an exit trap when the upgrade fails or is
  interrupted. Through InfluxDB's own CLI in its container, as the watchdog
  records its events, so no token is handled on the host; never a reason
  to stop the upgrade (one line when InfluxDB does not take a point; the
  end is retried for up to a minute while InfluxDB restarts). Overview and
  Connectivity & Quality draw each window as an *Upgrades* region labeled
  `from → to`. Pro with InfluxDB only: Basic and Standard have no
  time-series database, and the ClickHouse exporter has no table for
  events. The doctor's tag check now knows that a column a `map()` or
  `elapsed()` computes is not a tag. `docs/upgrades.md`, *The upgrade's
  window in the measurements*.
- **Shared degradation: is the whole connection losing, not one service?**
  The inference module found loss per destination only, and five
  destinations with a period each are not one shared period: the evenings
  of loss on the reference Pi (August to October, every destination a
  little at once) could only be read off the rows by eye. Each pass now
  also builds one series across every ICMP destination (lost over sent,
  per 15-minute bin) and runs the same degradation detector on it,
  unchanged. Before it: the monitor's own bins are left out (the radio
  deaf, or the ISP gateway losing as much as the destinations), a target
  that lost everything for six hours is out while it stays dead, and only
  targets that lost nothing in at least half their bins vote, so a host
  that rate-limits ICMP is never "the network". Each period is then
  qualified by who runs the infrastructure behind the targets: `shared`
  when a third of the voting operators lost at least 1 % and they are of
  two kinds of infrastructure or more (multi-tenant CDN, a content
  provider's own network, an embedded cache, cloud or origin, academic),
  `dominated` otherwise, with the target that carried the loss. The
  operators come from what each target reaches, not from a list of targets:
  the host the exporter records, its address and CNAME, and the origin AS
  from Team Cymru's DNS service, cached in the state volume, with a short
  curated table naming the kind of well-known networks; three names on one
  CDN count as one operator. Every category takes part, whatever
  `INFERENCE_CATEGORIES` selects for the per-target detectors. Replayed on 52 days of the reference Pi, it marks
  every evening of the August to October loss as shared, calls the three
  periods carried by one dead name `dominated`, and finds nothing on a CDN
  edge's route changes, the radio hangs or a quiet week. Written as
  `shared_degradation`; Connectivity & Quality draws it in its episode
  timeline as the *Whole connection* rows. Experimental like the rest of
  the module and out of the alert path. Costs one more change-point run
  and two storage-engine queries per pass, plus a few DNS lookups a day.
- **Shared persistent congestion: Jitterbug, unchanged, on an aggregate
  input.** Shared degradation answered for loss only; whether a queue
  stayed built up for the whole connection could be read only by comparing
  the per-target congestion rows by eye. Each pass now also runs Jitterbug
  as published (the same BCP + KS call as per target, no parameter changed)
  once, on one stream: per 5-minute cycle, the ten deciles of every
  voter's pings minus that voter's own floor (the 5th percentile of its
  15-minute minimum RTT over the trailing 7 days), when at least eight
  voters answered, the monitor's bins left out as in shared degradation,
  placed at the median voter floor (Jitterbug needs positive RTTs, and its
  BCP prior is centered on 0, so the same step reads differently at +10 ms
  and +100 ms: the base comes from the data, not a tuned constant).
  Reading 14 days of every target's raw pings each hour measured 22 s and
  662 MB on a staging Pi with ~23 targets (about a minute and 2 GB with the
  reference Pi's ~65), so the stream is built incrementally in the state
  volume: an hourly pass adds its 12 cycles in about 1 s and Jitterbug
  takes about 6 s on the 14 days; the first pass fills 7 days once (about
  30 s). Written as `shared_congestion`, drawn as an orange-dark *Whole
  connection* row in Connectivity & Quality's timeline, labeled
  experimental. Evaluated offline on 52 days of the reference Pi, the
  method finds nothing, which is the expected answer: the shared evening
  loss raised the relative floor by 0.25–0.55 ms, below a Jitterbug jump.
  Out of the alert path.
- **Who lost in a shared loss period.** A *Whole connection* loss period
  said whether it was shared and named one dominant target, but not which
  operators carried it or which destinations stayed clean, so telling a
  CDN's evening from the access network's meant opening every row. Each
  `shared_degradation` period now carries `by_operator` (the three
  operators that lost most), `by_kind` (every kind of infrastructure's
  loss) and `diverged` (voters that lost at most a tenth of the pooled
  loss), from the bins the pass already read; a table under the timeline
  lists them. `inference_pass` gains `shared_congestion_periods`.

### Fixed

- **Upgrades no longer read as the whole connection losing, and the
  shared pass costs 4 s instead of 10.** The shared passes of the
  inference module (shared degradation and Jitterbug on the aggregate
  stream) left out the bins where the radio was deaf or the gateway lost
  with the destinations, but not an upgrade restarting the stack: on the
  reference Pi the 2026-10-15 upgrade read as 4–6 % loss on every
  destination and the gateway, the 2026-10-18 one as 66 %, cuts in the
  series that a detector could take for the network's. The shared pass
  now reads the `upgrade` markers `smoking-pi upgrade` writes (`phase`
  `start`/`end`) and leaves those bins out, with one bin of margin on each
  side as for a deaf radio; a start with no end closes after 30 minutes,
  and with no markers nothing changes. Separately, every hourly pass
  re-read 7 days of deaf samples (4.5 s on the staging Pi: the alerter's
  query, a pivot InfluxDB cannot push down) and of loss bins (1 s) to
  learn about one new hour. Closed bins are now kept in the state volume
  (`loss.csv`, `deaf.csv`) and a pass reads only the last hour, plus the
  whole window once a day for points that arrived late: replayed on
  staging, ≈ 10 s → ≈ 4 s a pass, of which 3.2 s is the change-point run
  itself, with the same voters, monitor bins, periods and deaf bins as the
  full read. The docs now also say plainly why the aggregate stream sits
  at the median voter floor (≈ 7 ms): Jitterbug's BCP is not
  shift-invariant, and the same 3 ms step gave 2 change points at +10 ms
  and none at +100 or +1000 ms; kept as decided on 2026-10-20,
  experimental.

- **Target Detail's percentiles on long ranges are the window's own.**
  Each latency chart took every 5-minute cycle's 10th and 90th percentile
  (and SmokePing's median) and then averaged them over the point's window.
  That is exact up to a day, where a window holds one cycle, but on a week
  or a month a window holds two to nine cycles, and averaging percentiles
  is not a percentile: on the staging Pi at 21 days the DNS band's floor
  read 17 % high and its p90 6 % low, with points off by up to 86 ms
  (and the investigation on the reference Pi found the same pattern over
  30 days). A long range is opened to find the slow pings, and those are
  what the mean hid. The
  band and the line are now the percentiles of every ping in the window,
  pooled, for ICMP, TCP, DNS and the HTTP per-version medians; on staging
  they match a locally computed percentile to the microsecond at 1, 7 and
  21 days, and the two band queries of a 21-day chart take about 1 s each
  instead of 8–17 s (over the doctor's 10 s per query). Names and colors
  are unchanged; the median line moves slightly even at short ranges,
  since it is now the median of the stored pings rather than SmokePing's
  own median.
- **Target Detail's DNS charts said nothing for a target with no DNS
  probe.** The dashboard opens on `Google`, which has no DNS layer
  (Google's resolver is the target `GoogleDNS`), so its two DNS charts
  were empty axes and the doctor's live `dashboard-queries-run` warned
  about them on every Pi, a warning nobody could act on. The two DNS
  charts now say "no DNS data for this target in this range" and name a
  resolver target; the doctor skips panels that set their own empty text.
  Only the DNS charts set it, so the doctor still warns if the ICMP, TCP
  or HTTP charts of the default target come back empty, and a query that
  fails still fails.

## [2.43.1] — 2026-10-20

DNS response times are now measured at microsecond resolution. SmokePing's
DNS probe had been reading dig's coarse kernel clock, so every sample was
a multiple of 4 ms; a small wrapper around `dig -u` gives it the real
time, with the same queries and the same series. The DNS series change
resolution once, at the upgrade.

### Fixed

- **DNS probe samples sat on 4 ms steps.** SmokePing's DNS probe parses
  dig's `;; Query time: N msec`, and dig timestamps with the kernel's
  coarse clock (`CLOCK_REALTIME_COARSE`, a 4 ms tick at `CONFIG_HZ=250`)
  unless given `-u`. Every DNS sample on a Pi was a multiple of 4 ms: a
  resolver getting 1 ms slower was invisible, the minimum Jitterbug needs
  could not move between steps, and the DNS percentiles were coarse. The
  smokeping image now ships `/usr/local/bin/dig-usec`, which runs `dig -u`
  and rewrites only the `Query time: N usec` line to `N.NNN msec` (the form
  the probe's regex and its `pingfactor` take as is); other output and the
  exit code pass through. The probe points at it: in the seed, and on
  upgrade once for installed probes on the stock dig path, in
  PostgreSQL and in the config dir's `probes.yaml`. A binary you chose is
  left alone. No additional queries and no series renamed; **the DNS series
  change resolution at the upgrade**, so medians and minimums shift by up to
  one tick with no change in the network. Standard and Pro only: Basic keeps
  SmokePing's stock probe configuration. The smokeping and config-manager
  images must be deployed together, and a rollback to an earlier release
  needs the `DNS` binary set back to `/usr/bin/dig`. `docs/dns-probes.md`
  and the limits in `docs/inference.md` say so.

## [2.43.0] — 2026-10-18

The doctor now sees two failures that used to leave it green: a Wi-Fi
radio that hears nothing (`monitor-radio` fails while it is deaf and warns
on deaf runs in the day) and a target SmokePing measures whose points never
reach InfluxDB (`targets-in-tsdb`); `docs/doctor.md` says what each live
check covers per edition and backend. The daily digest no longer turns a
target red for loss that fell inside the monitor's own incident spans, and
Dependabot no longer proposes a Python the inference image cannot build on.

### Added

- **The doctor sees a deaf radio and a target that never reaches
  InfluxDB.** Two failures sat outside every doctor check. A Wi-Fi radio
  that hangs with the link up hears nothing, and every chart draws a
  network outage that is the monitor's own: the reference Pi did it for
  1 h 44 min and 5 h 31 min in October 2026, five of six confirmed
  microcuts were its radio, and the doctor stayed green throughout. And a
  target SmokePing measured whose points never reached InfluxDB passed
  `targets-measured` (which reads the RRD files) and `silent-series`
  (which reads only what InfluxDB holds), while no dashboard, alert or
  answer showed it. `smoking-pi doctor --live` adds `monitor-radio`, which
  reads the day's Wi-Fi samples with the alerter's own deaf definition
  (`common/microcuts.py`), fails when the radio is deaf now and warns on
  deaf runs of at least one SmokePing step (naming `smoking-pi watchdog
  enable` when the watchdog is off), and `targets-in-tsdb`, which fails
  when a target fresh in the RRDs has no InfluxDB point within three of
  its steps plus ten minutes (a warning just after SmokePing or InfluxDB
  started, as `upgrade` runs the doctor). Both read InfluxDB and skip, saying so, on Basic, Standard and
  ClickHouse; `docs/doctor.md` now has a table of what each live check
  covers per edition and backend. Run over the reference Pi's past week
  of samples, its deaf definition and run rule report exactly the two
  October hangs; both checks are green there today; on the staging Pi,
  which samples every 30 s and has single quiet samples every few minutes,
  it does not mistake those for a hang.

### Fixed

- **Dependabot proposed Python 3.14 for the inference image (#384).**
  The image is on 3.13 because `ruptures`, which Jitterbug requires,
  publishes no cp314 wheels (checked on PyPI 2026-10-18: 1.1.10 ships
  cp39-cp313 for aarch64 and x86_64); merged, the build would have failed, or
  compiled from source on either architecture. The inference directory now has its own
  Dependabot block that ignores `python` >= 3.14, with a comment saying when
  to lift it; the shared block now lists the other directories explicitly.
- **The digest no longer blames a destination for loss the monitor itself
  caused.** v2.42.0 subtracted the monitor's own minutes (its deaf radio,
  its DNS) from each target's mean loss as a share of the whole window:
  5 h 40 min of 24 h explained 23.6 points of anyone's mean. A target that
  existed for only part of the window was not covered by that. Replaying
  the 2026-10-13 digest on the reference Pi, it still opened 🔴 "problems
  worth a look" with five `Ocean_Reef_*` Netflix targets in red at 70.4 %
  mean loss: renamed the day before (#310), most of the points they had
  fell inside the deaf span. Each target now also gets
  `avg_loss_pct_measurable` and `loss_events_measurable`, its loss with
  the points inside the monitor's incident spans left out (None when it
  has no point outside them), and the digest lights red on that figure
  alone, showing it beside the mean ("70.4% loss (0.0% while it could)").
  The same replay now opens 🟡 "the monitor itself could not measure for
  5 h 40 min" with no target in red. The AI report gets both figures and
  is told to judge targets by the measurable one, and the 30 targets it
  sees are the worst by that figure, not by a mean the radio inflated.
  The span points are read by one pushed-down range per span, so the
  queries read only those minutes: 0.19 s of the 14 s a week's digest
  takes on the reference Pi.

## [2.42.0] — 2026-10-18

Every reader of loss now tells the same story. The web admin's chat
answers `get_loss_events` with the MCP server's own code (widespread runs,
episodes and the reading of a total loss, not the 200 newest raw points);
the daily digest and the AI report name the window's incidents as the
alerts and the assistants classify them, instead of summarizing mean loss
per target; the MCP server's widespread runs keep the alerter's 30 % loss
floor; the digest reads `HIGH_LOSS_PCT`, `WIFI_WEAK_SAMPLES` and leaves the
ISP gateway out of its destinations; and the doctor checks that the
dashboards' loss-event, microcut and weak Wi-Fi literals match `common/`.
Nothing is measured differently; the audit behind it is summarized in PR #387.

### Fixed

- **The web admin's chat reads loss as the MCP server does.** Its
  `get_loss_events` was a copy of an early version of the MCP tool: the
  200 newest loss points and nothing else — no `widespread` runs, no
  reading of a total loss (this host's uplink, the Internet, its own DNS),
  no per-target episodes, no background count, no breadth-only voters. The
  model assembled a story from raw points, or never saw it: asked about
  the last week on the reference Pi on 2026-10-18, the chat's 200 points
  were all from 2026-10-12 onward and held nothing of the 255-minute
  outage of 2026-10-11, which the MCP answer led with (`uplink_down`,
  247 of 247 targets). Both now call `common/loss_events.py`; the MCP
  server adds its links and uplink changes on top. Asked the same week,
  the chat now gets the same two widespread runs and 68 episodes.
- **The daily digest and the AI report name the incidents the alerts
  name.** Both summarized per-target mean loss and nothing else, while the
  alerts, `diagnose_loss`, the incident history and the chat classify
  every incident with the shared diagnosis. What that cost on the
  reference Pi: the digest of 2026-10-13 would have opened *problems worth
  a look* with 24 of 29 destinations at 20–70 % loss — a day the monitor's
  own radio heard nothing for 5 h 40 min (`local_wifi`, high confidence);
  the digest of 2026-10-16 said *all clear, all 19 targets clean* (worst
  mean 0.17 %) the day after a ten-minute cut of the line between the
  house and the ISP (`local_link`). `common/aggregates.collect` now hands
  on the window's incidents from `common/diagnosis_query` (probe misses
  counted, not listed). The digest lists them, says when the monitor
  itself could not measure — and puts that share of the window down to the
  monitor, not to the targets, before lighting any of them red (five deaf
  minutes do not excuse a destination lost all day) — and says when the
  incidents could not be read instead of reporting none. A `min_loss_pct`
  given to `get_loss_events` lowers the event bar, not the widespread one:
  widespread is the alerter's rule whatever the caller asks. The AI
  report gets the same list ahead of the per-target
  numbers, with the instruction to cite those causes.
- **"Widespread" means one thing.** The alerter's rule counts a cycle as
  widespread when `WIDESPREAD_PCT` of the targets lost at least
  `WIDESPREAD_LOSS_PCT` (30) *and* had a loss event; the MCP server's
  `widespread` runs left the 30 % out, so an assistant could call a cycle
  of two lost pings everywhere widespread that no alert could have fired
  on (none in the reference Pi's last week, by the numbers above). The
  setting is read in `common/definitions.py` for both, and the MCP server
  and the web admin are given it in Compose; the minimum of three
  reporting targets, the 99.9 % "lost everything" and the three cycles of
  total loss come from there too instead of copies.
- **The digest's thresholds are the alerts'.** Its red light was a
  literal 20 while the alert and the digest's own chart read
  `HIGH_LOSS_PCT`; its Wi-Fi line turned yellow on one sample under
  `WIFI_WEAK_DBM` where the verdict and the diagnosis need
  `WIFI_WEAK_SAMPLES` (6) of them; and its target list counted the ISP
  gateway's ping target, whose rate-limit floor is evidence about the
  line, not a destination. All three now follow the shared definitions
  (`HIGH_LOSS_PCT` moved into `common/definitions.py`; the charts read it
  and `MICROCUT_LOSS_PCT` as floats through the shared functions, where a
  value like `20.5` silently fell back to the default).

### Added

- **Doctor: `dashboard-definitions-match`.** A dashboard cannot import
  Python, so the loss-event bar (1.5 pings' worth), the cut-window bar and
  its line (50 %) and the weak Wi-Fi step (-75 dBm) are literals in the
  panels. All twelve matched `common/` on 2026-10-18 and nothing checked
  it; retuning one in `common/` now fails the static checks until the
  panels follow. The audit behind these changes found the Connectivity &
  Quality panels' "cycles with loss on 2+ destinations" (any loss, a fixed
  count of two) deliberately different from widespread, and says so in
  their descriptions already; they are unchanged.

## [2.41.0] — 2026-10-18

`smoking-pi doctor --live` now loads every provisioned Grafana dashboard
through Grafana's API and runs its queries, read-only: a panel whose
query errors, a variable that offers no value, a link to a dashboard that
is not installed and a dashboard Grafana does not provision now fail the
doctor instead of sitting green, and panels that return nothing are
listed as a warning. Nothing is measured differently.

### Added

- **`smoking-pi doctor --live` runs every dashboard's queries.** A panel
  could fail on every load, and a dashboard could open blank, while every
  doctor check stayed green: the static checks read the dashboards as
  files and cannot see what only running a query shows. On 2026-10-15 a
  hand-run audit found the DNS Wizard's *Top services by presence* failing
  on production with a Flux runtime error (`pivot: specified row key
  column does not exist in table: cdn`) and the CPE dashboard opening
  empty because its variable picked an address no longer measured; both
  had been that way for days, and nobody would have known without the
  audit. The new live check `dashboard-queries-run` does what that audit
  did, read-only, through Grafana's API on the host: it resolves each
  provisioned dashboard's variables by running their queries, runs every
  panel and annotation query with the dashboard's default range, and
  **fails** on a query error, a variable that errors or offers no value, a
  `/d/<uid>` link to a dashboard the installed Grafana does not have, and a
  dashboard of the repository's tree that Grafana does not provision. It
  **warns** on a query with no answer in ten seconds, on its two-minute
  budget running out, and with the list of panels that returned no rows
  (often by design, so listed, not failed; panels in a collapsed row or
  with their own "no value" text are left out). Dashboards a person
  created are skipped and counted. Sequential, with the elapsed time in
  its summary: 254 queries over 11 dashboards in 17 s on the reference
  Pi, 38 s on the staging Pi. Basic and Standard, which have no Grafana,
  skip it; it needs the env file, so run it with `sudo`.

## [2.40.1] — 2026-10-17

Four dashboards say what they show, after a review of production on
2026-10-17. The CPE loss panel names its lines and draws the gateway's own
24-hour floor so a 20 % bar is not read as a fault; Side-by-Side says it is
one category at a time and where the DNS Wizard's targets are; Individual
Pings and DNS Resolvers put each target's loss beside its latency. Nothing
new is measured.

### Changed

- **CPE Microcut Detection says what its loss bars are.** The *CPE Packet
  Loss (%)* panel drew bars reaching 20–40 % all day over a healthy
  gateway, with a dashed line at 30 and another at 50 that nothing named,
  and the *Current Loss %* tile showed the newest window in green with no
  explanation. The truth sat in a panel description and in
  `docs/detection-reliability.md`: home gateways rate-limit ICMP, so the
  5 pps probe loses answers at the router in most 10 s windows and the
  reference gateway's daily p90 is 14–22 % with nothing wrong; a microcut
  is a window above `MICROCUT_LOSS_PCT` (50). What it cost: the maintainer
  read "40 % packet loss" on production on 2026-10-17 and worried, which is
  what the alerter's own definition exists to prevent. The panel is now
  titled *ICMP the gateway did not answer – loss per 10 s window (%)*; the
  two lines are series named in the legend, *cut window (> 50 %)* and
  *watch (≥ 30 %)*; and the gateway's own floor over the last 24 hours is
  drawn as a band from the median window (p50) to the 90th percentile
  (p90), computed in InfluxDB with `quantile` over the rows already written
  (no new probe, no extra traffic; p50 10 % / p90 16 % on the reference
  gateway on 2026-10-17). The *Current Loss %* tile describes the floor and
  colors below 30 % green, 30–50 % orange and above 50 % red, the cut
  definition's `>`, and the bars use the same three colors by value; the
  IPv6 twins in the collapsed row get the same. The loss series is named
  `loss` instead of carrying its three tags.
- **Side-by-Side says it is one category at a time.** With `topsites`
  selected the dashboard showed five panels and nothing said why only
  those; the maintainer asked. The category variable is now labeled
  *Category (one at a time)*, and its description and the dashboard's say
  which categories exist (topsites, netflix as the five OCA slots, custom),
  that the DNS Wizard's targets are not offered here (well over a hundred
  panels) and where they are (the DNS Wizard dashboard and Target Detail);
  the Overview's *Where to look next* entry carries the same hint. The
  ClickHouse twin gets the same text.
- **Individual Pings and DNS Resolvers show each target's latency and loss
  on one row.** Both dashboards repeated two panels independently over the
  `target` variable (two per row, or one column each), so with *All*
  selected the page was a block of latency panels followed, rows later, by
  a block of loss panels: a lossy quarter hour and the latency it came with
  were a screen apart. Each dashboard is now a Grafana row repeated per
  target, titled with the target, holding its latency panel (two thirds of
  the width, with the Unreachable and Lossy bars as before) and its loss
  panel beside it. Deep links with `var-target=<name>` still land on that
  one target's row. The ClickHouse twins get the same shape; the InfluxDB
  Individual Pings loss series is named *Loss* instead of its tags.

## [2.40.0] — 2026-10-17

Target Detail now says whether the page describes the present. A target
that stopped being measured, such as a Netflix appliance name retired by
the stable slots, had read as a live target with its last days' loss;
three tiles now give the age of the newest point, whether the target is
measured now (and, for a retired Netflix name, which live slot carries it),
and how long the monitor's own radio was deaf inside the range, and the
target dropdowns list what wrote a point in the last day. A Diagnostics
dashboard in a new *Advanced* folder answers whether every collector is
writing and which series stopped; the doctor reads the watchdog's writes;
Connectivity & Quality hands the inference pass timing to Diagnostics.

### Added

- **Diagnostics dashboard, in a new *Advanced* Grafana folder.** The
  roadmap's "Advanced / Diagnostics" item asked where the monitor's own
  health should live; nothing did. The pieces existed but were scattered or
  invisible: the inference module's pass history sat inside Connectivity &
  Quality, the budget report's `complete` / `unpriced` / `over` flags had
  been written since v2.16.0 and charted nowhere, the watchdog's
  `watchdog_event` records had no panel, and no dashboard said how old each
  collector's newest point was or which target series had stopped. A gap in
  the InfluxDB copy (the sixteen minutes v2.39.0 records the v2.38.2
  upgrade cost when the card filled) was visible only by noticing a
  chart's hole. The new
  `diagnostics-v1` dashboard answers those questions for a maintainer: the
  age of every collector's newest point against its cadence, the targets
  silent for longer than two steps plus a minute (the web admin's rule, from
  the `step` field each point carries), points written per quarter hour by
  layer, and what the inference module, the watchdog, the alerter and the
  budget exporter record about themselves. Two text panels list why a panel
  can be empty on this hardware and where the health that never reaches
  InfluxDB lives (the doctor, the web admin's cards, `smoking-pi dns status`,
  `smoking-pi watchdog status`). The Overview's index links it;
  `docs/diagnostics.md` describes it. ClickHouse has no version of it, as
  decided for every non-per-target dashboard on 2026-10-15.
- **The doctor reads the watchdog's InfluxDB writes.** The checks
  `panel-measurements-written` and `panel-tags-written` learned the
  exporters' vocabulary from Python `Point()` calls only;
  `cli/lib/watchdog.sh` writes `watchdog_event` as line protocol through
  the database container's CLI, so any panel over it would have failed the
  doctor. The vocabulary now includes a shell writer's `printf`
  line-protocol literals (a measurement with at least one tag, then a
  field).

### Changed

- **Connectivity & Quality no longer carries the inference module's pass
  timing.** The *Inference passes* panel was the only panel there about a
  service rather than the connection; it moved to the Diagnostics
  dashboard unchanged.

### Fixed

- **The doctor failed every package install on `watchdog_event`.** The
  check that reads the watchdog's line-protocol writes (above) looked for
  `cli/lib/watchdog.sh` under the repository root; a package install has
  `/opt/smoking-pi` without `cli/` and the command at `/usr/lib/smoking-pi`,
  so on every host of the v2.40.0-rc.1 Release run the doctor reported the
  Diagnostics dashboard's `watchdog_event` panel as a measurement nothing
  writes, and the candidate was refused. `smoking-pi doctor` now tells the
  doctor where the command runs from (`SMOKING_PI_CLI_DIR`), and the doctor
  falls back to the packaged path; a clone's own `cli/` still wins.
- **A target that stopped being measured looked like a live, lossy one.**
  Target Detail opened on `Ocean_Reef_Club_Airport_ATL_c344_5`, one of the
  Netflix appliance names retired on 2026-10-12 when the
  `Netflix_<slot>_<site>` targets replaced them, showed 18.5 % loss and a
  56 ms median over the last seven days as if the target were still being
  measured. Nothing said that its data ended on 2026-10-11, that most of
  that loss fell inside the eleven hours this Pi's radio was deaf that day,
  or that the appliances are measured under the slot names now: a reader was
  told a destination was lossy when the monitor had stopped looking at it
  five days earlier. The target dropdown had offered every name with a point
  in the last seven days; it now offers those with one in the last 24 hours
  (the ClickHouse dashboards' dropdowns, which had no time window at all,
  get the same day), and a link or bookmark can still bring any name. Three
  tiles now open the dashboard. *Time since the last measurement* is the age
  of the newest point in the range, green within 11 minutes (two steps at
  the default step), orange within a day, red beyond, or "no data in this range". *Is it measured
  now?* says "measured in the last 24 h", or when the target stopped and, for a retired
  Netflix appliance name, which live slot carries its number and since when
  (from `target_host`; by slot number, not by address, because the appliance
  behind a slot changes daily), with a link to that slot and to the Netflix
  side-by-side. *Monitor deaf in range* is the time in which this
  Pi's radio heard nothing (the alerter's `local_wifi` incidents and the
  inference module's `monitor_deaf` periods, the shortest of those starting
  together), so the loss tiles, which include those minutes, can be read for
  what they are. The loss computation itself is unchanged.

## [2.39.0] — 2026-10-16

An upgrade no longer fills the card. `smoking-pi upgrade` had kept every
previous release's images, and on the reference Pi the v2.38.2 upgrade
landed on a card already holding 513 of them: PostgreSQL and Grafana
went down and InfluxDB lost sixteen minutes. Once the new
release is up and stays up, the upgrade now removes the images of other
releases, one at a time and never one a container still uses; the
package-install test checks that it does. The doctor gains a `disk-free`
live check so a filling card is a warning before it is an outage.

### Fixed

- **`smoking-pi upgrade` left every previous release's images on disk.**
  Each upgrade pulled a new set of thirteen images and removed none of
  the old, so a host that had upgraded since 2.13.8 carried every release
  in between: on 2026-10-16 the reference Pi's upgrade to v2.38.2 pulled
  onto a 117 GB card already holding 513 Smoking Pi images (92 GB), and
  the card filled. PostgreSQL crash-looped on `postmaster.pid`, Grafana's
  SQLite reported the disk full, and InfluxDB refused every WAL write
  ("no space left") until its container was restarted, even after space
  was freed: sixteen minutes of measurements lost, and the RRDs kept
  writing only because their files already existed. `upgrade` now removes
  the images of other releases once every container of the new release is
  running, and still running ten seconds later: each
  `ghcr.io/estcarisimo/smoking-pi/*` image whose tag is not the version
  just brought up, one `docker image rm` at a time (never
  `docker image prune -a`, which would take cloudflared and whatever else
  the daemon holds), never an image a container still references, and
  nothing at all while a container is not running at either look, so a
  failed upgrade keeps the previous release to go back to. It says how
  many it removed and up to how much space. The release's package-install
  test asserts that the previous release's images are gone after the
  upgrade.

### Added

- **Doctor `disk-free` live check.** Nothing had said the card was
  filling. The doctor now reads the free space of the filesystem under
  Docker's data root: a warning under 10 % or 5 GB free, a failure under
  2 % or 1 GB, the mount and the free space in the message, and, when the
  registry's images carry tags no container runs, the note that
  `smoking-pi upgrade` removes them.

## [2.38.2] — 2026-10-16

The DNS & Services dashboards, audited panel by panel. A single lossy
window no longer flattens the latency panel it marks: the overlay bars
live on a hidden scale of their own, so the latency axis keeps its range.
The ClickHouse DNS Resolvers and Side by Side dashboards drew the mean
twice and never the median; they lead with the median again, as their
InfluxDB twins do. The DNS Wizard gets a log scale, HTTP-sized thresholds,
links to Target Detail and ordered legends, and the seed's `uba_ar`
target says what it measures: Imperva's CDN edge, not Buenos Aires.

### Fixed

- **One lossy window flattened the latency panel it marked.** The
  *Unreachable* and *Lossy* overlays on every per-target latency panel
  (DNS Resolvers, Individual Pings, Side by Side and the ClickHouse
  twins) were bars 500 ms tall on the latency axis, so a single marked
  15-minute window stretched a panel of 10–50 ms lines to 500 ms and
  flattened exactly the period someone opened it to look at. The bars now
  sit on a hidden 0–1 scale of their own: they still fill the panel's
  height, and the latency axis keeps the latency's range.
- **The ClickHouse DNS Resolvers dashboard drew the mean twice and never
  the median.** Its solid line and its dashed *Mean* line were both
  `avg(avg_latency)`, so the one line the InfluxDB version leads with was
  missing although `median_latency` is in the table. The solid line is
  the median again. An unused *Status* variable (a PostgreSQL query no
  panel read) and a hardcoded list of the three seeded resolvers for
  *All* are gone.
- **The ClickHouse Side by Side dashboard drew the mean twice and never
  the median, too.** The same bug as DNS Resolvers, in its Targets twin:
  the solid line and the dashed *mean* line were both `avg(avg_latency)`,
  so comparing the targets of a category in ClickHouse mode showed no
  median, the line the InfluxDB Side by Side leads with, and a single
  spiky ping pulled the only line drawn. The solid line is now
  `avg(median_latency)`, labeled *Median*, and the dashed one *Mean*. The ClickHouse Individual
  Pings dashboard already drew average and median once each.
- **DNS dashboards, the smaller things a panel-by-panel audit found.** The
  packet-loss legend read `loss {category="dns", probe_type="dns",
  target=…}` and is now *Loss*, with a description. On DNS Wizard: the
  per-protocol median is on a logarithmic scale (ICMP and TCP, about
  9 ms, lay flat at zero under HTTP's 130 ms); the latest-median table
  colors HTTP at 300 ms / 1 s instead of ICMP's 80 / 200 ms, which had
  painted every HTTP cell orange; a service in either table links to its
  Target Detail; the legends run top 5, 10, 20 and services, CDNs,
  networks instead of alphabetically; three stat titles no longer cut off
  at 1600 px; and the header says *summarizes*.

- **The seed's `uba_ar` target is labeled for what it measures.** Its
  host, `uba.ar`, resolves to Imperva's anycast CDN, so the probe reaches
  the nearest Imperva edge (a few milliseconds away) and never Buenos
  Aires; next to the `UBA` name-server target (ns1.uba.ar, a far longer
  path) the two rows looked like the same place with contradictory
  numbers, and the gap read as a problem on the way to Buenos Aires that
  was never there. The title now says *uba.ar (Imperva CDN edge)* and the
  template explains why. The target name, and so its RRD history, is
  unchanged; an existing install keeps its own title until it is edited.

## [2.38.1] — 2026-10-15

The red bar on the per-target latency panels says "unreachable" only when
nothing answered. It had been marking 5 % loss as "Downtime", drawing one
mark as a block reaching the next one, hours wide over a panel that was
answering all along, and in the palette's blue because its color never
applied. It is now two honest overlays, each bar one 15-minute window
wide: red for windows in which every ping was lost, lighter orange for
windows that lost 1.5 pings' worth, the alerter's loss-event rule, under
that name; a window with no measurements stays blank.

### Fixed

- **"Downtime" on the latency panels meant 5% loss, and a bar could span
  hours of good measurements.** The red overlay on every per-target
  latency panel (Individual Pings, Side by Side, DNS Resolvers and their
  ClickHouse twins) was named *Downtime* and marked a 15-minute window
  that lost 1.5 pings' worth, the alerter's *loss event* rule: a window
  with 28 answered pings of 30 was "down". Worse, the series held only
  the marked windows, and Grafana sizes bars by the spacing of the points
  it is given, so one marked window at 12:45 and the next at 14:45 were
  drawn as a single block from one to the other, and a lone mark was as
  wide as Grafana's guess. On the reference installation on 2026-10-15
  Amazon, Facebook and the ISP gateway each showed a solid "Downtime"
  block over nearly three hours of a panel that answered all along
  (one lossy window each, at the 12:45 spike). A reader was told the
  connection was down while the chart beside the block showed it
  answering, and the one word that should mean something on a
  monitoring panel meant nothing. The overlay is now two: red
  **Unreachable (nothing answered)** marks a window only when every
  cycle in it lost every ping, and a lighter orange **Lossy (≥ 1.5
  pings lost in 15 min)** keeps the loss-event rule under its own name.
  Both series are dense (a 0 for every clean window, which draws
  nothing), so each bar is one window wide and sits over the window it
  judges; a window with no measurements at all is left blank, because
  no data is not nothing answered; both stay out of the tooltip. The
  red is now red: the overlay's color was written as two property ids
  Grafana does not know (`color.mode`, `color.fixedColor`), so every
  "Downtime" bar since 2026-09-24 was drawn in the palette's blue; the
  override now sets `color` the way every working override does. Verified
  read-only against production through Grafana's API: over 2026-10-15
  16:00–22:00 UTC, Amazon, Facebook and the gateway go from 1 "Downtime"
  mark each (Apple 2) to 0 unreachable and 1 lossy (Apple 2); over the
  Pi's deaf-radio spell of 2026-10-12 15:00–21:00 UTC every target goes
  from 23 marks to 22 unreachable and 23 lossy (the first window
  answered something before the radio hung). The gateway's permanent
  ICMP floor marks nothing unreachable. The ClickHouse dashboards apply
  the same two rules from `packet_loss` and `packets_sent`.
  [Alerting](alerting.md) describes the overlays as they are.

## [2.38.0] — 2026-10-15

Connectivity & Quality now shows when latency rises, not only when
packets are lost, and lets you shade a window of your own choice on every
day of the range. Two heatmaps join the "When does it happen?" row, ICMP
and HTTP median latency as a share above each day's quietest hour; a
"Selected window" box shades its band through an annotation named for
what it is, your selection, never a detected pattern. The ISP gateway's
loss and the Wi-Fi signal were evaluated for a heatmap and left out.

### Added

- **Connectivity & Quality shows when latency rises, and shades a window
  of your choice.** The "When does it happen?" row showed loss by day and
  hour of day and nothing else, so the nightly latency rise that goes with
  the loss band on the reference installation (about 02:00–05:00, +1 to
  +2 ms on a 19 ms floor, 15 of 28 days) was invisible, and the HTTP
  layer's daily swing (+17 % at night over the midday floor, every day
  since the HEAD probes) was not shown anywhere; and a band you had
  spotted in a heatmap could not be followed on the time axis without
  reading hours off the chart. Two heatmaps join the row, ICMP median
  latency and HTTP median latency, each hour as a share above that day's
  quietest hour, so a 19 ms and a 40 ms connection read alike and a step
  between days does not drown the band (the latency chart keeps the
  step). A **Selected window** box (`HH:MM-HH:MM`, dashboard time zone,
  crossing midnight allowed) shades that band on every day of the range
  through an annotation named *Your selected window*: your choice, not a
  detected pattern, and the docs say a detected band would be a separate,
  labeled annotation set. The ISP gateway's loss and the Wi-Fi signal were
  evaluated and left out: on the reference installation the gateway (two
  weeks of data) lost pings only in the hours when every destination did,
  with an hour-of-day mean under 0.4 % otherwise, and the Wi-Fi signal's
  hourly mean moved by 0.6 dB over the day.

## [2.37.0] — 2026-10-15

Connectivity & Quality explains its own events. A red dot on the top
edge of the loss chart marks each window a category went above the
10 % scale, with the real value in the tooltip; the alerter's diagnosed
incidents are drawn as annotations whose hover text gives the diagnosis
and a link that zooms the dashboard to the incident; and a bar on the
episodes timeline opens Target Detail for that target over the same
range. The latency chart keeps its full scale on purpose.

### Added

- **Events explain themselves on Connectivity & Quality, and off-scale
  loss is marked.** The loss chart is capped at 10 % so everyday loss is
  readable, which left a 100 % cut as a line running off the top with
  nothing saying so; a red dot on the top edge now marks each window where
  a category went above the scale, with the real value in the tooltip. The
  alerter's diagnosed loss incidents are drawn as annotations: hovering one
  gives the diagnosis (this host's Wi-Fi, the line, upstream, the
  destinations), its confidence and length, and a link that zooms the
  dashboard to the incident with a quarter of its length on either side.
  A bar on the detected-episodes timeline links to Target Detail for that
  target over the same range, where the period is shaded. The latency
  chart keeps its full scale on purpose: its spikes are the signal.

## [2.36.0] — 2026-10-15

The dashboards now lead from an aggregate to the series behind it. On
Connectivity & Quality, a category line opens that category's targets
side by side over the same range (the gateway's and the resolvers' lines
open their own dashboards), a new table lists the destinations behind
the loss in the range with each row opening Target Detail or Individual
Pings, and every side-by-side panel opens Target Detail for its target.
No new service: besides the links, the only new work is the table's
query, 0.2 s over a week of the reference Pi's history.

### Added

- **From an aggregate to the targets behind it, inside Grafana.** Connectivity
  & Quality said "loss rose on the top sites at 21:00", and finding which
  destinations carried it meant opening the side-by-side dashboard, picking
  the category by hand, then re-entering the time range, and then doing the
  same again on Target Detail: three dashboards, two variables, the range
  typed twice. Each category line now opens that category's targets side by
  side over the same range (the ISP gateway's line opens its cut dashboard,
  the resolvers' line theirs); a new table, *Targets behind the loss*, lists
  every destination that lost packets in the range with its mean loss, the
  cycles with loss and the worst cycle, most loss first, each row opening
  Target Detail or Individual Pings; and every side-by-side panel opens
  Target Detail for its target. One rule, in the docs: click a line for
  its category's targets, click a target for every layer of it. The table is
  three storage-engine aggregates per target (0.2 s for seven days of the
  reference Pi's 247 targets); the category lines keep their tag as a label
  instead of renaming it, so the link carries `topsites`, not "Top sites".

## [2.35.0] — 2026-10-15

The first read of a week of the experimental detectors on the reference
Pi found that nearly half of the "loss degradation" periods were the Pi's
own radio hangs, shaded red as if the destinations had failed, and that
a two-day congestion period had straddled two of those hangs without
saying so. The detectors are unchanged: each period now says whether the
monitor itself was deaf and how much of its span had data, the dashboards
shade a deaf period gray and leave it out of the count, and every pass
leaves a record of what it cost.

### Added

- **A degradation period says when the monitor itself was deaf.** The first
  read of the reference Pi's inference output found that 22 of its 51 loss
  degradation periods were the two radio hangs: 100 % loss on every
  destination for hours, which BCP on loss duly reports as a degradation of
  each target — shaded red on Target Detail as if the destinations had
  failed. The detector is unchanged (the published method, on the raw loss
  series); each period now carries `class` (`loss` or `monitor_deaf`, by
  the alerter's own deaf test from `common/microcuts.py`, when at least
  half of its bins were deaf) and `deaf_share`. Target Detail shades deaf
  periods gray under a *Monitor deaf (experimental)* annotation that says
  the loss is this Pi's; Connectivity & Quality draws them gray and leaves
  them out of the degradation count.
- **Every period carries its data coverage.** `coverage` is the share of
  the period's 15-minute bins that had data; the annotation says "data in
  40 % of the span". A 49-hour congestion period of a 3.7 ms jump that
  straddled the two hangs had looked like a solid span.
- **A per-pass history.** Each pass writes one `inference_pass` point
  (seconds, targets, errors, periods found); Connectivity & Quality shows
  seconds and failures per pass. `status.json` kept only the last pass and
  the container log vanished at every upgrade, so "what does a pass cost
  over time" had no answer.

## [2.34.0] — 2026-10-15

The dashboards, after the audit of every one of them. Fifteen became ten:
the three oldest, which drew a subset of newer ones, are gone, and the
three per-category side-by-side copies are one dashboard with a category
switch. Four folders with names instead of eleven directory slugs. The
alert and assistant links land on the per-ping dashboard; the digest's on
the Overview. Panels this hardware cannot fill — the Wi-Fi driver's noise
and modulation, IPv6 on a host without it — are folded away instead of
sitting blank at the top. `smoking-pi upgrade` removes the retired
dashboards and the emptied folders from an installed Grafana, since
provisioning never deletes, and the doctor now checks that every dashboard
a link names is provisioned.

### Changed

- **Grafana's folders have names, and there are four of them.** The eleven
  provisioned folders were the repository's directory names (`home`, `cpe`,
  `quality`, `side-by-side-pings`…), one dashboard each. Now: *Connection*
  (the Overview, Connectivity & Quality), *Targets* (Target Detail, Individual
  Pings, Side-by-Side, HTTP by Version), *Network* (Wi-Fi Link, CPE Microcut
  Detection) and *DNS* (DNS Resolvers, DNS Wizard); the ClickHouse tree
  follows. (*Connection* rather than *Home*: Grafana matches folder titles
  without regard to case, so a new name must not be an old slug in other
  letters — *Home* would have reused `home`, lowercase and all.) Dashboard uids do not change, so no link does. Provisioning moves
  a dashboard and leaves its old folder behind, empty: `smoking-pi upgrade`
  deletes those folders, and the four an older scheme left on the reference
  Pi, **only when they are empty** — anything inside, a dashboard of your
  own or the ClickHouse tree's *SmokePing* parent, keeps the folder.
- **One side-by-side dashboard instead of three.** *Top Sites*, *Netflix OCA*
  and *Custom Targets – Side-by-Side* were the same JSON with a different
  category filter. *Side-by-Side – every target of one category*
  (`side-by-side-v1`, ClickHouse `side-by-side-clickhouse-v1`) picks the
  category with a variable; the `compare_with_peers` link and the web
  admin's category links carry it (`var-category=topsites`, the InfluxDB
  tag, not the database's `top_sites`). The three old dashboards and their
  ClickHouse twins are retired and deleted on upgrade like the others.
- **Panels this hardware cannot fill are folded away.** The Wi-Fi dashboard's
  noise floor/SNR, modulation and channel-busy panels were blank on the
  reference Pi (`brcmfmac` reports none of it) and sat in the top rows; they
  are now one collapsed row at the bottom, and the panels that do have data
  take the full width. The CPE dashboard's five IPv6 panels, blank on a host
  without global IPv6, are a collapsed row as well, and the IPv4 panels take
  the width.
- **The three oldest dashboards are retired: *SmokePing Latency & Loss –
  Percentiles / Mean*, *SmokePing DNS Resolvers* and *SmokePing Latency –
  Side-by-Side*, with their ClickHouse twins.** The dashboards audit compared
  them query by query: the first two draw a strict subset of *Individual
  Pings* and *DNS Resolvers – Side-by-Side* (same six queries, minus the
  per-ping min/max and the pings lost), and the third is *Top Sites –
  Side-by-Side* without its category filter. Yet every alert and assistant
  answer linked the old pair as its `graph` while the web admin opened the
  new one — two answers to "show me this target". The `graph` link now opens
  *Individual Pings* (or *DNS Resolvers – Side-by-Side* for a resolver), the
  `per_ping_detail` link is gone because it would be the same URL, and the
  digest's `grafana_overview` opens the Overview rather than a per-target
  dashboard. `smoking-pi upgrade` deletes the retired dashboards from an
  installed Grafana: provisioning never deletes (`disableDeletion: true`),
  so without that step they would stay in the list pointing at the old
  generation. ClickHouse mode keeps six per-target dashboards and is
  documented as such.
- **The doctor checks that every dashboard a link names is provisioned**
  (`links-name-provisioned-dashboards`): a uid in `common/links.py` or the
  web admin's category map that no dashboard carries would open Grafana's
  "not found" page from every alert.

## [2.33.4] — 2026-10-15

Three dashboard fixes from the dashboards audit (roadmap front 2): the
DNS Wizard's service table renders again where names are exported, the
CPE dashboard opens on the gateway that is being measured instead of a
dead one, and the HTTP dashboard's TCP panel has its own row.

### Fixed

- **The DNS Wizard's *Top services by presence* table renders again where
  names are exported.** A service with no CDN, network or network name was
  written with an empty tag, which InfluxDB drops, so the table's pivot on
  `cdn`/`asn`/`org` failed with *specified row key column does not exist* on
  the reference Pi — the one install with `DNS_EXPORT_NAMES=1`; staging, whose
  observer sees no names, never showed it. The query now fills the missing
  columns before pivoting, and the exporter writes only the tags a service
  has instead of an empty value that was never stored.
- **The CPE dashboard opens on the live gateway.** Its `cpe` variable listed
  every gateway ever measured, alphabetically, and Grafana picked the first:
  on the reference Pi a gateway last seen a week earlier, so every panel was
  blank unless you arrived through a link. The variable now lists the
  gateways written in the last 7 days, most recent first.
- **The HTTP-by-version dashboard's TCP panel sits in its own row** (both
  backends) instead of floating under the repeated per-site panels.

(Found by the dashboards audit of 2026-10-15, roadmap front 2.)

## [2.33.3] — 2026-10-15

v2.33.2's count of the HTTP, TCP and DNS wizard targets was right but
slow over a week: on the reference Pi, InfluxDB checked every lossy point
against the list of 224 targets one name at a time, and a week held about
45 000 of them. InfluxDB now picks the targets itself before it reads
their points. Same answers; on the reference Pi, the count for a week
takes 1.7 s of InfluxDB's time instead of 13.6 s, and a week's whole
diagnosis 9.9 s instead of 22.9 s (8.4 s before those targets were
counted).

### Fixed

- **A week's diagnosis is fast again on the reference Pi.** v2.33.2 picked
  the voting HTTP, TCP and DNS wizard targets with `contains(set: [...])`,
  which no part of InfluxDB pushes down: Flux scanned the 224 names for
  every lossy point. A week on the reference Pi held a 16-hour outage of
  all of them, about 45 000 such points, so the four per-cycle counts took
  13 s of InfluxDB's time; called in the MCP server, `diagnose_loss(168)`
  took 22.5 s against 8.4 s before the breadth, and `get_loss_events(168)`
  20.7 s. The scan grows with the lossy points, so a longer outage would
  have cost more. The non-voters were not the cost (6 targets, a
  twentieth of the points): the scan was. The targets are now a balanced
  `or` tree of tag comparisons, which the storage engine applies, and
  which nests as deep as log2 of the names (8 for 224, 11 for 2 000), so
  it never meets "nested too deep". Measured read-only on the reference
  Pi, same answers cycle for cycle: the breadth's InfluxDB time for a
  week went from 13.6 s to 1.7 s, the week's whole diagnosis from 22.9 s
  to 9.9 s; a day stays at 1.2 s. A Flux `dict` lookup, measured too, was
  slower than the scan (13.8 s).

## [2.33.2] — 2026-10-15

v2.33.1's wider count worked on the staging Pi but not on the reference
Pi. With its 224 HTTP, TCP and DNS wizard targets, InfluxDB refused one of
the queries, and a week of their lost packets was more than the query was
allowed to return. Nothing failed: the answers fell back to the pinged
sites and resolvers, as designed, so the diagnosis still counted fewer
targets than the alert there. InfluxDB now counts those targets per cycle
itself. Measured on the reference Pi, that takes about 3 s for a day and
16 s for a week.

### Fixed

- **The breadth is counted on the reference Pi, not only on staging.**
  v2.33.1 counted the HTTP, TCP and DNS wizard targets in the diagnosis
  and `get_loss_events` on the staging Pi (63 of them) but on none of
  production's 224: InfluxDB refused the query that picks the voters'
  lossy points, whose 224 `or` comparisons were "nested too deep", and
  over a week those points were more than the 20 000-row bound anyway.
  Both fell back as designed, so nothing failed, but `breadth_only` stayed
  false and the counts stayed at the pinged targets. The voters are now a
  set (`contains`), which is flat at any size, and InfluxDB counts the
  lossy and the lost voters per cycle, each target once however many
  points it has there (one query per distinct event bar), so what comes
  back no longer grows with an outage and there is no row bound; what
  InfluxDB reads still grows with the lossy points. Measured on the
  reference Pi, read-only: about 3 s for a day and 16 s for a week of 224
  targets.

## [2.33.1] — 2026-10-14

An alert and the diagnosis attached to it now count the same targets.
v2.33.0 counted the HTTP, TCP and DNS wizard targets when judging whether
a loss was everywhere, but the diagnosis under the alert, `diagnose_loss`,
`get_loss_events` and the incident history still counted the pinged sites
and resolvers alone. An alert could say "180 of 243" over a diagnosis that
said "19 of 19", and a cut of the web services alone could raise an outage
alert with no incident to explain it. They now count the same way, and the
extra targets are only ever counts, never named on their own. Counting
them over a day or a week costs eight small InfluxDB queries, about 0.3 s
each on a Raspberry Pi; longer windows count the pinged sites and
resolvers alone.

### Fixed

- **An alert and its diagnosis count the same destinations.** v2.33.0 let
  the HTTP, TCP and DNS wizard targets vote on the alerter's widespread
  rule and verdict, but the diagnosis attached to the alert, the incident
  history and the MCP `diagnose_loss` and `get_loss_events` still counted
  the ICMP and resolver targets alone: an alert could read "180 of 243
  targets lost packets" over a diagnosis that said "19 of 19", and a cut
  of the web services alone, every ping answering, was an `outage` alert
  with no incident to explain it. The breadth-only query and voting moved
  to `common/breadth.py`, and every reader of breadth counts voters by the
  same rule (over its own window: the diagnosis votes over hours, the
  alerter over 20 minutes): per cycle in the diagnosis's shares, in
  `get_loss_events`' `widespread` runs, in `targets_reporting`. Over hours
  to a week only queries InfluxDB answers from its storage engine are
  asked (points per target, points above the event bar, each target's
  first and last point, the voters' lossy points), and the cycles are
  counted in Python (a target reports in every cycle from its first point
  to its last, so a gap of a few cycles counts as reporting): eight
  queries of about 0.3 s each for a week on the staging Pi. A first
  version grouped every point by time and reduced it in Flux; it took 108
  s for a week of 63 targets on staging, a 30-day `get_loss_events` froze
  that Pi until its hardware watchdog reset it, and it was never released:
  as first merged (after v2.33.0, so in no release) it did not even
  compile in InfluxDB (a doubled brace the mocked tests could not see), so
  every reader quietly fell back to the old count. A test now checks every
  breadth query for balanced brackets and for the shapes that cost that.
  The breadth is counted over a week at most: a longer `get_loss_events`
  counts the ICMP and resolver targets alone, and its new `breadth_only`
  field is false, as is `diagnose_loss`'s `coverage.breadth_only` whenever
  the breadth was not counted (a failed query, more than 20 000 lossy
  points, no target voting). A failed breadth query leaves the breadth as
  it was. Breadth-only targets stay counts: an incident's `targets`, the
  `probe_miss` and `destination` classes and every per-target list are the
  ICMP and resolver targets, scattered breadth-only loss widens a count
  only in a broad cycle, and a loss of breadth-only targets alone is never
  "this host's DNS". The answering guide says that a broad incident's
  `targets` is not everything that lost packets.

## [2.33.0] — 2026-10-14

Alerts can now stay quiet at night, and a dead host is no longer silence.
Quiet hours (`smoking-pi alerts --quiet 23:00-07:00`) hold alerts
overnight and send them as one silent message when they end. A total loss
and a monitor that has gone blind still go out at once. A dead man's
switch (`smoking-pi alerts --deadman`) checks in with an outside service
such as healthchecks.io, which tells you when this host stops checking in,
even when the whole house is down. Whether a loss is "everywhere" is now
judged on the HTTP, TCP and DNS wizard targets too, not only the ICMP and
resolver ones, so a few sites losing packets no longer reads as the link.
None of those targets raises an alert of its own, and the verdict's
"DNS only" and this host's own DNS failure (`monitor_dns`) are judged on
the same wider set. Quiet hours and the switch are off until you turn
them on. `smoking-pi upgrade` refreshes an installed OpenClaw skill that
the new release left stale, so the assistant answers from the current
guide.

### Added

- **Quiet hours: `smoking-pi alerts --quiet 23:00-07:00`.** Every alert
  went out the moment it was decided, so a lossy hour at 3 a.m. buzzed the
  phone as often as it would at noon. The only ways to stop it were muting
  targets one by one, which risks missing the next real outage, or
  turning delivery off. A phone that buzzes at night for a slow site gets
  muted, and then it stays silent for the outage too. Between `QUIET_FROM`
  and `QUIET_UNTIL` (in the digest's zone), per-target alerts, brief
  outages, microcuts, recoveries and watchdog events are now held. When
  quiet hours end they go out as one silent message: what is open now,
  then each incident once with when it started and whether it recovered
  (the outbox's summary, in local time). Some alerts are never held: a
  total loss (`uplink_down`, `internet_down`, `monitor_dns`), and the
  monitor going blind or deaf (`influx_unreadable`, `evaluation_failed`,
  `exporter_stale`, `measurement_stale`). MCP `list_alert_state` shows what
  is held, and until when. Off by default; `--quiet off` turns it off and
  sends what was held.

- **A dead man's switch: `smoking-pi alerts --deadman`.** Every alert came
  from the host being watched, so when that host hung, lost power or lost
  its SD card (all three have happened to the reference Pi), the alerts
  simply stopped. Silence read as "all well" until someone opened a
  dashboard. The alerter now checks in with an outside service
  (`ALERT_DEADMAN_URL`, a healthchecks.io ping URL or a self-hosted
  Healthchecks) after every evaluation, at most every 5 minutes. Once it
  has been unable to read InfluxDB for 5 minutes, it uses `URL/fail`
  instead, so an InfluxDB restart is not a page. The service alerts you when
  the check-ins stop, even when the whole house is down. The URL is a
  credential: read hidden or from stdin, never logged (the host is). The
  command prints the alerter's first check-in. Off by default.

### Changed

- **HTTP, TCP and DNS wizard targets count toward "widespread".** The
  alerter judged breadth on the ICMP and resolver targets alone: 19 of
  247 on the reference Pi. With that few, four sites losing packets in the
  same cycle were "the link" (an `outage` warning), and the verdict's
  "N of M destinations" described the same small set. The HTTP and TCP
  probes and the DNS wizard's adopted services now vote on breadth, in the
  rules and the verdict alike, and on nothing else: none of them raises
  a per-target alert. A breadth-only target counts in a window only when
  at least half of its points there were clean, so a CDN that drops ICMP
  (100% forever) or a probe that is lossy all the time never reads as part
  of an outage, and one on a slower step than the rules' bucket does not
  count at all. With hundreds of named targets, `monitor_dns` (this
  host's own DNS) now needs `WIDESPREAD_PCT` of them lost rather than
  every one: one reached by address would otherwise turn it into
  `internet_down`. The verdict's "DNS only" now means the resolvers, not
  the DNS wizard's services, whose category also starts with `dns`. It
  costs one more Flux query per cycle (loss, pings and step together, over
  the down window); a failed one leaves the breadth as it was, not the
  cycle without rules. `diagnose_loss`, the incident history and
  `get_loss_events` still count the ICMP and resolver targets only.

### Fixed

- **`smoking-pi upgrade` refreshes an installed OpenClaw skill.** The skill
  is the answering guide (`common/guide.py`), which changes in most
  releases, and nothing refreshed the installed copy: after v2.32.0 was
  installed on 2026-10-14, the skill was stale on both the production and
  the staging Pi, and the OpenClaw assistant went on answering from the
  previous guide until the installer was run by hand. Nothing said so: a
  stale skill answers fluently, just in the old shape. `upgrade` now
  compares the installed skill of the user who ran `sudo` with the
  release's and, when they differ, replaces it and reloads the gateway, as
  `smoking-pi openclaw` does in its step 5; then it says to start a new
  chat session. The copy it replaced is kept as `SKILL.md.bak`, and the
  output names it: a skill tuned in place is replaced too, and its
  tuning has to be applied to the new one before the next upgrade
  overwrites that `.bak`. A skill that was never installed is not
  installed, and a current one is left alone, so the gateway is not
  restarted on every upgrade. If the refresh fails, the upgrade still
  succeeds, shows what the installer said, and names the command that does
  it, `sudo smoking-pi openclaw`. `smoking-pi openclaw --check` now gives
  that command for a missing or stale skill too, instead of the
  installer's script path.

## [2.32.0] — 2026-10-13

The path from a measurement to a message on the phone was audited end to
end, and this release fixes what the audit found. One incident now reaches
the phone once, not as a burst, and OpenClaw no longer receives the same
alert several times. An alert that cannot be delivered is kept and sent
when the network is back, instead of being lost. Alerts no longer announce
"recovered" while a target is still down or silent, and an acknowledgment
ends when the incident recovers. The alerter says when it cannot see: an
unreadable InfluxDB, a measurement that stopped. It also reports what the
watchdog did. A total outage says where it is: this host's link, beyond
the house, or the Pi's own DNS. Because the ISP gateway no longer counts
as a destination, a stack with fewer than three other destinations no
longer raises total-outage alerts. "Widespread", "broad" and "weak Wi-Fi"
mean one thing in the alerts, the diagnosis and every assistant, and the
web admin's chat now answers from the same guide and diagnosis as the
MCP clients. Grafana's cut windows, gateway loss colors and ClickHouse
"unreachable" marks follow the alerts' rules too.

### Fixed

- **The web admin's chat answers like every other assistant.** Its
  system prompt was two sentences of its own, and it had no
  `diagnose_loss` or `get_incident_history`. It read raw loss events and
  attributed them itself, so it could still call the Pi's hung radio an
  outage, or a lost name lookup a dead line, after the MCP clients and
  the OpenClaw skill had stopped. That breaks the "one answering guide"
  rule, and the person asking in the page got the oldest answer. The chat
  now gets the shared guide (moved to `common/guide.py`, so both images
  can read it; `mcp-server` keeps a `guide` alias) after a short header:
  target changes need a confirmation, and tools it lacks point to
  Grafana. Both diagnosis tools answer through `common/incident_answers.py`,
  the MCP server's own code path. The compose file gives web-admin the
  same `WIDESPREAD_PCT`, `VERDICT_BROAD_PCT` and `WIFI_WEAK_*` thresholds.

- **Grafana reads cuts and loss the way the alerts do.** The Overview's
  *ISP gateway cut windows* counted every window above 50%, including those
  in the Pi's own deaf-radio spells. On the staging Pi over seven days it
  showed 2 083 in red, and 2 050 of them were one 17-hour radio hang: a
  reader would blame the line for the monitor's own radio. It now
  shows *the link* and *this Pi's radio* apart, from the same `wifi_link`
  evidence as `common.microcuts`. The *CPE Microcut Detection* dashboard
  turned red at 5% loss, below the gateway's normal rate-limit floor (daily
  p90 14–22%), so a healthy gateway was red all day, and a red that is always
  on hides the one that matters. It is now yellow at 30% and red above
  50%, a cut window. The ClickHouse dashboards' red
  *unreachable* overlay marked only steps that lost everything, while the
  InfluxDB one has marked 1.5 lost pings per 15 minutes since 2026-09-24;
  both now use the latter rule. The doctor learned to read a query that
  joins two pivoted streams, which it took for tag filters and a text stat.

- **"Widespread", "broad" and "weak Wi-Fi" mean one thing everywhere.**
  The alerter's rules and verdict, the diagnosis (alert lines, MCP
  `diagnose_loss`, the incident history) and MCP `get_loss_events` kept
  their own copies: 80% here, 60% there, a bare 0.8, -75 dBm and 6
  samples in three places, with comments saying "the same as the
  alerter's". Only the alerter read the settings, so tuning
  `WIDESPREAD_PCT` or `VERDICT_BROAD_PCT` changed the alert and not the
  diagnosis that led it, nor what an assistant was told. They are now read
  once, in `common/definitions.py`, and the MCP server gets the same
  settings. The ISP gateway (first hop) is out of every breadth count
  (the verdict and `get_loss_events` still counted it), and
  `get_loss_events` names a total loss the way the alerts do, in a new
  `reading` field: `uplink_down`, `internet_down` or `monitor_dns`, instead
  of "this host's uplink ... not the ISP" whatever still answered. The
  diagnosis gains a `monitor` class (`names`): every named site lost while
  the gateway and the resolvers answered, so `diagnose_loss`, the incident
  history and a `monitor_dns` alert now agree. The answering guide and the
  OpenClaw skill say so.

- **A total outage now says where it is.** Every destination at 100% read
  "this host's uplink is down; nothing beyond it can be judged" whatever
  still answered, so an ISP outage, the monitor's own name resolution
  failing (2026-08: a frozen `resolv.conf`, nine sites at 100% for nine
  days) and a hung radio all read the same, and the diagnosis could lead
  the same message with a different cause. The first hop (the ISP
  gateway) is now read, not counted in the breadth: lost too is
  `uplink_down`, this host's link; answering is `internet_down`, beyond the
  house; answering together with the DNS resolvers queried by address
  while every named site is lost is `monitor_dns`, the monitor's own DNS.
  Unmeasured, `uplink_down` says it cannot tell. The diagnosis no longer
  calls total loss with the gateway answering "the line", and when it
  leads a message the rule's line keeps its numbers and drops its own
  reading of the cause: one cause per message. A reading that changes in
  mid-outage (the gateway reboots, or is back before the ISP) supersedes
  the old one without a "recovered". Because the first hop no longer
  counts in the breadth, a stack with fewer than three destinations
  besides it no longer raises these.

- **What the watchdog does is now told.** `smoking-pi watchdog` reloads a
  hung Wi-Fi driver, reboots the Pi and restarts SmokePing exactly when
  the network is down, and its actions reached only its own log and the
  journal: a reboot at 3 a.m. left nothing a person would see, and the
  alerter could not say why the Pi had restarted. Each event is now also
  written to the stack's InfluxDB (`watchdog_event`, through the database
  container's own CLI, so no token leaves it), and the alerter sends it
  once, through the outbox: an action as information, an action it could
  not take or a SmokePing crash loop as a warning. Both of the
  watchdog's Docker calls are bounded (5 s and 10 s), so a wedged Docker
  daemon cannot stand between it and a reboot.

- **The alerter now says when it cannot see.** Three blind spots, found by
  the audit of the detection pipeline:
  - **InfluxDB unreadable meant silence.** Every cycle failed into the log
    and nothing was sent, not even that the alerter had gone blind. After
    five minutes it is now a critical, `influx_unreadable`, and every other
    open incident is held exactly as it was until the database answers,
    instead of recovering or re-sending on no data. It counts as readable
    after three good cycles in a row, so a database that fails every other
    cycle still alerts, and the stall rules wait one window after it is
    back. A bug in the alerter's own rules is `evaluation_failed`, not the
    database.
  - **Only one writer was watched.** `exporter_stale` counts `latency`
    alone, so the DNS, HTTP and TCP probes, the gateway microcut probe and
    the Wi-Fi exporter could stop unnoticed; the last two are the evidence
    the verdict and the diagnosis read. A new warning, `measurement_stale`,
    names the one that stopped, if it wrote within the last 24 hours; past
    that, it is taken as removed on purpose and retired without a
    recovery.
  - **One failing query silenced every rule.** A Flux error in any of the
    four Wi-Fi queries, or an exception in the verdict, failed the whole
    cycle. Both are now evidence that can be missing: the alert goes
    without that line.

- **An alert that cannot be delivered is no longer lost.** During the
  reference Pi's radio hangs on 2026-10-11 (11 hours) and 2026-10-12 (5.5
  hours), every alert the alerter decided on failed to send. It counted
  each as sent anyway: a failed alert waited for the next hourly cooldown,
  a failed `outage` warning was never retried, and a failed recovery was
  gone. Nothing afterwards said what had happened. Undelivered alerts and
  recoveries now wait in an outbox in the state file. While anything waits,
  new ones join it instead of being sent into a dead link, and the alerter
  checks with a TCP connect whether delivery could leave the house, once a
  minute, doubling to every 10 minutes. Once it can, it sends **one**
  message: the notification itself if it is the only one and under 10
  minutes old, otherwise a summary of the stretch (each incident once, when
  it started, whether it recovered, what is open now). `list_alert_state`
  shows what is waiting, and the history records each notification's
  delivery.

- **A failed OpenClaw send is no longer retried into duplicates.** The
  gateway keeps a failed send in its own durable queue and replays it
  later, so each of the alerter's three attempts was another copy: after
  the 2026-10-12 hang, the same stale alert arrived twice. Once the gateway
  has answered, the send is not retried; a gateway that did not answer
  still is.

- **One event now reaches the phone as one incident, not a burst of
  them.** After the reference Pi's radio hung for five and a half hours on
  2026-10-12, its return produced ten Telegram messages in 72 minutes,
  among them three "brief cut of the link, not a site problem" warnings.
  The `outage` key was the run's first cycle *still inside the 20-minute
  window*, so a long outage got a new key, and a new warning, every five
  minutes; and the cycles an ended `uplink_down` left in the window became
  fresh outages just as delivery worked again. An outage now keeps the key
  of the one it continues, and the tail of an `uplink_down` only suppresses
  the per-target fan-out: it is never sent, and the uplink's recovery waits
  until it is gone (up to one 20-minute window later than before). Loss
  that goes on after the uplink returned is an outage of its own, and a
  continued outage's message says how long it has lasted instead of
  calling it a brief cut. Found by the audit of the detection pipeline (roadmap
  front 8).

- **A target going down no longer announces "recovered" fifteen minutes
  in.** `high_loss` fires two cycles before `target_down`; once the target
  was down, `high_loss` was excluded, went missing, and sent a recovery
  while the target was still at 100%, then fired again as the 15-minute
  mean caught up. `target_down` now supersedes the warning (retired without
  a recovery), and `high_loss` stays off for the target until the
  `target_down` record is gone.

- **No data is no longer read as "recovered".** An incident whose target
  stopped reporting (a stalled probe, a stalled exporter, a removed target)
  went missing and announced its recovery fifteen minutes later, before
  `exporter_stale` had even fired. Such an incident is now held open until
  data shows the target answering, and dropped without a notice after 24
  hours with no data; `list_alert_state` shows `no_data_since`.

- **`ack_incident` ends when the incident recovers, as it says.** Its
  `clear_on_recovery` flag was written and never read, so an ack silenced
  the key for its full 24 hours, including a new outage of the same target
  after a recovery. An ack now covers only the occurrence first seen
  before it was made.

## [2.31.2] — 2026-10-13

Five fixes from the clean-install rounds on a Raspberry Pi. Most of them
make the stack say what is wrong instead of looking fine. The watchdog
reports a SmokePing that keeps dying at start, which used to read as
running. The doctor now checks that a Basic install measures. `smoking-pi
openclaw` no longer claims to have reloaded a gateway that is not running,
and `restore` says when OpenClaw must be connected again. Recreating the
InfluxDB container, as an upgrade does, no longer leaves an empty Docker
volume behind.

### Fixed

- **`smoking-pi openclaw` no longer says it "reloaded the gateway" when no
  gateway is running.** `openclaw mcp reload` exits 0 with no gateway at
  all, because it only drops the CLI's own cache. On the staging Pi,
  whose gateway was stopped, step 5 still reported success, and only the
  `--check` afterwards said otherwise. `install-openclaw-skill.sh
  --reload` now asks the gateway itself (`openclaw gateway status
  --require-rpc`, three tries, for a gateway it just restarted). It
  exits 3 when nothing answers, and step 5 then says the skill is
  installed and the gateway did not answer (not running, or it refused
  the probe). An `openclaw` too old to have that probe is not called
  stopped.

- **Recreating the InfluxDB container no longer leaves an empty Docker
  volume behind.** The InfluxDB image declares `/etc/influxdb2` a
  volume, and the Pro compose file did not mount it. Every time the
  container was recreated, Docker made a new anonymous volume and
  orphaned the old one: 18 on the staging Pi in two weeks. All were
  empty, none was ever in `smoking-pi purge`'s list, and the count grew
  forever. The directory is now a tmpfs, which is what it always was in
  effect: empty at every start, which the InfluxDB token repair relies
  on. Docker creates no volume for it (checked on the staging Pi: a
  container created without the tmpfs added an anonymous volume, one
  with it did not). Every other image's declared volumes were already
  mounted, which was checked container by container. Anonymous volumes
  left by earlier versions hold nothing and can stay: they carry no
  project label, so nothing can tell them apart from another program's.

- **`smoking-pi restore` says to connect OpenClaw again.** The restore
  brings back the backup's MCP token, and an OpenClaw gateway connected
  before it still sends the old one, so every tool call is refused until
  `smoking-pi openclaw` runs again. Clean-install round 2 found it on the
  staging Pi. When the restore writes an env file with the `mcp` profile
  and a token other than the one this host had, it now ends by saying so.

- **The watchdog now reports a SmokePing that keeps dying at start.**
  When SmokePing refuses its configuration, s6 starts it again every
  second or two, so `s6-svstat -u` answered "up" about half the time and
  the watchdog's check never fired. That is how a clean Standard install
  restarted 24,166 times in 14 hours with nothing measured and nothing said
  (fixed in 2.31.1). The check now also reads how long the service has
  been in its state. If it changed less than 30 seconds ago on two checks
  at least 50 seconds apart, the watchdog logs once per loop that SmokePing
  keeps restarting and to read `smoking-pi logs smokeping`. It does not
  restart the container, because the same config would fail again.
  Reproduced on the staging Pi with a broken `/config/General`.

- **`doctor --live` checks that Basic measures.** On Basic, the three
  "is it measuring" checks all skipped (no config-manager, no InfluxDB). A
  Basic install that measured nothing got the same 19 ok / 0 fail as one
  that worked, which is the #303 situation on the edition with the fewest
  other signals. `targets-measured` now reads the SmokePing container's
  `Targets`, `Probes` and RRD times where there is no config-manager.
  It judges them with config-manager's own freshness rules, so
  it fails on targets that never got data or stopped getting it, and
  waits out the first two steps after SmokePing starts. On the staging Pi
  it gave config-manager's exact answer (78 of 78 fresh, on Pro). A
  SmokePing container that exists but is not running now fails the
  check instead of skipping it.

## [2.31.1] — 2026-10-13

Two fixes found by a clean install of every edition on a Raspberry Pi. A
Standard install never measured: SmokePing refused a config file only Pro
creates, and restarted every second or two instead. And `smoking-pi
backup` skipped its PostgreSQL dump about one time in five, without
saying so. Upgrading repairs an existing Standard install: the upgrade
recreates SmokePing's container, whose start now creates the missing
file.

### Fixed

- **A Standard install measures again.** The generated `Targets` file
  `@include`s `/config/CPE_Targets`. Only Pro creates that file, through its
  exporters, and SmokePing refuses to start when an include is missing.
  Since the CPE microcut detector was added, every Standard SmokePing died
  at start, every second or two, and never measured. A clean Standard
  install on the staging Pi on 2026-10-13 restarted 24,166 times in
  14 hours with no RRD written. SmokePing's own start script and
  config-manager's link step (run before every reload) now create an
  empty `CPE_Targets` when there is none. That repairs existing Standard
  installs when the upgrade recreates the container, and at config-manager's
  next link step.

- **`smoking-pi backup` no longer skips its PostgreSQL dump at random.**
  The check for whether the edition has PostgreSQL piped Compose's service
  list into `grep -q`. Under `pipefail`, grep leaving at the first match
  (postgres is listed first) made Compose fail on the closed pipe, and the
  answer was "no" about one time in five on the staging Pi. A backup there
  on 2026-10-13 had every volume tarball but no `postgres.sql`. Nothing
  said so, and the dump is the restore path for a PostgreSQL major
  upgrade. The check and five other pipelines of the same shape (the DNS
  observer's start-up wait and DHCP note, `enable`'s name check, the
  backup's running check, the watchdog's link check), plus four in
  `verify-postgres.sh` and `install-openclaw-skill.sh`, now read their
  input in full. The watchdog's `head -1` reads keep what they captured
  instead of clearing it when the writer fails.

## [2.31.0] — 2026-10-12

The Pi now gets itself back on its feet after the two failures that used to
need someone at the power socket. A Wi-Fi radio that hangs (still up, often
still associated, receiving nothing) cost 21 hours, 3 h 20 min and 17 hours
of measurements on two Pis. A SmokePing that did not start after a power
cycle cost 16 minutes on 2026-10-12. `sudo smoking-pi watchdog enable`
reloads the Wi-Fi driver, then reboots if that was not enough, and restarts
a SmokePing that never came up. It was rehearsed on a Raspberry Pi with the
uplink cut at the firewall. Off until enabled.

### Added

- **`smoking-pi watchdog`: the host recovers by itself from a hung Wi-Fi
  radio and from a SmokePing that did not start.** Both failures used to
  need a person. The radio hang (wlan0 up, often still associated,
  receiving nothing) cost 21 hours, 3 h 20 min and 17 hours of measurements
  on two Pis, and a power cycle ended most of them. After the reference
  Pi's power cycle on 2026-10-12, its SmokePing container ran while the
  service inside stayed `down (not started yet)`, and nothing was measured
  for 16 minutes until a manual restart. `sudo smoking-pi watchdog enable`
  installs a systemd timer that checks both every minute:
  - **Wi-Fi:** nothing answers through Wi-Fi for 10 minutes (not the
    router, not the Internet, and the router does not answer on the link
    either) → reload the driver; still nothing 5 minutes later → reboot, at
    most once every 6 hours (`--no-reboot`: never).
  - **SmokePing:** service not up 5 minutes after its container started →
    `restart smokeping`, at most 3 times in 6 hours.

  Off until enabled, because it can reboot the host. `smoking-pi watchdog`
  shows what it did, from an event log that survives the reboot.
  `docs/watchdog.md`.

## [2.30.0] — 2026-10-12

Loss incidents are kept, so a chart can mark them and an assistant can be
asked about last month; Netflix targets keep one name, so their history is
one curve. The alerter now writes down each incident `diagnose_loss`
classifies once it is over (`loss_incident`), the *Smoking Pi Overview* and
*Target Detail* dashboards shade them, and a new MCP tool,
`get_incident_history`, reads up to a year of them. The reference Pi's radio
was deaf for 11 hours on 2026-10-11, and nothing had kept that. Netflix
targets were named after the appliance Netflix hands out, which changes
every day: 179 series in 60 days for 5 targets. Also: install offers every
assistant, only when the `mcp` service was picked; `smoking-pi COMMAND
--help` answers without sudo; and the doctor's Avahi check no longer skips a
running Avahi. And the DNS observer no longer saturates a core when its
canaries stop arriving: after the reference Pi's radio went deaf on
2026-10-12, it climbed from 0.04 to 0.53 cores over five hours and the Pi
peaked at 85.3 °C.

### Added

- **Loss incidents are written down, and dashboards and assistants show
  them.** `diagnose_loss` says what each loss episode was (this host's
  Wi-Fi, the line, one destination, upstream) with its evidence and a
  confidence. It ran only when asked, for at most a week, and nothing kept
  the result. "What happened last month?" had no answer, and a chart could
  not mark an incident. Every half hour the alerter now diagnoses the last
  `INCIDENT_HISTORY_HOURS` (default 12) in the background and writes each
  incident that is over into InfluxDB (`loss_incident`), stamped at its
  start with every value a field, so a later run overwrites it rather than
  duplicating it. An incident that began before the window makes the run
  widen it, up to a week, until the whole incident is inside: on the
  reference Pi the radio was deaf for 11 hours on 2026-10-11 (from 08:35
  UTC until it was power-cycled), and a fixed window would never have
  written it. Assistants read the history with a new MCP tool,
  `get_incident_history(days, include_probe_miss)`, up to a year. The
  *Smoking Pi Overview* and *Target Detail* dashboards shade each incident
  with its class and confidence; Target Detail shows the target's own
  incidents plus the ones about everything. It needs the `alerts` service
  and InfluxDB. See *Incident history* in the alerting docs.

### Fixed

- **The DNS observer no longer saturates a core when its canaries stop
  arriving.** Every 30 s it looked for each canary it had not seen yet with
  its own search of AdGuard's query log, and it keeps canaries for a day.
  When none arrive (this host's radio deaf, or a router that does not
  forward to the observer) up to 288 pile up, and AdGuard spent a whole core
  scanning its log for them. On the reference Pi, after its radio went deaf
  at 15:15 UTC on 2026-10-12, the observer's CPU rose from 0.04 to 0.53
  cores over five hours and the Pi from 65 to 71 °C mean (85.3 °C peak),
  fan at its top step: the loud fan heard while it was cut off. On the
  staging Pi, whose router does not forward to it, it never stopped
  (1,003 searches in 10 minutes, AdGuard at 99 % CPU). Now one search for
  the canary domain finds every pending canary (a 500-entry page, room for
  the hour's self-tests that share the domain), only those sent within the
  last hour are looked for (one that arrives does so within seconds), and
  with none pending there is no search at all.
- **The doctor's `avahi-host-name` check no longer skips a running Avahi.**
  It read the host name from Avahi's process title and accepted only
  `avahi-daemon: running [name]`. On the reference Pi the title stayed at
  `registering [smokingpi.local]` while Avahi answered for the name and
  reported itself running over D-Bus, so the check said "Avahi is not running
  on this host" and skipped: a rename after a conflict would have gone
  unreported. It now reads the name whatever the state word.
- **Netflix targets keep one name, so their history is one curve**
  ([#275](https://github.com/estcarisimo/smoking-pi/issues/275)). Each
  Netflix Open Connect target was named after the appliance Netflix handed
  out (`Ocean_Reef_Club_Airport_DFW_c534_1`), and Netflix hands out a
  different appliance at the same site every day. Every nightly refresh
  therefore started a new series and a new RRD and left the previous one
  stale. The reference Pi measured 5 Netflix targets and had **179 series in
  60 days**, each about a day long. A chart, the Netflix side-by-side
  dashboard or an assistant asked about last week saw fragments, and no
  long-term comparison of Netflix was possible. Over those 60 days each slot
  kept its site (two in Dallas, two in Kansas City, one in Atlanta); only
  the appliance changed. Targets are now named by slot and site,
  `Netflix_1_DFW`. The name ends in letters because the exporter reads a
  name ending in `6` as an IPv6 target. The refresh updates each slot's
  host in place instead of deleting and re-inserting the category. The
  appliance stays in the title and the database metadata, and a new
  InfluxDB measurement, `target_host` (tags `target` and `category`, fields
  `host` and `title`), records which host stood behind every target and
  when. The exporter writes it once at start and whenever a host changes,
  never per cycle. A tag on the latency points would have split the series
  again. On the first refresh after the upgrade, the old names are replaced
  by the slot names. Their history stays in InfluxDB under the old names,
  and their RRDs stay on disk, unused. `target_host` is InfluxDB only; the
  ClickHouse exporter does not write it. The refresh also reloads SmokePing
  now: it saved the new targets and regenerated the files, but SmokePing
  reads them only when signaled, so it went on measuring the previous
  appliances until some other change reloaded it (14 hours on the staging
  Pi with the first candidate).

- **Install offers every assistant, and only when there is one to connect**
  ([#305](https://github.com/estcarisimo/smoking-pi/issues/305)). The
  question at the end of `install` predated `connect`: it offered only
  OpenClaw, was asked even when the `mcp` profile (the server an assistant
  talks to) had not been picked, pointed a later setup at `smoking-pi
  openclaw`, and a skipped answer was missing from *Still to do*, which the
  guide promises lists every skipped question. Someone who wanted Claude,
  ChatGPT or Grok was told nothing about how. It is now asked only with
  `mcp`, offers OpenClaw on this Pi, an assistant on the Internet (`connect
  --tailscale`, then `connect NAME`) or OpenClaw elsewhere, and whatever is
  not done is listed under *Still to do* in `connect`'s words, `--yes`
  included. *Getting started* step 7 describes the same two paths.
- **`smoking-pi COMMAND --help` answers, without sudo**
  ([#305](https://github.com/estcarisimo/smoking-pi/issues/305)). On a
  package, `backup --help` refused because only root can read the env file,
  and with sudo it answered `unknown option --help`; so did `restore`. Help
  read nothing it needed to. Every command's `--help` (or `-h`) now prints
  that command's entry in the reference, before anything reads the env
  file; the doctor keeps its own.
- **The "already installed" refusal names `smoking-pi purge --config`**
  ([#305](https://github.com/estcarisimo/smoking-pi/issues/305)) instead of
  a `docker volume ls | grep` and an env file to delete by hand, which is
  the command that does exactly that and the one the guide names.
- **`purge --config` on a package leaves no empty `/etc/smoking-pi/config`**
  ([#305](https://github.com/estcarisimo/smoking-pi/issues/305)). It
  recreated the config and output directories, placeholders that only a
  clone tracks; install and restore create them again.
- ***Getting started* says alerts comes preselected**
  ([#305](https://github.com/estcarisimo/smoking-pi/issues/305)), as it has
  since the checklist turned it on; step 3 said all four optional services
  were off unless picked, and that alerts could go only to OpenClaw or a
  webhook (Telegram too).

## [2.29.2] — 2026-10-11

A clean install measures its own targets, and the doctor says when it does
not. A clean install on the staging Pi came up with every container
healthy and `doctor --live` all green while SmokePing measured the image's
sample config: none of its 22 configured targets got a single point.

### Fixed

- **A clean install measures its own targets, not SmokePing's sample
  ones** ([#303](https://github.com/estcarisimo/smoking-pi/issues/303)).
  When the SmokePing container started before config-manager had generated
  `Targets` and `Probes`, which a clean Pro install can do because nothing
  orders the two, its start-up script found nothing to link and kept the
  image's sample `Targets` (US universities). Every later reload loaded
  that sample again, because `/config/Targets` was still a real file. A
  clean install on the staging Pi came up healthy, with `doctor --live`
  all green, and 22 of 22 configured targets without a single measurement:
  a monitor that records nothing while looking fine, from its first day.
  config-manager now runs the same link step inside the SmokePing container
  before every reload it sends, and once at startup, reloading SmokePing
  when it had to link. An install that already hit this repairs itself when
  config-manager next starts, for example on the upgrade.
- **The doctor notices when configured targets are not measured**
  ([#304](https://github.com/estcarisimo/smoking-pi/issues/304)). In that
  clean install, `doctor --live` reported 21 ok while 22 of 22 configured
  targets had no data. `silent-series` read InfluxDB, which held points,
  and the dashboards were new, so nothing it checked was wrong. The
  instrumentation doctor said "all good" about a monitor recording none of
  its targets. A new live check, `targets-measured`, asks config-manager's
  `/measurements` (the web admin's Measurements card): it fails when
  configured targets never got data or stopped getting it, naming them, and
  points at `sudo smoking-pi restart smokeping` when none has any. A target
  within its first two steps is pending, so a stack that just started does
  not fail.

## [2.29.1] — 2026-10-09

The doctor stops warning about targets that are no longer measured.
Right after `dns adopt` deactivated two silent layers, `doctor --live`
still named them and told you to run `dns adopt`, for about eight hours.

### Fixed

- **The doctor no longer reports targets that were just deactivated as
  silent.** `silent-series` read only InfluxDB, which keeps a deactivated
  target's dead points for the rest of the 24 h window. Right after
  `sudo smoking-pi dns adopt` deactivated two silent layers on the
  reference Pi, `doctor --live` still warned about both and advised running
  `dns adopt` to deactivate them, for about eight more hours. A warning
  that names the fix just applied teaches the reader to ignore it. The
  check now reads the Targets file the SmokePing container loads, with the
  CPE_Targets it includes, and drops any series whose target is in neither;
  if Targets cannot be read, it reports every silent series as before.

## [2.29.0] — 2026-10-09

A dashboard for the question "has the connection's quality changed, and
when?", and alerts whose breadth count matches the incident they describe.

- **Connectivity & Quality** (Pro, InfluxDB) is a new Grafana dashboard.
  Loss is zoomed to 0–10 % and shown with latency, by kind of destination,
  on one time axis. Below them, the inference module's detected episodes
  for every target (when the experimental `inference` service is on). It
  also has two 30-day heatmaps by day and hour of day, where a recurring
  evening pattern stands out.
- An `outage` or `uplink_down` alert counts the targets the rule fired on.
  Before, the count came from 15-minute means and could disagree with the
  alert's own message. Alerts about one target no longer print a count.

No new settings, volumes or services. Upgrade with
`sudo smoking-pi upgrade`.

### Added

- **A Connectivity & Quality dashboard** (Pro, InfluxDB). Answering "has
  the connection's quality changed, and when?" meant reading the
  Overview's loss panel over a month. On a 0–100 % axis everyday loss was
  a flat line at the bottom. Spotting a daily pattern was left to the eye,
  and the inference module's episodes were visible only one target at a
  time, on Target Detail. The new dashboard puts the selected range on one
  time axis:
  - loss by category, zoomed to 0–10 %;
  - latency by category, without the ISP gateway's control-plane spikes;
  - every target's detected episodes;
  - range tiles, among them the share of probe cycles in which two or more
    destinations lost packets at once.

  Two 30-day heatmaps, day × hour in local time, show mean loss and that
  shared-loss share, so a recurring evening band is visible at a glance.
  Home and Wi-Fi are unchanged, apart from a link in the Overview's list.
  See `docs/connectivity-quality.md`.

### Fixed

- **An alert's breadth count is the incident's own** (#286). The context
  line under an alert printed the verdict's count: targets over the
  impaired threshold on their 15-minute mean. That can be a different set
  from the one the rule fired on, so an `outage` whose message said "12 of
  16 targets lost packets" could end in `1 of 16 affected`, and a
  `target_down` could carry a count that was about another target. A reader
  deciding whether the problem is theirs or the internet's was handed two
  numbers that disagreed. `outage` and `uplink_down` now carry their own
  `breadth` (count, total and target names), and that is what the line
  prints. Per-target rules print no count: their message names the
  target, and the verdict line states the breadth across all of them.

## [2.28.0] — 2026-10-08

Everything a person runs is now a `smoking-pi` command, and a setting is
checked before it is written instead of after it crashes a container.

- `smoking-pi enable` / `disable NAME` turn the optional services on and off
  by name; `smoking-pi tunnel start|stop` replaces the two tunnel scripts.
- `.env.template` is the settings' schema: sections and types. `config set`
  refuses a value of the wrong kind, `config describe KEY` explains one,
  and a new doctor check, `settings-schema`, keeps the template honest.
- The command lives in `cli/`, one file per area. The scripts it replaces
  and `DNS_MONITORING.md` are gone; the edition READMEs point into the docs.
- `PUBLIC_IP_GEO=false` (or `off`, `no`) now turns the ipinfo.io lookup off;
  only `0` did before.
- ClickHouse mode: four dashboards (DNS Resolution Times and the three
  side-by-side ones) were blank since v2.5.0; the doctor now reads their SQL.
- No new images, volumes or migrations. Existing env files are unchanged.

### Added

- **Settings have structure: sections, types, and a check before writing.**
  The env file is 148 flat keys (146 in Pro). `config set` took any string
  for any of them, and much of the code reading them is strict:
  - `CPE_PROBE_RATE=0` divides by zero, and `CPE_PROBE_RATE=fast` stops
    microcut detection with an `int()` at import, restarting every minute;
  - a `DNS_*` value out of range stops the DNS observer (for example, a
    `DNS_WIZARD_COVERAGE` of 0, or a `DNS_WIZARD_INTERVAL` between 1 and 59);
  - `ALERT_INTERVAL` and `REPORT_INTERVAL` crash their containers;
  - a mistyped `NOTIFY_MODE` silently makes alerts log-only.

  Every one of those was found only after the write, in a log. Now
  `.env.template` is also the schema. `## Section` lines group the keys,
  and a `#:` line above a key gives its type, typed from the code that
  parses it: `int` or `float` with bounds (`min=`, `gt=` for "above",
  `max=`, `allow=` for an exception such as "0 or at least 60"), `bool`,
  `enum:a|b`, `time`, `date`, `tz`, `url`, or `origin` (https, a host, no
  path). The same line carries the flags `secret` and
  `install`.
  - `config set` refuses a value of the wrong kind before anything reads it.
  - `config list` groups the keys by section, and a word narrows it
    (`config list alerts`).
  - The new `config describe KEY` prints a key's description, type and
    bounds, default, current value (a secret stays hidden) and the
    services that read it.

  Keys whose consumer takes free text stay untyped and accept anything,
  as before. The bool type accepts `true`/`false`/`1`/`0`: every consumer
  of a key typed bool reads all four correctly (some also read `yes`/`on`,
  but not all of them do). Choices and booleans are matched in any case
  and written lowercase. A new doctor check,
  `settings-schema`, fails on four things:
  - a key in no section;
  - a misspelled type;
  - a `#:` line that describes nothing;
  - a credential-looking or install-fixed key without its flag.

  It checks the template's own values against their types too. Nothing
  changes in an existing env file: no migration.

- **`smoking-pi enable` / `disable NAME`: the optional services, by name.**
  Turning on the MCP server, alerts, AI reports or the inference detectors
  meant editing `COMPOSE_PROFILES` in the env file (a `sed` line in the
  README) and then running `docker compose up -d` from the right directory.
  Skipping the first step left a service running *unmanaged*: the next
  `down` and `up` dropped it, and nothing said so. `enable` records the
  profile and applies the stack; `disable` removes it, and the
  containers with it; `enable` alone lists which are on. It refuses
  the backend profiles (fixed at install), names an edition lacks, and
  unknown names, before changing anything. `dns` goes to `dns enable`,
  which checks port 53 and gives the router steps.
- **`smoking-pi tunnel [start|stop]`: Cloudflare quick tunnels.** These
  were `shared/scripts/create-tunnel.sh` and `show-tunnel-urls.sh`,
  which ran an unpinned `cloudflared:latest` and published the pages with
  no confirmation. The command asks first (or `--yes`), runs the pinned image
  that `shared/cloudflare-tunnel` uses, labels each container so `stop`
  finds them by identity, and shows each tunnel's current hostname. `stop`
  also removes tunnels the old script started.

### Changed

- **`smoking-pi restart` on Pro with InfluxDB also checks Grafana's
  InfluxDB token**, as `shared/scripts/manage-containers.sh` did. That was
  the one thing the old script did that the command did not. When the env
  file's token and the volume's have diverged, Grafana's InfluxDB panels
  go empty while data keeps arriving.
- **The documentation names commands, not scripts.** The README, the
  getting-started, maintenance, MCP, OpenClaw, alerting, AI, ClickHouse
  and quick-tunnel guides, and the env template comments now say
  `smoking-pi enable mcp`, `smoking-pi alerts --telegram`,
  `smoking-pi tunnel start` and `smoking-pi install --database
  clickhouse`, instead of `sed` over the env file, `COMPOSE_PROFILES=…
  docker compose up` and `./setup.sh`. The three edition READMEs (1,300
  lines that still described files removed long ago) are now short
  pointers into the docs. AGENTS.md makes it a rule: anything a person
  runs is a `smoking-pi` command.
- **The `smoking-pi` command has its own directory, `cli/`.** It was one
  2,835-line file, `packaging/smoking-pi`, next to the `.deb` builder and
  the Homebrew checks, while the scripts it should have replaced were
  spread over `shared/scripts/` and every edition directory. Nothing said
  where a new command belonged, so every new step became another script
  for a person to find. Now `cli/smoking-pi` sets up the paths and
  dispatches, each area of commands is one file in `cli/lib/`
  (`config.sh`, `alerts.sh`, `lifecycle.sh`…), and the tests are in
  `cli/tests/`. The move changes no behavior: comparing `declare -f`
  before and after, every function is byte-for-byte what it was except
  the two that name the command's own path (`link_cli`, and the tip in
  `brief`), which now say `cli/smoking-pi`. The package installs `cli/` as
  `/usr/lib/smoking-pi` with `/usr/bin/smoking-pi` a link to it.
  `packaging/smoking-pi` stays as a one-line forward, because a clone's
  `/usr/local/bin` link still points there; `smoking-pi upgrade` (or
  `link`) repoints it to `cli/`. The Homebrew formula takes whichever of
  the two the release it installs has.
- **`packaging/deb/build.sh` sets `umask 022`.** The package's files kept
  the modes they were extracted with, so a package built under a
  desktop's or a Pi's umask of 002 shipped every file in `/opt`
  group-writable. Released packages were not affected because CI's runners
  use 022.

### Removed

- **`editions/pro/DNS_MONITORING.md`, replaced by
  `docs/dns-probes.md` (*DNS resolver probes*).** The old guide stated
  `dns_latency` loss as a 0–100 percentage where the exporter writes a
  0–1 ratio, queried a `latency` bucket that does not exist (it is
  `smokeping`), named the stack ("SmokePing Full Stack") and the
  dashboards by their first-generation names, and troubleshot with
  `docker compose exec` from a directory. A reader following it would
  have written a loss threshold 100 times too high and queries that
  return nothing. The new page keeps what is still true —
  the three seeded resolvers, the `dig` probe (5 queries every 300 s), the
  `DNS_Resolvers` RRD section, the InfluxDB and ClickHouse schemas with
  their units, the two dashboards — says how it differs from the DNS
  observer and the public-resolver identity, and troubleshoots with
  `smoking-pi doctor --live`, `logs` and `restart`.
- Scripts the command replaces or that had no remaining use:
  - `shared/scripts/manage-containers.sh` and its three edition wrappers;
  - the three `editions/*/show-passwords.sh` wrappers (`smoking-pi passwords`
    runs `shared/scripts/show-passwords.sh` itself);
  - `create-tunnel.sh` and `show-tunnel-urls.sh`;
  - `migrate-to-edition.sh`, which migrated from the layout before
    editions existed;
  - `editions/pro/detect-timezone.sh` and `init-passwords.sh`, which
    nothing called (`install` detects the timezone and generates the
    secrets);
  - `README-Zero-Touch.md`.

### Fixed

- **`PUBLIC_IP_GEO=false` turns the location lookup off.** The
  public-address collector stopped asking ipinfo.io only when the value was
  exactly `0`; `false`, `off` or `no` left it on. Someone who wrote
  `PUBLIC_IP_GEO=false` to keep their public address to themselves kept
  sending it to ipinfo.io once a day, with nothing saying so. Now `0`,
  `false`, `off` and `no`, in any case, turn it off, as `NETMETER` reads its
  switch; empty or unset stays on. The template types the key `bool`
  (`config set PUBLIC_IP_GEO false`) instead of `enum:0|1`.
- **Four ClickHouse dashboards were blank: their `$target` variable asked
  for RRD directory names.** DNS Resolution Times filtered
  `category = 'DNS_Resolvers' AND measurement_type = 'latency'`, and the
  Top Sites, Netflix and Custom side-by-side dashboards `'websites'`,
  `'Netflix'` and `'Custom'`; since v2.5.0 the ClickHouse exporter writes
  the InfluxDB vocabulary (`dns`/`dns_latency`, `topsites`, `netflix`,
  `custom`). The variables returned no targets, and every panel filters
  `target IN (${target})`, so in ClickHouse mode those dashboards showed
  "No data" over data that was there. v2.5.0 fixed the panels and missed
  the variables. `docs/clickhouse.md` and `docs/doctor.md` described the
  mistake as fixed and caught, and the doctor passed: `panel-tags-written`
  checked only the InfluxDB dashboards, and only Flux tag *names* (`r.x`).
  The variables now use the exporter's values, and `panel-tags-written`
  also reads ClickHouse SQL: every `category` / `measurement_type` literal
  compared with `=`, `!=`, `<>` or `IN` in a query over `smokeping.*` must
  be a value the exporter's `category_for()` / `measurement_type_for()` can
  return, read from its source. A valid value on the wrong dashboard
  (`measurement_type = 'latency'` for DNS) is still not caught.

## [2.27.0] — 2026-10-08

Pro can say when a path stayed congested and when a target's loss rose and
stayed up, not only when one probe cycle went bad. Recoveries report how
long the problem lasted rather than how long the alert was open.

- Pro, experimental: the `inference` profile (off by default) runs
  Jitterbug's persistent-congestion detection and a separate loss-degradation
  detector once an hour by default, writes the periods to InfluxDB and
  shades them on Target Detail. Never in the alert path. InfluxDB only.
- Alert recoveries end their duration at the first cycle the rule stopped
  firing; windowed rules (`microcut_burst`, `high_loss`) give none.
- Alerts no longer print "0 of N affected" when the 15-minute mean saw
  nothing.
- Log messages and comments no longer cite design notes that were never in
  the repository.
- New settings: `INFERENCE_SINCE`, `INFERENCE_INTERVAL`,
  `INFERENCE_TARGETS`, `INFERENCE_CATEGORIES`, `INFERENCE_CONGESTION_DAYS`,
  `INFERENCE_DEGRADATION_DAYS`, `INFERENCE_DEGRADATION_LOSS_PCT`,
  `INFERENCE_DEGRADATION_MIN_MINUTES`. One new image, `inference` (1.7 GB on
  arm64, nearly all PyTorch CPU), pulled only when the profile is on, with
  one new volume, `inference-state`.

### Added

- **Experimental: persistent congestion and loss degradation, per target
  (`inference` profile, off by default).** Smoking Pi measured loss and
  latency per target, but nothing said when a path stayed congested or when
  a target's loss rose and stayed up, as opposed to one bad probe cycle. An
  alert fires on a threshold; it cannot tell a queue that stays full from a
  radio dropping packets. Two separate detectors run once an hour on every
  ICMP target and shade the Target Detail dashboard:
  - **Persistent congestion** is [Jitterbug](https://github.com/estcarisimo/jitterbug)
    (PAM 2022) used as its package publishes it, in the paper's
    configuration: Bayesian change points on the 15-minute minimum RTT, then
    the latency jump and the KS jitter test. A path whose floor never moves
    gets no period, however jittery it is.
  - **Loss degradation** runs the same Bayesian change-point detection on
    packet loss in 15-minute bins. A segment at 2 % mean loss or more that
    lasts 30 minutes or more is a degradation. One lost ping reads 3.3 % in
    its bin, which is why the duration floor matters. Loss can come from the
    radio, a faulty line or an overloaded router, so it is a separate signal
    and never feeds Jitterbug's verdict.
  - Results go to InfluxDB (`persistent_congestion`, `loss_degradation`) and
    each pass replaces its window's earlier results. `INFERENCE_SINCE` keeps
    the windows from reaching before a date, after a move or a change of
    provider.
  - Not in the alert path. It runs at a lower CPU priority on at most one
    core. InfluxDB only. The image is the thirteenth, and the only one on
    Python 3.13: ruptures, which Jitterbug requires, has no 3.14 wheels yet.
    It carries PyTorch (the CPU-only build).
  - docs/inference.md explains both detectors, how to read the shading
    across targets, the settings and the cost.

### Fixed

- **A recovery says how long the problem lasted, not how long the alert
  was open.** The duration ran from the first alert to the end of the
  resolve grace period (`ALERT_RESOLVE_AFTER`, 15 min by default). For
  `microcut_burst` it also included the rule's 60-minute window, which
  keeps the rule firing after the last cut. So a short cut recovered as
  "down 1h20m" (reported from the v2.13.0-rc.3 acceptance), and a
  25-minute outage as 40 min. Read at face value, that sends someone to
  the ISP about an hour-long outage that never happened.
  - The duration now ends at the first cycle the rule no longer fired,
    and only the rules where nothing answered (`target_down`,
    `uplink_down`, `ipv6_down`, `exporter_stale`) give one. It can
    undercount by the window a rule needs before it fires; it no longer
    overcounts.
  - `microcut_burst` and `high_loss` recoveries give no duration: both keep
    firing while the event is inside their window (60 minutes; a 15-minute
    mean). Their message says what was measured.
  - Incidents recorded by the previous version still get a duration (the
    old one) on their recovery.

- **Log messages no longer cite design notes nobody has.** The Netflix OCA
  fetcher logged "disabled per TODO-257" when discovery failed, and code
  comments pointed to TODO-218/220/223 files that were never in the
  repository. Someone reading the log could not find out why no fallback
  OCA was measured. The messages and comments now say why: a guessed
  Netflix host is not the OCA that serves this network, and a hard-coded
  site list would look like a real ranking.

- **An alert no longer says "0 of 16 affected".** The breadth count in an
  alert's context line is the verdict's: targets whose 15-minute mean loss
  is over the impaired threshold. A brief cut (`outage`) or one target's
  run of lost cycles (`target_down`) fires its rule without moving that
  mean, so the alert could end with "outage · 0 of 16 affected" (seen in
  the v2.13.0-rc.3 acceptance) above a verdict line saying "No target is
  above 5% mean loss over 15m". Read on a phone, a real cut looked
  dismissed by its own alert. Both are now left out when the mean saw
  nothing; the message keeps the rule's own count, and the link note
  stays. A nonzero count can still differ from the rule's (#286).

## [2.26.0] — 2026-10-06

Alerts can reach a phone without OpenClaw, and every loss alert says what
the episode was and how sure the diagnosis is. Connecting an assistant
gives that assistant's own steps and a check that it really uses the tools.

- Pro: `sudo smoking-pi alerts --telegram` sends alerts through a Telegram
  bot of your own (`NOTIFY_MODE=telegram`). OpenClaw delivery is unchanged.
- Pro: loss alerts carry the `diagnose_loss` result when it is high or
  medium confidence (`ALERT_DIAGNOSIS_HOURS`, default 3, 0 turns it off).
- Pro: `sudo smoking-pi connect NAME --check` proves the assistant called
  the tools from its newest sign-in.
- `smoking-pi restore` enables the boot unit; CPE discovery reloads
  SmokePing only when the CPE changed.
- New settings: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
  `ALERT_DIAGNOSIS_HOURS`. No new volumes or images.

### Added

- **Alerts say what the episode was, with evidence and confidence.** The
  assistants' `diagnose_loss` sorted every loss episode into this host's
  Wi-Fi, the line, upstream or one destination, with a confidence. It saw
  what the alert's verdict could not: the first hop's loss against its
  usual level, a deaf radio, and the app layer. But only an assistant asked
  it, so an alert could say "your line" about minutes the diagnosis would
  call a deaf radio, and the person reading it would call the ISP for
  nothing.
  - When a loss alert fires, the alerter now diagnoses the last
    `ALERT_DIAGNOSIS_HOURS` (default 3) through the same code
    (`common/diagnosis_query.py`, moved out of the MCP server unchanged).
  - The matching incident's summary replaces the verdict line when it is
    high or medium confidence, followed by a "Why:" line with its evidence.
  - At low confidence the verdict stays. "No measurements are arriving"
    still outranks everything.
  - The match is strict, because a wrong cause stated with confidence is
    worse than none. An alert about a target gets only an incident naming
    that target. `microcut_burst` gets a cut on the line or a deaf radio,
    `ipv6_down` an IPv6-only incident, and `outage`/`uplink_down` a broad
    one. `unclear` never replaces the verdict.
  - Cost: about ten InfluxDB queries, only when an alert is being sent and
    once per evaluation, with a 20 s budget before the alerts go out
    without it. A failure leaves the alert as it was.

- **Alerts straight to Telegram, no OpenClaw: `sudo smoking-pi alerts
  --telegram`.** Before this, a push reached a phone only through an
  OpenClaw gateway on the Pi, or through a webhook the owner built. Remote
  assistants (Claude, ChatGPT, Grok) answer when asked but never push. So a
  Pi without OpenClaw detected outages and told nobody: `NOTIFY_MODE` was
  `off` and the guided install left alerts unchecked. The new `telegram`
  mode sends through a bot of the owner's own (`TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHAT_ID`), with the same messages, charts and silent digest as
  the OpenClaw path.
  - The command asks for the token (hidden, or on stdin; never the command
    line) and finds the chat id from a message the owner sends the bot. Reading a
    bot's messages to find the chat would have taken updates meant for
    OpenClaw on a bot it already polls (Telegram allows one reader, and
    reading marks them read), losing a message. So the command first asks
    whether another program uses the bot, and if one does it says to give
    `--to CHAT_ID` instead.
  - A preflight at start checks that the token belongs to a bot and that
    the bot can reach the chat. A bot may write only to someone who wrote
    to it first.
  - The token is in every Bot API URL. The alerter logs at INFO, which is
    also the level at which httpx logs every request URL, so the token
    would have been in `docker compose logs alerter` on every alert (the
    independent review found this). httpx's log is now turned down to
    warnings and redacts `/bot<token>`. The alerter's own code logs no URL
    and no exception text.
  - The chat id comes only from a message sent after the command asked, and
    the owner confirms whose it is. A message held from earlier, or from a
    stranger who found the bot, is not used.
  - HTML that Telegram cannot parse is sent again as plain text, so a
    formatting mistake never costs an alert.
  - Telegram is the first choice in the `alerts` menu, and Alerts is now
    preselected in the guided install, which then asks where to send them.

- **`smoking-pi connect NAME` prints that assistant's own steps.** The
  printout said "add a custom connector" for every assistant. Each one hides
  it in a different menu and names its instructions field differently, and
  the owner had to know where the sign-in should return to before checking
  it on the pairing page. A name now picks its assistant's steps: `claude`,
  `claude-code`, `chatgpt`, `cursor` or `grok`, or a name that starts with
  one (`claude-work`). The steps say where the connector goes, where the
  paragraph for its instructions goes, and where its sign-in returns to.
  `--as KIND` picks the steps for any name, and `connect --list` shows them.
  Other names get the general steps, as before. The templates are data
  (mcp-server/assistants.py): adding an assistant changes nothing in the
  server, and a test fails if the docs do not list it.
- **`smoking-pi connect NAME --check`: proof that an assistant uses the
  measurements.** A connector that signed in looked the same as one that
  answers from the measurements. The OpenClaw integration once looked
  healthy for days while the agent answered from its own shell. The server
  now records each remote connector's last tool call (at most one write a
  minute), and `connect` shows it. `--check` succeeds only when the
  assistant called a tool in the last seven days. Otherwise it says what is
  missing: the code was never typed, the assistant signed in but never
  called a tool, its last call is older than that, or it holds no live
  token. A refresh token keeps an assistant connected for 90 days after it
  last asked anything, so being connected proves nothing.
- **`connect NAME` without a tunnel offers to make one.** It used to stop
  at "set MCP_PUBLIC_URL". In a terminal with Tailscale installed, it now
  offers to run `connect --tailscale` first and then continues to the
  pairing. Otherwise it names that command.

### Fixed

- **A restored install comes back after a reboot.** `smoking-pi restore`
  started the stack but never enabled the `smoking-pi` systemd unit, which
  only `install` did. A backup restored onto a new card ran until the first
  power cut, then stayed down, losing history with nobody told (#271).
  `restore` now enables the unit on a package install. It also starts the
  unit, unless `--no-start`, which enables it for the next boot only.
- **SmokePing is reloaded only when the CPE changed.** The CPE discovery
  rewrote its targets file and sent SmokePing a reload (`HUP`) every hour,
  even when it found the same first hop. A reload restarts the probe
  cycles, so it could cost a point in every series, and the hourly noise
  hid the reloads that meant something (#272). An unchanged discovery now
  writes nothing and reloads nothing.
- **The command's "this never happens" tests can fail now.** Fifty-five
  checks in `packaging/tests/cli.bats` were written `! grep -q …`. Bash's
  errexit ignores a pipeline negated with `!`, so one of those fails a test
  only as its last line, and anywhere else it proved nothing. Among them were
  "the MCP token never reaches curl's command line", "a refused command
  touches no container" and "a secret is never passed on". A regression in
  any of them would have shipped green. Proven by injecting the token into
  curl's logged command line: the old check passed, the new one fails.
  - Every such check is now `if cmd; then false; fi`, which works with the
    bats that Ubuntu's apt installs in CI. Two in `show-passwords.bats` were
    changed the same way.
  - The docker log is created in `setup`, so a check for what must be
    absent gets a clean "not there". Before, six checks grepped a file that
    did not exist, and grep's "no such file" also passed.
  - Every rewritten check passes against the current command, so none of
    them was hiding a bug.

## [2.25.0] — 2026-10-06

On Pro, publishing Smoking Pi for remote assistants is one command, and
connecting one tells you what to paste into its instructions.

- Pro: `sudo smoking-pi connect --tailscale` signs the Pi in to Tailscale,
  keeps Tailscale out of the Pi's DNS (checked before anything is
  published), turns Funnel on, sets `MCP_PUBLIC_URL` and checks it from
  outside. `--off` undoes it. Other tunnels work as before.
- Pro: `sudo smoking-pi connect NAME` also prints a paragraph for the
  assistant's own instructions. Failed sign-ins no longer leave connectors
  behind.
- No new settings, volumes or images.

### Added

- **`sudo smoking-pi connect --tailscale`: the tunnel for remote assistants
  in one command.** Publishing the MCP server took five commands by hand on
  the reference Pi, and three traps: Tailscale's DNS taking over the Pi's
  resolver (which once left every container with a dead one), a tailnet
  name typed as a placeholder twice, and a `-1` suffix nobody expected. The
  command installs Tailscale if asked, keeps it off the Pi's DNS, signs in
  (printing the link, which also works over SSH), turns Funnel on, sets
  `MCP_PUBLIC_URL` from the name Tailscale reports, and checks the address
  from outside. `--off` undoes it. Other tunnels are unchanged
  (docs/remote-connector.md).
- **What to paste into an assistant's own instructions.** The server tells
  every assistant how to answer, but nothing made one think of Smoking Pi
  when asked about the internet in passing. `smoking-pi connect NAME` now
  prints a short paragraph for the assistant's custom instructions (rules,
  system prompt): when to use the tools, and that it cannot change
  anything. It is one text for every assistant (`ASSISTANT_INSTRUCTIONS` in
  mcp-server/guide.py), quoted word for word in the docs.

### Fixed

- **A failed sign-in no longer leaves a connector behind.** Each of the
  failed attempts on the reference Pi left a `grok  signed out` row that no
  token could ever reach, next to the one that worked. `disconnect grok`
  would have removed all three. A pairing whose authorization code expired
  unused is now dropped, including ones already there.

## [2.24.2] — 2026-10-05

A remote assistant whose own sign-in callback redirects, as Cursor's does
(and so Grokbot's), can now finish signing in. 2.24.1 fixed only the first
hop of the way back.

- Pro, remote assistants only; nothing changes for OpenClaw on the Pi.
- No new settings, volumes or images. After upgrading, make a new code
  (`sudo smoking-pi connect NAME`) and reconnect from the assistant.

### Fixed

- **A remote assistant still could not finish signing in when its own
  callback redirects (Cursor, so Grokbot).** 2.24.1 let the pairing form
  return to the assistant's callback origin, but browsers apply
  `form-action` to every hop after a form POST, and Cursor's callback at
  `www.cursor.com` redirects to `cursor.com`: that second hop was blocked
  the same silent way, and the owner's third attempt on the reference Pi
  failed like the first two. No list of sources can foresee an
  assistant's redirects. Now the form never leaves the Pi: a right code
  goes to a *Connected* page on the Pi (`303`), which sends the browser on
  to the assistant, so the form's policy is back to `form-action 'self'`.
  The token to that page rides in an HttpOnly, Secure cookie, not the URL,
  so the access log never holds what leads to the code. Proven in
  Chromium against the real server with a callback that redirects to
  another origin: 2.24.1 logs the CSP violation and the assistant never
  gets a code; this version arrives and the code exchanges for a token.

## [2.24.1] — 2026-10-05

A remote assistant can now finish signing in. In 2.24.0 the right pairing
code was accepted, but the browser was not allowed back to the assistant,
and a second click said the sign-in had expired.

- Pro, remote assistants only; nothing changes for OpenClaw on the Pi.
- No new settings, volumes or images. After upgrading, make a new code
  (`sudo smoking-pi connect NAME`) and reconnect from the assistant.

### Fixed

- **A remote assistant could not finish signing in: the browser never went
  back to it.** The pairing page allowed its form to go only to the Pi
  (`form-action 'self'`), and browsers apply that to the redirect after the
  form too, so with the right code the return to the assistant was blocked
  without a word. The first real connection (Grokbot, through Cursor's
  callback) failed this way twice; the in-process tests never ran a
  browser. The owner then clicked again and was told *This sign-in
  expired*, though the code had been accepted and used up. Now the page also
  allows exactly the address the sign-in returns to (its origin, or an
  app's own scheme; never `javascript:`, `data:` and the like), checked in
  headless Chromium before and after; and a second click with the same code
  sends the browser back again while the assistant has not used it, or
  says the sign-in was already used.

## [2.24.0] — 2026-10-04

On Pro, any assistant that can add a remote MCP server — Claude, ChatGPT,
Grok and the next one — can now connect to Smoking Pi with a URL and a
pairing code, read-only, and every assistant gets the same answering guide.

- Pro: `sudo smoking-pi connect NAME` prints the URL and a one-time pairing
  code; the assistant signs in with it and gets the read tools only.
  `smoking-pi disconnect NAME` signs one out. Off until `MCP_PUBLIC_URL` is
  set to the HTTPS address of a tunnel to the Pi (docs/remote-connector.md).
- Pro: how to answer from the tools is one guide, sent by the server to
  every assistant; the OpenClaw skill is generated from it.
- One new setting, `MCP_PUBLIC_URL` (empty = off), and one new volume,
  `mcp-connector`. No new images. After upgrading, `sudo smoking-pi
  openclaw` installs the new skill.

### Added

- **Any assistant can connect, the same way: `smoking-pi connect NAME`.**
  Only an assistant on the Pi itself could use Smoking Pi (OpenClaw, with
  the local token); a cloud assistant had no way in short of a shell on the
  Pi, which gives an AI agent far more than reading measurements needs —
  every target, the configuration and months of history, with no way to
  take it back short of rotating the machine's keys.
  With `MCP_PUBLIC_URL` set to the HTTPS address a tunnel publishes the MCP
  server at, any assistant that can add a remote MCP server (Claude,
  ChatGPT, Grok, and the next one) adds that URL and signs in with a
  one-time pairing code from `sudo smoking-pi connect NAME` — OAuth 2.1, as
  the MCP specification asks, served by the MCP SDK. It gets the read tools
  only: the seven that change something answer *read-only*, and
  `get_chart` does not post into the owner's chat for it. Each assistant has
  its own revocable tokens (`smoking-pi disconnect NAME`); the local token
  keeps full access on the Pi only — through the tunnel it is refused, so a
  leaked one is not a key from the internet. Off unless `MCP_PUBLIC_URL` is set; a new
  `mcp-connector` volume holds the hashed tokens. `smoking-pi connect
  openclaw` is the old `smoking-pi openclaw`, which stays. See
  docs/remote-connector.md.

### Changed

- **One answering guide for every assistant.** How to answer from the
  tools — start from `diagnose_loss`, read the units, the false alarms, the
  report shape, the links — lived in the OpenClaw skill, so any other
  assistant (Claude, ChatGPT, Grok…) connected to the same server got only
  the tool descriptions and a short note, and every new assistant would
  have needed its own copy of the rules to keep in step. The guide is now
  `shared/modules/mcp-server/guide.py`, sent by the server as its
  instructions to every MCP client, with this Pi's timezone added. The
  OpenClaw skill is generated from it plus a short OpenClaw/Telegram header
  (`shared/scripts/build-openclaw-skill.py`; a test fails on a stale copy),
  and shrinks from 535 to 263 lines. Deployment values are no longer
  written in: the gateway's loss floor is read from `get_microcut_stats`.
  After upgrading, `sudo smoking-pi openclaw` installs the new skill.

## [2.23.0] — 2026-10-04

On Pro, the assistant can now say what each loss episode was — this host's
own Wi-Fi, the line to the ISP, one site, or beyond the line — with how
sure it is and the evidence, instead of attributing raw numbers on its own.

- Pro: the new MCP tool `diagnose_loss(hours)` classifies every loss
  episode in up to a week. On the reference Pi's week, its 2026-10-01
  radio hang is `local_wifi`, `high`. The OpenClaw skill now starts from
  it.
- Pro: `smoking-pi dns adopt --dry-run` says when an adoption fits the
  measurement budget.
- No new images and no new settings. After upgrading, `sudo smoking-pi
  openclaw` refreshes the OpenClaw skill.

### Added

- **`diagnose_loss`: what each loss episode was, with the evidence and how
  sure.** The assistant answered "what happened last night?" by reading
  loss events, microcuts and Wi-Fi numbers and attributing on its own — the
  way a hung radio came to be reported as an outage. The new MCP tool folds
  every loss episode in a window (up to a week) across all targets and
  classifies it: `probe_miss`, `local_wifi` (this host's own radio: deaf,
  disassociated, carrier, weak signal), `local_link` (the line to the ISP),
  `destination`, `upstream` (including `spread`, a low-grade loss on most
  paths with a normal first hop), or `unclear`. Each carries `high`/
  `medium`/`low` confidence, the `evidence` and `against` sentences with
  their numbers, and a graph link; a class that needs evidence the host
  lacks is never `high`. The ISP first hop's ping target is no longer read
  as a destination (its loss is the gateway's rate-limit floor). Replayed
  on the reference Pi's week: its 2026-10-01 radio hang is `local_wifi`,
  `high`; five lone two-ping points are `probe_miss`; a night of a ping or
  two lost on 11 of 18 destinations with a normal first hop is `upstream`/
  `spread`, `medium`. The OpenClaw skill
  and the server instructions now start from it; after upgrading,
  `sudo smoking-pi openclaw` refreshes the skill. Logic in
  `common/diagnosis.py`; see docs/detection-reliability.md.

### Fixed

- **`smoking-pi dns adopt --dry-run` now says when an adoption fits the
  measurement budget.** It spoke only when one did not, although its help
  says it "says whether it fits", so a silent dry run could mean "fits",
  "forced" or "the budget could not be read". It now prints "Fits the
  measurement budget.", or that the budget could not be read (the API's
  dry-run verdict carries `checked: false` then, so "admitted" is not
  mistaken for "fits").

## [2.22.0] — 2026-10-04

On Standard and Pro, a change that would push the measurements past the
measurement budget is now refused before it is saved, with the numbers,
instead of turning the budget card red afterwards.

- Adding or turning on a target, moving it to a costlier probe, a shorter
  step or more pings, and, on Pro, a DNS wizard adoption are priced
  against the budget; over a ceiling, config-manager answers
  `409 over_budget` and nothing is saved. `?force=1` admits it anyway (on
  Pro, `smoking-pi dns adopt --force`; `dns adopt --dry-run` says
  beforehand whether it fits).
- A change that lowers the cost is always admitted, nothing already
  measured is throttled, and a budget that cannot be computed never blocks.
- No new images and no new settings. A Pro Pi close to its ceiling may
  now see a large DNS wizard adoption refused: raise
  `MEASUREMENT_BUDGET_MB_PER_DAY` (or `_SAMPLES_PER_HOUR`) or pass
  `--force`.

### Added

- **A change that would push the measurements past the budget is refused
  before it is saved (admission).** The measurement budget only reported:
  a DNS wizard adoption, a faster step or a batch of top sites could take
  a metered connection several times past its ceiling, and the card turned
  red after the fact (on the reference Pi the first reading was 126% of
  the ceiling, almost all of it one adoption). config-manager now prices
  each change that adds cost (a target added or turned on, a move to a
  costlier probe, a shorter step or more pings, an adoption net of the
  layers it deactivates) against the budget as it stands, and answers
  `409 over_budget` with the cost, the totals now and after, the ceilings
  and the headroom when it would cross one. Nothing is saved. `?force=1`
  (`smoking-pi dns adopt --force`) admits it anyway, and
  `dns adopt --dry-run` says beforehand whether it fits. A change that
  lowers the cost is always admitted, even over budget, and a budget that
  cannot be computed never blocks. The web admin (add, edit, toggle,
  Probes page, top-sites picker), its assistant and the MCP tools say
  "Not saved: over the measurement budget" with the numbers, in their own
  words. Priced as the report prices it: on the staging Pi's generated
  files the two agree to the sample for all nine probes. Nothing already
  measured is ever throttled or dropped; a YAML upload, the first-run seed
  and the nightly OCA refresh are not admitted (they set the
  configuration rather than add to it). A guardrail, not a lock: two
  changes sent at the same moment can each fit and cross a ceiling
  together.

## [2.21.0] — 2026-10-03

On Pro, a hung Wi-Fi radio on the monitor is no longer reported as a
microcut, and alert and assistant links can use the Pi's `.local` name
instead of its LAN address.

- Pro: when the Pi's own Wi-Fi was deaf for a whole cut (associated but
  receiving nothing, or not associated), the cut is reported apart, as
  `deaf`, by the assistants, the digest, the AI report and the alerter,
  not as a microcut. On the reference Pi that was five of the six
  confirmed cuts since 19 September.
- Pro: `sudo smoking-pi links --lan mdns` points links at the name the
  mdns service holds; `smoking-pi links` warns if it changes.
- Docs and tools: the DNS observer's own-traffic list is right only on a
  Pi that does nothing but measure; `tools/dns-explore` reports top-K
  stability.
- No new images and no new settings. After upgrading, `sudo smoking-pi
  openclaw` refreshes the OpenClaw skill, which now says how to report a
  deaf span.

### Added

- **Assistant and alert links can use the Pi's `.local` name: `smoking-pi
  links --lan mdns`.** Since v2.18.0 the mdns service holds
  `smoking-pi.local`, but links could only follow it by typing the name,
  and a typed name goes stale: after a name conflict the service holds
  `smoking-pi-2.local`, and a link to `smoking-pi.local` then opens the
  other host. So answers in Telegram kept a raw LAN address, which breaks
  when the DHCP lease changes. `--lan mdns` stores the name the service
  holds now (it refuses while the service is off or still probing), and
  `smoking-pi links` warns when the stored `.local` name and the held one
  part ways. `--lan auto` stays the default advice: only devices that
  resolve `.local` names can open these links. Checked from a Linux
  machine on the reference network (not yet from a phone):
  `smoking-pi.local` resolves, and Grafana and the web admin answer on
  it. The template's example address is generic now (it was the
  reference Pi's).

- **`tools/dns-explore` reports how stable the daily top-K is, and what
  an enter/leave rule would have done with it.** The week of DNS log
  collected for the domain-selection design was meant to set the
  automatic selection's hysteresis, but the tool only gave a mean
  day-over-day Jaccard, which cannot say how long a service stays away
  before it returns, nor how many changes a rule allows. Without those
  numbers the thresholds would have been guesses, and each wrong
  retirement cuts a target's time series. The new table gives, per level,
  score and K: units ever in a daily top-K, in it every day, on one day
  only; re-entries and the longest absence before a return; units tied at
  the K-th score; and the adds and drops under each `--rules E:L` pair.
  Churn and stability now compare whole days only: the re-run's
  first and last days covered about 7 and 17 hours and read as churn
  (`--all-days` restores the old behavior).
  On the reference Pi's week (27 September–2 October, whole days),
  services by queries at K = 20: 15 of the 26 that were ever in a daily
  top-20 were in it every day, no service came back after more than two
  days away, and enter-after-3/leave-after-5 made no change all week at
  K = 10 and 20 (+3/−6 at K = 50). Presence ties 18 services at the cut
  for K = 10: it filters, it does not rank, and its rows look stable only
  because the tie-break picks the same names every day. A day missing
  from the log is unknown, not adjacent: it breaks every streak and never
  starts an absence. The scheduled run itself still used the 26 September tool,
  which counted SmokePing's lookups (28.7% of the log) as the house's;
  it was re-run with this version.

### Fixed

- **A hung Wi-Fi radio on the monitor is no longer reported as a
  microcut.** The CPE probe measures through the host's own uplink, so
  when the radio hangs (associated, receiving nothing) its windows read
  100% and fold into one long "confirmed cut". On the reference Pi, five of
  the six confirmed cuts since `wifi_link` began (19 September) were that,
  from 70 s to 3 h 25 min. The assistant answered "3 cuts, the longest
  1 h 52 min" for a week whose line never dropped, the AI report and digest
  said the same, and `microcut_burst` fired beside `uplink_down` for one
  fact. Each cut is now checked against the uplink interface's
  received-packet counter: if it stood still (or the radio was not
  associated) through the whole cut, the cut is this host's. It is left out
  of the cuts, counts, worst windows and `microcut_burst`, and reported
  apart as `deaf` / `deaf_note` ("this host's Wi-Fi heard nothing for
  2 h 43 min (3 spans): the monitor was deaf, not the link cut"). A wired
  host, or one without `wifi_link`, keeps every cut as before. Only the
  deaf samples leave InfluxDB, so a healthy week costs nothing. Evidence
  and the replay are in `docs/detection-reliability.md`, *When the monitor
  is deaf*. The OpenClaw skill says how to report `deaf`; refresh it with
  `smoking-pi openclaw` after upgrading.

- **The DNS observer guide warns that the own-traffic list is right only
  on a Pi that does nothing but measure.** The wizard and
  `tools/dns-explore` leave out Smoking Pi's own lookups by a list of
  names; they cannot tell anything else the Pi runs from the house,
  because every query reaches the observer from the router's address. On
  a Pi that also hosts an assistant, a bot or a tunnel (the reference Pi
  does), those lookups rank as services the house uses, and a selection
  built on that ranking would measure them. The guide and the tool's
  README now say so, and name the remedy: `DNS_WIZARD_EXCLUDE`
  (`--exclude-file` in the tool), or moving those programs elsewhere.
  Decided with the domain-selection design: keep excluding by list first.

## [2.20.0] — 2026-10-02

On Pro with InfluxDB, the Overview shows the bandwidth in use, in bit/s,
per service and for the whole Pi, beside the MB/day it already showed.
The netmeter stops logging an ERROR on every upgrade.

- Pro (InfluxDB): *Bandwidth in use by service (bit/s)* stacks each
  service's rate with the whole Pi's uplink as a dashed line, and
  *Bandwidth now* gives the uplink's last five-minute interval, download
  and upload apart. Five-minute averages, not peaks.
- Pro: for its first 120 s after a start, the netmeter logs the
  config-manager and InfluxDB errors of a stack still starting at INFO,
  and sends a batch InfluxDB refused once more with the next interval.
- No new images and no new settings. As with every release, the upgrade
  recreates the containers on the new version.

### Added

- **Pro: the bandwidth in use, in bit/s, per service (InfluxDB).** The
  Overview showed each service's traffic as MB/day, a budget figure, but
  not the question a link asks: how much of it is in use now? A 10 GB/day
  reading had to be converted by hand into ~0.9 Mb/s. A new panel,
  *Bandwidth in use by service (bit/s)*, stacks each service's rate from
  the netmeter's counters with the whole Pi's uplink as a dashed line,
  and *Bandwidth now* gives the uplink's last interval, download and upload
  apart. Each window's rate is its bytes over its seconds, not a mean of
  per-interval rates, and a window with less than a minute counted (the
  sub-second interval a restart leaves behind) is left out, so neither
  can spike it. Five-minute averages: a burst shorter than that is spread
  over its interval. `docs/measurement-budget.md`, "Bandwidth".

### Fixed

- **Pro: the netmeter no longer logs an ERROR on every upgrade, and keeps
  the interval InfluxDB was not ready for.** Right after `smoking-pi
  upgrade` recreates the stack, config-manager and InfluxDB are still
  starting, so the netmeter logged `WARNING containers not read:
  ConnectionResetError` and `ERROR traffic not written: ProtocolError`
  (seen on the staging Pi during the v2.19.0-rc.1 acceptance), then
  recovered on its own within a minute. An ERROR on every upgrade trains
  people to skip past real ones, and the interval that failed was dropped
  from the `service_traffic`/`internet_traffic` series (the state file
  had it). For the first 120 s after it starts, the netmeter now logs
  those two at INFO, saying the service is still starting; afterwards
  they stay WARNING and ERROR. A batch InfluxDB refused is sent again
  with the next interval's, once: at most one batch waits, never a
  backlog. Only the exception's type is logged, as before.

## [2.19.0] — 2026-10-02

On Pro, the Pi now answers "how much this month?": a ledger of the bytes
it sent and received per day, with the part that went to the Internet, in
a command, a dashboard card and (with InfluxDB) a Grafana row. The
measurement budget's estimate is corrected for HTTP/3, and the doctor
warns when Avahi renames the host.

- Pro: the uplink meter and the netmeter keep bytes in and out per local
  date for 400 days, and each service's bytes per month for 25.
  `smoking-pi traffic`, `GET /traffic` and the web admin's new Traffic
  card report today, yesterday, this week, this month, last month (and, in
  the command, the last 30 days), each with how much of the period was
  measured. The ledger starts at the upgrade, so the card and the command
  count from then; the Overview's new Traffic row sums the series already
  in InfluxDB, back to when the meters started in v2.18.0 (the
  Internet-only line starts at the upgrade).
- Pro: the netmeter's nftables table gains the Internet-only totals:
  traffic whose other end is not on the local network (private,
  link-local, multicast, broadcast). Still counters only; with InfluxDB, a
  new `internet_traffic` series.
- The budget prices an HTTP/3 sample at 18 KB, and HTTP/1.1 and HTTP/2 at
  12.5 KB (was 12 KB for all). On a test Pi the estimate had been 29%
  under what the netmeter counted for SmokePing; the shipped seed's
  estimate rises from ~98 to ~117 MB/day.
- `doctor --live` has a new check, `avahi-host-name`, which `smoking-pi
  upgrade` runs unless given `--skip-doctor`.
- No new images and no new settings. As with every release, the upgrade
  recreates the containers on the new version; the netmeter reloading its
  table costs one five-minute interval of coverage.

### Added

- **The doctor notices when Avahi renames the host.** On the reference Pi
  the host's Avahi renamed itself to `smokingpi-2.local` eight seconds
  after boot, twice (2026-09-29 and 2026-10-01). From then on
  `smokingpi.local` resolved nowhere, and nothing said so until someone
  typed the name and it failed. `smoking-pi.local` (the mdns service) is
  unaffected, but people and old bookmarks still use the host's name. A
  new `doctor --live` check, `avahi-host-name`, reads Avahi's process
  title and warns when it answers for anything but `<hostname>.local`,
  with the fix. `smoking-pi upgrade` runs it unless given `--skip-doctor`.
  `docs/mdns.md` has a new section, "When `<hostname>.local` stops
  resolving": how to check, the restart that is known to work, and the
  unproven cause.

- **Traffic accounting: what the Pi sent and received, by day, week and
  month (Pro).** The meters gave a rate (MB/day over the last 24 h), but
  not the question a data plan asks: how much this month? Nothing kept
  more than a day, outside InfluxDB, and nothing at all with ClickHouse.
  The uplink meter and the netmeter now keep a ledger in their state
  files: bytes in and out per local date (`TZ`) for 400 days, and each
  service's bytes per month for 25 (per day it would more than double a
  file rewritten every five minutes on an SD card). Since that file now
  holds history nothing else keeps, each meter writes it with an fsync,
  keeps the previous copy, and sets a damaged file aside instead of
  starting over. The netmeter also counts the **Internet-only** part: the
  same uplink without traffic to private, link-local and multicast
  addresses, which is what an ISP's cap sees (two new totals in its
  nftables table, still counters only; with InfluxDB, a new
  `internet_traffic` series). config-manager's new `GET /traffic` and
  `smoking-pi traffic [--json]` report today, yesterday, this week, this
  month, last month and the last 30 days, each with how much of the period
  each meter measured: a reboot loses five minutes, and nothing is
  extrapolated. `docs/measurement-budget.md`, *Traffic accounting*; it
  also corrects that the microcut detector's pings stay on the LAN: they
  go to the ISP's first hop, across the uplink.
- **A Traffic card on the web admin's dashboard (Pro).** "How much this
  month?" needed SSH and `smoking-pi traffic`; the dashboard, where people
  look, had only the 24-hour rate. The card shows the same figures: this
  month's total (sent and received, and the Internet-only part), a row per
  period (today, yesterday, this week, this month, last month) with how
  much of it was measured, flagged when under 99%, and this month by
  service. The top *Bandwidth Usage* card adds "This month: …". `/traffic`
  is cached for a minute like `/budget`, and a failure is never cached.
- **A Traffic row in Grafana's Overview (Pro, InfluxDB).** The Overview
  showed traffic only as a rate (MB/day per five-minute interval), which
  cannot be read as "how much this month". The new row has received and
  sent per day over 30 days with the Internet-only part as a line, this
  month so far and last month, and each service per calendar month over 12
  months, summed from the existing series. Days and months follow the
  dashboard's time zone (Flux `timezone.location` from `${__timezone}`);
  checked in a real browser on a test Pi under Chicago and Tokyo, where a
  naive month label would have put Tokyo's October in September. The
  *Measured traffic by service* panel's description no longer says the
  microcut detector pings "the router".

### Fixed

- **The measurement budget priced HTTP/3 like HTTP/2, and HTTP/3 costs
  about 45% more.** On a test Pi with the DNS wizard's HTTP targets, the
  netmeter counted SmokePing at ~528 MB/day over 14.6 steady hours while
  the budget estimated 410: 29% short, on the figure the ceiling and the
  red/green of the card are judged by. Each probe's HEAD, measured on its
  own (the probes' exact curl command in a throwaway container, reading
  its byte counters; 40 targets, 3 samples each), cost 12.5 KB on average
  over HTTP/1.1 and HTTP/2 but 17–18 KB over HTTP/3: QUIC pads every
  client Initial to 1200 bytes and acknowledges on its own. The budget now
  prices a Curl probe whose arguments ask for HTTP/3 at 18 KB and the
  others at 12.5 KB (was 12 for all). With the microcut detector's ~24
  MB/day, which the estimate does not count, that comes within 1% of the
  meter. The shipped seed's estimate rises from ~98 to ~117 MB/day (12% of
  the default ceiling); a wizard-heavy install, mostly HTTP, rises the
  most. `docs/measurement-budget.md` also corrects the microcut detector's
  share: ~24 MB/day per CPE address, counted under `smokeping`, not ~50 of
  LAN traffic.

## [2.18.0] — 2026-10-01

The Pi answers for `smoking-pi.local` from its own container, and the
measurement budget now has a meter beside its estimate: what the uplink
really carried, and (Pro) which service carried it.

- A new `mdns` service, in every edition, answers for `smoking-pi.local`
  (`MDNS_NAME`). On the reference Pi the host's Avahi renamed itself to
  `smokingpi-2.local` eight seconds after a boot, so the only `.local`
  name it had stopped resolving. A second Smoking Pi on the same network
  takes `smoking-pi-2.local`; give each its own `MDNS_NAME`.
  `smoking-pi url` prints the name the service holds.
- Pro: a new exporter in the SmokePing container reads the uplink
  interface's byte counters every five minutes. `/budget`,
  `smoking-pi budget`, the dashboard's budget card and the Overview show
  the measured MB/day against the same ceiling as the estimate.
- Pro: a new `netmeter` service attributes the uplink's bytes to each
  container with a counter-only nftables table (`inet smoking_pi_meter`)
  that never drops, accepts or rewrites a packet, and is removed when the
  container stops; `NETMETER=off` loads nothing. Ten minutes on the test
  Pi (still under the hour a daily figure needs): the uplink ~524
  MB/day, SmokePing ~464 MB/day of it, against a ~410 MB/day estimate.
- Two new images, `mdns` and `netmeter`: twelve in all. `smoking-pi
  upgrade` starts `mdns` in every edition and `netmeter` in Pro.

### Added

- **The Pi answers for `smoking-pi.local`, from its own container.** The
  only name the Pi had on the network was the host's `<hostname>.local`,
  from Avahi, and on the reference Pi it broke twice (2026-09-29 and
  2026-10-01): eight seconds after
  a boot Avahi logged `Host name conflict, retrying with smokingpi-2` and
  from then on answered only for `smokingpi-2.local`. Nothing else held
  the name; Avahi took its own echoed announcement for another host.
  `smoking-pi url` kept printing `smokingpi.local`, which no longer
  resolved, and the fix was a manual Avahi restart until the next boot.
  The new `mdns` service, in every edition, is a small multicast DNS
  responder on the host network that claims `MDNS_NAME` (default
  `smoking-pi`) after probing for it, ignores packets from its own
  addresses and records identical to its own, falls back to
  `smoking-pi-2.local` only when another host really answers with other
  addresses (and only to packets from the LAN, with IP TTL 255, from
  port 5353), and says goodbye (TTL 0) when it stops. It needs no Avahi
  on the host, runs as `nobody` with no capabilities on a read-only
  filesystem. `smoking-pi url` now prints the name the service holds.
  `MDNS_NAME=off` turns it off; `MDNS_INTERFACES` narrows the interfaces
  (Docker bridges, veths and VPNs are never used). It is the eleventh
  published image. `docs/mdns.md`.

- **Measured traffic by service.** The uplink meter says how much the Pi
  sends and receives, not who. On the test Pi it read above the budget's
  estimate, and the only way to find the difference was to guess:
  SmokePing, the DNS observer, an image pull, an assistant on the
  host. Getting an outlier wrong means cutting the wrong thing. A new Pro
  service, `netmeter`, attributes the uplink's bytes to each container
  with a counter-only nftables table (`inet smoking_pi_meter`).
  Host-network services are keyed by their sockets' cgroup and a
  conntrack mark byte clear of Tailscale's. Bridged containers are keyed
  by address in the forward hook. The rest goes to `host` and
  `other_containers`. No rule drops, accepts or rewrites a packet, and
  stopping the container removes the table. It runs with `CAP_NET_ADMIN`
  only, asks config-manager which container is which (new
  `GET /meter/containers`) instead of holding the Docker socket, and
  writes `service_traffic` to InfluxDB. `smoking-pi budget`, the web
  admin's budget card and a new Overview panel list the services, most
  traffic first. Ten minutes on the test Pi (still under the hour a daily
  figure needs): the uplink ~524 MB/day,
  the services ~506 MB/day between them, SmokePing ~464 MB/day of it
  against its ~410 MB/day estimate (a two-minute reading had said ~943:
  short windows catch probe bursts). `NETMETER=off` loads nothing. It is
  the twelfth published image. `docs/measurement-budget.md`, "By
  service".

- **Measured traffic beside the budget's estimate.** The measurement
  budget only ever estimated: configured probes times a per-sample cost
  measured once. Nothing said what the Pi really sent and received. On
  the reference Pi the estimate read 1263 MB/day and nothing could confirm
  or refute it. A wrong per-sample cost, or traffic outside SmokePing (the
  microcut detector, the DNS observer, image pulls, an assistant), would
  stay invisible until a metered connection's bill showed it. A new
  exporter in the SmokePing container (Pro), `uplink_traffic`, reads the
  uplink interface's own byte counters every five minutes. It writes them
  to InfluxDB (`uplink_traffic`) and keeps the last 24 hours on the config
  volume. `/budget`, `smoking-pi budget` and the dashboard's budget card
  show the measured MB/day, in and out, against the same ceiling. The
  Overview draws it as a solid line over the stacked estimate. It counts
  everything on the interface, so the gap between the two is what the Pi
  spends on other things. `docs/measurement-budget.md`, "Measured traffic".

## [2.17.0] — 2026-10-01

What the measurements cost is on the dashboard and in Grafana, and two
places that echoed exception text no longer do.

- The web admin dashboard (Standard and Pro) has a *Measurement budget*
  card: MB/day and samples per hour against their ceilings, the headroom,
  and every probe, most expensive first. The old *Bandwidth Usage* card
  counted 64 bytes a sample and showed the seed at 0.2 Kbps, about 2 MB a
  day, when the budget says about 98 MB a day; the Probes page's *Traffic*
  column put an HTTP probe at about 1/190 of its cost. Both now use the
  budget's measured costs.
- Grafana's Overview (Pro, InfluxDB) has a *Measurement budget* row: MB/day
  and samples per hour per probe, stacked, against the ceiling as a dashed
  line. A new exporter in the SmokePing container writes the budget every
  five minutes, so the row fills from the upgrade on. The SmokePing
  container is recreated on upgrade to pick up two new settings
  (`CONFIG_API_URL`, `CONFIG_API_TOKEN`); it reads the same
  `CONFIG_API_TOKEN` config-manager does.
- The DNS observer card and the Probes page's refusal messages no longer
  show exception text.
- `docs/wifi.md`'s power save steps work on netplan hosts, and the page
  shows how to keep the journal across reboots. It also dates the change
  on the reference Pi: a latency-floor shift after 2026-10-01 17:15:07 UTC
  is most likely power save going off.

### Added

- **The dashboard shows the measurement budget, and its bandwidth figures are
  the measured ones.** The *Bandwidth Usage* card counted every sample as 64
  bytes. An HTTPS HEAD is about 12 KB, so on the shipped seed it showed 0.2
  Kbps, about 2 MB a day, when the budget says about 98 MB a day: some 45×
  low. The Probes page's *Traffic* column used the same formula, which put
  an HTTP probe at about 1/190 of its cost. Anyone deciding what to add on a
  metered link was reading the wrong number. Both now come from
  config-manager's `/budget` (`smoking-pi budget`'s report). A new
  *Measurement budget* card shows MB/day and samples per hour against their
  ceilings, the headroom, and every probe, most expensive first. It turns
  yellow at 75% and red over the ceiling, and says when config-manager
  cannot be asked instead of guessing. The report is reused for a minute,
  since each one reads two files out of the SmokePing container. The old
  64-byte estimate (`calculate_bandwidth`) is gone.
- **Grafana's Overview charts the measurement budget over time.** The budget
  was a snapshot: `smoking-pi budget` and the dashboard card say what the
  configured measurements cost now. Nothing showed when it changed. On
  staging, the DNS wizard's HTTP probes are 300 of its 410 MB a day (41% of
  the ceiling), and nothing recorded when they were added. A new exporter,
  `measurement_budget.py` in the SmokePing container, writes
  config-manager's `/budget` report to InfluxDB every five minutes
  (`measurement_budget`, `measurement_budget_probe`). A *Measurement budget*
  row on the Overview stacks MB/day and samples per hour per probe against
  the ceiling as a dashed line, with the share of each ceiling in use. The
  SmokePing container now gets `CONFIG_API_URL` and `CONFIG_API_TOKEN` to
  ask config-manager; it already held the InfluxDB token. InfluxDB only:
  ClickHouse mode has no Overview.

### Security

- **The DNS observer card no longer returns exception text.** When
  `status.json` could not be read, config-manager's `/dns/observer` put the
  exception into the card's `reason` (`Unreadable status file: [Errno 13]
  Permission denied: '/dns-observer/status.json'`), and the web admin showed
  it. That broke the rule that error responses never come from the exception
  object (CodeQL alert #80, `py/stack-trace-exposure`, open since #161). The
  leak was small: an internal path or a JSON parse position, behind the API
  token. The card now says the status file is unreadable and points to the
  logs, and the exception goes to the config-manager log with its traceback.
- **A refused probe change no longer shows exception text.** The Probes
  page showed whatever text came with a `ValueError` when a
  probe change failed. That was config-manager's `error` string for any
  400/404 answer, from whatever answered at `CONFIG_MANAGER_URL`. It was
  also the parser's text when the answer was not JSON, because `requests`'
  `JSONDecodeError` is a `ValueError`. That broke the rule that error text
  never derives from an exception, and an HTML error page from a proxy
  would have been described on the page. config-manager's probe refusals
  now carry a fixed `reason` code (`step_not_allowed`,
  `pings_out_of_range`, `cycle_outruns_step`, `probe_not_found`, …) and
  their numbers. The page picks its own text from a literal table by that
  code, and fills in only numbers the client checked. An answer that is
  not JSON is now an ordinary failure ("config-manager did not save the
  change."), and config-manager's own text goes to the web-admin log.
  Found while reviewing PR #232.

### Changed

- **The Wi-Fi power save advice covers netplan hosts and the journal.**
  `docs/wifi.md` named a NetworkManager profile by SSID and assumed `iw` was
  on the `PATH`. On Debian 13 the profile is `netplan-wlan0-<SSID>` and `iw`
  is in `/usr/sbin`, so the steps as written failed. It also gave no way to
  keep the evidence: Raspberry Pi OS keeps the journal in RAM, so the reboot
  that ends a radio hang erases its kernel messages, which is why the
  2026-10-01 outage has no explanation. The page now gives the netplan
  profile name, the shorter path when `iw` already switched power save off
  (no link drop, no revert timer), and a capped persistent-journal override.
  It also dates the change on the reference Pi: power save off since
  2026-10-01 17:15:07 UTC. A latency-floor shift after that instant is most
  likely this change.

## [2.16.0] — 2026-10-01

The HTTP probes measure what they claim to, and what the measurements cost
is visible.

- The HTTP probes downloaded the whole home page on every sample, about
  5.7 GB a day for the seed's Cloudflare target alone, and timed the
  download. They now send `HEAD`, 3 samples per round, and the whole seed
  comes to ~98 MB/day. The HTTP series steps down at the upgrade;
  SmokePing's own HTTP graphs restart (the old files are archived), and
  Grafana's history runs straight through.
- `smoking-pi budget` reports samples per hour and MB per day per probe
  against a ceiling (default 1000 MB/day). Accounting only.
- Charts are drawn as paper figures sized for a phone (1100 × 880 px,
  16–20 pt type) and sent as inline photos. **Installs that never set
  `CHART_THEME` or `ALERT_IMAGE_AS_DOCUMENT` change look on upgrade**; the
  entry below says how to keep the old one.
- The assistant no longer draws charts of its own.
- **`smoking-pi install` no longer prints the new install's passwords and
  tokens.** An install made before this release still has them in whatever
  recorded that terminal (scrollback, transcripts, anything pasted from it).
- `smoking-pi install` sets up the services it switches on, and a new
  install's first alert is no longer a false "critical: the monitor, not
  the network".
- After upgrading, run `sudo smoking-pi openclaw` so the assistant picks up
  the new skill text.

### Added

- **The documentation shows the charts.** The alerting and MCP docs
  described the alert, digest and `get_chart` images in words only, so
  nobody could tell what an alert would look like before receiving one.
  `docs/alerting.md` and `docs/mcp-server.md` now include them, rendered by
  `tools/chart-examples/render.py`: the real renderer on invented data,
  rerun whenever the chart code changes.

- **`smoking-pi budget`: what the configured measurements cost.** Nothing
  said how much traffic a target list generates. The DNS wizard can adopt
  60 services over ICMP, TCP and three HTTP versions, and a growing list
  was invisible until it showed up on a metered bill. The HTTP probes had
  been downloading whole home pages at about 5.7 GB a day for one seed target
  without anyone noticing. The command (and `GET /budget` on
  config-manager) reads the generated SmokePing config. It reports samples
  per hour and approximate MB per day per probe, most expensive first,
  against two ceilings: `MEASUREMENT_BUDGET_MB_PER_DAY` (default 1000) and
  `MEASUREMENT_BUDGET_SAMPLES_PER_HOUR` (default 20000). The bytes per
  sample were measured, not derived: 168 for ICMP, 300 for DNS, 200 for
  TCP, 12 KB for an HTTPS HEAD. The seed comes to ~98 MB/day, 10% of the
  default. This is accounting only; nothing is throttled yet.
  Standard and Pro. See `docs/measurement-budget.md`.

### Changed

- **Charts are drawn to be read on a phone, as paper figures.** The PNGs
  Smoking Pi sends were dark, 8 × 4.5 in with 7.5–11 pt type, and went to
  Telegram as documents. A document is a file card to tap open, and once
  opened the text was a few pixels tall on a phone. Alert, digest and
  `get_chart` charts now follow the conventions of the author's published
  figures:
  - a white page, 10 × 8 in (1100 × 880 px, under Telegram's 1280 px
    photo limit, so it is not rescaled), with 16–20 pt type and a bold
    title;
  - a dashed major and dotted minor grid, and a framed legend;
  - matplotlib's tab colors: the subject in a strong line, peers in thin
    gray, and status in tab red/orange, since the old warning yellow
    vanishes on white;
  - a solid black threshold line, so it never reads as grid, with its
    label at the left where it cannot cover the latest trace;
  - concise date ticks.

  They are sent as inline photos (`ALERT_IMAGE_AS_DOCUMENT` now defaults to
  `false`); at this size Telegram's JPEG re-encoding leaves them legible.
  **This changes on upgrade for every install that never set these two**
  (the `.env.template` ships them empty, so the new compose defaults apply).
  To keep the old look, set them before upgrading:
  `sudo smoking-pi config set CHART_THEME dark` and
  `sudo smoking-pi config set ALERT_IMAGE_AS_DOCUMENT true`. The dark
  palette keeps the new sizes.

- **`smoking-pi install` finishes what it starts.** A clean install on the
  staging Pi, following the getting-started guide, worked (about 3.5
  minutes, eight containers, the doctor all green). What it said and asked
  along the way made a first install harder than it needed to be:
  - The edition menu opened on Basic, while the guide says to pick Pro.
    Pro is now preselected.
  - The optional-services list was too narrow for its own text, and it
    spoke in jargon ("needs NOTIFY_MODE", "ANTHROPIC_API_KEY in the env
    file").
  - A service chosen there was switched on and left unconfigured: an
    alerter evaluating into the log, an AI reporter with no key. The only
    notice was a line that the next menu drew over. Install now sets up
    each one after the stack starts, through the command that owns it:
    `smoking-pi alerts` asks where alerts go, the AI key is typed at a
    hidden prompt, and `smoking-pi dns enable` waits for the observer and
    prints the router setting. With `--yes`, or a question skipped, the end
    of the install lists those commands under *Still to do*.
  - Starting at boot was a separate manual step (step 4 of the guide). A
    package install now enables the `smoking-pi` service itself. It is
    started without waiting (`--no-block`), because its `smoking-pi up`
    would wait for the stack lock that install holds.
  - Install ended on the whole `smoking-pi passwords` banner, about 130
    lines, with each edition's `setup.sh` summary before it. These listed
    `localhost` URLs (on a laptop connected over SSH, that is the laptop).
    It now ends on a short summary, when to run `doctor --live`, anything
    left to do, and the address to open, with `sudo` in the commands where
    a package install needs it. Run on its own, `setup.sh` points to
    `smoking-pi url` instead of `localhost`.
  - `smoking-pi` without sudo, on a package nobody has set up yet, said
    "Installed, with its settings in /etc/smoking-pi". It cannot tell, so
    it now also says `sudo smoking-pi install`.

  The release host test now also checks that install enabled the unit.

### Fixed

- **The assistant drew charts of its own.** Nothing told it not to, and
  an agent with a shell improvises: matplotlib from scratch, ASCII or
  emoji bars. The results looked worse than either of the two things
  Smoking Pi already offers, and they cost the reader the interactive
  Grafana view they could have had with one tap. The MCP server
  instructions and the OpenClaw skill now say it outright: never draw a chart yourself. "How did it
  look" gets the Grafana link the tool returned, a picture to keep or
  forward comes from `get_chart`, and without either the answer is words
  with times and numbers. After upgrading, `sudo smoking-pi openclaw`
  reinstalls the skill and the gateway picks it up in a new session.

- **The HTTP probes downloaded the whole home page on every sample.** They
  were meant to answer two questions: does the server respond over
  HTTP/1.1, /2 and /3, and how fast. Instead each sample was a `GET` of
  `https://<host>/` with the body thrown away. That body was 1.3 MB for
  www.cloudflare.com, so the seed's Cloudflare target alone (three
  versions × 5 samples × 288 steps) cost about 5.7 GB a day. Sites the DNS
  wizard adopted could cost more (www.netflix.com is 3.2 MB). The recorded
  time was mostly that download, not the documented "connect + TLS +
  request + first byte": 1.85 s for Netflix against 0.56 s to its first
  byte. The probes now send `HEAD` (`-I`): the handshake and the response
  headers, a few KB per sample. On upgrade, config-manager switches the
  installed CurlHTTP1/2/3 probes and the wizard's copies to `HEAD` once,
  in PostgreSQL and in the config dir's `probes.yaml` (what YAML mode
  reads), then reloads SmokePing. A probe whose `extraargs` you edited is left
  alone. **The HTTP series steps down at the upgrade**, most for sites with
  heavy home pages; it is the same measurement done the way it was
  described, not a network change. Some servers answer `HEAD` with a `405`
  or `503`; that is still the server answering, and it is timed. A full
  page load is a separate, application-layer measurement and is not run on
  a 5-minute step; it is on the roadmap.

- **HTTP probes take 3 samples per round instead of 5, and SmokePing
  checks its files before it starts.** With HEAD each sample is cheap,
  but every sample is a full TLS handshake, about 12 KB. Five per HTTP
  version per round bought little over three. Three keep a median, a
  spread and loss in thirds. The seed's HTTP traffic drops from ~155 to
  ~93 MB/day, and the whole seed from ~161 to ~98 MB/day. On upgrade the
  installed CurlHTTP1/2/3 probes and the wizard's copies go from 5 to 3
  once, in PostgreSQL and in `probes.yaml`. A count you chose yourself, or
  a probe with your own `extraargs`, is kept.
  **SmokePing's own HTTP graphs restart at this upgrade; Grafana's do
  not.** SmokePing stores the sample count in each RRD and dies on a
  mismatch, so the old files are moved to `/data/.archive/<time>/` in the
  SmokePing volume (readable with `rrdtool`, not deleted) and new ones
  begin. Grafana reads InfluxDB, where every point records the pings it
  was measured with, so its history runs straight through.
  Without a second change this would have been a crash loop:
  config-manager runs the RRD guard before every reload, but
  it cannot reach a SmokePing that is not running yet. On an upgrade that
  recreates both containers, SmokePing could start on the new Probes file
  and die on every restart, measuring nothing. config-manager now writes
  `cadence.json` (what each RRD must look like) next to `Targets` and
  `Probes`. SmokePing's container runs the same guard against it at start
  (`custom-cont-init.d/06-rrd-guard.sh`), before the daemon loads. That
  also covers any later change of a probe's step or pings.

- **A new install's first alert was a false "critical: the monitor, not the
  network".** `exporter_stale` fires when the last `STALE_WINDOW` (20 min)
  holds no latency point. On a fresh install that is true for the first
  minutes, because nothing has been measured yet and the first point comes
  after the first 300 s step. The staging Pi's new install logged it one
  minute after the alerter started. It stayed in the log only because
  delivery was off. Since `smoking-pi install` now asks where alerts go
  during the install, the first message a new user got would have been
  that one. Now, when the window is empty, the alerter first asks whether
  the bucket holds any latency point at all (one `first()` per series,
  0.18 s on the reference Pi's months of history). If it holds none, the
  stack has not measured yet, which is not a stall. It asks only when
  no `exporter_stale` incident is open, so an open stall stays open.
  Nothing depends on when the alerter started, so a restart changes
  nothing. A failed query leaves the rule as it was. A stack that has
  never measured anything is not reported by this rule, and nothing else
  reported it either. The web admin's
  Measurements card reads the RRD files, which were fine, and the doctor's
  `silent-series` said "every series answered in the last day" about an
  empty bucket. That check now warns when no latency point was written in
  the day. Its query gained one marker row, and on the reference Pi it
  returned the same silent series as before.

- **`smoking-pi openclaw --check` blamed the wrong thing when the agent
  could not answer.** On the staging Pi, OpenClaw's gateway was not
  running. `openclaw agent` exited 1 with "gateway agent requires
  credentials before opening a websocket", but the check threw its output
  away. It reported "the agent answered without calling the MCP server"
  and told the user to reload the tool set and check the skill, which is
  the wrong direction when the gateway is simply stopped. The check now keeps the
  agent's exit code and output. When the agent did not answer, it says so,
  shows the last lines OpenClaw printed, and points to `openclaw gateway
  status`. The answered-from-its-shell advice is unchanged, but it now names
  the skill check and the guide by their full paths instead of paths that
  only work from inside a checkout.

- **Starting over left pieces behind.** On the staging Pi, `smoking-pi
  purge --config` followed by `apt purge smoking-pi` should have left
  nothing. It left `/etc/smoking-pi` (the edition record install writes
  beside the env file, which purge did not remove, so the postrm's
  `rmdir` failed quietly), root's `__pycache__` from `sudo smoking-pi
  doctor` inside `/opt/smoking-pi`, and an empty
  `/opt/smoking-pi/editions/pro/smokeping/config`. That last one was
  created by Docker for a config-manager bind mount
  (`./smokeping/config:/app/smokeping-config`) that nothing has read since
  the multi-edition refactor. The unit was also left `failed`: purge
  removed the env file while it was active, and its stop then ran `down`
  without one. Now `purge --config` stops and disables the unit while the
  env file is still there (install enables it again), and removes the
  edition record. The doctor runs without writing bytecode. The postrm
  removes the edition record with the env file, and on remove or purge it
  clears bytecode and empty directories that dpkg does not own under
  `/opt/smoking-pi`. The dead mount is gone.

- **`smoking-pi install` printed every password and token of the new
  install.** Found by a clean install on the staging Pi, following the
  getting-started guide. Each edition's `setup.sh` runs
  `generate-passwords.sh`, which listed the web admin, Grafana, PostgreSQL,
  InfluxDB and ClickHouse credentials on the terminal. That was a few
  screens above install's own "Your passwords are set but not printed
  above". The secrets ended up in scrollback, in any transcript of the
  session, and in anything pasted from it. It also ended with "Next steps:
  review the env file, run `docker compose up -d`", which is wrong for a
  package install, where install has already done both. The script now
  writes the secrets and says only how to read them (`smoking-pi passwords
  --show-secrets`, which refuses a pipe or a file). The env file is also
  created 0600 before any secret goes into it. A `cp` of the template used
  to leave it 0644 until the last line of the script. A new bats suite
  (`packaging/tests/generate-passwords.bats`, in CI) fails if any generated
  value reaches the output, on Standard and Pro (Basic generates none). An
  install made before this one still has its credentials in whatever
  recorded that terminal. `editions/pro/init-passwords-docker.sh` is gone
  too. Nothing had called it for several releases, and it printed the first
  eight characters of each secret and wrote them all to a plaintext file.

## [2.15.8] — 2026-09-29

The web admin reads right and points to the right places.

Going through every web admin page in a browser found small things that
add up on a first visit:
- HTTP, TCP and DNS wizard targets were titled "Http", "Tcp" and "Dns
  Wizard", and the welcome tour headed its groups with raw keys such as
  `top_sites`;
- the Grafana buttons for those categories opened a dashboard with nothing
  on it (now fixed on InfluxDB installs), and nothing linked to Grafana's
  home;
- the dashboard counted one more measured target than it listed, without
  saying that it is the ISP's first hop;
- the AI page and its guide gave instructions from before the package.

Each is fixed, and there is now a Grafana link in the navigation bar. The
Overview's "Targets that need a look" no longer lists a target twice after
its category changes.

### Fixed

- **The web admin counted one more measured target than it listed.** The
  dashboard said "Total Targets 77" beside "78 of 78 targets updated", and
  the welcome tour said 78 too. The 78th is the ISP's first hop, which CPE
  discovery adds by itself and which no list shows as a target. Both now
  say how many of the measured ones were added automatically, and what.
  (Targets gated out by `IPV6_MODE` still make the measured count the
  smaller one; that is a different difference.)

- **Turning on the AI reports followed instructions from before the
  package existed.** The web admin's AI page said to edit "the edition's
  `.env`" and run `COMPOSE_PROFILES=ai docker compose up -d`, and
  docs/ai-insights.md said to paste a Compose snippet "not committed yet".
  On a package install neither applies: the env file is
  `/etc/smoking-pi/env`, the Compose project lives under `/opt`, and that
  command names only `ai`, not the profiles already on. Both
  now give the package's commands (`sudo smoking-pi config set
  ANTHROPIC_API_KEY`, then `ai` added to `COMPOSE_PROFILES`), with the clone
  route in the docs. `AI_MAX_INPUT_CHARS`, documented and read by the
  reporter, never reached its container; Compose and `.env.template` now
  pass it.

- **The web admin titled HTTP, TCP and DNS wizard targets "Http", "Tcp"
  and "Dns Wizard".** Category names without a special case went through
  `.title()`, which lowercases acronyms: the dashboard's cards, the Targets
  page's badges and filter all showed them that way. They now read HTTP,
  TCP and DNS Wizard, from one table of display names, with a test; the
  delete dialog shows the same name instead of the raw key. The navigation
  bar's "Custom Targets" is now "Targets": the page lists every category.
  The welcome tour's step 4 headed its groups with the raw keys
  (`top_sites`, `dns_resolvers`, `http`); it uses the same names now.

- **The web admin's Grafana buttons for HTTP, TCP and DNS wizard targets
  opened an empty page, and nothing linked to Grafana's home.** The
  per-category buttons knew four categories and sent every other one to
  Individual Pings, which charts ICMP only, so HTTP and TCP targets opened on
  nothing. On InfluxDB installs they now open HTTP by version (TCP
  handshakes are at its bottom) and the DNS wizard dashboard. A test checks
  that every category the seed ships has a dashboard, and that every linked
  dashboard is provisioned. The navigation bar gains a **Grafana** link to
  Grafana's home, which on InfluxDB installs is the Overview.

- **The Overview's "Targets that need a look" listed a target twice after
  its category changed.** The table took each target's last measurement
  per category, so for 15 minutes after v2.15.7 retagged the ISP gateway
  from `unknown` to `cpe`, `CPE_IPv4` appeared twice, one row already 15
  minutes stale, for anyone reading the table then. It now takes one row per
  target and layer, from the newest point by time.

## [2.15.7] — 2026-09-29

Grafana's dashboards show what they measure.

Since v2.14.0 the Overview's first row said "unknown" and "not collected
yet" on every Pro installation, over data that was there. Its text panels
now show their text, and its per-layer counts are labeled by layer again.
The page also gains:
- the Wi-Fi signal and the round trip to the ISP's gateway right now;
- latency and loss per kind of destination over the selected range, with
  counts of cuts, drops and uplink and resolver changes;
- a table of the targets that need a look, each linking to Target Detail.

A pass over the dashboards in a browser found more. "SmokePing Latency &
Loss" opened on the ISP gateway's probe, drawn in the wrong units. It and
four other dashboards now chart only the measurements they are for, which
also speeds them up. Target Detail opens on a target measured every
way. The Wi-Fi Link and CPE microcuts dashboards had seven overrides that
never applied, and those now apply too. The ISP gateway's ping target is no
longer categorized "unknown" from the upgrade on. Two new doctor checks,
`text-stats-name-their-field` and `overrides-match-a-series`, fail on the
panel mistakes behind these.

### Fixed

- **The doctor must run on Python 3.10.** The package runs it on the
  host's own `python3`, which is 3.10 on Ubuntu 22.04. This release's first
  candidate used a regex possessive quantifier (`\w*+`, Python 3.11+) in
  the new `text-stats-name-their-field` check, and its doctor crashed on
  import there; the release's install test caught it before anything
  shipped. The pattern uses `\b` instead. A test fails on 3.11-only regex
  syntax in the doctor, CI runs the doctor on Python 3.10 as well as 3.14,
  and the doctor's `requires-python` and lint target say 3.10.

- **The ISP gateway's ICMP target charted as "unknown".** CPE discovery
  writes its own SmokePing section (`CPE`, holding `CPE_IPv4`/`CPE_IPv6`),
  which the exporters' directory-to-category map did not know, so its
  pings were tagged `category=unknown`. Grafana's per-category charts drew
  an "Uncategorized" line with nothing to say what it was. It is now tagged
  `cpe` by both exporters (InfluxDB and ClickHouse), like the
  high-frequency gateway probe (`cpe_latency`). A test checks that every
  section CPE discovery writes has a category. Points written
  before the upgrade keep `unknown`: a chart grouped by category shows the
  switch as one line ending and another beginning.

- **"SmokePing Latency & Loss" opened on the ISP gateway's probe, drawn
  in the wrong units.** Its target list, and those of the side-by-side,
  individual-pings, Custom and Netflix dashboards, took every measurement
  except DNS. That included `cpe_latency`, whose target is an IP address,
  so it sorted first and became the default. `cpe_latency` is in
  milliseconds and percent, while these panels expect seconds and a 0-1
  ratio. The latency panel multiplied it by a thousand and drew nothing
  useful; the loss panel pinned at 100%. The "all but DNS" filter also made
  every query scan every measurement: the main dashboard was still loading
  after 18 seconds. They now name what they chart (`latency`,
  `http_latency`, `tcp_latency`), so the main dashboard opens on a real
  target, and its target lists cover the last 24 hours instead of a year of
  retired names. The gateway probe keeps its own dashboard, CPE microcuts.

- **Target Detail opened on a target measured one way, and the CPE
  microcuts' Wi-Fi panel drew failures on the signal's axis.** Target
  Detail defaulted to the first name alphabetically (Amazon on the seed),
  which only pings, so three of its four layers opened empty. It now opens
  on Google, which the seed measures over ICMP, TCP and HTTP/1.1-3. On CPE
  microcuts, the override that puts TX failures/s on a right-hand axis as
  red bars matched a series name Grafana never produced, so failures were
  drawn in dBm next to the signal and flattened it. The series are now
  named per interface ("signal wlan0", "tx failed/s wlan0", which changes
  that legend), and the override matches the failures' query. The Wi-Fi
  Link dashboard had six overrides of the same kind that never applied: the
  average signal's dashed line, the noise and SNR units and axis, the
  modulation's width axis, channel busy's 0-100 % scale and link quality's
  right axis. They now match their queries too.

- **The Overview's first row said "unknown" and "not collected yet" over
  data that was there.** Uplink, Wi-Fi network, public address, network,
  location, resolver and IPv6 were all written and their queries answered,
  but a Grafana stat reduces numeric fields only unless told which field
  to show, so every text answer fell through to its "no value" text. Since
  v2.14.0 the landing page has told every installation it was not
  connected anywhere. The Wi-Fi dashboard's SSID and BSSID said "No data"
  for the same reason. Both now name their field, and a new doctor check,
  `text-stats-name-their-field`, fails on a text stat that does not.
- **"Targets answering, by layer" printed each layer's name as an
  unreadable string of timestamps.** The query's window columns rode along
  as labels; it now keeps the count alone.

### Added

- **A doctor check for overrides that can never apply.**
  `overrides-match-a-series` fails on a Grafana override matched `byName`
  on a `yield(name:)` value, which Grafana never uses as a series name. Seven
  such overrides sat in the CPE microcuts and Wi-Fi Link dashboards, drawing
  failures in dBm and dropping units, axes and a 0-100 % scale, and nothing
  noticed: the panels rendered, just wrongly.

- **The Overview says how the link is doing, not only where it is.** A
  *Right now* row adds the Wi-Fi signal and the round trip to the ISP's
  gateway (the first hop past the home router that answers). An
  *Over the selected range* row (24 h by default) adds ICMP latency and
  loss per category, the ISP gateway's latency against the Wi-Fi signal, and
  counts of ISP gateway cut windows, Wi-Fi drops, uplink changes and changes of
  the router's resolver,
  with uplink and resolver changes marked on the charts. A *Targets that
  need a look* table lists every target and layer, the lossiest first and
  then the furthest above its own 24-hour median, each linking to Target
  Detail. Before, the page could not answer "is my Internet OK?" without
  opening another dashboard.

## [2.15.6] — 2026-09-29

One target per edge network, and a command that says when it needs sudo.

The DNS wizard counted every CloudFront distribution, Fastly customer,
Akamai edge and Google API host as a service of its own. An edge
network's endpoints are now one service, measured through one host. And
`smoking-pi dns adopt` keeps one target per service, deactivating the
others it adopted before (history kept). Old names in the wizard's
three-day memory merge into today's instead of ranking twice. Run
without sudo on a package install, the command now says to use sudo,
instead of reporting that nothing is installed. And the docs for
changing a probe's cycle now say that about one point goes missing at the
change, and that switching back needs a manual restore to keep
SmokePing's own graphs.

### Fixed

- **config-manager did not log which layers it deactivated.** The list
  (2.15.4) was only in `smoking-pi dns adopt`'s output, which is gone
  once the terminal closes. On the reference Pi the first real run
  deactivated 72, with nothing in the logs to say which. They are now
  logged, once the change is committed (not on a dry run).

- **Without sudo, a package install's command answered as if nothing
  were installed.** `/etc/smoking-pi` is root's (0750) because the env
  file holds the passwords, so an ordinary user cannot even see the file,
  and every reader took that for "absent". On the staging Pi, as the
  normal user: the bare `smoking-pi` said "Not installed on this machine
  yet. Start with: smoking-pi install", `config list` and `config get`
  said every key was unset, `passwords` said the file was not found, and
  `dns status` said the observer was not running. It was running. Someone
  debugging would have been told to reinstall a working install, or that
  a setting was at its default when it was not. Every command that reads
  the env file now stops and says so, naming the `sudo` command to run;
  the bare command says it is installed. `version`, `--help`, `doctor`,
  `discover` and `link` work as before. With sudo nothing changes.

- **The DNS wizard kept ranking services under names 2.15.5 had
  retired.** 2.15.5 names a CDN endpoint after the CDN
  (`dynamic.x.com.cdn.cloudflare.net` is `cloudflare.net`, not
  `com.cdn.cloudflare.net`), but only for new lookups. The observer keeps
  72 h of presence per service under the name computed at the time. On
  the reference Pi, after the upgrade, `sudo smoking-pi dns adopt` still
  listed `com.cdn.cloudflare.net` (44 h present) as a candidate, while
  `cloudflare.net` had 1 h. The same happened to 16 more: `com.akadns.net`,
  `com.edgekey.net`, `tv.map.fastly.net`, and others. Each CDN ranked
  twice, both times too low, for three days after every such change, and
  a retired name could be adopted. The state now renames a key when all
  the hosts it counted get one other name today, merging its counts.
  A key that is still a name of its own stays, for example
  `shop.pages.dev` looked up directly. Checked on a copy of that
  Pi's state: 17 keys merged, `cloudflare.net` 1 → 45 h, `akadns.net`
  1 → 70 h.

### Changed

- **An edge network's endpoints are one service, measured through one
  host.** The DNS wizard counted every CloudFront distribution, Fastly
  customer, Akamai edge and Google API host as a service of its own. On
  the reference Pi, three days of lookups gave 84 googleapis.com hosts, 52
  CloudFront distributions, 38 Fastly customers and 47 Akamai edges. Four
  googleapis.com hosts were adopted as 20 targets, all reaching the same
  Google front end. Measuring many endpoints of one edge multiplies the
  targets without measuring anything new, and crowds real services out of
  the ranking and the cap. Endpoints under a listed edge suffix
  (`cloudfront.net`, `fastly.net`, the Akamai and Azure edges,
  `googleapis.com`, …) are now one service, measured through its busiest
  stable host. AWS load balancers stay one service per region, and sites
  under a shared suffix (`github.io`, `myshopify.com`) stay their own.
  `tools/dns-explore` uses the same rule. For targets adopted before,
  `dns adopt` (and `--retire-only`) now recognizes a service measured
  under an older name, and does not adopt it again. Where several adopted
  targets are now one service, it keeps one and deactivates the others,
  with their history kept. One turned back on in the web admin is
  deactivated again by the next `dns adopt`. The cap now counts only
  services with an active layer. On the reference Pi that is
  googleapis.com: one of four kept, and three places freed under the cap.

- **Changing a probe's cycle: the missing point, and how to switch
  back.** The first end-to-end test of the Probes page (on a test Pi:
  DNS to 10 queries every 2 minutes and back, through the web form) found
  it working: files archived, SmokePing reloaded and alive, the new
  cycle in InfluxDB, and the alerter and MCP server reading it. But
  `measurement-frequency.md` did not say two things a user would hit.
  About one point goes missing at each change. Going back to the old
  cycle starts SmokePing's graphs from empty again, rather than returning
  the old file. Someone who changed a probe by mistake would have lost
  SmokePing's graphs twice, not knowing the first file was one `mv` away.
  The page now describes both, with the restore tested on that Pi.

## [2.15.5] — 2026-09-29

Deactivating the DNS wizard's silent layers, on its own.

`smoking-pi dns adopt --retire-only` deactivates the layers that answered
nothing for a day and adopts nothing new. Until now that came only
together with adopting whatever the wizard had selected since the last
run. And a CDN endpoint that embeds a customer's domain
(`www.x.com.cdn.cloudflare.net`) now counts as the CDN's service, not as
a service named after a top-level domain.

### Added

- **`smoking-pi dns adopt --retire-only`.** Deactivating the silent layers
  (2.15.4) came only with adopting whatever the wizard had selected since
  the last run. On the reference Pi that was 7 new services the maintainer
  had not asked for, one of them bogus (below). `--retire-only`
  deactivates the silent layers and adopts nothing. It does not need the
  wizard's selection, so it works while the observer is stopped.

### Fixed

- **A CDN endpoint became a "service" named after a top-level domain.**
  The wizard names a service by its registrable domain, private suffixes
  included, so `user.github.io` is a site of its own. But a CDN endpoint
  that embeds a customer's domain in front of a private suffix, such as
  `www.x.com.cdn.cloudflare.net` or `x.com.akadns.net`, became the service
  `com.cdn.cloudflare.net`. It was selected on the reference Pi and would
  have been adopted as `W_com_cdn_cloudflare_net`, beside the CDN's real
  service. When the label before a private suffix is a real top-level
  domain (per the ICANN list: `com`, `uk`, `app` …) with a name before it,
  the service is now the CDN (`cloudflare.net`). A site whose own name is
  a top-level domain (`www.io.github.io`) is taken for a CDN endpoint too:
  a rare case, and a harmless one. `tools/dns-explore` names services the
  same way.

## [2.15.4] — 2026-09-29

The DNS wizard stops measuring what a host does not serve.

Many hosts do not answer every layer the wizard measures: some drop
ping, many have no HTTP/3, some names serve no web at all. Each such
layer charted a permanent outage (72 of 265 on the reference Pi).
`smoking-pi dns adopt` now tries each new layer first and skips one that
does not answer. It also deactivates adopted layers silent for a day,
keeping their history. It deactivates nothing while most of the network
is down, nor when a whole layer (QUIC, ping) is blocked. The doctor's
new `silent-series` check names every series that answered nothing for a
day. After upgrading, `sudo smoking-pi dns adopt --dry-run` shows what
it would deactivate.

### Fixed

- **The DNS wizard measured layers the host does not serve, forever.** It
  adopts each service with the whole suite (ICMP, TCP 443, HTTP/1.1, /2,
  /3), and many hosts do not serve every layer. Some drop ping, many have no
  HTTP/3, and some names (a CDN's fallback, Apple's Private Relay) have no
  web server at all. On the reference Pi, 72 of the 265 adopted series
  answered nothing for a whole day: 33 HTTP/3, 26 other HTTP, 10 ICMP and 3
  TCP. Among them was one of the Pi's own containers, adopted before the
  filter that now refuses bare names. Each charted a permanent outage, and
  the assistant could report "hbo.com at 100% loss". Nothing flagged it: it
  was found by counting silent series by hand. `smoking-pi dns adopt` now
  tries each new layer once, from the SmokePing container and the way its
  probe does, and skips a layer the host does not answer. It also
  deactivates adopted layers that answered nothing for a day, provided at
  least half of the others did, and at least a fifth of the same layer type:
  a day-long outage deactivates nothing, nor does a block of one layer (QUIC
  or ping dropped everywhere). Both changes are made in one transaction. A
  deactivated layer keeps its row and history, stops being probed, and can
  be turned back on in the web admin. The rest of the service is still
  measured, and services are still only added. A new doctor check,
  `silent-series`, names every series (configured or adopted) that answered
  nothing for a day. It would also have caught the seed's bare `amazon.com`.
  `docs/dns-observer.md`, `docs/doctor.md`.
## [2.15.3] — 2026-09-29

Standard and Pro stop shipping a target that can never answer.

The seed's *Amazon* target was bare `amazon.com`, which does not answer
ping: a flat 100% loss from the first day and a critical *Amazon down*
incident that stayed open. The seed now uses `www.amazon.com`. An
install whose seeded row is unchanged has it corrected once at start,
keeping its history; an edited target, or an install in YAML mode, is
left alone. The release checklist now also runs `smoking-pi openclaw`
against a real OpenClaw and checks the DNS observer's counts.

### Fixed

- **Standard and Pro shipped a target that can never answer.** The seed's
  *Amazon* target was bare `amazon.com`, which does not answer ICMP. Every
  new Standard or Pro install charted a flat 100% loss for it from the first
  day and carried a critical *Amazon down* incident that never cleared: the
  alerter keeps such a target out of its verdict, but the incident stays.
  Basic already used `www.amazon.com`. Found by a network outage test on a
  fresh package install, where it was the one incident left after recovery.
  The seed now uses `www.amazon.com`. An install with the seed's row
  unchanged (named *Amazon*, host `amazon.com`) has it corrected once at the
  next start, and SmokePing is told to reload so it measures the new host at
  once. The name is kept, so the target's history continues. If the
  correction fails, it is logged, the start goes on, and the next start
  retries. A target someone edited is left alone, and a change back to the
  bare name is not undone. An install in YAML mode (no database) keeps its
  file: change the host in `targets.yaml`.

### Changed

- **The release acceptance checks the two things 2.15.0 got wrong.** The
  checklist asked for "one question through OpenClaw" but never for
  `smoking-pi openclaw` itself, and nothing about the DNS observer's
  numbers. So 2.15.0 shipped a registration OpenClaw 2026.8 refuses,
  resolution times that dropped late-written queries, and a last-hour count
  that read low and fell to zero every hour. All three were found on the Pi
  after release and cost two patch releases (2.15.1, 2.15.2).
  `docs/release-acceptance.md` now asks for `smoking-pi openclaw` against
  the OpenClaw the host runs, with its version recorded. It also asks for
  the observer's counts after queries sent from another machine: fewer than
  1,000, which AdGuard still holds in memory, then more, which it writes.
## [2.15.2] — 2026-09-28

One fix: the DNS Wizard's *Queries (last hour)* counts the last hour.

It counted the clock hour, so it fell to zero at every hour boundary, and
only the queries AdGuard had written to its log, which it does in batches
of 1,000: on a house making a few hundred queries an hour, the figure
could drop to 0 between writes. It now counts the last 60 minutes and
adds the queries AdGuard still holds in memory, as does the 24-hour
count. For the first hour after upgrading it counts from the upgrade on.

### Fixed

- **The DNS Wizard's "Queries (last hour)" was mostly wrong.** It counted
  the clock hour, not the last hour, so it fell to zero at every hour
  boundary and read 10 minutes of queries at ten past. And it counted only
  what AdGuard had written to its log, which it does in batches of 1,000
  queries: on the reference Pi, a house making a few hundred queries an
  hour, that is a write every few hours, and the figure read 0 at 19:00
  on a normal evening. The same number is in the per-service table. It
  now counts the last 12 five-minute buckets, and adds the queries AdGuard
  still holds in memory, read from its API: the entries newer than the
  log's last line, with the same filters as the log (the Pi's own traffic
  and SmokePing's targets left out). They are counted in the snapshot, not
  kept, so a query is never counted twice once written. The 24-hour count
  includes them too; the 7-day volume and presence, which choose what to
  measure, still come from the log alone. One API read of up to 1,000
  entries per pass (every 10 minutes) took about 0.6 s on a Pi 4. If
  AdGuard does not answer, the pass counts the log alone and the observer
  says so once. For the first hour after upgrading it counts from the
  upgrade on. `docs/dns-observer.md`.

## [2.15.1] — 2026-09-28

Two fixes to 2.15.0: the `smoking-pi openclaw` step it asks you to run
after upgrading, and the DNS resolution times it added.

`smoking-pi openclaw` registers with OpenClaw 2026.8 again: it sent the
MCP timeouts under keys OpenClaw has retired, and 2026.8 refused the whole
registration, so the assistant's skill was not refreshed. A gateway that
was already connected kept working. The keys it sends now need OpenClaw
2026.4 or newer. After upgrading, run `smoking-pi openclaw` (with `sudo`
on a package install).

The DNS observer now publishes the resolution times of queries AdGuard
writes to its log late, up to six hours afterwards, instead of dropping
them. On a quiet network, or at night, those were easy to lose.

### Fixed

- **`smoking-pi openclaw` could not register with a current OpenClaw.** It
  handed `openclaw mcp set` the timeouts as `connectTimeout`/`timeout`
  (seconds), aliases OpenClaw has since retired: 2026.8 refuses the whole
  registration ("Unrecognized key: connectTimeout"), so step 4 failed and
  the skill was never refreshed. Found by running it on the reference Pi
  after upgrading to 2.15.0, whose release notes ask for exactly that
  step; the running gateway was untouched and kept working. It now writes
  `connectionTimeoutMs`/`requestTimeoutMs` (milliseconds), which OpenClaw
  has read since 2026.4, and so do the two `mcp set` examples in
  `docs/openclaw-integration.md` and `docs/remote-openclaw.md`. Checked
  against OpenClaw 2026.8.1: the old spec is refused with that message and
  the new one is saved.

- **DNS resolution times read late were dropped.** AdGuard writes its
  query log in batches of 1,000 queries, and the observer published only
  the last two hours of 5-minute buckets. On a quiet network, or at night,
  a bucket's queries could reach the disk after that and were kept but
  never exported (seen on a test Pi during the 2.15.0 candidate). Every
  bucket the observer keeps, six hours, is now published and rewritten as
  its queries arrive. Queries read more than six hours late are still left
  out, and the observer logs how many. The same batching makes "Queries
  (last hour)" read low on a quiet network; that is not changed.

## [2.15.0] — 2026-09-28

One page per target, the Pi on the local network, and how fast the house's
DNS answers.

The Target Detail dashboard shows everything measured for one destination
(ICMP, the TCP handshake, HTTP/1.1, /2 and /3, DNS) with median, loss and
the spread of each cycle's pings, and says "not measured" for a layer it
lacks (InfluxDB only). The assistant's answers carry an `all_layers` link
to it. `install`, `up` and `upgrade` announce the stack on the LAN with
DNS-SD, and `smoking-pi discover` lists every Smoking Pi on the network.
Announcing is on by default, including after an upgrade, and everyone on
the network can read the edition and version: `SMOKING_PI_ANNOUNCE=0` in
`/etc/default/smoking-pi` turns it off. The DNS observer now times the
house's own queries: percentiles per 5 minutes, from AdGuard's cache and
per upstream, in a new row of the DNS Wizard dashboard. After upgrading,
run `smoking-pi openclaw` (with `sudo` on a package install) to refresh
the assistant's skill.

### Added

- **How fast the house's DNS queries resolve.** The resolver probes time
  one query SmokePing sends every few minutes. How long the house's own
  queries take, which is what people wait for, was in AdGuard's log and
  went unused. The DNS wizard now keeps each query's resolution time as a
  histogram per 5-minute bucket and per path: `cache`, each upstream,
  `upstreams` together. It exports the count and the 10th–99th percentiles
  as `dns_resolution` (seconds, timings only, no names). The DNS Wizard
  dashboard gains a row: upstream answers as a median over a p10–p90 band
  with p99, the share answered from cache, and the median per upstream.
  The doctor now also understands a `pivot` over a tag (`path`), not only
  over `_field`. `docs/dns-observer.md`.

- **Smoking Pi announces itself on the local network.** Finding the Pi
  meant remembering its IP address, or its `.local` name if you knew the
  hostname. When Avahi runs (Raspberry Pi OS runs it), `install`, `up` and
  `upgrade` now write `/etc/avahi/services/smoking-pi.service`. It
  announces the web page as `_http._tcp` and as `_smoking-pi._tcp`, with the
  edition, version and Grafana port in its TXT record: nothing secret, but
  readable by everyone on the network, so `SMOKING_PI_ANNOUNCE=0` (in
  `/etc/default/smoking-pi`) turns it off and withdraws it. `down`
  withdraws it and uninstalling the package removes it. The new
  `smoking-pi discover` lists every Smoking Pi on the network and where to
  open it (it needs `avahi-browse`; on a Mac, `dns-sd -B _smoking-pi._tcp`).
  `docs/getting-started.md`.

- **A Target Detail dashboard: one target, every layer.** Looking at one
  destination meant opening a dashboard per probe: ICMP in one, TCP and
  HTTP in another, DNS in a third, each with its own selector, and nothing
  said which probes a target even had. The new dashboard takes one target
  from a dropdown and shows every layer measured for it (ICMP, the TCP
  handshake, HTTP/1.1, /2 and /3, DNS for a resolver). It gives the median
  and loss per layer, with latency drawn as a median line over the 10th–90th
  percentile band of each cycle's pings. A layer the target has no probe
  for says "not measured". Targets pair by name without the probe suffix
  (`Google`, `Google_tcp443`, `Google_h2`; the DNS wizard's
  `W_<service>_icmp` … `_h3`). The Overview, latency, resolver, per-ping
  and HTTP-by-version dashboards link to it with their target. InfluxDB
  only. `docs/target-detail.md`.
- **The assistant can link to it.** MCP responses carry an `all_layers`
  link for every ICMP, DNS, HTTP and TCP target, opening the Target Detail
  dashboard on the target's base name (`Google_h2` → Google). The OpenClaw
  skill says when to offer it. Refresh the installed skill with
  `smoking-pi openclaw` after upgrading.

## [2.14.1] — 2026-09-28

Three fixes from running a package install on the Pi.

`sudo smoking-pi openclaw` works on a package install: it finds OpenClaw
under nvm and runs it as you, not as root, and keeps the MCP token out of
sudo's journal. Grafana no longer downloads plugins no dashboard uses at
every start, nor logs an error doing it. `acceptance-record.sh` names the
package's version instead of `<no tag>`.

### Fixed

- **Grafana (Pro) downloaded plugins nobody uses at every start, and
  logged an error doing it.** Grafana 13 preinstalls a list of plugins (Explore
  Traces, Metrics Drilldown, Elasticsearch, Loki and others) from
  grafana.com in the background each time it starts. No Smoking Pi
  dashboard uses them. On the Pi that meant a download at every boot and
  one `level=error msg="Failed to install plugin" pluginId=elasticsearch`
  (permission denied on the bundled copy), seen on 2.13.8 and 2.14.0. The
  image now sets `GF_PLUGINS_PREINSTALL_DISABLED=true`. The ClickHouse
  plugin, the one the dashboards need, stays baked into the image. Apps
  an earlier start already installed stay in Grafana's volume and keep
  loading; they are harmless, and `docker exec pro-grafana-1 grafana cli
  plugins remove <id>` removes one.

- **`acceptance-record.sh` printed `<no tag>` on a package install.**
  `/opt/smoking-pi` is not a git checkout, so the record's first line said
  `<no tag> / <not a git checkout>` even with `--tag`, and the version had
  to be typed by hand into every release's Validation section. It now
  reads the package's version (`VERSION` for a candidate, `CITATION.cff`
  otherwise, as the `smoking-pi` command does). It also defaults the
  expected image tag to that version (a `SMOKING_PI_VERSION` pin in
  `/etc/default/smoking-pi` still wins), and points to the evidence file
  for the commit. Seen accepting v2.14.0-rc.1 on a package install.

- **`sudo smoking-pi openclaw` could not connect OpenClaw on a package
  install.** A package install needs `sudo` to read `/etc/smoking-pi/env`,
  but `sudo` drops nvm's `~/.nvm/.../bin` from PATH. So the command said
  "No 'openclaw' command on this machine" on a Pi where OpenClaw was
  running, and `sudo smoking-pi install` gave the same answer when you
  chose "OpenClaw runs on this Pi". Had it found the command, it would have
  run it as root: the registration would have gone to `/root/.openclaw`,
  which no gateway reads. The workaround was `sudo env PATH=$PATH`. The
  command now finds the invoking user's `openclaw` (PATH, then nvm's
  default version, then the usual per-user bin directories). It runs the
  registration, the skill install and `--check`'s question as that user,
  with their session bus so `systemctl --user` reaches the gateway. The
  token goes to `openclaw mcp set` through a 0600 file, never through
  sudo's command line, which sudo writes to the journal.

## [2.14.0] — 2026-09-28

Grafana opens on an Overview page, and a working InfluxDB token is no
longer called rejected.

The Overview says where the Pi is connected (uplink, Wi-Fi network,
public address, the network that announces it, an approximate location,
the resolver) and what it is measuring, with links to every dashboard. A
new collector, `public_ip.py`, finds the public address and, by default,
sends it to ipinfo.io once a day for the place; set `PUBLIC_IP_GEO=0`
before upgrading to keep it there. The Overview is Pro with InfluxDB. `sync-influx-token.sh`, after the 2.9 upgrade,
failed on the right token and exited 1; it now checks with a command that
needs no org. The architecture diagram is redrawn.

### Added

- **Grafana opens on an Overview page.** "Smoking Pi – Overview" shows
  where the Pi is connected (uplink, Wi-Fi SSID, public address, the
  network that announces it, an approximate location, the resolver) and
  what it is measuring (targets answering per layer in the last 15
  minutes, targets losing packets, measurements in the last hour), with
  links to every detailed dashboard. The DNS wizard's targets are counted
  apart: on the reference Pi, 78 of the 79 targets losing packets were
  wizard targets, most of them CDNs that drop ICMP. A new collector,
  `public_ip.py`, finds the public address by asking Google's authoritative
  DNS directly, its network through Team Cymru, and its city, region and
  country through ipinfo.io once a day. `PUBLIC_IP_GEO=0` keeps the address
  from ipinfo.io. With ClickHouse, Grafana keeps its own home page.
  `docs/public-address.md`.

### Changed

- **The architecture diagram is redrawn.** A Raspberry Pi frame holds the
  containers in four columns (configure, measure, store, look and act);
  what the Pi talks to sits outside it. Arrows run on a grid with rounded
  corners, every box has an icon, data stores are cylinders, a tag on the
  box names its Compose profile, and `you` badges mark web-admin and
  Grafana as the two places you come in. The `.excalidraw` file follows
  the same layout. The spec and its tests are unchanged in what they
  guarantee.

### Fixed

- **`sync-influx-token.sh` called a working token rejected.** v2.13.8's
  check asked InfluxDB `influx bucket list --token`, which also needs an
  org. The InfluxDB container keeps no CLI config (it is not in the data
  volume, so a recreate drops it). So on the reference Pi, after the
  upgrade, the check failed with "must specify org" for the right token,
  and the script printed that InfluxDB rejects it and exited 1. `.env` was
  never touched, and Grafana kept working (its datasource health: "3
  buckets found"). The check now uses `influx org list --token`, which
  needs no org: 0 for the Pi's token, 1 for a made-up one.

## [2.13.8] — 2026-09-27

The DNS observer is easier to find, and InfluxDB moves to 2.9.

The dashboard shows whether the observer is working, and how to start it
when it is off. The welcome tour lists every measurement layer, not only
ping. The README says what the observer sees and what it cannot. The DNS
wizard no longer counts the Pi's own lookups as the house's: SmokePing's
lookups of the hosts it measures, `config-manager`, `piwheels.org` and
`grafana.com`. InfluxDB goes from 2.7 to 2.9, whose migration hashes
its tokens and cannot be undone, so back up before upgrading.
`sync-influx-token.sh` no longer rewrites a working token to `admin`.
`upgrade` runs one stack change at a time, so two sessions no longer
clash on container names.

### Changed

- **InfluxDB 2.7 → 2.9.** On first start, 2.9 migrates its metadata and
  replaces the stored API tokens with hashes. It keeps a copy of the old
  metadata in the data volume as `influxd.bolt.pre-v2.9.x-upgrade.backup`,
  named after the 2.9 patch that runs it (2.9.1 today; the image follows
  the `influxdb:2.9` tag).
  The migration is one-way: to go back to 2.7, restore a backup taken
  before the upgrade (`smoking-pi backup`). Tokens keep working, but
  `influx auth list` no longer shows them, which is why
  `sync-influx-token.sh` changed (see Fixed). Checked by hand on a
  throwaway volume (#157): data written by 2.7 reads back in 2.9, and new
  writes succeed. The reference Pi's upgrade is the first on real data.

### Added

- **An architecture diagram that cannot go stale** (`docs/architecture.md`).
  It shows every container of the Pro edition, what each reads and writes,
  which Compose profile turns it on, and what sits outside the Pi. One spec
  (`tools/architecture/architecture.py`) writes both the SVG on the docs
  site and an `.excalidraw` file to edit by hand. Its tests fail when a
  Compose service, a profile or an exporter the SmokePing container starts
  is missing from the drawing, or when the drawings are older than the
  spec. It replaces three diagrams from December 2025 in `editions/pro/`
  (`ARCHITECTURE.md`, `architecture-diagram.txt`, `excalidraw-guide.md`),
  which predated config-manager's database, the DNS observer, the MCP
  server, the alerter and ai-insights.

- **`tools/dns-explore` leaves out SmokePing's lookups of what it
  measures**, as the observer's wizard now does. `--targets` (default
  `auto`) reads SmokePing's generated `Targets` from a clone or a `.deb`
  install and counts those names apart; `--targets none` keeps them in.
  On the reference Pi's log they were 33.5% of all queries. The week-long
  run due around 3 October needs no hand-made exclusion file.

- **The welcome tour shows every measurement layer, not only ping.** A new
  step 3, "What it measures", lists ICMP ping, DNS resolution, the TCP
  handshake and HTTP/1.1, 2 and 3. For each it says what the layer tells
  you and how many active targets use it. It says plainly that the TLS
  handshake is inside the HTTP times and has no probe of its own. On Pro
  it also shows the DNS observer's state, or how to start it. That text is
  shared with the dashboard card. The seeded targets move to step 4, and
  the optional assistant to step 5.

- **The dashboard says whether the DNS observer is working, and invites
  you to start it when it is off.** Until now, the only way to see its
  state was `smoking-pi dns status` on the Pi. It was one row in the README,
  and the web admin never mentioned it. A new card, "What this house uses",
  appears on Pro only. While the observer is off, it explains what it would
  give, the commands to start it (`dns enable`, point the router, `dns test`,
  `dns adopt`), and what it cannot see. Once it runs, it shows the
  observer's state (`observing`, `quiet`, `not_receiving`...), the reason
  and the fix, how long ago the last query was seen, and how many of the
  router test queries came back in 24 h. config-manager serves this as
  `GET /dns/observer`, from the `status.json` it already mounts. A stale
  heartbeat reads as `down`, as it does in the observer itself. The house's
  top domains are not included.

### Fixed

- **The DNS wizard adopted two of the Pi's own chores.** `piwheels.org`
  (Raspberry Pi OS's pip index) and `grafana.com` (Grafana's update check;
  its usage stats go to `grafana.org`) were selected and measured as
  services of the house on the reference Pi. They are now on the Pi's
  own-traffic list, next to `pypi.org`, in the observer and in
  `tools/dns-explore`. A house that really uses them is not counted for
  them either.

- **`smoking-pi passwords` wrote colour codes into pipes and files.**
  `smoking-pi passwords | grep Grafana`, or a copy saved to a file, came
  out full of `\033[0;36m`. Colours are now used only when the output is a
  terminal, and never when `NO_COLOR` is set.

- **The DNS wizard ranked SmokePing's own lookups as the house's use.**
  The Pi resolves through the router, which forwards to the observer, so
  every name SmokePing measures reached the log about once per TTL, all
  night. On the reference Pi, 40% of the queries counted between 02:00 and
  06:00 were lookups of the 53 targets the wizard had just adopted, which
  made those services look present every hour. From 72 h of data the
  ranking is by presence, so the selection would have kept choosing what
  it had already adopted. The docs said these lookups were left out; they
  were not. The observer now reads SmokePing's `Targets` (mounted
  read-only) and counts each measured name as the Pi's own. Replayed on the
  reference Pi's log, the Pi's share of the queries went from 22% to 55%.
  Clients are anonymized, so the Pi cannot be told apart by address. The
  house's lookups of exactly those names are left out too, and
  `docs/dns-observer.md` says so.

- **The DNS wizard adopted `config-manager`, one of the Pi's own containers,
  as a service of the house.** `smoking-pi dns adopt` on the reference Pi
  created five `W_config_manager_*` targets. The Pi's host-networked
  containers look that bare name up through the router, which forwards it
  to the observer. With no registrable domain, `service_of()` returned the
  raw name, and the own-traffic list only matches suffixes such as `.lan`.
  A name outside the public suffix list (a bare name, `.internal`, `.lan`)
  now counts as the Pi's own traffic in the observer and in
  `tools/dns-explore`. The snapshot also skips such names already counted in
  the seven-day state, and `wizard_adopt` refuses a bare name or a private
  TLD (`.internal`, `.lan`, …) from an older observer's snapshot. Targets adopted earlier are left in place: removing
  them is a decision for the operator.

- **`upgrade` failed when another command changed the stack at the same
  time, and could leave containers under temporary names.** The v2.13.2 and
  v2.13.7 upgrades on the reference Pi stopped with `Conflict. The container
  name "/<id>_pro-influxdb-1" is already in use`. The old containers kept
  running, and a later retry went through. It was not a Compose bug that
  `COMPOSE_PARALLEL_LIMIT=1` avoids. Each time, a second session's
  `upgrade` or `config set` was running `compose up` on the same project.
  Compose replaces a container by creating `<old id>_<name>`, then stopping
  and removing the old one, then renaming. Two runs that see the same old
  container choose the same temporary name, so one is refused. Two
  concurrent `up`s on the Pi's Docker 28.3.2 and Compose v2.38.2 reproduce
  that error. They also leave services *running* under their temporary
  names, which the next `up` reports as "Running" and never fixes. Then
  `docker exec pro-postgres-1`, and anything else that finds a container
  by name, misses it. The command now changes containers one at a time,
  with a `flock` on the edition directory; commands that only read do not
  wait. After every `up` it renames a temporary-named container back
  when its name is free and removes a never-started copy beside the real
  one. If `up` fails while such a container exists (a `docker compose` run
  by hand is not under the lock), it waits for the other run, repairs, and
  tries once more.

- **`sync-influx-token.sh` would have replaced a working InfluxDB token
  with the word `admin`.** It read the token from the fourth field of
  `influx auth list`. InfluxDB 2.9 stores tokens hashed and leaves that
  column blank, so the fourth field becomes the user name. On a 2.9 test
  instance whose `.env` held the right token, the script reported a
  mismatch, wrote `INFLUX_TOKEN=admin` and restarted Grafana. The script
  runs on every Pro setup and restart. Nothing on 2.7 was affected, but
  the pending `influxdb:2.9` bump would have triggered it. The script now
  first asks InfluxDB whether it accepts the `.env` token. It adopts a
  token from `auth list` only after InfluxDB accepts that token too. When
  none works, it stops with an error and leaves `.env` untouched.

- **The welcome tour's assistant step never saw a call.** Step 4 counts
  the MCP server's `mcp.tools: tool=<name>` log lines as proof that the
  assistant was used. The server's `basicConfig` asked for that format,
  but importing the MCP SDK had already given the root logger a bare
  `%(message)s` handler, so `basicConfig` did nothing. The real line was
  `tool=system_status args=- -> ok in 1209ms`, with no logger name, and the
  step said "not used yet" however often the assistant was used. The
  server now configures logging with `force=True`. A tool call against
  the rebuilt image logs `INFO mcp.tools: tool=list_targets …`, which the
  tour matches. `smoking-pi openclaw --check` only greps `tool=` and reads
  either format.

## [2.13.7] — 2026-09-26

config-manager uses its database again.

From v2.13.0 on, a newer SQLAlchemy picked a PostgreSQL driver the image
does not carry, and config-manager silently fell back to its YAML files.
SmokePing measured those instead of the database's targets. This release
names the driver the image has, and `doctor --live` now fails when the
database is configured but unreachable.

### Fixed

- **config-manager had not used its database since v2.13.0, and nothing
  said so.** SQLAlchemy 2.1 changed the driver a bare `postgresql://` URL
  selects, to psycopg (v3), which the image does not carry. Every
  connection failed, and the API fell back to YAML mode silently. From then
  on SmokePing measured `targets.yaml` instead of the database. On the
  reference Pi, five targets that exist only in the database went
  unmeasured for about a day and a half, and everything needing the
  database answered "Database not available": target edits through the API
  and `smoking-pi dns adopt`. The URL now names `postgresql+psycopg2`,
  which the image has. An explicit driver in `DATABASE_URL` is left alone.
  Proven on the reference Pi: with it, the running container connects and
  reads the database's 30 targets.

### Added

- **The doctor checks that config-manager uses its database**
  (`config-manager-database`, with `--live`). It fails when `DATABASE_URL`
  is set but the app cannot connect, naming the error. On the reference
  Pi, before this release, it fails with `ModuleNotFoundError`. It would
  have caught the problem above on the day it started.

## [2.13.6] — 2026-09-26

The config-manager image starts again.

v2.13.5's config-manager image lacked a new module and could not boot.
This release ships it, and a test now catches a module the API imports but
the image does not carry.

### Fixed

- **v2.13.5's config-manager image did not start.** The Dockerfile copies
  the application file by file, and it did not copy the new
  `wizard_adopt.py`. Every worker then failed to boot with `No module named
  'wizard_adopt'`. The unit tests passed because they import from the
  source tree. The image now carries the file, and a new test fails when a
  module the API imports, directly or through another local module, is
  missing from the Dockerfile. It fails on v2.13.5's Dockerfile. On the
  reference Pi, `config-manager` was rolled back to 2.13.4 until this
  release; SmokePing kept measuring meanwhile.

## [2.13.5] — 2026-09-26

Measure what the house uses.

The DNS wizard now selects which services to measure, and how many comes
from the data: the services behind 80% of the house's activity, plus one
for every network and CDN holding at least 0.5% of it that those miss, at
most 60. `smoking-pi dns adopt` makes them targets with ICMP, TCP 443 and
HTTP/1.1, /2 and /3, on the wizard's own HTTP probes. Adoption only adds.
The DNS Wizard dashboard shows their latency and loss per protocol. The
alerter, the digest and the assistants leave these targets out, since many
CDNs drop ICMP and would read as down.

### Added

- **Measure what the house uses: `smoking-pi dns adopt`.** The DNS wizard
  now selects which services to measure, and how many comes from the data,
  not a constant:
  - the services behind 80% of the house's activity;
  - plus one service for every network (AS) and CDN holding at least 0.5%
    of it that those miss;
  - at most 60 services.

  On the reference house that is 46 services, covering 14 of the 27
  networks seen. `smoking-pi dns adopt` makes them targets, in one
  transaction and one SmokePing reload. Each service gets ICMP, TCP 443,
  and HTTP/1.1, /2 and /3, in a new DNS wizard category. HTTP runs on the
  wizard's own probes, with 20 requests in parallel and a 5 s timeout. The
  curated probes allow about 30 targets per HTTP version, because their
  cadence check assumes every request times out. With these settings, 50
  services fit in at most 75 s of a 300 s round. Adoption only adds: a
  service stays measured once adopted, until removals with hysteresis come
  from a week of data.
  The DNS Wizard dashboard gains three panels: median latency per protocol
  across the adopted services, and the latest median and loss per service
  and protocol. The two per-service tables show the last 30 minutes only,
  so retired targets do not linger in them. Settings: `DNS_WIZARD_SCORE`, `DNS_WIZARD_COVERAGE`,
  `DNS_WIZARD_FLOOR`, `DNS_WIZARD_MAX`.

### Changed

- **The DNS wizard's targets are kept apart from alerting and reports.**
  Many CDNs drop ICMP, so their targets would read as "down" and page. The
  alerter, the digest and the assistant (everything built on
  `common.tsdb.base_flux`) leave the `dns_wizard` category out, and so do
  the target pickers of the other dashboards. On the live InfluxDB, the
  filter keeps every existing series: the same point counts, with and
  without it, for five measurements.

## [2.13.4] — 2026-09-26

The DNS wizard: what the house uses, from its DNS, in Grafana.

The DNS observer now summarises the query log every 10 minutes: per
service, the hours it was seen in the last week, the CDN that serves it
and the network behind it. The new **DNS Wizard** dashboard shows how
diverse and how concentrated that is (services, CDNs, networks), what the
top 10 covers and how much it moved since yesterday. With
`DNS_EXPORT_NAMES=1`, a table lists the top services themselves. It changes
no target. `tools/dns-explore` reads the same log offline, comparing
aggregation levels, scores and depths side by side.

### Added

- **The DNS wizard: what the house uses, from its DNS, in Grafana.** Every
  10 minutes the DNS observer summarises what the query log gained, per
  hour and per service (registrable domain). For each service it records
  the CDN at the end of the CNAME chain and the network (origin AS) behind
  it. It then writes a snapshot with:
  - the busiest services by presence (hours seen in the last week);
  - the diversity and concentration of services, CDNs and networks: count,
    effective number (1/HHI), Shannon's effective number, and the share the
    top 5, 10 and 20 cover;
  - the CDNs and networks behind the top 10;
  - how much the top 10 moved since yesterday.

  The Pi's own traffic is left out. A new exporter puts the numbers in
  InfluxDB, and the new **DNS Wizard** dashboard shows them. The table of
  top services shows the latest snapshot only, so services that dropped out
  do not linger as stale rows. Names reach InfluxDB only with
  `DNS_EXPORT_NAMES=1`, because Grafana may be reachable from outside; the
  numbers carry no names. It changes no target yet. Reading the log is
  incremental, including across AdGuard's rotation: on the reference Pi,
  6,212 log entries took 0.6 s of CPU. Settings: `DNS_WIZARD_INTERVAL` (0 = off),
  `DNS_WIZARD_TOP`, `DNS_WIZARD_EXCLUDE`, `DNS_EXPORT_NAMES`. See
  `docs/dns-observer.md`, "The DNS wizard".
- **`tools/dns-explore`: how diverse, concentrated and stable is what the
  house resolves.** This is iteration 0 of automatic target selection from the DNS
  observer. It reads the observer's query log and reports at five aggregation levels: hostname,
  service (eTLD+1), CDN (end of the CNAME chain), origin AS, and AS
  organisation. It uses three scores: queries, uncached queries, and hours present.
  For each, it gives richness, HHI and its effective number, Shannon's
  effective number, and coverage of the top-K for several K. It also shows how many
  CDNs and ASes sit behind the top-K services, which is what coalescing
  hides, and the day-over-day churn of the top-K. The Pi's own traffic is
  left out: it resolves through the router, so it reaches the
  observer too. The tool changes nothing. The design's thresholds are
  meant to come from its numbers after a week of data. CI runs its tests
  with the modules'.

## [2.13.3] — 2026-09-26

A test for the DNS observer's setup, and a canary routers forward.

`smoking-pi dns test` checks each link of the DNS path right after you
point the router at the Pi. The last check asks the router for ten unique
names and counts how many arrive at the Pi. Each failing line says what to
do. The setup guide's step 6 is now "Test it", with a section for checking
the same path by hand with `dig` from a laptop. The canary's default name
moves from `.invalid`, which routers that follow RFC 6761 answer themselves,
to `home.arpa`.

### Added

- **`smoking-pi dns test`: is the DNS path working, in seconds.** After
  pointing the router at the Pi, the only confirmation was the canary: one
  name every 5 minutes, about 15 minutes to a verdict. That is too slow to
  tell right away whether the router setting was saved. The new command checks each link
  in order:
  - the Pi answers on loopback and on its LAN address;
  - its encrypted upstreams answer;
  - the router answers;
  - the router forwards to the Pi. It asks the router for ten unique names
    and counts how many arrive in the Pi's query log.

  All, some or none is the verdict: all is working, some means a
  secondary DNS, none means the router is not using the Pi. Each failing
  line says what to do, and the command exits 1 when a check failed. Every
  name it asks is unique and under the canary domain, so the observer does
  not count the test as the house's traffic. It
  also notes when the Pi's own address is a DHCP lease that has to be
  reserved. On the reference house it caught both problems of the day: a
  router setting that had not saved (0/20 arriving, then 20/20), and the
  canary name issue below.
  The guide's step 6 is now "Test it", with a table from each failing line
  to its fix, and a section to check the path by hand with `dig` from a
  laptop. See `docs/dns-observer.md`.

### Fixed

- **The DNS observer's canary could never arrive on routers that follow
  RFC 6761**, so a working setup read `partial` or `not_receiving`. The
  canary was `<random>.canary.smoking-pi.invalid`, and such routers answer
  `.invalid` (and `.test`) themselves, flagged authoritative, without
  forwarding. The reference router does. The default is now
  `canary.smoking-pi.home.arpa`: `home.arpa` (RFC 8375) exists nowhere
  publicly either, and that router forwards it. An env file that sets
  `DNS_CANARY_DOMAIN` keeps its value. The `partial` hint names
  `smoking-pi dns test`, which tells this case apart from a secondary DNS.

## [2.13.2] — 2026-09-26

Who answers the house's DNS on the Internet side, and an admin page you
can find.

Every 15 minutes the Pi now checks which public resolver really answers:
through the router, and through the DNS observer when it runs. It records
the resolver's owner, its egress addresses, and the client subnet it passes
on to websites, which CDNs pick servers from. It shows on the web admin's
Connection card, in Grafana, in the daily digest and in the assistant's
`system_status`.

The DNS observer's admin password has its own section in `smoking-pi
passwords`. `smoking-pi config set DNS_ADMIN_ADDRESS 0.0.0.0:3053` opens
AdGuard Home's page to the network (the default stays this machine only).
The setup guide checks with `dig`.

### Added

- **Which public resolver answers for the house, over time.** The router is
  the DNS server devices use, but it forwards to someone else. That
  resolver's public address, and the client subnet it passes on, are what
  CDNs pick your servers from, so a change of resolver can move every
  CDN-backed measurement at once. Nothing recorded who it was. Now
  `resolver_identity.py`, in the SmokePing container, asks
  `whoami.akamai.net` and `o-o.myaddr.l.google.com` every 15 minutes through
  the router and through the DNS observer when it runs, and finds each egress
  address's owner (ASN and name) from Team Cymru's DNS service. On the
  reference Pi, through its router: Google (AS15169), whose egress address
  changed on each of three queries, passing a /24 client subnet. Through
  the DNS observer: Cloudflare and Google, AdGuard's two upstreams. Written
  to InfluxDB as `dns_resolver`, and shown in four places:
  - the web admin's *Your connection* card;
  - a *Resolver changed* Grafana annotation next to *Uplink changed*;
  - a digest line on a day it changed hands;
  - a `resolver` block in the assistant's `system_status`.

  A change means the owners share none with the owners seen before. A
  pool's varying mix is not a change: otherwise the observer, spreading
  over Cloudflare and Google, would read "changed" on every other cycle.
  See `docs/public-resolver.md`.

### Fixed

- **The DNS observer's admin password and address were hard to find, and
  the admin UI could not be opened from another computer.** It listened
  on `127.0.0.1:3053` only, on purpose (the query log holds every name
  the house resolves), so `http://<Pi>:3053` from a laptop failed. The
  way around it, an SSH tunnel, was one line at the bottom of the privacy
  section, and `smoking-pi passwords` listed the password as a hidden
  line under "API Tokens". Four changes:
  - `smoking-pi passwords` now has its own DNS observer section: the URL
    that works for the current `DNS_ADMIN_ADDRESS`, the user, the password
    (with `--show-secrets`) and whether the observer runs. The health
    checks include port 53 when the `dns` profile is on;
  - `DNS_ADMIN_ADDRESS` and the other `DNS_*` keys are real keys in the
    env template, so `smoking-pi config set DNS_ADMIN_ADDRESS 0.0.0.0:3053`
    opens the UI to the network. Before, they were comments, which
    `config set` refuses as unknown keys;
  - with the UI on `0.0.0.0`, the supervisor reads the API on loopback
    instead of connecting to the wildcard address;
  - the guide says where the password is and both ways in.

### Changed

- **The DNS setup guide checks with `dig`, and examples use a generic
  address.** Step 3 now runs `dig @<Pi> example.com` and says what a good
  answer looks like (`status: NOERROR`, an address in the ANSWER SECTION).
  `smoking-pi dns enable` prints the same command. The guide and
  `docs/mcp-server.md` used the reference Pi's own LAN address as the
  example; they now use `192.168.1.10`, so nobody copies a real address
  that is not theirs.

## [2.13.1] — 2026-09-26

Which names does this house resolve? And when the answer stops coming, why?

The new opt-in `dns` profile (Pro) runs AdGuard Home on port 53 for the
router to forward the house's DNS to. A supervisor around it tells a quiet
house from a router that stopped forwarding, a hung server, and a stopped
container. `smoking-pi dns enable|status|disable` manages it, and a
step-by-step guide covers any router. It includes reserving the Pi's
address and testing it from a laptop before the router depends on it.

`smoking-pi` is now a command in every directory, from a clone too. A clone
on a release tag runs that release's images without a variable in front of
every command.

Fixes:
- A Docker stop then start left packaged installs down (the web UI was
  silent for 5 minutes on all five release hosts).
- `restore` refused a Basic or Standard backup on a new card.
- The assistant could not see the 12 HTTP and TCP targets.
- `upgrade` left a disabled profile's container on its old image.

Every release now tests recovery on a real stack: `backup`, `purge`,
`restore`, and a reinstall after `apt purge`.

### Added

- **A step-by-step guide for pointing a router at the DNS observer.** The
  first version of `docs/dns-observer.md` said what to set but skipped two
  steps that decide whether the house keeps its DNS:
  - **reserve the Pi's address first.** Without a reservation, a new DHCP
    lease leaves the router forwarding every query to an address nobody
    holds;
  - **check from a laptop that the Pi answers** (`nslookup example.com
    <Pi>`) before the router depends on it.

  The guide is now six numbered steps plus undo, for any router. It also
  says how to tell whether a router is a DNS proxy, which is what the setup
  needs. `smoking-pi dns enable` prints both checks.

- **`smoking-pi` is a command in every directory, from a clone too.** A
  clone installed with `setup.sh` never put it on the `PATH`: the reference
  Pi had no `smoking-pi` at all, and reading the passwords meant typing
  `~/smoking-pi/packaging/smoking-pi passwords --show-secrets`. Now
  `setup.sh` (all three editions), `smoking-pi install` and `upgrade` link the
  checkout's command into `/usr/local/bin` (through `sudo` only when it asks
  no password), else `~/.local/bin`; `smoking-pi link` does it on demand.
  It never shadows the package's `/usr/bin/smoking-pi` and never replaces a
  real file. `smoking-pi` with no command now says what is installed, how
  many services run, where to open it and the handful of commands people
  use; `--help` stays the full reference. The setup scripts' closing tips,
  the README and the guides use the command instead of `./show-passwords.sh`,
  `docker compose` or `packaging/smoking-pi`.

- **A DNS observer: which names this house resolves, and a clear answer when
  it stops knowing.** Measuring "the services that matter here" needs to know
  what the house uses, and the Pi, an ordinary LAN client, only saw its own
  traffic. The new opt-in `dns` profile (Pro; `smoking-pi dns enable`) runs
  **AdGuard Home** (pinned v0.107.79, filtering off) on port 53. The router
  forwards the house's DNS to it, and it forwards over DoH to 1.1.1.1 and
  8.8.8.8. We reused AdGuard instead of writing a DNS server: it already has
  DoH/DoT/DoQ upstreams, a plain-DNS fallback, serve-stale caching and a
  query log. We wrote the part AdGuard does not have: a supervisor that says
  whether observations are arriving and, if not, why. The three causes look
  the same from the Pi (no queries), so each gets its own evidence:
  - **container or Docker down**: a `status.json` heartbeat that readers
    treat as `down` after 90 s, keeping the time of the last real query; a
    stop on purpose is written at once as `stopped`;
  - **router never set, or reverted**: a canary. A unique name under
    `.invalid` is asked of the router every 5 min; seen here means the path
    works (`quiet`, not an outage, when the house is silent); three misses
    and no queries mean `not_receiving`; misses while queries arrive mean
    `partial` (a secondary DNS takes a share);
  - **AdGuard hung**: an `ANY` self-test every 10 s that AdGuard answers
    locally, so an internet outage never fails it; after 3 misses AdGuard
    is killed and restarted.

  The house's own fallback is the router's secondary DNS, and
  `smoking-pi dns enable` tells you to set one. The generated config also
  closes four traps, each tested:
  - AdGuard's default 20 queries/s rate limit, which would throttle the whole
    house behind the router's single address;
  - an empty `DNS_ALLOW_CLIENTS`, which AdGuard reads as "everyone" (an open
    resolver);
  - upstreams or reverse lookups that go to the router, which loop back to
    the Pi;
  - AdGuard's 90-day query log, cut to 7 days with client addresses masked.

  Names never leave the Pi. See `docs/dns-observer.md`. The e2e run on the
  reference Pi used side ports and a stand-in router; the real router has
  not been pointed at it yet.

- **An uplink change reaches the diagnosis, not only the dashboards.** A
  latency step at the moment a cable was plugged in is the path changing,
  not the ISP. Everything that interprets the measurements now says so, in
  the same words, from `host_uplink`:
  - the alert's verdict gets one more sentence when the change was in the
    hour before the alert (*"Also: this host's uplink moved from wlan0 to
    eth0 (wired) at 14:02, so the measurements before and after crossed
    different links."*), whatever the scope;
  - the daily digest lists the day's changes under *Local link*, on a wired
    host too, and the AI report's prompt lists them all;
  - the MCP server's `system_status` has an `uplink` block (the interface,
    its kind and the last change within a week), and `get_loss_events` has
    `uplink_changes` when one falls in its window.

  A quiet uplink adds nothing to any of them, and a failed or missing
  `host_uplink` query changes nothing either.

### Fixed

- **A clone on a release tag runs that release's images, without a variable
  typed in front of every command.** The compose files name
  `…/<service>:${SMOKING_PI_VERSION:-dev}`, and a clone never recorded a
  version: the reference Pi, checked out on `v2.13.0` and running the
  `:2.13.0` images, was one bare `smoking-pi up` or `upgrade` away from
  recreating every container on stale local `:dev` builds. The command now
  takes the version from the checkout's release tag (the final release over
  its candidates on the same commit; `git` reads the checkout under `sudo`
  too), `smoking-pi paths` says where it came from, and `upgrade` stops with
  the way out when the tag has no published images. Off any tag it builds
  `:dev` as before; `SMOKING_PI_VERSION=dev` builds whatever the checkout is
  on, and any other value is still a pin. The release-acceptance steps no
  longer need the variable.

- **A Docker upgrade could leave a packaged install down until the next
  boot.** The unit `Requires=docker.service`, so when Docker stops it stops
  too, and its `ExecStop` removes the containers. Nothing started it again
  when Docker came back: a Docker package upgrade that stops and then
  starts the daemon left the monitor off. `Requires=` carries a restart of
  Docker over to the unit, which is why `systemctl restart docker`
  recovered, but a stop is carried over and a later start is not: on all
  five release hosts the stop then start left the web UI silent for 5
  minutes. The unit is now also wanted by `docker.service`, and the package
  re-enables an already enabled unit on upgrade so existing installs get
  it. Every release now restarts Docker, and stops and starts it, under
  the running unit. A clone (like the reference Pi) has no unit; its
  containers come back by their `unless-stopped` policy.
- **`restore` refused a Basic or Standard backup on a new card.** With
  only the package installed there is no edition recorded, so the command
  assumed Pro and answered "backup is of the basic edition, this is pro" —
  exactly when the backup was needed. A packaged host with no env file and
  no recorded edition now restores the backup's edition and records it, the
  way `install` does. An installed host still refuses another edition's
  backup, and a recorded edition is never overwritten.
- **Recovery was never tested on a real stack.** `backup`, `restore` and
  `purge` ran only against a stubbed docker. Every release now runs, on each
  Ubuntu host, a marker in the data volume, `backup`, `purge --config`, the
  edition file removed (a new card), `restore`: the same env file, config
  and marker must come back and the web UI answer. After `apt purge`, it
  reinstalls the package and starts the kept env file and volumes: the same
  secrets and data, as the purge message promises.
- **The assistant could not see the HTTP and TCP targets, and a wrong
  target name looked like missing data.** `get_latency_stats` read only the
  ICMP and DNS measurements, so the 12 `*_h1/_h2/_h3` and `*_tcp443`
  targets were invisible to it: asked about `Google_h2`, it answered "no
  data points". It now reads `http_latency` and `tcp_latency` too (30 of 30
  targets on the reference Pi, up from 18), in the MCP server and in the
  web admin's assistant. In the MCP server, a name that is not a target
  (an agent asked for `Cloudflare` and `CPE_Gateway` in a real session on
  2026-09-24) now returns an error with the real names, a `did_you_mean`
  (`cloudflare`), and a pointer to `get_microcut_stats` when the name looks
  like the CPE, instead of an empty result that reads like an outage.

- **`smoking-pi upgrade` left a disabled profile's container running on
  its old image.** `up -d --remove-orphans` removes only containers of
  services the compose files no longer define; a service that is defined
  but whose profile is off is not an orphan to Compose. During the
  v2.13.0-rc.3 acceptance, `pro-ai-insights-1` (the `ai` profile off)
  stayed on `ai-insights:dev` for days while everything else moved to
  `2.13.0-rc.3` — an unversioned, unwanted service quietly running beside
  a release. `upgrade`, `up` and `config set COMPOSE_PROFILES` now stop and
  remove the project's containers (by the `com.docker.compose.project`
  label) whose service `compose config --services` does not list, and say
  which. Volumes are kept. When Compose cannot list the enabled services
  nothing is removed, since an empty list would otherwise mean the whole
  stack.
- **The release guide named a candidate asset that does not exist.**
  `docs/release-acceptance.md` said to download
  `smoking-pi_X.Y.Z~rc.N_all.deb`; GitHub replaces `~` in asset names
  with `.`, so the file is `smoking-pi_X.Y.Z.rc.N_all.deb` and the
  documented `apt install` named a file that is not there. The
  guide and `docs/packaging.md` now name the real file and say that apt
  still installs version `X.Y.Z~rc.N` from it.

## [2.13.0] — 2026-09-24

Is it measuring, and can you tell? Plus a release process that proves
itself.

The web admin's dashboard now answers the first question a new install
raises. It shows how many targets were written in the last two
measurement steps, judged from each target's own RRD rather than a file
count (the reference Pi has 180 RRDs for 30 targets). A SmokePing reload
that failed is no longer reported as done. `smoking-pi install` ends on
the address to open, `smoking-pi openclaw` connects the assistant, and a
getting-started guide walks through the rest. The doctor names the
interface every measurement crosses. It now resolves the uplink by route
metric, and IPv6-only hosts are covered.

Secrets stay off the screen unless you ask: `show-passwords.sh` withholds
them, `--show-secrets` refuses a pipe unless forced, and the env file's
permissions are checked. Three health checks that had never worked now
do, and none of them puts a credential on a command line. The config API
finds containers by their Compose labels instead of guessing their names.

The documentation was held to the code. The backup recipe backed up
nothing. "Switching databases" told you to delete every target. The MCP
tool table was four tools short. All of these are fixed.

Every PR now builds all nine images for both architectures and runs
CodeQL. Releases are cut from a candidate tag, `latest` moves only after
every install test has passed, and each release carries an evidence file
tying it to its commit, digests and package checksum. This is the first
release cut that way.

The third candidate brings more. How often each probe measures can be
changed from the web admin. The analysis reads each target's real cycle,
loss events and Grafana's "unreachable" overlay count pings lost instead
of a fixed percent, and a guard keeps SmokePing running when an RRD no
longer matches its probe. The first login is a welcome tour: whether it
is measuring, what your own network suggests (the router, the
resolvers), and the seeded targets you may want to pause. Settings that
meant editing the env file now have commands: `smoking-pi config`, `alerts` (with the daily digest)
and `links`. The InfluxDB dashboards mark the moment the uplink changes,
from Wi-Fi to Ethernet or back.

### Added

- **`shared/scripts/acceptance-record.sh`: the release record, filled in
  from the Pi.** It prints the *Validation* block of
  `docs/release-acceptance.md` with the tag, commit, Pi model, OS, kernel,
  architecture, per-container image, restart count and uptime, and the
  doctor's summary. It names any container that isn't on the candidate's
  tag. Read-only, no secrets. Its first run found `ai-insights` on `:dev`
  during the v2.13.0-rc.2 acceptance.
- **Which interface the measurements crossed, over time.** A cable plugged
  into a Pi that measured over Wi-Fi moves every measurement onto Ethernet
  (NetworkManager gives it metric 100 against Wi-Fi's 600), and nothing
  recorded it: latency stepped down with no explanation. On an
  Ethernet-only Pi nothing recorded the uplink at all. The Wi-Fi collector
  now writes `host_uplink` (`interface`, `kind`, `family`) on every Pro host,
  at once on a change and every minute otherwise, and it adds `previous` on
  the point where the interface changed. The last value is read back after a
  restart, so a change across a reboot is marked too. Every InfluxDB
  dashboard has an **Uplink changed** annotation (*Uplink wlan0 → eth0
  (wired)*). `doctor --live` names the standby route when both links are up,
  and the doctor now checks annotation queries against what the exporters
  write, as it does panel queries. `docs/wifi.md` explains how to choose the
  interface with route metrics (*Choosing the interface*).
- **`smoking-pi alerts --digest HH:MM|off [--digest-tz ZONE]`: the
  daily summary without editing the env file.** The time and zone are
  checked before anything is written; a value the alerter cannot read
  would disable the digest. It works alone or together with a delivery
  mode, and it says when `NOTIFY_MODE` is off and the digest would only be
  logged.
- **`smoking-pi links`: where the links in alerts and assistant answers
  point.** With no option, it shows the at-home and from-anywhere
  addresses. `--lan auto` sets `PUBLIC_BASE_HOST` to the address the local
  network sees (the default route's source, not an SSH session's address,
  which can be a tailnet one). `--tunnel` sets `TUNNEL_BASE_HOST` and
  refuses a bare host, which would become a dead `http://host:3000` link.
  `--off` clears both. The alerter and the MCP server are recreated.
- **`smoking-pi alerts`: where alerts go, in one command.** With the
  `alerts` profile on and `NOTIFY_MODE` unset, alerts were evaluated,
  logged and delivered nowhere, with no error. The command:
  - sets the mode (`--openclaw --to telegram:<id>`, `--webhook` with the
    URL on stdin or at a prompt, `--off`, or asks) and its keys;
  - takes the gateway token from the user's `openclaw.json` when there is
    one, never printing it;
  - refuses a bare chat id, which OpenClaw does not deliver to;
  - turns on the profile, recreates the alerter (and the mcp-server, which
    delivers charts through the same keys), and prints the alerter's own
    delivery preflight line;
  - with `--test`, or a yes at the prompt, sends one labeled message
    through the new `alerter main.py --test`, which refuses after a failed
    preflight. A message arriving is the only proof that the recipient is
    right.
- **`smoking-pi config`: settings by name, not by editing the env
  file.** `list` shows every key the edition's `.env.template` declares,
  with secrets hidden. `get` prints one, and a secret only with
  `--show-secrets`. `set` and `unset` write it, then recreate only the
  services whose compose entry reads it, and only those the recorded
  profiles enable: naming a service on `compose up` would start one
  nobody enabled. `COMPOSE_PROFILES` applies to the whole stack. Guards:
  - a key the template doesn't declare is refused, with the nearest one
    suggested (`NOTIFY_MOD` → `NOTIFY_MODE`);
  - a secret (TOKEN, PASSWORD, SECRET, `*_KEY`) is never taken from the
    command line, where `ps` and the shell history would keep it. It's
    typed at a prompt or piped on stdin;
  - credentials that install generated are refused, because the data
    volumes hold them (the same reason `install` won't run twice).
  - a value with `$` (a pbkdf2 or bcrypt hash, many generated secrets)
    is single-quoted in the env file. Compose interpolates `$` in `.env`
    files and bash in the scripts that source it, and an unquoted
    `pbkdf2:sha256:260000$salt$hash` reached the container as
    `pbkdf2:sha256:260000`. Plain values are written as before.
- **The welcome tour's optional fourth step: a chat assistant.** It
  says what an assistant such as OpenClaw adds, and whether one is using
  this install. The answer comes from the MCP server's own `tool=` log
  lines, the evidence `smoking-pi openclaw --check` reads, never from an
  assistant's reply. It shows *Not set up* (run `sudo smoking-pi
  openclaw`), *Stopped*, *Not used yet* since the server started (run
  `--check`), or *Connected* with the last tool and time. config-manager
  serves it as `GET /assistant` through the Docker socket it already
  uses. Nothing is registered from the web: connecting stays the host
  command's job (`docs/cli-scope.md`).
- **A probe's step and pings can be changed in the web admin.** A new
  **Probes** page lists each probe's cycle, its targets and its traffic.
  **Change** sets how often (every minute to every hour) and how many
  pings (3 to 20). Before saving, it says that every target of the probe
  starts a new SmokePing history, with the old files archived and not
  deleted, and that Grafana keeps everything. It needs a confirmation.
  Behind it, `PUT /probes/<name>` on the config API accepts only those
  two values. It refuses a cycle that could outrun its step (pings ×
  per-ping timeout), then saves, regenerates and reloads, and the RRD
  guard archives the old files. The welcome tour no longer promises
  "every five minutes". This is part 3 of stage D; see
  `docs/measurement-frequency.md`.
- **The first login is a welcome tour.** The install seeded 21 targets
  in silence, and a new user met a dashboard of numbers with no idea what
  was being measured or why. The web admin (Standard, Pro) now opens once
  on three steps. First, whether it is measuring. Second, what this host's
  network suggests (the *Your connection* entries below, checked by
  default, added in one click through the add form's own validation).
  Third, the seeded targets by category, each with a switch to pause it.
  The seeding stays (a decision of 2026-09-24): an install measures from
  the first minute, and the tour shows what it set up instead of asking
  first. It adds and pauses through the Targets page's endpoints, so it
  has no write path of its own. *Finish* and *Skip for now* are
  remembered by config-manager (`GET`/`POST /first-run`, a file beside
  the generated config). The dashboard's **Welcome tour** button reopens
  it. If config-manager cannot be asked, the dashboard opens normally: an
  outage never traps a login in the tour. Existing installs see it once
  after upgrading, which is also where they meet the router suggestion.
- **The dashboard suggests what your own network should be measured
  against.** Every install seeded the same targets, and the one address
  that separates "my Wi-Fi" from "my ISP", your router, was never among
  them: it differs in every home. On the reference Pi, the router was not
  measured at all. On Pro, a **Your connection** card now reads the host's network
  from inside SmokePing's namespace: the uplink interface and whether it
  is Wi-Fi (stage A had left this out), the default gateway, the
  resolvers the host was given, and the CPE that discovery found. For
  each one it says whether it is measured and under which name. What is
  not measured has an **Add…** button that opens the normal add form,
  pre-filled, so a suggestion goes through the same validation as
  anything typed, and nothing is added without you. Local resolvers
  (`127.0.0.53`, Tailscale) and link-local IPv6 routers are explained,
  not suggested. On Standard, whose SmokePing is on a Docker network, the
  card says it cannot see the host, instead of calling Docker's gateway
  your router. The gateway comes from the same route parsing that picks
  the Wi-Fi verdict's uplink (`wifi_link.default_route4/6`), so the two
  cannot disagree. `GET /recommendations` on the config API.

- **Releases are cut from a candidate, and `latest` waits for the install
  tests.** Until now `latest` moved to a new release's images in the same
  job that published them, before a single host had installed it. A
  release that failed its install tests was already what a `:latest` pull
  fetched. Also, the Raspberry Pi acceptance ran on a clone built locally,
  not on the artifacts the release shipped, and nothing on the release said
  which digests or which package checksum it had shipped. Now:
  `vX.Y.Z-rc.N` tags run the whole release pipeline on a GitHub
  pre-release. The Pi is accepted on those exact images and `.deb`, and
  the release is tagged on the same commit. `latest` moves in a `promote`
  job only after every host passed, and never for a candidate. Every
  release and candidate carries `smoking-pi_<version>_evidence.md`, with
  the commit, the run, each image's digest and the package's sha256.
  `attach` refuses a candidate that is not a pre-release (apt would serve
  it) and a release that is one (apt would skip it). The docs site and
  `/apt` are deployed for a release only. Which tags are accepted is
  `packaging/release-version.sh`, tested in CI; anything else starting
  with `v` is refused before anything is pushed. The checklist is one
  page: `docs/release-acceptance.md`, *Releasing, step by step*. The
  first candidate, `v2.13.0-rc.1`, failed its own install tests on all
  nine hosts, and correctly so. Its package reported `2.13.0`, because
  `CITATION.cff` already says so. A packaged install would have pulled
  `:2.13.0` images, which do not exist until the release. The candidate
  package now carries a `VERSION` file (`2.13.0-rc.2`), which the
  command reads first. `latest` never moved.
- **Every PR builds every image, for arm64 and amd64.** Until now no PR
  built an image: all nine were built only when tagging a release, so a
  broken Dockerfile (a new module without its `COPY` line, a base image
  that stopped resolving) surfaced as a release blocker instead of in the
  change that caused it. `ci.yml` now builds the nine on native runners
  of both architectures, nothing pushed, with a per-image build cache;
  `Images build (all)` is the one check the branch rules require. Where
  each image builds from moved into `packaging/image-context.sh`, which
  the PR build and the release both read, and `packaging/check-images.py`
  now fails when the PR matrix and the Dockerfiles disagree, as it already
  did for the release matrix.
- **Is it measuring? A Measurements card on the web admin's dashboard,
  and `GET /measurements` on the config API.** "SmokePing: Running" meant
  the container was up, nothing more: a target added a minute ago, or one
  that stopped updating last week, looked exactly like a healthy one. Now
  every configured target is checked against the modification time of its
  RRD, which SmokePing rewrites at every step: *fresh* within two steps,
  *stale* after that, *missing* if it never wrote one, *pending* if it has
  no data since the target itself last changed (added, renamed, re-enabled)
  or since SmokePing started, less than two steps ago. Not the `Targets`
  file's age: every regeneration rewrites that file, and a target broken
  for weeks read as "just added" after each unrelated edit. The
  expected set is the generated `Targets` file plus the router targets
  `cpe_discovery.py` includes, never the RRDs on disk: the reference Pi
  holds 180 RRDs for 30 targets, the rest left by targets deleted long ago.
  Per-probe steps come from the generated `Probes` file. Read through the
  Docker socket config-manager already uses, so no compose file changed.
- **`smoking-pi install` ends on the address to open, and `smoking-pi url`
  prints it again.** The install used to end on `http://localhost:8080`,
  which, to someone who installed over SSH from a laptop, is the laptop.
  Now it prints the address that computer can actually reach: over SSH,
  the address the SSH client connected to (under `sudo`, which drops
  `SSH_CONNECTION`, the source address this machine uses toward the
  `who -m` client); otherwise the default route's source address; never
  `hostname -I`'s first entry, which on a Pi with Docker and Tailscale can
  be a bridge or the tailnet. IPv4 even over an IPv6 session: Pro publishes
  the web admin as `0.0.0.0:8080`, which Docker binds on v4 only, and the
  v6 URL got no answer on the reference Pi. It gives the username for each
  page, the `.local` name when avahi runs, waits up to two minutes for the
  page to answer rather than printing a URL that fails on the first try,
  and over SSH adds the `ssh -L` line for when a firewall is in the way.
  `smoking-pi url` exits 1 if nothing answers.
- **`smoking-pi openclaw` — the assistant connection as a command, ending
  in proof.** `smoking-pi install` used to ask "Connect a chat assistant?"
  and, on yes, print the path to a document. That was the whole feature.
  Meanwhile `docs/openclaw-integration.md` is six steps, and the
  interesting thing about them is that four have a failure mode that looks
  like success: registering the MCP server without installing the skill
  leaves the agent answering from its own shell; a running gateway keeps
  its cached tool set, so a correct registration reaches nothing until it
  is reloaded and a new session started; and `openclaw mcp probe` reports
  healthy in both cases. The command now does the mechanical part — the
  token, the `mcp` profile, starting the server, checking the port refuses
  an unauthenticated request, registering, installing the skill — and
  `smoking-pi openclaw --check` asks the agent a question and then greps
  the MCP server's own log for `tool=` lines. **The answer is never the
  test**: a well-primed agent produces a fluent, accurate-sounding reply
  from `ping` while the server sits untouched, and that false positive is
  how four days of a dead integration went unnoticed. When there is no
  `tool=` line the command says so and lists the three causes in order.
  The MCP token reaches curl on stdin (`-K -`), never in argv, for the
  reason PR #96 established — a command line is readable by every account
  on the host — and a test asserts both halves, because one that only
  checks the credential is *absent* passes just as happily when it never
  arrived. A token that is not generated (someone wrote it by hand) is
  refused if it holds characters that would break the registration JSON,
  rather than producing a malformed payload that reads like a connectivity
  failure.
- **The install now distinguishes "no OpenClaw" from "OpenClaw on my
  laptop".** The old yes/no could not: it printed the same-machine
  document either way — which is the wrong advice for the remote case,
  where a tunnel between the two loopbacks has to exist first. The prompt
  is a three-way choice (here / another machine / not now); *here* runs
  the connector, *another machine* points at `docs/remote-openclaw.md`,
  *not now* names the command. Every branch ends with a working install:
  the stack is already measuring by the time the question is asked, and
  nothing about the assistant is required. `--yes` never prompts and still
  names the command.
- **The maintenance page is on the documentation site.**
  `shared/docs/maintenance.md` was deliberately kept off the site when it
  went up (PR #73), because it still described pre-editions container
  names and scripts that no longer existed — publishing it would have been
  publishing wrong instructions. Packaging backlog #7 rewrote it around
  the `smoking-pi` command and Compose's labels, which removed the reason,
  and nothing moved it. It is now `docs/maintenance.md`, in the nav under
  *Operating*, with its plain-text `docs/*.md` references turned into real
  cross-links. It is the page for the states the command does not handle —
  a stuck container, an orphaned volume, emergency recovery — and it was
  reachable only by browsing the repository. Publishing it turned up one
  thing worth fixing first: the page described `smoking-pi restart` as
  doing `down` then `up` and implied it re-syncs Pro's InfluxDB token.
  Neither is true — `restart` is `docker compose restart`, which restarts
  containers in place and so picks up no changed compose file, image or
  env file, and the token resync lives in
  `manage-containers.sh --action restart`. A Pro user trusting the page
  could restart, get empty Grafana panels while data was arriving, and
  have no reason to suspect the token. Both corrected, with the symptom
  named so the divergence is recognizable.
- **CodeQL runs on every PR, stacked ones included, as a workflow.** It was
  GitHub's default setup, which analyzes only PRs that target the default
  branch: a PR based on another PR's branch got every other check green and
  no CodeQL at all, and nothing said so (#102). `.github/workflows/codeql.yml`
  analyzes the same four languages (actions, JavaScript/TypeScript, Python,
  Ruby) under the same categories, so existing alerts keep their numbers, on
  every PR whatever its base, on pushes to `main` and weekly.
  `CodeQL analysis (all)` is the check the branch rules can require.
- **A decision about what belongs in the command and what belongs in the
  API** (`docs/cli-scope.md`, in the site nav under *Operating*). The
  roadmap asked for this before the CLI grew any further, and it grew
  again this week. The rule it settles on follows from one fact that was
  never written down: the API *is* a container in the stack it would
  manage, so it is available exactly when it is not needed. The command
  therefore owns everything that must work with the stack down — install,
  upgrade, backup, restore, purge, up/down, passwords, doctor, logs,
  status — and the API and web admin own everything about what is
  measured, because those are PostgreSQL rows with validation and a UI
  already built for them. Hence no `smoking-pi add-target`: it would be a
  second writer to that database. `restart` and `status` are the only
  deliberate overlap, and the page shows they are two different
  operations sharing a word — Compose-level for the command, SmokePing
  specifically for the API, right after a config change. It ends with
  four questions to answer before adding a command.

- **A getting-started guide** (`docs/getting-started.md`, in the site nav
  right after Home). Seven numbered steps from a bare Raspberry Pi to a
  stack that is measuring: what you need, Docker with Compose v2 (and why
  Raspberry Pi OS needs Docker's own repository), `apt install` or a clone,
  `smoking-pi install` with what each answer means, the systemd unit, and
  then the part no install guide had: **how to tell it is actually
  working** — the six containers and which five report healthy, the URLs,
  the fact that every graph is empty until the first 300-second step
  completes, and `doctor --live`. It ends with a symptom → cause → fix
  table of the nine things that actually go wrong, from
  `docker: 'compose' is not a docker command` to a Grafana password that
  looks wrong because it lives in the volume.
- **A doctor check that the MCP tool table is the tool list**
  (`mcp-tools-documented`). It compares the functions registered with
  `@mcp.tool()` against the rows of the table in `docs/mcp-server.md`, in
  both directions: an undocumented tool is one nobody knows to ask for, and
  a documented tool that no longer exists reads as a promise. Static, so it
  runs in CI.
- **The doctor names the interface every measurement crosses**
  (`uplink-interface`, a live check). Nothing said this before, and the
  omission cost a year: the reference Pi has `eth0` with no carrier and its
  default route on `wlan0`, so every latency figure recorded since the stack
  went up had crossed a Wi-Fi hop nobody was measuring. It now prints
  `measuring over wlan0 (wireless, IPv4)`, says **wired** explicitly — so an
  empty Wi-Fi dashboard is distinguishable from a broken collector — and
  **warns** when the default route has moved onto a Docker bridge, a VPN
  tunnel, Tailscale or WireGuard. That last case is the one it exists for:
  the latency figures then describe that path, and the Wi-Fi verdict is off,
  because it requires the wireless interface to carry the default route.
  Nothing anywhere said so.

### Changed

- **The analysis reads each target's real probe cycle.** The alerter and
  the MCP server assumed every target is measured every 300 s with 10
  pings. That holds for the shipped probes, but on a probe with a
  600 s step the 1200 s down window held two points where `target_down`
  needs three, so it could never fire. The exporter now writes `step` and
  `pings` as fields on every `latency`/`dns_latency` point, and
  `common/cadence.py` reads the latest per target. `DOWN_WINDOW`,
  `STALE_WINDOW` and `ALERT_RESOLVE_AFTER` are floored at four, four and
  three steps of the slowest probe. The mean behind `high_loss` covers
  three of those steps, and widespread cycles are bucketed to that step.
  `get_loss_events` folds each target's episodes on its own step and
  reports `step_s` and `pings` per target. On the shipped probes every
  window, message and result is what it was. This is the first of three
  parts of editable measurement frequency (first-run stage D). See
  *Probe cadence* in `docs/alerting.md`.
- **Loss events are counted in pings lost, not in a fixed percent.** The
  15% bar meant "2 of 10" on FPing, "3 of 20" on a 20-ping probe, and a
  single lost DNS query of five cleared it. An event is now more than 1.5
  pings' worth lost, of however many the target's probe sends. The RRD
  spreads a cycle's lost pings over two aligned steps, so loss values are
  not whole pings, and 1.5 keeps one lost ping out whole. The same rule
  applies to `high_loss` persistence, the floor for `outage` (with
  `WIDESPREAD_LOSS_PCT`), `get_loss_events` in the MCP server and the web
  assistant, and the digest's and AI report's `loss_events`. Each builds
  the per-target bar into its Flux query as a `dict`. `min_loss_pct`
  still sets a fixed percent when given. On the shipped probes, only one
  lost DNS query stops counting: seven days on the reference Pi gave 843
  events under both rules. This is part 2 of stage D.

- **Grafana's "unreachable" overlay counts pings lost, too.** Five
  dashboards marked a 15-minute window when its mean loss reached 5%. On
  the shipped step that is 1.5 lost pings of 30, and on any other step it
  means something else. A window is now marked when its points lost 1.5
  pings' worth between them (loss × `pings`, with 10 or 5 when a point
  predates the field). On the reference Pi over seven days, ICMP went
  from 387 marked windows to 383 and DNS from 46 to 45. HTTP (194 → 142)
  and TCP (78 → 62) lose their single-lost-fetch marks, the same rule as
  the loss events. The doctor now reads a pivoted field (`r.loss` after
  `pivot`) as a column, not a tag filter.

- **`show-passwords.sh` no longer prints your secrets unless you ask.** It
  ran at the end of every install and on every `smoking-pi passwords`, and
  it printed all nine of them — the Grafana password, the config-manager
  and MCP tokens, the InfluxDB admin password and API token, the PostgreSQL
  password and the `DATABASE_URL` that embeds it, the web-admin password
  and the Flask `SECRET_KEY` — with no way to ask for anything less. The
  default view now shows each one as `set (hidden)`; `--show-secrets`
  prints the values. Two things deliberately did *not* move behind the
  flag: whether a secret is **set at all**, because `unset` means an
  unauthenticated endpoint and that is a warning, not a credential; and
  everything that was never secret — URLs, usernames, container state,
  port checks. `smoking-pi passwords` forwards its flags, so
  `smoking-pi passwords --show-secrets` is the whole interface.
- **`--show-secrets` refuses a pipe, a file or a capture unless forced.**
  When stdout is not a terminal it exits 3 with an explanation instead of
  printing, because a redirect outlives the screen — a log, a transcript,
  an assistant reading the run. `--force` says you meant it. The install
  tail is unaffected: it ends on the hidden view and one line saying where
  the values are.
- **The env file's permissions are checked.** Hiding secrets on screen
  means little if any account on the host can read the file they all live
  in, so a mode looser than `x00` is called out with the `chmod` to fix it.

### Fixed

- **An IPv6 address in `PUBLIC_BASE_HOST` or `TUNNEL_BASE_HOST` now makes
  a working link.** The "already has a port?" test was "contains a colon",
  which every IPv6 literal does. So `2001:db8::5` became
  `http://2001:db8::5`: no brackets and no port, a URL no browser opens.
  With one set, every Grafana and web-admin link in alerts and assistant
  answers was dead. The address is now bracketed and gets its port
  (`http://[2001:db8::5]:3000`), and `[addr]:port` keeps its own port.
  A link-local `fe80::` address, or one with a zone id, makes no links and
  logs why once: it routes only with a zone id, and browsers reject zone
  ids in a URL. A bare host with a path now gets its port before the path
  (`pi.lan:3000/x`, not `pi.lan/x:3000`). Values with a scheme are used
  as given, as before. `smoking-pi links --lan` now takes a global or ULA
  IPv6 address and stores it bracketed. It still refuses link-local and
  `::1`, and warns that the web admin (`0.0.0.0:8080`) is IPv4-only. It
  also shows a `host:port` value once, instead of with `:3000` appended.

- **SmokePing no longer dies on an RRD it cannot load.** An RRD is made
  for one step and one ping count. When a target's file disagrees with its
  probe, SmokePing stops at reload ("RRD parameter mismatch ... You must
  delete ...rrd") and every target stops being measured. That happens when
  a probe's step or pings change, when a paused target is resumed after
  such a change, or when a deleted target is added again under the same
  name. Before every reload, config-manager now runs `rrd_guard.py` in the
  SmokePing container with what the new configuration expects of each
  RRD, CPE targets included. Mismatches are moved, not deleted, to
  `/data/.archive/<time>/`; SmokePing creates fresh files, and Grafana
  keeps the full history from InfluxDB. On the reference Pi, a dry run
  checked all 30 RRDs and found nothing to move. The ClickHouse exporter
  skips the archive. See `docs/measurement-frequency.md`.
- **`step_seconds` and `pings` on a target are refused, not dropped.**
  `POST`/`PUT /targets` accepted both and discarded them without a word:
  they belong to the probe, and SmokePing measures every target of a probe
  on the same cycle. The API now answers 400, naming the fields.
- **The add form says DNS sends 5 queries.** It said 10. The form now
  states each probe's cadence from its configured settings.
- **The dashboard's bandwidth estimate uses each target's probe.** It
  assumed 10 pings every 300 s for every target, which counted DNS, HTTP
  and TCP targets (5 per cycle) double. The OCA fetcher's copy of the same
  estimate, which nothing read, is gone.

- **A SmokePing reload that failed was reported as done.** After every
  target change config-manager sends `killall -HUP smokeping` into the
  SmokePing container, and it ignored the exit code: with no smokeping
  process to signal it still logged "Sent reload signal", and the web admin
  said "config regenerated automatically" whether or not anything had
  happened (in YAML mode, even when generating the configuration had
  failed). Now the create, update, delete, toggle, `PUT /config` and
  `/generate` responses carry `reloaded`, and the web admin and the chat
  assistant say "saved, but SmokePing did not confirm the reload: restart
  SmokePing" when it is false. An older config-manager that sends no
  `reloaded` field is not treated as a failure.

- **The docs workflow's safety was implied, and its signing key was
  everywhere.** `docs.yml` runs on `workflow_run` (with this repository's
  secrets) and checks out and executes the triggering Release run's
  commit. That is safe only because Release runs on tag pushes, which only
  maintainers can make; code scanning alert #75 flagged it, correctly, as
  resting on nothing written down. The job now publishes only when the
  Release run was a `push` from this repository. And `APT_SIGNING_KEY`,
  which sat in the job's environment where every step could read it --
  `mkdocs`, its plugins and whatever the checkout installs -- now reaches
  only the step that signs the apt repository and a step that reports
  whether it is set (a yes or no, never the value).

- **The config API identified containers by guessing at their names.**
  Packaging backlog #7 established that nothing in the stack guesses a
  container name and fixed every shell script; the three places in
  `config-manager/api.py` that do the same were never touched. `GET
  /api/containers` accepted any container whose name merely *contained* the
  project name, and the default project is `pro` — so an unrelated
  `prometheus` or `proxy` on the same host was reported as part of the
  Smoking Pi stack. Worse, `resolve_container_name` matched the
  `com.docker.compose.service` label **without** the project label: on a
  host running two editions side by side, two containers answer to
  `smokeping`, whichever the daemon listed first won, and that is the path
  `POST /restart` takes — the web admin's restart button could have
  restarted the other edition's SmokePing while reporting success. Both now
  test `com.docker.compose.project`, and resolution requires project *and*
  service. The name-pattern and substring fallbacks are gone with them:
  Compose labels every container it starts, including the ones that set an
  explicit `container_name` (`smokeping-mcp-server` carries
  `com.docker.compose.project=pro`), so there was nothing left for them to
  find that the labels miss. The third place was the same guess wearing a
  default: `_check_smokeping_status`, behind `GET /status`, fell back to the
  name `<project>-smokeping-1` whenever resolution failed. It now says the
  container is not there, which is both true and more useful than a report
  on somebody else's. Resolution also sees stopped containers now, which the
  name patterns used to reach and the label loop would not have — so
  restarting or inspecting a stopped SmokePing, which used to 404, works.
  None of the three had misfired on the reference Pi — its only labeled
  containers are the `pro` project's — so this is a fix for the second host,
  which is exactly the one nobody is watching.
- **Two health checks put a credential on the command line.** The InfluxDB
  and ClickHouse checks passed their token and password as `curl -H` and
  `curl -u` arguments, and a command line is readable by every account on
  the host (`ps`, `/proc/*/cmdline`) no matter what the output does — so
  these leaked on *every* run, including the hidden one, and including the
  one at the end of `install`. Both now pass the credential to curl on
  stdin (`-K -`). Note for anyone touching this again: curl's config
  syntax **must** be quoted here. `header = A: B` unquoted parses as a
  key/value line and the header is dropped in silence, which looks exactly
  like an authentication failure; a quote or backslash inside the value
  needs escaping in turn. Both are covered by tests that fail on the
  unquoted form.
- **The PostgreSQL health check had never once succeeded.** It ran
  `docker exec grafana-influx_postgres_1`, a container name that stopped
  existing when the editions split (and Compose v2 joins names with
  dashes, not underscores). Every healthy Pro stack was told
  `PostgreSQL connection failed`, `Config-manager will use YAML fallback
  mode`, and to delete a volume. It now goes through Compose
  (`compose exec -T postgres`), which resolves the container whatever the
  project is called.
- **The InfluxDB and ClickHouse credential checks passed with the wrong
  credentials.** Both used `curl -s`, which exits 0 on an HTTP 401, so
  `InfluxDB token is valid` was printed for any token at all — the single
  thing the check exists to catch. Both now use `curl -sf`. (Verified: a
  deliberately wrong token against the reference Pi's InfluxDB returned
  401 and the check reported success.)
- **`init-passwords-docker.sh` carried the same dead names, and worse
  advice.** It looked for `grafana-influx_*` volumes, so its "existing
  volumes detected" warning had never fired on a real install; and when it
  did fire it said to delete the InfluxDB volume because "SmokePing will
  repopulate data automatically", which is false — that history does not
  come back. It now resolves the project the way Compose does, leads with
  the non-destructive fix, and states the real consequence. It also
  honors `SMOKING_PI_ENV_FILE`: it wrote `./.env` unconditionally, so on
  a packaged install (env at `/etc/smoking-pi/env`) it generated a second
  set of secrets that nothing reads.
- **The troubleshooting advice named volumes that are not yours and one
  script that does not exist.** `grafana-influx_grafana-data`,
  `_influxdb-data` and `_postgres-data` belong to a Compose project this
  repository has not used in a long time; on a current install the names
  are `<project>_*`, which the script now computes the way Compose does.
  Fixing the names alone would have turned dead advice into destructive
  advice — "delete `influxdb-data`" is a year of measurements and
  "delete `postgres-data`" is every target — so the remediation changed
  too: `sync-influx-token.sh` for a token mismatch (it existed all along;
  the `verify-influxdb.sh` the script pointed at never did),
  `grafana cli admin reset-admin-password` for a Grafana login, logs and a
  restart for PostgreSQL, with the destructive option named as destructive
  where it is genuinely the last resort. `./verify-postgres.sh` is now
  offered only for Pro, which is the only edition that ships it.
- **Pro's documentation still named a Compose project that has not existed
  in years.** Eleven references to `grafana-influx-<service>-1` containers
  and `grafana-influx_*` volumes survived in `editions/pro/README.md`,
  `DNS_MONITORING.md` and `README-Zero-Touch.md` — the scripts were cleaned
  out separately, the prose was not. Every diagnostic command in the DNS and
  IPv6 troubleshooting sections therefore ended in `No such container`,
  which is the least useful possible answer to "why is there no data". They
  now go through Compose (`docker compose logs smokeping`,
  `docker compose exec -T influxdb ...`), which resolves the service
  whatever the project is called, with a line saying to run them from
  `editions/pro` and pointing at the `smoking-pi` command as the equivalent
  that works from anywhere. The stale Compose v1 `docker-compose` calls in
  the same blocks went with them.
- **The backup recipe backed up nothing, and correcting it alone would have
  been worse.** `docker run --rm -v influxdb-data:/data ... tar czf` names an
  unprefixed volume; the real one is `<project>_influxdb-data`, and
  `docker run -v` **creates** a missing volume instead of failing — so the
  command has always produced a valid-looking tarball of an empty directory,
  a backup that only reveals itself at the restore. The section now leads
  with `smoking-pi backup`, which needs no volume names at all, keeps the
  by-hand form with the `PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}"`
  idiom the scripts use, and says to check the tarball is not empty.
  Alongside it, two recipes offered `docker compose down -v` as "fresh
  start" and "stop & delete everything" with no statement of what `-v`
  costs. They are now split: `down` (no `-v`) is the reset almost everyone
  means and keeps the data, and the destructive one is labeled as such,
  preceded by a backup, and says what goes — `influxdb-data` is every sample
  ever recorded and `postgres-data` is every target, category and source,
  and neither comes back.
- **"Switching databases" told you to delete every target you had.** Pro's
  README opened the backend switch with `docker-compose down -v`, and `-v`
  takes `postgres-data` — the target, category and source list, which is not
  backend-specific and has nothing to do with the switch. Nothing about
  changing backends requires deleting a volume (`docs/clickhouse.md` has
  said so all along: each backend keeps its own history and the provisioning
  tree rebuilds itself). It is now `docker compose down`, with the reason
  `-v` does not belong there stated next to it.
- **Pro's directory layout diagram described a repository that no longer
  exists.** It was rooted at `grafana-influx/` and showed `smokeping/`,
  `influxdb/` and `grafana/` as children of the edition; the images moved to
  `shared/modules/` when the editions split, and seven services the diagram
  never mentioned have shipped since. Redrawn as the two real trees, so it
  answers the question it is there for: what is in `editions/pro`, and where
  do the images come from. The "Comparison with Minimal" table beside it
  compared against an edition name retired at the same time — it is Basic,
  and Basic has neither the Web Admin nor the Config Manager the table
  listed as "Optional".
- **The MCP tool table was four tools short.** `mute_alerts`,
  `unmute_alerts`, `ack_incident` and `list_alert_state` shipped with the
  alerting work and were explained in `docs/alerting.md`, but the MCP
  server's own page — the one someone reads to find out what the server can
  do — still listed eleven of the fifteen. Added, with their real
  signatures, and the check above makes the drift impossible to repeat.
- **The packaged commands were written without `sudo`, and would have failed
  on the first try.** `/etc/smoking-pi` is `0750` and root-owned — which is
  the point of keeping the secrets there — so `smoking-pi status` as an
  ordinary user stops at `permission denied` on the env file rather than
  degrading. Corrected in the new guide, in `docs/packaging.md`'s lifecycle
  table and in the README's lifecycle line, with the reason stated once
  rather than a bare `sudo` everywhere.
- **The install said an optional profile without its key would crash-loop.**
  Neither does: `ai-insights` logs the missing `ANTHROPIC_API_KEY` and exits
  0, and the alerter's `NOTIFY_MODE` defaults to `off`. The truth is worse
  and worth saying — the container stays up and healthy while nothing is
  ever delivered — so the note in `smoking-pi install` and the guide's
  troubleshooting row now say that instead.
- **The site's front door still described a pre-package project.**
  `docs/index.md` said "Nothing is published to a package registry: install
  from a clone" and that whether this stack could be an `apt install` was
  "answered, and parked" — both true until v2.12.0 shipped the apt
  repository the same day. It now leads with the package and links the new
  guide; the README's Quick Start puts `apt` first and the clone second,
  where it belongs as the development path.
- **A v6-only host could never say "it's your Wi-Fi".** The uplink was read
  from `/proc/net/route`, which has no IPv6 at all. With no IPv4 default
  route there was no row to find, so every Wi-Fi sample was tagged
  `uplink=0` — and the Wi-Fi verdict requires the uplink — while the
  interface selection silently fell back to "the first wireless one". It now
  falls back to `/proc/net/ipv6_route`, where `::/0` also appears twice on
  `lo` as an unreachable route, so the flags decide rather than the
  destination — `RTF_UP` alone. A default route installed **on-link, with no
  gateway** (what `wg-quick` writes; a PPP peer route has the same shape) is
  a real default route: on a real kernel `ip -6 route add default dev wg0`
  produces flags `0x00000001`, no `RTF_GATEWAY`. The first version of this
  fix required a gateway and would have dropped exactly that route, turning
  the most useful thing the check can say — *"your uplink is a tunnel"* —
  into *"no default route on this host"*. The rows that must be excluded do
  not set `RTF_UP` at all, so requiring it loses nothing.
- **An unreachable IPv4 default route could be reported as the uplink.**
  `ip route add unreachable default` is listed in `/proc/net/route` like any
  other default route, with the interface name literally `*` and
  `RTF_REJECT` set. With a lower metric than the real route it would have
  been picked, and `*` reported as the interface being measured.
- **Two default routes were resolved by file order, not by metric.** With
  Ethernet and Wi-Fi both up only the lowest metric carries traffic. The
  kernel does emit the prefix metric-ascending — verified by adding the
  high-metric route first in a throwaway namespace and reading the file back
  — so the old code was right by accident, on undocumented behavior nothing
  pinned. The metric is now compared, with a test.
- **`WIFI_INTERFACE` pointing at a non-wireless interface failed silently.**
  A typo, or a wired interface asked to report Wi-Fi statistics, disabled the
  collector with no message — indistinguishable from "no wireless hardware".
  It now logs which it is, and the wireless interfaces it did find.

### Removed

- **The `static:` block of `sources.yaml`.** It listed websites, IPv6
  sites and DNS resolvers that nothing read: the seeded targets come from
  `targets.yaml`, and what depends on the host is now suggested by the
  card above. Existing installs keep their copy; it stays unread.

## [2.12.0] — 2026-09-22

Smoking Pi is a package: `sudo apt install smoking-pi`.

Until now the only way to run it was a clone and an edition's `setup.sh`,
with the state the stack rewrites living inside the source tree. This
release is the packaging backlog of `docs/packaging.md`, all eight items:
the state is relocatable, the containers run the code baked into their
images, every service's image is built for arm64 and amd64 on the release
tag and pulled by version, a `smoking-pi` command does the lifecycle
(`install`, `upgrade`, `backup`, `restore`, `purge`), a `.deb` is built
from the tagged tree, and a signed apt repository on GitHub Pages
publishes it. Homebrew has a formula, untested on a Mac.

What makes it a release rather than a build: before deciding anything,
the package was installed with each supported host's own `apt` — Debian
12 (today's Raspberry Pi OS) ships no Compose v2 at all, Debian 13 split
the Docker CLI out of `docker.io`, and the first `Depends` line failed on
three of the four hosts. The release workflow now installs the `.deb` on
Ubuntu 22.04 and 24.04 VMs (both architectures — starting Basic with the
release's own images, the unit enabled and stopped) and on Debian 12 and
13 containers, then installs the previous release first and upgrades over
it; the `.deb` is attached only when all of them pass. Writing that
upgrade test found two ways the package would have broken every user's
`apt upgrade` and fixed them before any `.deb` had shipped. `apt remove`
and `apt purge` never delete a measurement, and say so. A Raspberry Pi is
still the only proof of a Raspberry Pi: `docs/release-acceptance.md` is
the checklist every release goes through on the reference Pi, and the
Validation section in these notes is its record.

Also in this release: the helper scripts told the truth for the first
time in a while (SmokePing's port, health checks that never worked on a
stock Raspberry Pi OS because `nc` is not there, a tunnel target that
could never resolve), and `manage-containers.sh` applies the ClickHouse
overlay.

### Added

- **The release builds, checks and attaches the `.deb` (packaging backlog
  #5, first half).** The package existed as a trial builder nobody ran;
  now `release.yml` builds it from the tagged tree, installs it on the
  runner and checks what a package can prove without Docker — the version
  it reports is the tag's, `smoking-pi paths` shows the packaged layout
  with the images pinned to that version, the systemd unit verifies, the
  doctor's static checks pass from `/opt`, `install` refuses over an
  existing env file, and removal keeps `/etc/smoking-pi` and
  `/var/lib/smoking-pi` — then keeps it as a workflow artifact and
  attaches it to the GitHub release the maintainer created for the tag.
  The workflow never creates a release. What remains of #5 is the signed
  apt repository on Pages.
- **A Homebrew formula (packaging backlog #8), untested on a Mac.**
  `Formula/smoking-pi.rb`: `brew tap estcarisimo/smoking-pi
  https://github.com/estcarisimo/smoking-pi && brew install smoking-pi`
  installs the release tarball under the Cellar with the command on
  PATH, Homebrew's bash/coreutils/gnu-sed ahead of macOS's (bash 3.2,
  BSD `sed -i` and `readlink`), a venv with PyYAML for the doctor, and
  `brew services` for `smoking-pi up` at login. The caveats say what the
  doc says: on macOS Pro's host-network measurements see Docker's Linux
  VM. `packaging/homebrew/bump.sh vX.Y.Z` is the maintainer's post-release
  step; CI proves the formula parses and its checksum is the tarball's,
  which is all that can be proven without a Mac.
- **Script hygiene (packaging backlog #7): no script guesses a container
  name, and the helper scripts tell the truth.** `sync-influx-token.sh`
  and `verify-postgres.sh` ask Compose for the container
  (`COMPOSE_PROJECT_NAME` decides the names) and honor the relocated env
  file; `create-tunnel.sh` reads the edition and project from Compose's
  labels and tunnels to **service** names on the project's network — its
  Pro SmokePing target (`pro-smokeping-1` on `pro_default`) could never
  have resolved, because that service runs on the host network; it now
  goes through the host gateway. `show-passwords.sh` printed SmokePing on
  8080/8081/8081 for Basic/Standard/Pro when the compose files map
  80/8081/80, and its health checks used `nc`, which a stock Raspberry Pi
  OS does not have, so every port read "not accessible" for as long as
  the script existed — bash's `/dev/tcp` now, only this edition's ports,
  service state from `compose ps`. Every `docker-compose` (v1) call and
  hint became `docker compose` or the `smoking-pi` command;
  `manage-containers.sh` is v2-only (v1 cannot parse these files), adds
  the ClickHouse overlay when the profile is recorded (it never did: a
  restart quietly rendered the InfluxDB stack), and `--edition` works
  from the repository root as the README always claimed.
  `shared/docs/maintenance.md` rewritten around the command and Compose
  labels; the `your-repo` placeholder URLs are the real ones.
- **`sudo apt install smoking-pi` (packaging backlog #5, second half).**
  GitHub Pages now serves a signed apt repository under `/apt` next to
  the docs site: `docs.yml` runs when the Release workflow has finished
  for a `vX.Y.Z` tag (not on the tag push, which raced the `attach` job),
  downloads every release's `.deb`, builds a flat repository
  (`packaging/apt/build-repo.sh`) signed with the `APT_SIGNING_KEY`
  secret, and deploys it with the site as one artifact. Without the
  secret the site is published with no `/apt` at all — an unsigned
  repository would only teach people `[trusted=yes]`. CI builds and
  signs a repository with a throwaway key on every PR and installs from
  it with `apt`. The README has the three lines a user types; Raspberry
  Pi OS and Debian 12 users install Docker from Docker's repository
  first, because Debian 12 ships no Compose v2. Publishing waits for the
  maintainer's key (`docs/packaging.md`, *The apt repository*).
- **The release proves the upgrade, not just the install — and the
  upgrade test found two ways the package would have broken it.** Once a
  release carries a `.deb`, each `host` job first installs *that* one and
  starts Basic on it, then installs the new package over it and runs
  `smoking-pi upgrade`: the secrets must survive byte for byte and every
  container must run the new images. Proven locally first, on Debian 12
  with Docker's engine under a nested daemon, which is where the two
  findings came from. (1) The package version lived in
  `/etc/default/smoking-pi`, a conffile that `install` had just started
  editing; dpkg keeps an edited conffile on upgrade, so every upgrade
  would have kept pulling the first release's images. The packaged
  command now takes its version from the tree it installed. (2) Worse: an
  edited conffile plus a shipped conffile that changes by one character
  stops the upgrade at dpkg's conffile prompt, which under a
  non-interactive `apt` is a failed upgrade for every user. `install` now
  records the edition in `/etc/smoking-pi/edition` (the unit no longer
  hard-codes `pro`), nothing edits the conffile, and the check fails if
  its md5 ever differs from the one dpkg recorded.
- **Uninstall policy (packaging backlog #6).** `apt remove` keeps
  everything; `apt purge` removes the regenerable directories and the
  conffile but never the Docker volumes nor `/etc/smoking-pi/env` — the
  file holds the credentials the volumes are locked with, so deleting it
  alone would turn a year of kept measurements into unreadable ones —
  and `postrm` says so. `smoking-pi purge --config` is the explicit way.
  Documented in `docs/upgrades.md`, *Uninstalling*.
- **Release acceptance on a Raspberry Pi** (`docs/release-acceptance.md`):
  the checklist every release goes through on the reference Pi before the
  tag — backup and rollback first, upgrade, reboot recovery, doctor, data
  for every probe, detections, 24 hours of stability — and the
  *Validation* section its GitHub release notes end with, untested
  combinations named. What the release workflow proves on Ubuntu VMs and
  Debian containers is written next to what only the Pi shows.
- **The release installs the `.deb` on every supported host, with that
  host's own `apt`.** Measured first, in containers of each OS: Debian 12
  (today's Raspberry Pi OS) ships no Compose v2 at all, Debian 13 split
  the Docker CLI out of `docker.io` and calls its Compose v2
  `docker-compose`, and apt takes the first installable alternative — so
  the original `Depends` line would have failed on three of the four
  supported hosts, or paired Debian's 20.10 daemon with Docker's Compose 5.
  The corrected line (`docker-ce | docker.io`, the CLI named explicitly,
  `docker-compose (>= 2)`) resolves on all of them. `release.yml` now
  checks it on every tag (first exercised by the throwaway runs
  `test-d0b5662` and `test-host-matrix`, before any release): Ubuntu 22.04
  and 24.04 VMs, amd64 and arm64,
  install the package, start the Basic edition with the release's images,
  enable and stop the unit; Debian 12 and 13 containers on both
  architectures check the package without a daemon, every edition
  rendered with that Debian's Compose. The `.deb` is attached to the
  release only after all of them pass. One script does the whole check
  (`packaging/tests/check-package.sh`), locally too. Also found by the
  new check: `install` never recorded the chosen edition, so a packaged
  Basic install would have been started as Pro by the systemd unit — it
  is written to `/etc/default/smoking-pi` now. `docs/packaging.md`,
  *Supported hosts*, has the measured matrix and what a container does
  not prove about a Raspberry Pi.

- **The `smoking-pi` command does the lifecycle (packaging backlog #4).**
  The prototype could start, stop and install; a backup was a `pg_dumpall`
  and a `docker run … tar` typed from `docs/upgrades.md`, a restore was
  nowhere written down, and "start over" meant finding the right `docker
  volume rm`. Now: `upgrade` (the release's images pulled, or rebuilt with
  fresh bases from a clone; `up -d`; the doctor), `backup` (dump, then the
  volumes the active services mount as tarballs with the stack stopped,
  env file, config), `restore` (each tarball's Compose volume key resolved
  to the volume this stack mounts — fixed `name:` included — after you
  type the project name; env and config where missing), `purge` (after
  typing the project name; `--config` for a clean `install`), and `install
  --profiles mcp,alerts,ai` (or a whiptail checklist).
  `packaging/tests/cli.bats`, 22 tests against a stubbed docker, runs in
  CI. Verified on the reference
  Pi: an offline backup (156 s, ~1 min stopped), a restore into a scratch
  project, a purge of it, an upgrade against the published throwaway
  images (28 s, doctor 13 ok).

- **Published multi-arch images and a release-only build (packaging
  backlog #3).** Nothing built the nine images anywhere but on the target
  host: CI built four of them, amd64 only, on every PR, and published
  none, so a first start on a Pi was a 20-minute build and an `apt install`
  would have been too. `.github/workflows/release.yml` now builds all nine
  for `linux/arm64` and `linux/amd64` on every `vX.Y.Z` tag — natively,
  each architecture on its own GitHub-hosted runner, pushed by digest and
  merged into one manifest per service — to
  `ghcr.io/estcarisimo/smoking-pi/<service>:<version>` (and `:latest`),
  refusing a tag that disagrees with `CITATION.cff` (a `test-*` tag runs the
  same pipeline for a throwaway image tag). Every built service in
  the compose files names that image next to its `build:` with
  `pull_policy: missing`, which makes Compose pull first and build only if
  the pull fails: `SMOKING_PI_VERSION` unset means `:dev`, a tag never
  published, so a clone still builds what it checked out; the package sets
  its version in `/etc/default/smoking-pi` and pulls. `smoking-pi paths`
  prints which. `packaging/check-images.py` (CI) fails when the compose
  files, the Dockerfiles and the workflow matrix stop agreeing. In line
  with the release-only CI decision, PR CI no longer builds images and the
  docs site deploys from the release tag rather than every push to `main`;
  a Dockerfile change is proven by the deploy on the reference Pi before
  merge. On a development host the next `up -d` recreates every container
  under the new image names (`docs/upgrades.md`).

### Fixed

- **Installed by the package, `smoking-pi` looked for the tree in `/`.**
  The command took its home as one directory above itself — right from a
  checkout (`packaging/smoking-pi`), and `/` from `/usr/bin/smoking-pi`;
  the `/opt/smoking-pi` fallback the usage text promised was never
  written, so a packaged `smoking-pi version` said `unknown` and `doctor`
  found nothing to check. The first run of the release's package job
  caught it. Now: the checkout when there is an `editions/` beside it,
  `/opt/smoking-pi` otherwise (the package's defaults file documents the
  knob, commented out — set there it would also capture a checkout's own
  script on the same host); the job checks `paths` reports
  `/opt/smoking-pi`.
- **`smoking-pi help` printed `dev: command not found`** — the usage text
  is a heredoc, and the previous change put a backticked word in it. Quoted.

- **`shared/modules/influxdb/Dockerfile` was never in git.** A `.gitignore`
  rule for InfluxDB *data* directories (`influxdb/`) matched the module
  directory too, so every clone but the reference Pi lacked the file the
  Pro compose file builds from — `docker compose up` failed on
  `influxdb` — no CI job built that image, and Dependabot's docker
  ecosystem (with its careful influxdb major-version guard) watched a file
  it could not see. The first run of the release workflow found it. The
  directory is un-ignored and the Dockerfile tracked; `check-images.py`
  now fails CI when an edition builds from a directory without a
  Dockerfile in the checkout.

### Changed

- **Packaged mode runs no source bind-mounts (packaging backlog #2).**
  From a clone, seven directories of `shared/modules` are bind-mounted into
  the Pro containers as development overlays — the exporters, three Grafana
  provisioning trees, the web-admin `app` package, the PostgreSQL and
  ClickHouse init SQL. For a package that would mean `apt upgrade` changing
  code under running processes. Every one of them is already baked into its
  image except the exporters, which now are (`COPY` into the smokeping
  image from a `shared/` build context, like the four Python images).
  `docker-compose.packaged.yml` (Pro; a one-service one for Standard)
  replaces those services' volume lists without the code mounts, and
  `SMOKING_PI_PACKAGED=1` — set by the package in `/etc/default/smoking-pi`
  — makes the `smoking-pi` command, `setup.sh` and `manage-containers.sh`
  add it, last, so it also wins over the ClickHouse overlay. Because a
  Compose override can only replace a volume list, not remove one entry,
  `packaging/check-packaged-override.py` renders both stacks with every
  profile on and fails if the override drops or adds anything but the code
  mounts; CI runs it for both editions. The doctor's `deployed-code-current`
  now hashes `/exporters` in the smokeping container too, so a stale
  smokeping image in packaged mode is reported like a stale alerter image.
  From a clone nothing changes. `docs/packaging.md`, *Packaged mode*.

- **Relocatable state (packaging backlog #1).** The YAML config-manager
  edits, the SmokePing config it generates and the `.env` with the secrets
  all lived inside the source tree — the first two *tracked in git*, so the
  reference Pi's `git status` had shown the running stack's rewrites as
  modified files since the day it went live, and a package upgrade would
  have replaced them. Three variables now decide where that state lives:
  `SMOKING_PI_CONFIG_DIR`, `SMOKING_PI_OUTPUT_DIR` (read by the compose
  files' bind mounts) and `SMOKING_PI_ENV_FILE` (passed as `--env-file` by
  `setup.sh`, `generate-passwords.sh`, `show-passwords.sh`,
  `manage-containers.sh` and the `smoking-pi` command). Unset, everything
  stays where it was — beside the edition's compose file — but untracked:
  `editions/pro/config-manager/{config,output}` keep only a `.gitkeep`. A
  fresh config directory is seeded by config-manager's bootstrap from
  `templates/`, as it always was for a missing file; the image no longer
  copies a `config/` directory, and the stale
  `shared/modules/config-manager/{config,output}` copies (five probes,
  untouched since Sprint 3) are deleted — `templates/` is the one seed set
  and a test holds it to the current probe list. The `.deb` sets the
  packaged layout (`/etc/smoking-pi/{env,config}`,
  `/var/lib/smoking-pi/output`) in `/etc/default/smoking-pi` and creates the
  directories; CI renders both compose files with that layout and fails if
  a mount is hardcoded again. `docs/packaging.md`, *Relocatable state*.
  **Upgrading a running host:** `git pull` past this change deletes the
  now-untracked files from the working tree; recreate config-manager and
  it regenerates everything from the database — `docs/upgrades.md`,
  *Pulling past v2.11*.

## [2.11.0] — 2026-09-20

The detectors say what happened, once; the documentation has a site; and
ClickHouse mode writes rows.

After weeks of use the reference Pi's alerts were mostly false in a
specific way — one event, one cause, reported once per target. The night
its Wi-Fi radio hung (still associated, receiving nothing for 3 h 20 min)
produced about a hundred and ten messages; a three-minute blink produced
thirty-six; and the assistant listed every single lost ping as a loss
event, and called the gateway's ICMP rate-limit floor "strong microcuts"
for weeks. This release treats downtime and microcuts as two separate
investigations, each with weeks of the Pi's own data as evidence, a
definition, and tests that replay the real nights; the microcut definition
was also run against the Pi's InfluxDB before merging, in the MCP tool and
again in the in-UI assistant. A loss that hits most targets in the same
probe cycle is one incident, named as a hung radio when the Wi-Fi counters
show one; a
microcut is a run of windows above the threshold, confirmed or possible,
with a duration, beside the floor it stands on. The same definition is
read by the alerter, the MCP server, the in-UI assistant, the digest and
the AI report. The radio itself is documented, with the power-save
hypothesis and the reasons the one-line fix is left to the operator.

The guides are now a site — [estcarisimo.github.io/smoking-pi](https://estcarisimo.github.io/smoking-pi/)
— built strictly in CI and deployed from `main`. And ClickHouse mode,
revived in 2.5 and believed to work since, had never written a row; it
now does, verified end to end against a ClickHouse 24.1 from the Pi's
RRDs — on a throwaway stack, not on the reference Pi, which stays on
InfluxDB.

### Added

- **Microcuts are cuts, not windows.** The other half of the detection
  reliability work. On a gateway that rate-limits ICMP, 98% of the 10 s
  CPE windows show some loss and the daily p90 sits at 14–22% with nothing
  wrong — and `get_microcut_stats` counted "windows with any loss" and
  always returned a top-5, so every answer for weeks said "strong
  microcuts, worst 82%" about the floor's tail; the alerter's
  `microcut_burst` counted windows above 50% and called two isolated ones
  23 minutes apart a burst. Fourteen days of data held 17 isolated windows
  and one real cut: six consecutive windows at 100%. One definition now
  lives in `common/microcuts.py` and every consumer reads it: a cut is a
  run of consecutive windows above `MICROCUT_LOSS_PCT`, confirmed with two
  or more windows or a 100% window, possible when it is one isolated window
  below that; the floor is reported as p50/p90 beside it. The tool returns
  `cuts` with durations and zoomed links, the per-target floor, only cut
  windows in `worst_windows`, and a note stating the floor when there were
  no cuts; the alert says *"1 cut of 2 min 40 s (6 windows, all at 100%)"*
  and fires on a confirmed cut or `MICROCUT_BURST_N` (now 3) possible ones;
  the digest and the AI report carry the same fields. Verified against the
  Pi's data before merging: over seven days, one 3 h 25 min total cut (the
  hung radio), the 2 min 40 s cut, one possible cut.

- **Downtime detection reports one event once.** After weeks of use the
  reference Pi's downtime alerts were mostly false in a specific way: one
  event with one cause reported once per target. The night its Wi-Fi radio
  hung (associated at −49 dBm, zero packets received for 3 h 20 min) the
  alerter sent 18 `target_down` criticals and 18 `high_loss` warnings, each
  re-sent every cooldown — about 110 messages — with a verdict of "local
  link cutting out"; a three-minute blink on 2026-09-19 produced 18
  warnings and 18 recoveries; and the assistant's `get_loss_events` listed
  every single lost ping (60–300 a day across every target) as an event.
  Now: a loss that hits most targets in the same probe cycle is ONE
  incident — `uplink_down` (critical) while every target sits at 100%,
  named as a hung radio when the Wi-Fi counters show one; `outage`
  (warning, no recovery) for a brief cut that already ended — and the
  per-target incidents it would fan out into are not sent. `high_loss`
  needs the loss to persist for two cycles. `get_loss_events` defaults to
  15% (two or more lost pings), counts the single-ping background instead
  of listing it, folds consecutive points into `episodes` with durations,
  and reports `widespread` runs with their cause. The evidence and the numbers
  behind every threshold are in `docs/detection-reliability.md`; the
  microcut half is the entry above.

- **A documentation site.** Thirteen guides lived under `docs/` and three
  more under `shared/docs/`, reachable only by knowing the path, with
  cross-links nobody checked. `mkdocs.yml` now builds them into a site at
  [estcarisimo.github.io/smoking-pi](https://estcarisimo.github.io/smoking-pi/)
  (Material theme, search, a landing page, the changelog included by
  snippet). CI builds it with `--strict`, so a broken link or a dangling
  anchor fails the PR — the first build found one. `docs.yml` deploys the
  same build to GitHub Pages on every push to `main`. The two tunnel guides
  moved from `shared/docs/` into `docs/` so they are on the site; the
  maintenance guide stays out until it is rewritten for the edition layout
  (packaging backlog #7), because a published page that names containers
  that do not exist is worse than none.

### Changed

- **When the radio hangs.** The two September hangs of the reference Pi's
  Wi-Fi radio (associated, transmitting, receiving nothing; 21 h and
  3 h 20 min) have a likely cause — `brcmfmac` with power save on — and a
  one-line fix that is deliberately not applied. `docs/wifi.md` now records
  the signature, what is known against what is only suspected, why the
  change is the operator's (it is a host setting reached over the link it
  drops, and it moves the latency floor the Pi measures), how to try it so
  it reverts itself, and the decision to leave it on.

### Fixed

- **The in-UI assistant reads the same microcut definition.** The
  web-admin chat kept its own copy of `get_microcut_stats` that could not
  import `common`, so after the MCP tool, the alerter and the reports were
  fixed it still counted every window with any loss and returned a top-5 —
  the same "strong microcuts, worst 82%" about the gateway's floor, now
  from the other assistant. The web-admin image builds from `shared/` like
  its three siblings and copies `common/` in; the tool folds cuts, reports
  the floor as p50/p90 and states it in a note when there were none, and
  `get_loss_events` defaults to the shared 15% instead of 5%, so a single
  lost ping out of ten is background there too. Verified against the Pi's
  InfluxDB beside the MCP tool: identical cuts and floor over 24 h and 7 d.

- **ClickHouse mode never wrote a row.** Since the Sprint 13 revival the
  exporter connected without a database so it could create the schema
  first — and then never selected it, so every insert went to
  `default.latency`, which does not exist; a second defect sent an
  `rrd_file` column the table has no room for, which ClickHouse answers by
  rejecting the whole batch. The schema check in `docs/clickhouse.md`
  passed throughout, so nothing looked wrong until the HTTP dashboard's
  ClickHouse variant was finally run against a live ClickHouse. The
  exporter now selects the database once it exists and writes a declared
  column list that a test holds against its own `CREATE TABLE`. Verified
  against ClickHouse 24.1 from the reference Pi's RRDs: all four
  measurement types land, and the *HTTP by Version* ClickHouse dashboard's
  variable and panels return rows through Grafana's query API.

## [2.10.0] — 2026-09-19

The web is measured the way it is served, and two roadmap questions get
their answers.

Ping told you the path was fine; it never told you whether a page would
load, or over which protocol. This release fetches `https://<host>/` over
HTTP/1.1, HTTP/2 and HTTP/3 as three separate probes, with the negotiated
version *enforced* — a server that quietly downgrades produces a loss, not
a mislabeled sample — and times the bare TCP handshake beside them so the
chart shows what each protocol adds on top of the connection. HTTP/3 needed
a curl no distribution ships, so the image carries one, pinned by hash. The
probes reach the exporters, a side-by-side dashboard (written for both
backends, so far run only against InfluxDB), the add-target form and the
assistant's deep links.

Two questions the roadmap kept open are now closed with a document each:
the last-mile signal the customer gateway would have to report, and does
not; and what a package of this stack would have to be — one that manages
the Compose deployment — with the trial `.deb` and the backlog that trial
produced.

### Added

- **HTTP/1.1, HTTP/2, HTTP/3 and TCP probes.** Four new probes, all
  running inside the SmokePing container: `CurlHTTP1`, `CurlHTTP2` and
  `CurlHTTP3` fetch `https://<host>/` over one HTTP version each, and
  `TCPPing` times the SYN/SYN-ACK handshake to port 443. The version is
  enforced, not requested: the probe's own `-w` format prints the
  negotiated version and `expect` turns a downgrade into a loss, so an
  HTTP/2 series never quietly contains HTTP/1.1 samples. HTTP/3 needs a
  curl with a QUIC backend, which neither Alpine nor `curlimages/curl`
  ship, so the image adds a static `stunnel/static-curl` build pinned by
  version and sha256 per architecture — and the 1.1 and 2 probes use the
  same binary so the comparison is protocol, not TLS stack. Sub-probes
  (`+ Curl` / `++ CurlHTTP2`) are now expressible in `probes.yaml` via a
  `module` key; the `probes` table gains `module` and `options` columns,
  added to existing tables on startup. An already-migrated deployment
  picks up the new probes, the `http`/`tcp` categories and their example
  targets on its next start. Exporters classify `HTTP/` and `TCP/` RRDs as
  `http_latency` / `tcp_latency` with the version in `probe_type`; a
  Grafana dashboard (InfluxDB and ClickHouse) overlays the three versions
  per site with the TCP floor underneath; web-admin's add form offers
  *HTTPS fetch* (with a version) and *TCP connect*; the assistant's deep
  links for the new measurements resolve to that dashboard. The ClickHouse
  dashboard variant follows the plugin's documented format and has not yet
  run against a live ClickHouse. `docs/http-probes.md`.
- **The CPE last-mile question, answered.** `docs/cpe-last-mile.md`
  records the read-only exploration of the reference gateway (a Nest Wifi
  Pro in front of a transparent Fiber Jack), documents the one endpoint it
  exposes (`/api/v1/status`: WAN state, lease, first ISP hop, uptime — no
  physical layer), and parks the feature: PHY numbers reach the customer
  only when the ISP's own device is the router.
- **The packaging question, answered.** The roadmap asked whether a stack
  of ten Compose services fits apt and Homebrew, and what would have to
  change. `docs/packaging.md` inventories the system as a package sees it
  (nine images built on the target, a checkout that is a runtime
  dependency, git-tracked directories the running stack mutates, no unit,
  no registry), weighs four shapes, and picks one: a package that manages
  the Compose deployment, never native packages of four upstream projects.
  An end-to-end trial backs it — `packaging/deb/build.sh` builds a 1.4 MB
  `.deb` from `git archive HEAD` in two seconds, and `docker compose
  config` resolves the relative `../../shared` paths from `/opt/smoking-pi`
  unchanged — and the trial's failures are the backlog: relocatable state
  first (`.env` and the mutated config directories out of the tree, which
  also ends the runtime churn in `git status`), no source bind-mounts in
  packaged mode, multi-arch images on GHCR, the `smoking-pi` command as the
  installer the roadmap wants, a signed apt repository, an uninstall that
  never deletes a year of measurements. About ten days in total; the first
  two pay for themselves without any packaging. The prototype CLI and unit
  ship under `packaging/` and work from a clone today; the builder took the
  working tree on its first run and shipped the reference Pi's target list,
  which is why it now takes only what is committed.

### Removed

- The `EchoPingDNS` and `EchoPingHttp` entries in the probes template:
  `echoping` is unmaintained and not packaged, so they never ran.

## [2.9.0] — 2026-09-19

The hop every measurement crosses is measured too, and the project says
what it is in the languages it uses.

The reference Pi, it turns out, has never had a cable in `eth0`: every
latency and loss figure in a year of history reached the internet over
`wlan0`, and nothing recorded the state of that link. So a microcut that
coincided with a signal dip could only ever be blamed on "your line". This
release records the Wi-Fi uplink every ten seconds, charts it under the
microcuts it explains, gives the assistant a tool for it, and lets the
verdict say *the router or the air, not the ISP* — while stating plainly
which numbers a Raspberry Pi's own driver does not report, rather than
drawing an empty panel and calling it zero.

Around it, the housekeeping a public project owes its readers: a guide for
running OpenClaw on another machine without opening a port, a citation file
CI keeps honest, a security policy that says what happens after you file,
one spelling (American) written into the rules and applied to the tree, and
a review step that is an independent session rather than a bot whose
review request often went unanswered. Charts learned to scale to the typical shape instead of the worst
spike, and an MCP error can no longer carry an InfluxDB token in its text.

### Added

- **The Wi-Fi hop is explained: the verdict, the digest and an MCP tool now
  say it.** With the hop recorded and charted, the last step is letting the
  words follow. `get_wifi_stats(hours, interface)` answers "how is the
  Wi-Fi?" from the record — current association and signal, the window's
  signal range and weak share, disconnects, roams, failures, peak throughput,
  and the five weakest samples each linked to its moment — and
  `system_status` carries a `wifi` block so an assistant knows every other
  number crossed that link. The verdict gains a `wifi` scope, 📶, taken when
  the first hop is cutting *and* the uplink spent at least a minute's worth
  of samples below −75 dBm (or dropped) in the hour: *"Your Wi-Fi — the
  first hop is cutting out and the signal fell to −78 dBm; the router or the
  air, not the ISP."* When the Wi-Fi was fine every existing line is
  byte-identical, and the context line shows `wi-fi min −54 dBm` so a reader
  sees it was checked; a spare radio that is not the default route is
  reported and never acted on. The daily digest and the AI report get a
  *Local link* line. Two settings, `WIFI_WEAK_DBM` and `WIFI_WEAK_SAMPLES`,
  shared by the alerter and the MCP server. The OpenClaw skill learns the
  tool, the dBm bands, and a Wi-Fi bullet in its report template.

- **The doctor reads negative defaults.** `DEFAULT_WIFI_WEAK_DBM = -75.0`
  is a `UnaryOp` in the AST, not a `Constant`, so the compose-vs-module
  default check silently skipped it and would have reported OK for a value
  it never compared. It now resolves negated numbers; the test reintroduces
  the gap with a compose file pinning −70 against a module −75 and asserts
  the FAIL.

- **The Wi-Fi hop is charted, next to the microcuts it explains.** A *Wi-Fi
  Link* dashboard (six rows: link now, signal, PHY bitrate, throughput,
  quality, roaming) and — the panel that matters — the Pi's signal and TX
  failures drawn under the CPE microcut panels on the same time axis, so a
  cut that lines up with a dip reads as the router or the air rather than
  the ISP. Panels for what a driver may not report (noise, MCS, retries)
  say so in their description instead of looking broken. Current-value
  tiles use `group() |> last()`, because after a roam a plain `last()`
  would happily show the stale access point. `links.wifi_links()` builds
  deep links into the dashboard (the graph pair only — an interface has no
  per-ping detail, peers or edit page), and `system_status`, the digest and
  alerts gain a `grafana_wifi_link` entry point. Every panel query was run
  through Grafana's datasource API on the reference Pi: 0 errors, and the
  empty ones are exactly the fields brcmfmac does not write.

- **The Wi-Fi hop is recorded.** The reference Pi's uplink is `wlan0` —
  `eth0` has never had a carrier — so every latency and loss figure in a
  year of history crossed a Wi-Fi link that nothing measured. A microcut
  coinciding with a −80 dBm dip is a router problem; the verdict could only
  ever call it "your line". A new collector in the smokeping container,
  `wifi_link.py`, samples the wireless uplink every 10 s through nl80211
  (`iw`, now in the image; no capability needed under host networking) and
  writes a `wifi_link` measurement: signal, negotiated PHY bitrate,
  channel/band/width, failures, the interface byte counters that survive
  re-association, `carrier_down_count` for disconnects, and — on drivers
  that report a survey — noise, SNR and channel-busy share. Tags are
  `interface` and, while associated, `ssid`/`bssid`, so a roam is a visible
  series change. On a wired host it logs one line and idles. Field types
  are pinned (counters `int`, levels `float`; a Python bool would have been
  written as a boolean field and poisoned the measurement) and the tests
  assert the line protocol. What the Pi's own `brcmfmac` does *not* report
  — noise, retries, beacon loss, MCS — is documented as absent rather than
  invented. The dashboard, the MCP tool and the verdict follow in their own
  changes; `docs/wifi.md` says what is recorded and how to query it.

- **A guide for OpenClaw on another machine.** The integration docs assumed
  OpenClaw and Smoking Pi share a host, and the security model depends on
  it: both the MCP server and the gateway listen on loopback and nothing
  else can reach them. Someone with OpenClaw on a desktop or a VPS had no
  supported way to connect the two, and the obvious one — forward the port,
  rely on the bearer token — puts a server that can add targets and restart
  SmokePing behind a single secret on the public internet.
  `docs/remote-openclaw.md` gives three ways that keep both loopback binds,
  ranked by exposure: an SSH tunnel carrying both directions in one session
  (after which the co-located guide applies verbatim; a systemd unit in
  `examples/openclaw/` keeps it up), a Tailscale/WireGuard mesh (Serve or a
  direct tailnet bind), and Cloudflare Tunnel with an Access service token
  (with the plain statement that Cloudflare then sees the traffic). It says
  what each one exposes to floods and to third parties, what not to do, and
  records the resolv.conf trap that once cost the reference Pi nine targets
  for ten days when Tailscale logged out. One knob to make the tailnet
  variant possible: `MCP_HOST` is now overridable from `.env` (default
  unchanged, `127.0.0.1`; the template says never `0.0.0.0`). The reference
  deployment stays co-located.

- **`CITATION.cff`, checked by CI.** GitHub's "Cite this repository" button
  now works, giving author, title, version, date and repository URL in a
  form citation managers import. Left out on purpose: a DOI and an ORCID,
  because neither exists for this project yet and a citation file should
  not carry a guess. The file is the kind that rots — the version in it is
  wrong the day after the next release — so a CI job validates it against
  the CFF schema and fails if `version`/`date-released` do not match the
  newest released section of this changelog. The release procedure in
  CONTRIBUTING and AGENTS names the step.

- **SECURITY.md says what to send and what happens next.** The policy had a
  private channel, a scope and a supported-versions line, but a reporter
  had to guess what a useful report contains (edition, version, backend,
  entry point, impact) and had no idea what happens after filing — whether
  a fix goes on a public branch, when the advisory is published, whether
  they get credit, when they may disclose. It now spells out the report
  contents, a four-step coordinated-disclosure process on GitHub's private
  advisory fork, the 14/30-day escalation, and who reads the reports
  (the CODEOWNERS owner).

- **The logo is back.** A steaming raspberry pie, drawn in August 2025 and
  committed only to a branch that never merged; the README on `main` had
  pointed at `img/logo.jpg` for a year without the file existing. Recovered
  while pruning that branch, downscaled from 2048² (700 KB) to 512² (about
  a tenth of that), and placed above the title at 220 px.

### Changed

- **American English is the project's language, and now it says so.** The
  code base had grown up bilingual: `initialize` next to `initialise`,
  `color` in matplotlib calls and `colour` in the comment above them, a
  `centre` variable in the deep-link code, `behaviour` in the agent
  instructions and the OpenClaw skill. Harmless in any single file, but an
  agent that reads AGENTS.md and copies its spelling into a new function
  name produces the next inconsistency, and a reviewer has no rule to point
  at. AGENTS.md, CONTRIBUTING.md and the PR template now state the rule
  (US spelling for names, docs, comments and agent instructions), and the
  sixty-odd existing deviations across docs, comments, the OpenClaw skill,
  two shell messages and one local variable are corrected. No public name
  changes: the one shipped British spelling (`cancelled` in the web-admin AI
  result) stays, and the policy says why.

- **The review step is an independent session, not Copilot.** AGENTS.md,
  CONTRIBUTING.md and the PR template said every PR gets a Copilot review
  and must not merge over it unread. In practice the review-request API
  silently did nothing on a third of recent PRs (#57, #62, #63) and the
  reviews cost more than they found. The rule is now: a reviewer who did
  not write the change — a fresh model session (the maintainer uses a cold
  Sonnet session per PR) or a human — reads the whole diff against
  AGENTS.md's contracts, and its findings and their resolution are recorded
  in a PR comment. The first two such reviews caught a broken link and two
  lock files that had leaked into a docs PR, which is the argument.

- **Charts: the scale follows the typical shape, not the worst spike.**
  A 24 h gateway chart with nine windows peaking at 180 ms on a 9 ms link
  let those nine set the axis, pressing the median and the inner band —
  the part a reader actually judges by — into the bottom fifth of the
  panel. The latency axis now follows the 90th percentile of the outer
  band (with a third of headroom over the inner band, and never cutting
  the median or a peer line); windows above it are clipped and *counted*,
  and the count and true maximum are written on the chart ("▲ 9 windows
  peaked above 81 ms (max 187 ms)"). A clipped chart that says it is
  clipped hides nothing.

  The loss panel draws the alert threshold that applies — `MICROCUT_LOSS_PCT`
  for the gateway, `HIGH_LOSS_PCT` otherwise — as a dashed line with its
  value, so the gateway's permanent 10 % ICMP floor reads as "well under
  the line" rather than as loss. Dashed is reserved for this: the grid is
  solid hairlines. The median gets a halo in the surface color so it stays
  legible where it runs through its own band. All three apply to alert
  charts too, since the renderer is shared; the `mcp-server` service now
  receives both threshold variables so `get_chart` draws the same line the
  alerter would.

### Security

- **MCP tool errors no longer carry exception text.** The roadmap's "make
  sure the MCP server cannot leak credentials" item, reviewed end to end:
  the bearer check is constant-time, tool arguments named like secrets are
  redacted in the log, `/status` carries no secrets, and no token or
  password can reach a tool result. Two internals could: an InfluxDB
  failure returned the client's whole exception — response headers, body
  and the Flux query — and a config-manager failure embedded the API's
  base URL (which in a proxied deployment can carry userinfo) and up to 200
  bytes of whatever body came back. A tool result goes to the model and
  from there into a chat, so it now gets the same discipline as an HTTP
  error body: a message chosen in code plus an `error_id`, detail in the
  mcp-server log under that id. `ConfigAPIError` names the operation, the
  HTTP status and config-manager's own (static) error, nothing else. Tests
  raise an `ApiException`-shaped error carrying a fake token inside every
  measurement tool and assert it never comes back.

## [2.8.1] — 2026-09-14

The repository grows the files a contributor looks for first, and a fresh
install stops shipping two APIs unauthenticated.

Two days after 2.8.0, on reading the code reviews that 2.8.0's PRs were
merged over: `setup.sh` had never generated the config-manager or MCP bearer
tokens, so both services — one of which exposes every mutation the other
has — came up unauthenticated on a new box while the README said otherwise.
They are generated now. The rest is the housekeeping a project is supposed
to have (`CONTRIBUTING.md`, `AGENTS.md`, a code of conduct, owners,
templates), a rule that every PR's Copilot review gets read, and the
smaller findings from the reviews that went unread.

### Security

- **`setup.sh` now generates `CONFIG_API_TOKEN` (Standard, Pro) and
  `MCP_API_TOKEN` (Pro).** Both shipped empty, which both services treat as
  *unauthenticated*, while the README described the APIs as bearer-protected
  — a Copilot review comment on the README, and a fair one. The MCP server
  exposes every mutation the config API has. `show-passwords.sh` prints both
  tokens with the header each expects, and says so in red when one is
  unset. Existing `.env` files are not touched; set the two keys by hand to
  get the same protection (`openssl rand -hex 32`).

- **`safe_path.confine()` also resolves symlinks**, and rejects `.`, `..`
  and the empty name explicitly (on a root base, `..` normalizes to the
  base itself, which the prefix check cannot see). Both from the Copilot
  review of #52; the lexical check remains what CodeQL scores.

### Fixed

- **`manage-containers.sh` works with Compose v2.** It required the legacy
  `docker-compose` binary while the README's requirements only promised the
  `docker compose` plugin. It now uses whichever is present, preferring v2.

- **Tests that were not testing what they said** (Copilot review of #52,
  #53): the config-manager unknown-type test used a path Werkzeug
  normalizes away before routing, so its assertions never ran; the status
  test patched a method that does not exist; the CrUX confinement test
  failed the regex before reaching the code under test; the backslash
  redirect guard had no test at all. Each now exercises the real branch.

- **README claims that were not true**, from the same review: setup does
  not wait for health in every edition; profiles given on the command line
  are not persisted (edit `COMPOSE_PROFILES` in `.env`); `.env` is not
  exported into your shell; deep links appear only once `PUBLIC_BASE_HOST`
  is set; the config file paths and the data-flow description are Pro's,
  now labeled as such; CodeQL runs through GitHub's default setup, not the
  workflow; and every multi-line `cd` example is anchored at the checkout
  root.

### Added

- **The community files a repository is supposed to have.** `CONTRIBUTING.md`
  (setup, per-module tests, the things that look wrong but are load-bearing,
  PR and commit conventions, what a useful bug report contains),
  `AGENTS.md` (the same for AI coding agents: commands, constraints such as
  the error-response contract and path confinement, and the rule that every
  PR gets a read Copilot review), a Contributor Covenant `CODE_OF_CONDUCT.md`
  pointing at the maintainer's GitHub handle and the private advisory link,
  `CODEOWNERS`, a pull request template with the checklist CI cannot run for
  you, and bug/feature issue templates that ask for the edition, the
  `error_id` and the doctor's output. The README's documentation table now
  lists them.

## [2.8.0] — 2026-09-12

A picture you can hand to anyone, and nothing on the wire that should not be.

The one thing every answer this stack gave still lacked was something a
person *without* a login could look at. Public snapshot links were the
obvious route and were rejected in 2.7.0 for what they are — permanent,
world-readable URLs with the measurements inside. This release takes the
opposite trade: a static PNG, drawn on request by the MCP server, delivered
into the chat, forwarded by the user to whoever they choose. It draws what
SmokePing always drew — the median with the spread of the individual pings
around it — because a jittery-but-alive link and a clean one can share a
median, and the band is what tells them apart.

The other half is the sixty-nine open CodeQL alerts, which were five
problems: every route echoed exception text to the browser, request-named
files reached the filesystem on the strength of a regex, a token lived in
`localStorage`, the login redirect echoed its input, and CI's token had more
rights than it used. None was a known exploit; all were the kind of thing
that turns a small bug elsewhere into a disclosure. All closed, each with a
test that reintroduces it, and the count on `main` is zero.

### Added

- **`get_chart` — a picture, on request.** New MCP tool that draws one
  target's latency and packet loss over a window as a PNG: the median line
  with the spread of the individual pings shaded around it (outer min–max,
  inner quartiles — SmokePing's "smoke"), loss underneath on a fixed 0–100
  axis, major and minor gridlines, local-time axis, and a footer naming the
  source and when it was drawn. `with_peers=true` adds the same-category
  peers as faint lines.

  The point is the person who has no login here. Every other answer this
  stack gives ends in a Grafana link that asks the reader to sign in; a PNG
  is something the user can forward to a friend or the ISP. It is the
  opposite trade from the snapshot links switched off in 2.7.0: a static
  file the user chose to send, not a permanent world-readable URL.

  It is on request only. No other tool attaches images, and the server
  instructions and the OpenClaw skill both say so — an ordinary "how is my
  internet?" stays text. `deliver=true` also posts the file into the OpenClaw
  chat through the same Gateway endpoint the alerter uses (the agent can
  *see* an MCP image but cannot forward it), and the result reports
  `delivered` or a `delivery_error` that names the missing setting; the
  image comes back either way.

### Security

Sixty-nine open CodeQL alerts, five causes, three PRs (#51, #52, and the
error-response change below). None was a known exploit; all were the kind of
thing that turns a small bug elsewhere into a disclosure.

- **Error responses no longer echo exception text** (51 ×
  `py/stack-trace-exposure`, config-manager and web-admin). Every route
  answered `{'error': str(e)}`, so whatever an exception carried — a
  database DSN with its password, the config-manager URL and token, a
  filesystem path, a library's internal message — went to the browser and to
  anything else on the port. Routes now return a message chosen in code plus
  an eight-character `error_id`; the detail goes to the log with a traceback
  under that id (`docker compose logs web-admin | grep <id>`). Validation
  reasons are unchanged: the config-manager validators return lists of
  plain strings instead of raising, and `PUT /config/<type>` returns them as
  `problems`. "Not found" is the static `Target not found`. Tests in both
  apps raise an exception containing a fake secret inside every affected
  route and assert it never reaches the body — reintroduce `str(e)` and
  the route's case fails.

- **Request-named files are confined to their directory** (10 ×
  `py/path-injection`). The CrUX and Cloudflare cache files are named after
  a `country` query parameter, the AI reports page takes a `file` name, and
  config-manager formatted `CONFIG_DIR/<type>.yaml` before checking the
  type. Each had a regex allow-list, which bounds the string but not the
  path. web-admin gains `services/safe_path.confine()` (normalize, then
  require the base-directory prefix); config-manager resolves the type
  through a literal table so request text is never formatted into a path.

- **The Cloudflare API token is no longer written to `localStorage`** (1 ×
  `js/clear-text-storage-of-sensitive-data`). It was saved on every
  keystroke, readable by any script on the origin. It now lives in the
  password field for the life of the page and travels only in the POST
  body; values left by earlier versions are removed on load.

- **The post-login redirect is rebuilt, not echoed** (1 ×
  `py/url-redirection`), and rejects backslashes, which browsers read as a
  second slash.

- **CI's `GITHUB_TOKEN` is `contents: read`** (6 ×
  `actions/missing-workflow-permissions`). No job writes to the repository.

### Changed

- **README rewritten** in the shape of the sibling Netflix OCA Locator's:
  features first, one quick start, usage by task, a documentation table
  that only lists files that exist. The broken logo reference
  (`img/logo.jpg` never existed) is gone until there is a logo; the
  security contact points at `SECURITY.md`'s private reporting instead of
  a placeholder address.

- **The chart renderer moved to `shared/modules/common/charts.py`** so the
  alerter and the MCP server draw the same picture. The alerter's
  `charts.py` is an alias, as `flux.py` already was. The OpenClaw
  `/tools/invoke` payload builder moved with it (`common/openclaw.py`); the
  alerter's `openclaw_invoke_payload` delegates to it.

- **Alert charts gained the dispersion band and minor gridlines** as a
  consequence of sharing the renderer. A jittery-but-alive link and a clean
  one can share a median; the band is what tells them apart.

- **The `mcp-server` service runs on the host network**, as the alerter
  does and for the same reason: the OpenClaw gateway listens on the host's
  loopback only — which is the right setting — and no bridge network can
  reach it. Exposure is unchanged: `MCP_HOST=127.0.0.1` pins the listener
  exactly where the old `127.0.0.1:8090:8090` port mapping put it.
  `CONFIG_API_URL`/`INFLUX_URL` for this service now default to the
  loopback addresses both backends already publish on (override with
  `MCP_CONFIG_API_URL` / `MCP_INFLUX_URL`).

### Fixed

- **Microcut alert charts drew the gateway's latency ×1000.** `cpe_latency`
  stores milliseconds while `latency`/`dns_latency` store seconds, and the
  renderer scaled every measurement as seconds — so a 7 ms gateway plotted as
  7000 ms. Visibly wrong, but only on a chart nobody had compared to the
  dashboard. Now scaled per measurement, with a test that reintroduces the
  bug.

- **web-admin no longer logs `Control server error: Permission denied:
  '/home/smokeping'` at startup.** Its user was created with `useradd -r`,
  which makes no home directory, and gunicorn 26 opens a control socket
  under `$HOME`. The user now gets a home. Harmless before, but a
  permanent error line on a clean boot is one you learn to skip, and then
  you skip the one that matters.

- `docs/alerting.md` now lists `ALERT_CHARTS` and the `CHART_*` knobs in the
  environment reference; they were documented only in `.env.template`.

## [2.7.0] — 2026-09-01

Alerts that reach you, say what they mean, and can be told to be quiet.

The alerting engine had been evaluating rules correctly for a week and telling
nobody: `NOTIFY_MODE=openclaw` POSTed to a path no OpenClaw build serves, and
the 404 got generalised into "the gateway has no HTTP ingress at all". It has
one. Everything else here follows from delivery actually working — a verdict
line so an alert leads with what it means, the chart in the message, a daily
digest so silence stops being ambiguous, and mute control by conversation.

The other half is about not trusting green lights. The doctor learned to check
the *running* stack, not just the repo, after a masked build failure let a
three-week-old image look healthy. That check then caught a stale container and
a Dependabot PR that would have initialized an empty PostgreSQL over the config
source of truth — with CI passing, because CI never builds that image.


### Fixed

- **Grafana snapshots switched off.** A snapshot is a permanent,
  *unauthenticated* URL with the measurements embedded in it — `GET
  /api/snapshots/<key>` answers 200 with no credentials, confirmed on 13.0.2.
  Everything else on this host is behind a login (anonymous auth off, the API
  401s), so this was the one way to publish home network data by accident, and
  it sits one click away in Grafana's share dialog. `external_enabled` is worse
  and defaulted **on**: it publishes to the third-party `snapshots.raintank.io`.
  Both are now off, and creating a snapshot returns `403 Dashboard Snapshots
  are disabled` even as admin.

  This also closes out the planned "public snapshot links" feature. A spike
  showed it was technically workable on 13.0.2, but the design it implies —
  handing out world-readable URLs — is the wrong trade for this deployment.
  Alert links point at Grafana and the reader signs in.

- **`container-dns-fresh` no longer warns about a resolver that was pinned on
  purpose.** Two changes from the same batch disagreed: one pins the smokeping
  container's resolvers in Compose (`dns:`) so it cannot inherit a resolver
  that later evaporates, and the other warns whenever a container's resolver
  is not the host's. The warning only appeared once smokeping was next
  recreated and the pin actually took effect — so it would have shown up on a
  later unrelated deploy, looking like a new fault.

  Left alone it would have warned on every run forever, and a check that
  always warns is one you stop reading. That is expensive here specifically:
  this check exists because a frozen resolver silently killed half the
  monitoring for ten days. It now reads the Compose `dns:` blocks and treats a
  pinned resolver as expected, resolving container to service by Compose label
  rather than by name (`container_name:` overrides make name-parsing wrong).
  A resolver that is neither pinned nor the host's is still reported, and a
  pin on one service does not excuse another — both pinned by tests.

### Changed

- **Python base images 3.11 → 3.14**, and CI now tests on the version it
  ships. CI ran `setup-python@3.11` while every container ran a different
  interpreter, so a 3.14-only break could not have been caught — the same
  class of drift as a compose default silently overriding a module constant.
  Verified before bumping: all seven modules' suites pass on 3.14, and both
  the heaviest image (alerter — matplotlib, numpy, pillow) and the one with
  Rust-compiled deps (web-admin — pydantic) build on **arm64** with real
  wheels rather than falling back to source builds on a Pi.

- **Dependabot no longer groups stateful major bumps with routine ones.**
  `postgres` and `influxdb` majors are ignored outright; other docker majors
  are split from minor/patch, matching what the Python ecosystem already did.

  This is a fix for a near miss. One grouped PR carried the harmless Python
  bump *and* `postgres:15→18`, and CI passed it — the Docker build matrix
  never builds the postgres image, and no CI job has an existing volume, so
  there was nothing for a green check to mean. Two failure modes were
  confirmed against the live volume: PG18 refuses to start on a PG15 directory
  (loud, safe), and — worse — PG18 relocated its default `PGDATA` to
  `/var/lib/postgresql/18/docker`, so with our mount it reports
  "uninitialized" and would initialize an **empty** cluster while the real
  data sat orphaned. Postgres is this project's config source of truth, so
  that mode presents as every target vanishing from a container reporting
  healthy. New `docs/upgrades.md` carries the dump-first procedure for both
  stateful images.

### Added

- **Alert mutes, controlled by conversation rather than buttons.** Four MCP
  tools — `mute_alerts`, `unmute_alerts`, `ack_incident`, `list_alert_state` —
  so "mute amazon for two hours, I'm reflashing the router" works. Telegram
  buttons cannot call back without an OpenClaw channel plugin; natural language
  is the better interface anyway, because it carries a scope, a duration and a
  reason that no button could.

  Two containers now share state, and the race is removed by construction
  rather than managed: each file has exactly one writer (the alerter owns
  `state.json`, the mcp-server owns `mutes.json`) and the other side mounts it
  `:ro`, so single-writer is OS-enforced instead of conventional. Neither
  process ever read-modify-writes the other's file, so there is no lock to
  acquire and none to leak. Cross-container reads are safe because both writers
  already used temp-file-plus-`os.replace`; that was introduced as crash
  safety and is now also the concurrency contract.

  Muting is the one feature here that can *cause* a missed outage, so the
  mitigations are structural rather than left to discipline: a 24-hour cap on
  any mute, every digest listing what is muted and how many alerts each one
  swallowed, recoveries never suppressed for an incident that was already
  announced, no recovery at all for one muted from first sight (nobody heard it
  start), and `unmute_alerts(all=True)`. A missing or corrupt mutes file means
  **everything alerts** — delivery must never depend on that file being
  readable, so its failure mode is noise rather than silence.

  The check sits after the cooldown and after the rate limiter, and never calls
  `_record_notification()`. After the limiter so an alert the ceiling already
  blocked is not also counted as one the mute suppressed; before recording so
  the hourly budget counts only what was really sent; and inside the incident
  loop so `last_seen` and the severity refresh still run — skipping them would
  leave the incident looking brand new when the mute lifts, sending it down the
  first-seen path and reintroducing the flapping the grace period fixed.

- **The doctor can now check the running stack (`--live`).** Two checks, both
  for failures that already happened here, and both sharing one shape: the
  broken thing keeps looking healthy, so nothing goes red and nobody looks.

  `deployed-code-current` compares the sha256 of every deployed `.py` — the
  module's own source and the shared `common` package — against the
  repository. This is `dde5e36` ("the flap fix never reached the deployed
  container"), and it recurred while building this batch: an image failed to
  build, the failure was masked by a shell pipeline's exit code, `docker
  compose up -d` recreated the container from a three-week-old image, and
  every surface reported success. A container it cannot read is reported as
  "cannot verify", never as drift — claiming a difference it did not measure
  would be the same class of bug.

  `container-dns-fresh` compares each container's resolvers against the
  host's. Docker writes `/etc/resolv.conf` once, at container creation, so a
  container created while a VPN was up keeps that resolver after the VPN is
  gone — which cost nine of eighteen targets for ten days, with hostname
  targets at 100% loss and raw-IP targets perfectly healthy. Loopback
  resolvers are excluded: `127.0.0.11` is Docker's own embedded DNS and is
  fresh by construction. Without that exclusion the first real run flagged
  all six healthy containers, which is how a check gets ignored.

  Both skip cleanly without Docker, so CI and the static path are unchanged.

- **A daily digest, so silence stops being ambiguous.** Alerts only fire when
  something breaks, which means a quiet channel means either "nothing
  happened" or "the monitoring stopped" — and those are the two states you
  most need to tell apart. `DIGEST_ENABLED` opts into a wall-clock summary of
  the last 24 hours, in the same shape as everything else this bot sends:
  traffic lights, bold sections, worst targets first.

  **Firing exactly once is the hard part**, because the loop wakes every 60
  seconds and has no idea what time it is between ticks. Each tick resolves
  the most recent scheduled instant at or before now — the *slot* — and state
  records which slot last fired, making delivery idempotent: ten ticks in a
  minute resolve one slot and send one message.

  The slot is persisted, **not the send time**. That distinction is the whole
  mechanism: anything recorded before the slot instant re-fires the same day,
  which is what a send-time-like value becomes after an NTP step backwards or
  on a container whose RTC starts behind. A reintroduction test pins it.
  Both DST transitions are covered — a nonexistent wall time resolves to one
  stable instant, an ambiguous one picks the same instant every tick, and a
  full day of five-minute ticks across a transition delivers once.

  Past `DIGEST_MAX_LATENESS` (4h) a slot is recorded as fired **without**
  sending. A Pi that was off for two days must not deliver 08:30's digest at
  19:00, and must not deliver two.

  **It never claims health it did not verify.** If the aggregate query raises
  or returns zero targets, nothing is sent and the slot is retired with an
  error. "All clear" when the truth is "InfluxDB did not answer" would turn a
  broken monitor into a reassuring message — the exact failure the digest
  exists to catch, and the first thing its tests assert.

  Counts come from a new `state["history"]`, appended on each alert or
  recovery the alerter decides to send — whether or not delivery then
  succeeded — and pruned to 200 entries and 48 hours. Necessary because
  `reconcile()` pops a record on recovery, so by 08:30 an incident that fired
  and cleared at 03:00 has left no other trace.

### Fixed

- **`show-tunnel-urls.sh` reported the first hostname a tunnel ever had.** A
  quick tunnel gets a brand new `*.trycloudflare.com` name every time
  cloudflared reconnects, and every one stays in the container log; the script
  took `head -1`, so it printed the oldest — dead, and entirely plausible
  looking. Found with 12 distinct URLs in one log while it reported #1. It now
  takes the last, and warns when a tunnel has rotated, since anything saved
  earlier into `.env`, a bookmark or a chat is already a dead link.

- **A text-only message could not be delivered silently.** `silent` was set
  only on the image path and only from `ALERT_SILENT`, so a text-only digest
  would ring a phone at 08:30 regardless. It is now a per-message property
  that wins over the env default — quiet is a fact about *this* message, not
  about the deployment — and it survives the text-only retry after an image
  send fails, which previously dropped it.

- **Every link is offered twice: at home, and from anywhere.** Deep links were
  built from a single base URL, which forced a choice nobody can make
  correctly — the same person reads an answer from the couch and from a train.
  A LAN address is the better link at home (one hop, and up even when
  Cloudflare isn't) and a dead link on cellular.

  `TUNNEL_BASE_HOST` (plus `GRAFANA_TUNNEL_URL` / `WEB_ADMIN_TUNNEL_URL`)
  configures the from-anywhere address separately, and every entry in a `links`
  object gains a `_tunnel` twin: same panel, same window, different host.
  `system_status` twins its three entry points the same way.

  Three behaviors keep a twin from lying about where it goes: a tunnel with no
  LAN address configured *becomes* the primary link rather than leaving a
  tunnel-only Pi with no links at all; two tiers resolving to the same base are
  not twinned, because two labels on one URL invite a reader to try "the other
  one"; and the ClickHouse gate covers both tiers, since a twinned 404 is still
  a 404.

  In alerts only the *graph* gets a twin. A Grafana deep link is ~120
  characters and a caption budget is 1024, so mirroring all four would spend a
  quarter of it saying the same thing twice.

- **The monitoring skill now specifies the shape of a report, not just its
  content.** `examples/openclaw/smokeping-monitoring/SKILL.md` gained a report
  template — traffic lights per section (🟢 nothing to do, 🟡 real but minor,
  🔴 acted on), bold headings, one fact per bullet with its number and window,
  a verdict as the headline rather than a summary, and a graphs section
  offering both links.

  Three rules exist because the first real reports broke them. **Headings are
  the channel's bold, never `###`** — Telegram does not render Markdown
  headings, so `### DNS` arrives as literal hashes or flat text, which is what
  made an otherwise correct report read as one wall. **Every section is
  bullets, one fact and one line each** — a semicolon or a second measurement
  means it is two bullets, with a worked wrong/right pair in the file.
  **A bullet that names a moment carries that moment's link**, taken from the
  `worst_windows` entries `get_microcut_stats` already zooms to ±15 minutes;
  the week-long overview URL drops the reader into 168 hours to hunt for a
  spike the agent had already found.

  **The template is a structure, not a script.** It is written in English
  because the tools and metric names are; the skill directs the agent to answer
  in whatever language the question arrived in, and marks what never gets
  translated — target names (they are database keys: a translated
  `CloudflareDNS` cannot be looked up, muted, or found on a dashboard), numbers
  with their units, the traffic lights, and the links.

  It also names the four values an operator must tune before installing — the
  host's names, the CPE loss floor, the timezone, the example target names —
  rather than saying "replace the placeholders" and marking none of them. A
  wrong loss floor makes the agent confidently wrong rather than obviously
  broken, which is the harder failure to notice.

- **`shared/scripts/install-openclaw-skill.sh`** installs the skill, backs up
  an existing one, and optionally reloads the gateway. `--check` exits
  non-zero when the installed copy is stale — the failure this guards against
  is invisible, since an agent running a stale skill keeps answering, just in
  the old shape, and the install looks like it silently did nothing. The
  README and `docs/openclaw-integration.md` now point at it.

- **Alerts arrive with the graph.** Each one carries a rendered PNG: median
  latency over packet loss for the target, its same-category peers as gray
  context, and a marked line at the moment the incident started — so the
  question a Grafana trip is usually made to answer (*since when, and is it
  just this one?*) is answered in the notification.

  Rendered with matplotlib in-process, **not** `grafana-image-renderer`: that
  is a headless Chromium at ~400 MB resident and seconds of CPU per render, on
  a Pi that has already hit its soft thermal limit. Bytes are posted as base64
  in the invoke body, so there is no shared filesystem between the container
  and the gateway, no path translation and no retention sweep.

  Design decisions that are load-bearing rather than cosmetic:

  - **Two stacked panels, never twin y-axes.** Latency and loss have different
    scales; a dual axis invents a correlation that is not in the data.
  - **The loss axis is pinned 0–100.** Autoscaled, 4% loss looks catastrophic,
    and loss is the axis a reader interprets absolutely.
  - **Emphasis, not eight hues.** The story is one target, so it wears a status
    color and the peers recede.
  - **Digest bars are one hue.** Coloring each bar darker-where-bigger
    double-encodes bar length on nominal categories; over-threshold rows are
    marked with a glyph and a status-colored value instead, so color never
    carries meaning alone — and it keeps discriminating when everything is over
    the line.

  A chart never costs an alert: rendering is wrapped, matplotlib is imported
  lazily, and a failed image send retries **exactly once** as text rather than
  doubling the retry budget.

- **Alerts now answer the question they raise.** Every notification carried a
  measurement (`amazon: mean loss 22.4% over 15m`) and left the reader to work
  out whether it was their line, their ISP, or that one site. Each alert now
  leads with a verdict: `🌐 Not you — 12 of 16 destinations affected but your
  local link is clean, so this is upstream.`

  It is deterministic and costs **no additional queries** — the breadth,
  microcut and liveness rows were already fetched by the rules and thrown
  away, which matters on a Pi that has hit its thermal limit.
  `evaluate_with_context()` returns them; `evaluate()` is unchanged.

  Two properties are load-bearing:

  - `exporter_stale` outranks every other scope unconditionally. Announcing a
    network fault from an *absence* of measurements is the worst thing this
    could do, and when the exporter stalls every target reads as 100% lost
    precisely because nothing is arriving.
  - Hosts that never answer ICMP are excluded from breadth entirely. A target
    pointed at a host that does not respond charts a permanent flat 100%;
    counting those turns one slow site into an ISP outage. Anything at 100%
    whose incident predates `VERDICT_STALE_DOWN_HOURS` is dropped from both
    numerator and denominator. This is not hypothetical — see the DNS note
    below, where nine such targets would have made every verdict wrong.

  Every verdict logs its own inputs, so one you disagree with is diagnosable
  from `docker compose logs alerter` without reproducing the moment.

### Changed


- **Code that two images need now lives in `shared/modules/common/`**, copied
  into each image at build time (`context: ../../shared`). Containers cannot
  import across each other, so the Flux helpers were already triplicated
  between the alerter, ai-insights and the mcp-server, and the deep-link UID
  maps existed twice. Extracted rather than copied a fourth time: `tsdb.py`
  (queries, the loss-unit clamp), `aggregates.py` (the per-target and CPE
  rollups) and `links.py` (Grafana/web-admin URLs).

  The old module paths remain as re-export shims, so no call site changed.
  Two tests did, and the reason is worth writing down: a shim forwards public
  names, but **`monkeypatch.setattr(shim, "query_influx", ...)` does not
  intercept the real function**, because the moved code resolves that name in
  its own globals. The ai-insights collector tests were silently querying the
  live InfluxDB instead of their stub. They now patch the module that owns the
  code. The alerter's lazy-client test moved for the same reason — asserting
  `_influx_client is None` through a shim would hold forever whether or not a
  client had been built.

### Fixed

- **The smokeping container's resolver is pinned instead of inherited.** Docker
  writes `/etc/resolv.conf` once, at container creation, and never again — so a
  container inherits whatever the host's resolver happened to be at that
  instant and holds it forever.

  This deployment captured a Tailscale MagicDNS address. Tailscale later logged
  out, and from then on **nine of eighteen targets reported 100% loss for ten
  days** — every hostname target, while every raw-IP target stayed green.
  Nothing surfaced it, because "100% loss" and "that host is down" are
  indistinguishable; the alerter dutifully tracked the incidents to
  `notified_count=253` with delivery switched off.

  `dns:` is honoured even under `network_mode: host`. Public resolvers by
  default (`SMOKEPING_DNS`, `SMOKEPING_DNS_FALLBACK` to override) so this does
  not depend on any particular LAN addressing. Only the smokeping service is
  pinned: the bridge-network services must keep Docker's embedded resolver for
  service-name lookups.

  The real lesson is the missing signal, not the missing config — a *live*
  doctor check for "a target that has never produced a non-100% point" would
  have caught this in an hour instead of ten days. `docs/doctor.md` lists the
  live checks as still unbuilt.

- **Chart timestamps matched the data, not the label.** matplotlib formats
  dates with `rcParams["timezone"]` (UTC) regardless of each datetime's own
  tzinfo, so the x-axis rendered UTC beneath a footer naming the local zone —
  an hour's silent offset, which is exactly what makes someone mis-correlate an
  incident with whatever they were doing at the time. Caught by rendering the
  chart and looking at it.

- **Messages are composed to a budget instead of truncated.** Telegram allows
  4096 characters for a message but only **1024 for a caption**, so an alert
  carrying a chart has a quarter of the room. Sections now carry a priority
  and the least valuable are dropped until the message fits — mute hint, then
  the breadth recap (the verdict line already states that number), then the
  links, which survive longest because a static image caption cannot be
  explored and a link is the way out of it. The headline, verdict and numbers
  are never dropped.

  Only if those alone overflow is text trimmed, and then on a line boundary.
  `reports_watcher` previously did a hard `content[:max_chars]` slice, which
  the moment messages became HTML could cut a tag in half — Telegram rejects
  that with a 400, which `/tools/invoke` reports as HTTP 200 with
  `{"ok": false}`, so the notifier would burn three retries and log a
  permanent delivery failure for what is really a formatting bug. That cap
  also now bounds the **whole** message including its header; it previously
  delivered 3530 characters at a documented limit of 3500.

  Every interpolated value is escaped: target names are user-editable and
  `a<b&c` is a legal one today.

- **Several alerter tests depended on the developer's own shell.** The env
  scrub list in `conftest.py` was missing `STALE_WINDOW`, `ALERT_RESOLVE_AFTER`,
  `ALERT_MAX_PER_HOUR` and `MICROCUT_LOSS_PCT`, so an exported value silently
  changed what they asserted.

- **Deep links no longer point at dashboards that do not exist.** Every
  dashboard UID in `links.py` comes from the InfluxDB provisioning tree, but
  the ClickHouse tree is a parallel set with different UIDs and no CPE
  dashboard at all. Under `TSDB_TYPE=clickhouse` every link the MCP tools
  emitted resolved to a Grafana 404 — while looking entirely valid in the
  answer. Links are now gated on the active backend, the same doctrine the
  module already applied to an unset base URL: no link beats a broken one.
  `system_status()` distinguishes the two reasons, since telling someone to
  set `PUBLIC_BASE_HOST` when it is already set is its own dead end.

- **The flap fix below did not reach the deployed container.** Raising
  `DEFAULT_DOWN_WINDOW` from 900 to 1200 fixed nothing in practice, because
  `docker-compose.yml` pinned `DOWN_WINDOW=${DOWN_WINDOW:-900}` and a Compose
  default silently wins over a module default. Both files read as correct on
  their own; only the pair was wrong. `STALE_WINDOW`, added in the same batch,
  was declared in no compose file, no `.env.template` and no doc at all.

  Fixed, and — more usefully — made impossible to repeat. The instrumentation
  doctor gained **`alerter-env-defaults-match`**, which extracts every env var
  the alerter reads out of its own source with `ast` (including the
  `os.environ.get(...) or DEFAULT_X` idiom) and asserts that each Compose
  `${VAR:-x}` default equals the `DEFAULT_*` constant behind it. Reverting the
  compose line to 900 now fails CI with the specific pair that disagrees.
  A companion **`alerter-env-declared`** warns when a knob exists only in
  Python — discoverable in neither compose, `.env.template`, nor the
  `docs/alerting.md` table.

  `docs/alerting.md` also contradicted itself on this value (900 in the rules
  table, 1200 in the flap-damping section), and `exporter_stale`'s alert text
  still claimed a hardcoded "10m" after the window became configurable and
  moved to 1200 s. The message now reports the window it actually queried.

- **`COMPOSE_PROFILES` is now persisted, not passed once.** Starting an
  optional service with `COMPOSE_PROFILES=mcp docker compose up -d mcp-server`
  leaves it running but unmanaged: the next `docker compose down` removes it
  and the following `up -d` does not bring it back, with nothing to say so.
  That is how this deployment ended up running an MCP server that Compose no
  longer knew about — and when it goes, OpenClaw silently falls back to
  answering from the shell instead of the monitoring data. `setup.sh` now
  writes `COMPOSE_PROFILES` into the generated `.env` alongside `TSDB_TYPE`,
  and `.env.template` documents the full profile list.

- **A flapping incident sent unbounded notifications.** Found the hard way: a
  `target_down` incident on a test target alternated alert/recovery on a
  five-minute cycle and delivered ~48 messages every two hours to a real
  phone, for four hours.

  Three defects at three layers, all now fixed:

  1. `DOWN_WINDOW` was 900 s with `DOWN_MIN_POINTS=3` on a 300 s probe step —
     *exactly* three points, so ordinary window jitter yielded two, the rule
     stopped matching, and the incident looked resolved. Now 1200 s (four
     points where three are required).
  2. Recovery deleted the incident record outright, so the next appearance
     took the first-seen path and alerted immediately. `ALERT_COOLDOWN` only
     ever suppressed *continuously* active incidents and did nothing in the
     one case where it matters. An incident must now be absent for
     `ALERT_RESOLVE_AFTER` (default 900 s) before it counts as recovered;
     reappearing inside that window is silent.
  3. New `ALERT_MAX_PER_HOUR` (default 6): a hard ceiling per incident key,
     independent of the lifecycle logic, so a future lifecycle bug cannot
     reach a phone at that volume. `0` disables it.

  Verified by replaying the observed flap pattern against both versions: 48
  notifications per two hours before, at most 3 after.

- **Alert delivery to OpenClaw now works.** `NOTIFY_MODE=openclaw` was
  unusable: it POSTed to `{OPENCLAW_URL}/hooks/agent`, a path that exists on no
  OpenClaw build. The resulting 404 was read as *"the gateway is WebSocket-only
  and has no HTTP ingress at all"*, and the mode was documented as needing an
  HTTP-RPC plugin or a bridge that was never written — so the alerting engine
  ran for a week evaluating rules correctly and telling nobody.

  The gateway does serve HTTP, multiplexed onto the same port: `POST
  /tools/invoke` is always enabled. Alerts are now delivered by invoking
  OpenClaw's `message` tool through it, authenticated with the Gateway token
  (`OPENCLAW_GATEWAY_TOKEN`; `OPENCLAW_HOOK_TOKEN` still accepted). Verified
  against OpenClaw 2026.7.1-2. The generalisation from one missing route to a
  whole missing protocol is called out in both docs, since the shape of that
  mistake is more useful than the fix.

- **A refused send counted as a delivered alert.** `/tools/invoke` answers
  **HTTP 200 with `{"ok": false}`** when the tool itself fails — a blocked
  tool, a bad channel, an unknown recipient. The notifier checked only the
  status code, so every one of those would have been recorded as success. It
  now inspects the body and retries, for this endpoint only (a generic webhook
  keeps status-code semantics).

- **The preflight could not tell a blocked tool from a missing endpoint.** Both
  answer 404. Reporting the wrong one is exactly the inference that caused the
  bug above, so the body is now read to separate "your tool policy blocks
  `message`" from "that route does not exist", alongside 401 for a rejected
  Gateway token and a connection error for a gateway that is down.

- `NOTIFY_MODE=openclaw` now refuses to run with `OPENCLAW_TO` unset instead of
  posting a message with no recipient and reporting success.


## [2.6.0] — 2026-08-09

Answers that come with the graph, and a tool that checks the monitoring is
actually monitoring.

Two additions that point in the same direction. Deep links close the gap
between "median 8 ms, 0% loss" and the panel that shows what the number is
hiding. The instrumentation doctor closes a wider one: nothing in CI could
tell whether a dashboard, a datasource, or an exporter still agreed with its
neighbours, which is how v2.5.0 shipped a Grafana that would not start.

Upgrade notes:

- **Deep links are off until you configure them.** Set `PUBLIC_BASE_HOST` on
  the mcp-server service to the address a reader can actually open. Unset is a
  supported state, not a broken one — tool responses simply carry no links.
- **The `Grafana dashboards & provisioning` CI job now runs the doctor.** It
  checks strictly more than the validator it replaces, so a repo that passed
  before may now fail — that is the point. Run it locally with
  `PYTHONPATH=shared/modules/doctor python -m doctor`.

### Added

- **An instrumentation doctor** (`shared/modules/doctor/`, `docs/doctor.md`),
  which verifies the monitoring wiring rather than the network: the case where
  a measurement or panel looks fine and silently charts nothing. Nine static
  checks compare each stage of the pipeline against the next — dashboard
  queries against the vocabulary the exporters actually write, datasource
  references against what provisioning declares, dashboard files against the
  paths Grafana scans.

  It replaces the inline JSON/YAML validator in the `Grafana dashboards &
  provisioning` CI job, so every PR is now gated on it. Notably it catches the
  two-datasources-claim-default configuration that made v2.5.0 unable to start
  Grafana, which nothing in CI would have caught before.

  The vocabulary is read out of the exporter source with `ast` rather than
  copied into a list, since a copied list drifts silently — which is the bug
  class the tool exists for. Every check is proved by a test that reintroduces
  the bug and asserts the doctor fails; a doctor that only ever passes is
  indistinguishable from one that does nothing.

  The live checks (target present in the DB but missing from the RRDs or the
  TSDB, queries that error or return empty, loss outside its declared range)
  are not built yet — `docs/doctor.md` lists them.

- **Deep links in MCP tool responses.** Each target now carries a `links`
  object — the Grafana panel scoped to that target and time window, the
  per-ping detail, the side-by-side against its peers, and the web-admin page
  for editing it — so an assistant can hand over the graph instead of only the
  median. `get_microcut_stats` zooms each of its worst-5 windows to a
  ±15-minute range around when it happened.

  Off until configured, deliberately: this host answers on a LAN IP, a
  Tailscale name and possibly a tunnel hostname, and a link to the wrong one
  looks right in the transcript and fails silently on the reader's phone.
  Set `PUBLIC_BASE_HOST` (standard ports appended) or `GRAFANA_PUBLIC_URL` /
  `WEB_ADMIN_PUBLIC_URL` where a proxy hides the ports. Unset means no links
  at all rather than a guess; `system_status()` is the one place that says so.

### Changed

- `get_loss_events` returns a `by_target` rollup (event count, worst loss, the
  span covered, links) alongside the raw events. It was returning up to 500
  individual points with no summary, and a hundred loss points on one target
  is one story rather than a hundred.

### Fixed

- The web-admin login redirect dropped the query string — it redirected to
  `next=request.path`, so opening `/targets/?q=Amazon` while logged out landed
  on the unfiltered list of every target after logging in. Uses `full_path`
  now. Absolute and protocol-relative `next` values are still rejected.
- `/targets/` now honours a `?q=` parameter by pre-filtering the list, so the
  links above open on the target being discussed.

## [2.5.1] — 2026-08-07

A hotfix release. **v2.5.0 does not start Grafana in the default InfluxDB
mode** — a fresh install comes up with no Grafana at all — so 2.5.0 should not
be deployed. Alongside that, two nightly jobs turned out to have been failing
silently (the IPv6 gate was being undone every night; the OCA refresh had not
completed since 2026-08-03), the MCP integration was found not to be wired
despite reading as healthy, and the Pi was found thermally throttled by its
own steady-state logging and probe rates.

The common thread is instrumentation that reports success without doing the
work — none of these four had a failure signal a person would notice. Each fix
adds one.

### Fixed

- **v2.5.0 will not start Grafana in the default InfluxDB mode** (PR #29). The
  ClickHouse work set `isDefault: true` on the ClickHouse datasource, on the
  reasoning that ClickHouse mode provisions that file alone. InfluxDB mode,
  however, bind-mounts the whole datasources directory read-only, so *both*
  files are provisioned, two datasources claim default, and Grafana refuses to
  start. The `isDefault: false` that was replaced existed for exactly this
  reason. It stayed latent through the release because Grafana had not been
  restarted since the merge. The committed file is `false` again and the
  ClickHouse entrypoint flips it while building its own writable tree.
  **A fresh v2.5.0 install in the default mode comes up with no Grafana; use
  2.5.1.**
- **The IPv6 gate was undone every night** (PR #29). `ipv6_check` held its
  verdict in a module-level variable, but config is generated in more than one
  process: the API regenerates in-process, while the nightly OCA refresh runs
  `oca_fetcher.py` as a *subprocess* that imports `ConfigGenerator` and
  regenerates there too. That subprocess started with an empty cache, hit the
  deliberate unknown-means-allowed rule, and wrote IPv6 targets back in — with
  the generator's log going to captured subprocess output, so nothing appeared
  to have run. The verdict is now persisted to a JSON file with an atomic
  replace and `get_status` falls back to it. Fail-open is kept but scoped
  correctly: unknown now means *nobody has ever checked*, not *this process
  has not checked*.
- **The nightly OCA refresh had been failing** with `UniqueViolation` on
  `targets_name_key` since at least 2026-08-03 (PR #29). The replace deletes
  then re-adds in one transaction, but SQLAlchemy's unit of work emits saves
  before deletes within a flush, so new rows collided with old rows not yet
  deleted — guaranteed whenever the OCA list came back unchanged, which is the
  normal case. An explicit flush after the deletes orders them correctly while
  keeping the single transaction that stops a mid-way failure from emptying
  the category.
- **An agent with shell access answered network questions by running `ping`
  instead of using the MCP server** (PR #29), even with the server registered
  and healthy. Three causes: the OpenClaw skill was never installed (now a
  documented required step, with the exact symptom of skipping it); FastMCP
  was constructed with no `instructions`, so a client saw nine getters with no
  hint that this host holds months of continuous measurement; and the tool
  docstrings read like one-shot getters — `get_latency_stats` even offered
  "how is my connection to 8.8.8.8?" as an example, precisely the question
  being answered with `ping`. Docstrings now state the measurement cadence,
  say to prefer recorded history over a live probe, and carry the two
  looks-broken-but-isn't cases (ICMP-dark hosts, the CPE rate-limit floor).
- **The MCP integration could not be verified, only guessed at** (PR #29). The
  access log showed just `POST /mcp 200`, which cannot distinguish an agent
  invoking a tool from an agent merely connecting — so a plausible-sounding
  answer produced from the shell read as proof the tools were wired. Every
  tool now logs name, compacted args, result shape and duration (args named
  token/password/secret/key redacted, long values truncated), and the
  verification step in `docs/openclaw-integration.md` requires a `tool=` line
  in the server log. The real root cause is documented too: a gateway already
  running when the server is registered never picks it up — the Codex runtime
  fingerprints the server set per thread, so `openclaw mcp reload`, a gateway
  restart and a fresh session are required, while a separate config-reading
  poller keeps `mcp probe`/`mcp doctor` green.

### Changed

- **Steady-state load on the Pi cut substantially** (PR #29), after the host
  was found to have hit its soft temperature limit and frequency-capped
  (`throttled=0xe0000`). Measured 65.9 °C → 60.9 °C.
  - Grafana ran at `debug`, writing ~1,100 log lines every three minutes to an
    SD card. Now `info`, env-overridable. Measured 1151 → 178 lines/3 min.
  - Container logs were unbounded (json-file default, no daemon config). All
    Pro services now rotate at 10 MB × 3 via a compose anchor.
  - Dashboard provisioning re-walked the directory and rebuilt the search
    index every 30 s; now 300 s.
  - The microcut detector never idled — it slept `PROBE_WINDOW - elapsed`,
    which is ~0, so it probed back-to-back forever at 5 pps (~432k
    packets/day) and pressured the gateway's ICMP rate limiter, making part of
    the "constant loss floor" self-inflicted. `CPE_PROBE_IDLE` (default 20 s)
    gives a 1-in-3 duty cycle; measured cadence 10 s → 30 s. Set it to `0` to
    restore the old behavior. `MICROCUT_BURST_N` rescaled 6 → 2 to match,
    since it counts *observed* windows.
  - `rrd2influx` re-fetched all 40 RRDs every 60 s although SmokePing writes
    on a 300 s step, so most cycles spawned 40 `rrdtool` processes for
    nothing. It now skips RRDs whose mtime has not advanced past their last
    export, failing open on a `stat` error so a target can never silently stop
    exporting.

## [2.5.0] — 2026-08-03

Alerting, IPv6 gating, MCP hardening, Grafana 12, and a working ClickHouse
backend (Sprints 10–13).

The alerting engine is the headline, but building it surfaced a set of
measurement bugs that had been quietly corrupting the data it was meant to
watch. Loss was understated 2–4× in InfluxDB and overstated 10× in ClickHouse,
both because the exporters guessed at how many pings a probe sends instead of
reading it from the RRD. Two of the five new alert rules could never have
fired against that data, and a third fired permanently. Everything below was
verified against the live stack or a throwaway copy of it, not just unit
tests.

Upgrade notes:

- **Loss values change scale.** InfluxDB `latency`/`dns_latency` loss written
  before this release is understated (half the true value for ping targets, a
  quarter for DNS); points after it are correct. Grafana panels will show a
  step at the cutover. Nothing rewrites history.
- **IPv6 targets may disappear.** On a host with no global IPv6 they are
  omitted from the generated config rather than charting 100% loss. Database
  rows are untouched and return automatically. `IPV6_MODE=force` keeps the old
  behavior.
- **Grafana jumps three majors** (10.4.2 → 12.4.3). The database migrates in
  place; dashboards need no changes.
- New opt-in profiles: `alerts` (alerting engine) and the existing `mcp`.
  `MCP_API_TOKEN` is optional — unset preserves the current unauthenticated
  transport.


- Fixed: ClickHouse mode never had a schema. `shared/modules/clickhouse/init/`
  is mounted at `/docker-entrypoint-initdb.d`, but Docker seeds a fresh named
  volume from the image and the official ClickHouse image ships a populated
  `/var/lib/clickhouse`, so its entrypoint reports "directory appears to
  contain a database" and skips the init hook entirely — silently. The exporter
  now applies `CREATE DATABASE / TABLE IF NOT EXISTS` on connect, which is
  idempotent and independent of whether the hook fires.
- Fixed: `rrd2clickhouse.py` computed `packet_loss = loss * 100`, but the RRD
  `loss` source is a COUNT of lost pings — a fully lost 10-ping cycle was
  recorded as 1000%. It now divides by the RRD's own `ping1..pingN` count, the
  same denominator `rrd2influx.py` uses. `packets_sent`/`packets_received` were
  derived from that broken percentage and are now taken directly.
- Fixed: the ClickHouse exporter never set `measurement_type` (so DNS probes
  were indistinguishable from ping targets) and took `category` from the
  immediate parent directory rather than the top-level section, using raw
  directory names where the InfluxDB exporter uses a mapped vocabulary. Both
  now share `DNS_DIRS`/`CATEGORY_MAP`, and a test asserts the two exporters
  agree.
- Fixed: the ClickHouse Grafana datasource was configured with a top-level
  `url`, which the official plugin ignores in favour of `host`/`port` in
  `jsonData` — its health check failed with "invalid server host". The
  unavailable `vertamedia-clickhouse-datasource` entry is gone.
- Changed: the ClickHouse datasource plugin is baked into the Grafana image at
  build time instead of downloaded on boot, and staged into the data volume on
  first start, so a Pi that boots before its network is up still gets a working
  datasource.
- Fixed: the ClickHouse dashboards were never provisioned — their provider
  config sits in a directory Grafana does not scan, and the read-only
  `provisioning/` bind mount makes it impossible to add one in place (Docker
  cannot create a mountpoint for a file inside a read-only mount). In
  ClickHouse mode the entrypoint now builds a writable provisioning tree
  containing only the ClickHouse datasource and dashboards.
- Fixed: all eight ClickHouse dashboards targeted the unavailable vertamedia
  plugin, filtered on raw directory names (`category = 'DNS_Resolvers'`) that
  the exporter does not write, and asked for `measurement_type = 'latency'` on
  DNS panels. They now use the official plugin's `rawSql` form and the
  exporter's vocabulary. Fourteen panels also used `target = ${target:sqlstring}`,
  which is a syntax error whenever the variable expands to more than one value
  — reachable via the "All" option — and now use `IN (...)`.
- Changed: ClickHouse is no longer marked experimental/unmaintained in the
  README. All 58 dashboard queries were executed against a real ClickHouse
  instance in a throwaway Compose project; docs in `docs/clickhouse.md`.

- Changed: Grafana 10.4.2 -> 12.4.3. All nine dashboards use only `timeseries`,
  `stat`, and `row` panels, so nothing needed a schema rewrite. Verified by
  running 12.4.3 against a copy of the live database in a scratch Compose
  project before touching the real one: migrations clean, 9/9 dashboards
  provisioned, InfluxDB and PostgreSQL datasources healthy, and an identical
  15-frame result from the same query on both versions. 13.0.2 was tested the
  same way and also worked, but logs a plugin-install error on every boot that
  12.4.3 does not, so the mature line won.
- Fixed: the Grafana entrypoint reset the admin password with `grafana-cli`,
  which Grafana 11 deprecated and 13 removed. On 13 the step failed and was
  swallowed as a passing warning, which would have silently pinned the admin
  password to whatever the database already held — changing
  `GF_SECURITY_ADMIN_PASSWORD` in `.env` would have stopped working with no
  clear signal. It now prefers `grafana cli`, falls back to the old binary for
  older base images, and reports a real error when both fail.

- Added: optional bearer auth on the MCP server's HTTP transport. Setting
  `MCP_API_TOKEN` requires `Authorization: Bearer <token>` on every request;
  unset leaves the transport open exactly as before, and stdio is never gated.
  The tool surface includes mutations and the port is loopback-bound, so this
  gives it a credential of its own, separate from `CONFIG_API_TOKEN` and from
  OpenClaw's gateway token. `/health` stays open for liveness probes.
- Fixed: the alerter's `openclaw` delivery mode targeted `/hooks/agent`, which
  does not exist. A stock OpenClaw gateway (verified on 2026.7.1-2) is
  WebSocket-only and returns 404 for every HTTP path — `openclaw hooks`
  manages internal agent lifecycle hooks, not inbound HTTP. The path is now
  configurable via `OPENCLAW_HOOK_PATH` so it can point at an HTTP-RPC plugin
  or a bridge, and the alerter probes the endpoint at startup and logs an
  explicit error on 404 instead of failing silently on the first incident.
  `webhook` mode remains the portable option.
- Added: `docs/openclaw-integration.md` (MCP registration via `openclaw mcp
  set`, tool filtering, token separation, and what alert delivery actually
  requires) and `examples/openclaw/smokeping-monitoring/SKILL.md`, a
  placeholder-only skill giving an agent the loss conventions and the
  looks-broken-but-is-not cases.

- Added: IPv6 gating. config-manager checks global IPv6 reachability and omits
  `FPing6` targets from the generated `Targets` file when the host cannot reach
  the IPv6 internet, instead of charting a flat 100% loss that reads as an
  outage. The check requires a global-unicast address (ULA and link-local do
  not count — a router's `fd00::/8` prefix or a Tailscale address makes
  `scope global` non-empty on a host with no IPv6 at all), a usable default
  route, and an actual reply from a probe host. It runs inside the SmokePing
  container, whose `network_mode: host` namespace is what its probes see;
  config-manager's own bridge network has no IPv6 and would report a false
  negative. Rechecked every `IPV6_RECHECK_INTERVAL` (900 s) with config
  regenerated only when the verdict flips, so targets return on their own when
  IPv6 comes back. `IPV6_MODE=force|off` overrides; database rows are never
  modified. New `GET /ipv6-status` and `POST /ipv6-status/refresh` endpoints.
  Docs: `docs/ipv6-gating.md`.

- Fixed: the RRD → InfluxDB exporter divided the loss count by a fixed 20
  pings, but SmokePing's probes send 10 (FPing/FPing6) and 5 (DNS), so every
  loss ratio written since 2026-07 was understated — 2× for ping targets, 4×
  for DNS. A fully unreachable target recorded 0.5 instead of 1.0, which made
  the alerter's `target_down` and `ipv6_down` rules (threshold 0.999)
  impossible to trigger and halved the loss shown on every Grafana panel. The
  denominator is now read per RRD from its `ping1..pingN` data sources;
  `SMOKEPING_PINGS` remains only as a fallback. Points written before this fix
  keep their old scale.
- Fixed: alerter `microcut_burst` counted any `cpe_latency` window with loss
  above zero. CPE gateways rate-limit ICMP, so the 5 pps probe sees a constant
  loss floor (measured on the live link: p50 10%, p99 30%, never a fully lost
  window in 24 h) — every window qualified, and the rule fired permanently
  without ever recovering. A window now counts only above `MICROCUT_LOSS_PCT`
  (default 50%), which is what an actual brief cut looks like.
- Changed: alerter `DOWN_WINDOW` default 300 s → 900 s. SmokePing probes on a
  300 s step, so the old window could only ever contain one point and never
  met `target_down`'s 3-point minimum.
- Sprint 10: Alerting engine + OpenClaw delivery.
  - New `shared/modules/alerter/` service: deterministic (no-LLM) rules
    evaluated against InfluxDB every `ALERT_INTERVAL` (60 s) —
    `target_down`, `high_loss`, `microcut_burst`, `exporter_stale`, and an
    aggregate `ipv6_down` — with incident dedup/cooldown/recovery state
    persisted atomically to a named volume.
  - Delivery via `NOTIFY_MODE`: `off` (log-only default), `openclaw`
    (POST `/hooks/agent` on a local OpenClaw gateway), or `webhook`
    (generic JSON POST with optional bearer token); 3 retries with
    exponential backoff. Also forwards ai-insights `report-*.md` files
    (at most one per `REPORT_DELIVERY_INTERVAL`, truncated
    Telegram-friendly).
  - Pro compose: `alerter` service behind the opt-in `alerts` profile
    (`network_mode: host`, `alerter-state` volume, read-only `reports`
    mount); `.env.template` gains an all-empty alerting block; CI
    validates the `alerts` profile. Docs: `docs/alerting.md`.

## [2.4.0] — 2026-07-31

AI insights & in-UI assistant (Sprint 9) — final sprint of the 2026-07
modernization plan.

- Sprint 9: AI insights & in-UI assistant.
  - New `shared/modules/ai-insights/` service: pulls latency/loss/microcut
    aggregates from InfluxDB and writes plain-language Markdown health
    reports via Claude (Haiku by default; `AI_MODEL` overridable). Guardrails:
    input-size cap, `AI_REPORTS_PER_DAY` cap, clean no-op when
    `ANTHROPIC_API_KEY` is unset. Compose snippet documented in
    `docs/ai-insights.md` (compose/editions files owned by an in-flight PR).
  - web-admin: new AI section — `/ai/reports` renders the generated reports
    (tiny built-in safe Markdown renderer, empty-state help when disabled)
    and `/ai/chat` is an assistant that reuses the MCP tool surface
    (stats read tools run inline; add/remove/toggle target require an
    explicit confirmation card before executing). No `apply_config` tool —
    regeneration is automatic.
- Pro compose: `ai-insights` service behind the opt-in `ai` profile with
  a shared `reports` volume (PR #21); `.env.template` gains
  ANTHROPIC_API_KEY and AI tuning knobs (PR #21). Enable with
  COMPOSE_PROFILES=influxdb,ai.

## [2.3.0] — 2026-07-31

Editions repair, AI-operability, and three production fixes found while
deploying (Sprints 7–8).

### Fixed (post-deploy)
- **OCA refresh data loss** (PR #19): the daily Netflix OCA refresh could
  replace the `netflix_oca` targets with an empty set when the fetch
  failed (this happened in production while the locator install was
  broken). Empty fetches are now refused and the DB replace is a single
  transaction. Golden tests moved to immutable fixtures so live runtime
  state can never fail CI (PR #19).
- **Slow container boot** (PR #18): the exporter cont-init script blocked
  s6 service startup (~5 min to web UI after recreate); loops are now
  detached and the sleeps removed — boot-to-web measured at 9 s.

- Sprint 8 (PR #13): MCP server module — manage targets and query latency
  data conversationally from Claude Code / Claude Desktop.
- Sprint 7: editions repair & compose unification.
  - Pro: generated `Targets`/`Probes` now reach SmokePing via a read-only
    directory mount (`/config/generated`) plus a `05-link-generated-config.sh`
    cont-init symlink script, replacing single-file bind mounts that went
    stale after config-manager's atomic (inode-swapping) writes.
  - Standard: fixed broken data path (config-manager's output volume never
    reached SmokePing) using the same shared-volume + symlink pattern; added
    a persistent `/app/config` bind mount, `COMPOSE_PROJECT_NAME` /
    `CONFIG_API_TOKEN` / `CONFIG_DIR` / `OUTPUT_DIR` env wiring, and
    `CONFIG_API_TOKEN` pass-through to web-admin.
  - Exporter dependencies (python3, rrdtool, traceroute, influxdb-client,
    PyYAML) baked into the smokeping image at build time; cont-init/
    entrypoint installs now only run as a fallback (no more apk/pip on
    every boot).
  - config-manager image: real multi-stage build — production stage copies
    the venv and oca-locator checkout from the builder instead of
    re-installing git/docker.io and re-cloning (~300 MB smaller);
    `netflix_oca_locator` is now installed into the venv the app actually
    runs from. Docker packages dropped entirely (only the Python docker
    SDK is used).
  - Pinned floating images: `linuxserver/smokeping:version-2.9.0-r0`,
    `cloudflare/cloudflared:2026.5.0`; removed obsolete compose `version:`
    key; `PUID`/`PGID` are now `${PUID:-1000}`-style overridable in all
    editions; InfluxDB gained a compose healthcheck.
  - Pro: `mcp-server` service wired in behind the `mcp` profile
    (127.0.0.1:8090); CI now validates `COMPOSE_PROFILES=mcp`.
  - `setup.sh` scripts use `docker compose` (v2) instead of `docker-compose`.

## [2.2.0] — 2026-07-30

Web-admin hardening and UX overhaul (Sprints 4–5).

### Security (PR #14)
- Password-hash auth (`WEB_ADMIN_PASSWORD_HASH`) with constant-time
  plaintext fallback; no hardcoded default credentials; `SECRET_KEY`
  required; CSRF protection on every state-changing request; per-IP
  login lockout; open-redirect fix; Cloudflare token out of URL query.
- Single authenticated data path: web-admin only talks to the
  config-manager API (Bearer token); YAML fallback dual-writes and the
  silently-broken dashboard delete removed; Docker socket removed from
  web-admin entirely; scheduler runs exactly once.
- API token auth active end-to-end on the Pro deployment.

### UX (PR #16)
- Bootstrap + icons vendored locally — the UI works during the exact
  internet outages it exists to diagnose.
- Toast/confirm/fetch helpers replace all alert()/confirm() dialogs.
- Targets: search, category/status filters, inactive targets visible
  with activate toggle, edit dialog, bulk delete, CSV export.
- Add-target: identical client/server name validation, inline errors,
  IPv6-aware hostname checks, auto-regeneration feedback.
- Dashboard: full target lists with per-target SmokePing and Grafana
  links; shows Database/YAML mode.
- Top Sites: loading-modal deadlock fixed, honest selection counters,
  review-before-update, real CrUX per-country lists; broken countries
  page removed.

## [2.1.0] — 2026-07-29

Modernization phase 1 (Sprints 1–3, 6 + CI). PostgreSQL becomes the real
source of truth and the Grafana/exporter pipeline is made trustworthy.

### Added
- **CPE microcut detection** (PR #7): hourly traceroute discovery of the
  ISP CPE (IPv4+IPv6), 5 pps windowed probing, `cpe_latency` measurement
  in InfluxDB, and a dedicated Grafana dashboard.
- **CI pipeline** (PRs #8, #9): ruff critical rules, per-edition
  `docker compose config` validation (incl. ClickHouse overlay), shell
  syntax checks, Grafana dashboard/provisioning validation with
  duplicate-uid detection, auto-discovered per-module pytest suites, and
  Docker image build checks.
- **API auth** (PR #10): optional shared-token auth on the config-manager
  API (`CONFIG_API_TOKEN`, Bearer / X-API-Token).
- First real test suites: config-manager (32), smokeping-exporters (30).

### Changed
- **PostgreSQL activated as the configuration source of truth** (PR #10):
  idempotent YAML→DB migration runs at startup; YAML becomes
  import/export. Config generation is atomic, file-locked, in-process
  (the subprocess fork-chain "zombie generator" is gone), and fully
  data-driven (hardcoded big-name template blocks removed). Gunicorn
  replaces the Flask dev server. FPing6 seeded (fixes IPv6 target 500s).
- **Grafana overhaul** (PR #11): single dashboard provider
  (`foldersFromFilesStructure`), dashboards organized into folders,
  entrypoint rewritten with proper signal handling (`exec`), Dockerfile
  HEALTHCHECK, dead `$status` variable and user-specific exclusions
  removed, CPE microcut dashboard finished (target variable, thresholds,
  proper panel options).
- **Exporter rewrite** (PR #11): `rrd2influx.py` now fetches incrementally
  since the last exported timestamp with downtime backfill (≤24h) and
  writes **loss as a ratio 0–1** (was a raw lost-ping count that rendered
  1 lost ping as 100%). Historical points keep the old scale.

### Fixed
- **6-week outage root cause** (PR #7): no `restart:` policy on any core
  Pro service — a reboot left the whole stack down. All services now
  `restart: unless-stopped`.
- **All 8 InfluxDB dashboards rendered empty** (PR #7): queries targeted
  bucket `latency`; the deployment writes to `smokeping` (67 refs fixed).
- Atomic writes preserved root ownership on host bind mounts, breaking
  git and host tooling (PR #12); DB reads had no `order_by`, making
  generated config nondeterministic (PR #12).
- Env template drift (PR #8): `TIMEZONE`→`TZ` (timezone config was a
  silent no-op in basic/standard), wrong `POSTGRES_DB` in standard.

### Removed
- ~2,300 lines of committed runtime artifacts (PR #8): YAML backups,
  `cookies.txt`, OCA dumps, orphaned modules (`api_docs.py`, stub
  compose file, broken test scripts). Backup retention added so they
  cannot accumulate again.

### Deprecated
- ClickHouse support marked experimental/unmaintained (parked; known
  datasource-plugin and schema mismatches documented in README).

## [2.0.0] — historical

Pre-modernization state: three Docker editions (Basic/Standard/Pro),
config-manager + web-admin + Grafana/InfluxDB stack as of PR #6.
