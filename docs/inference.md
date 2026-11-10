# Congestion and degradation detectors (experimental)

!!! warning "Experimental"
    These detectors are off by default, are not in the alert path, and may
    change or go away without a deprecation period. Their results shade the
    Target Detail dashboard; nothing pages anyone. Validate them against
    your own connection before you rely on them.

The `inference` service runs two detectors on every ICMP target, once an
hour. They answer different questions and use different signals, so they
are kept apart: one never feeds the other.

| | Persistent congestion | Loss degradation |
|---|---|---|
| Question | Did a queue on the path stay built up? | Did the target start losing packets, and keep losing them? |
| Signal | The minimum RTT, then the RTT distribution | Packet loss |
| Method | [Jitterbug](https://github.com/estcarisimo/jitterbug), as published | Bayesian change points on loss |
| Window | the last 14 days | the last 7 days |
| InfluxDB measurement | `persistent_congestion` | `loss_degradation` |
| Shade on Target Detail | orange | red |

## Persistent congestion: Jitterbug

[Jitterbug](https://github.com/estcarisimo/jitterbug) (Carisimo et al.,
*Jitterbug: A New Framework for Jitter-Based Congestion Inference*,
PAM 2022) is used as its package publishes it, `jitterbug-inference` with
the `bcp` extra, in the paper's configuration: Bayesian change-point
detection (BCP) and the Kolmogorov-Smirnov jitter test. Every other option
stays at its default.

1. The RTTs are binned into 15-minute minimums, which removes most of the
   queueing noise.
2. BCP finds the change points of that minimum.
3. A period is a candidate when its minimum rose over the previous one's by
   more than 0.5 ms (the latency jump).
4. The KS test compares the RTT distributions on either side.
5. A period is congested when both say so.

What it finds is a queue that stays built up: the floor of the RTT rises and
stays risen. A path whose minimum never moves gets no congestion period,
however jittery or lossy it is; that is a correct answer, not a miss. Each
period carries Jitterbug's confidence and the size of the latency jump.

SmokePing stores the pings of each cycle sorted, so the order in which they
were sent is lost. The KS test compares distributions and does not depend on
that order, which is one more reason the paper's configuration is the one
used here.

### Anycast destinations

Jitterbug assumes the path to a target stays put, so that a floor which
rises and stays risen is a queue. A destination behind a multi-tenant CDN
breaks that assumption: the address you ping answers from whichever edge
the CDN's routing picks (anycast: Cloudflare, Fastly, Imperva...) or its DNS
picks (Akamai, CloudFront), and that edge can change while the name, the
address and the AS path do not. A farther edge raises the floor exactly as
a queue would, and Jitterbug, as published, reports it as congestion.

The seed's `uba_ar` is such a target: `uba.ar` is fronted by Imperva's
anycast CDN, so pinging it measures the nearest Imperva edge, a few
milliseconds away, not the university in Buenos Aires (`ns1.uba.ar`, on the
university's own network, is the real path there). Its 15-minute minimum
RTT sits on three stable modes, 6–8, 15–21 and 30–40 ms, as the answering
edge changes. On the reference
Pi, every one of the 12 congestion periods Jitterbug reported on it up to
2026-11-09 was the edge changing or flapping between edges: clean floor
shifts to 18–25 ms, spans of 14 to 71 hours in which only how often the
farther edge answered changed, an artifact over gaps when the monitor was
deaf, a flicker. None was a standing queue. On a destination served from
its own network (Facebook), the same detector over the same weeks reported
five plausible evening queues.

So a congestion period on a CDN destination is marked, not dropped. The
mark is a reading, not a measurement: it was established on one target
(`uba_ar`), and on another CDN it says what is likelier, not what happened;
a real queue on the way to a CDN target is marked all the same.

- **Target Detail** keeps the orange shade, and the annotation says
  *edge change likely (anycast/CDN destination)*: the floor rose, probably
  because a farther edge started answering, not because a queue built up.
- **Connectivity & Quality** draws the period pale orange on its own row,
  *edge change likely (anycast/CDN destination)*, beside the target's
  ordinary congestion row. The *Persistent congestion periods* count still
  includes it.

The period is still detected and written as before (`persistent_congestion`,
the same fields): the mark is the dashboards' reading of it, from the
target's delivery kind, and it does not change what Jitterbug finds. Read
such a period as a route change, and check it against the latency panels
(a floor that jumps between a few fixed levels is an edge moving) and the
target's [path](path.md) before calling it congestion.

**Which targets are marked.** Every target whose delivery kind is `cdn`:
the exporter looks up the AS each target's address answers from and
classifies it ([Service taxonomy, Delivery](taxonomy.md#delivery)), and
writes the kind on `target_labels`, which the dashboards read. There is
nothing to set: a target of your own on Cloudflare, Fastly, Akamai,
CloudFront or Imperva is marked as soon as its owner is looked up, and the
mark applies to its earlier periods too, since it is read when the
dashboard draws them. The list of CDN networks is `KNOWN` in
`shared/modules/common/asn.py` plus the `cdn` rows of
`common/data/asn_roles.csv`. Two consequences of reading the kind rather
than storing a flag:

- The mark follows the target's current kind: a target that moved off a
  CDN loses it on its older periods too.
- An anycast address outside a CDN (a public DNS resolver, a content
  network's own anycast) is not marked. Automatic detection of a floor
  that sits on a few fixed levels was considered and left out until a
  second case shows it is needed.

## Loss degradation: change points on packet loss

Loss can come from the radio, a faulty line, an overloaded router or a
policer, not only from a queue that stays full, so it is a separate signal
with its own detector rather than evidence for Jitterbug.

1. Each probe cycle's loss is averaged into 15-minute bins, the bin
   Jitterbug uses for the minimum RTT.
2. Bayesian change-point detection runs on that series exactly as Jitterbug
   runs it on the minimum (a constant prior, a Student-t likelihood, a change
   point where the probability exceeds 0.25).
3. The bins between two change points are a segment. A segment is degraded
   when it lasts at least 30 minutes and its mean loss is at least 2 %.
   Adjacent degraded segments are reported as one.

With 10 pings a cycle and 3 cycles a bin, a single lost ping reads 3.3 % in
its bin. Change-point detection may well cut a segment around it; the
30-minute floor is what keeps one lost ping from being a degradation.

### Bursts: one bin of loss read as hours

A segment's mean pools all of its bins, and BCP cuts segments where the
series changes, not where the loss starts and stops. So one 15-minute bin
at 31 % between clean ones can end up in a segment of 14 bins and read as
"3.5 h at 2.2 %"; a bin at 15 % beside a clean one reads as "30 min at
7.5 %". Both pass the floor, both are real loss, and neither lasted longer
than 15 minutes: the shown duration is the segment's, not the problem's.
On the reference Pi, 7 of the 22 degradation periods read from mid-October
to 2026-11-09 were such short bursts on one destination.

The detector is unchanged (the decision of 2026-11-09: mark bursts, do not
filter them). Each degradation period now also says how its loss was
spread:

- `lossy_bins`: how many of its bins had any loss (mean loss above 0);
- `peak_pct`: its highest bin's mean loss, and `peak_start` (epoch
  seconds) the start of that bin — the earliest one on a tie;
- `burst`: 1 when exactly one bin had loss (`lossy_bins == 1`), else 0.
  The rule reads the period as reported, merged or not: with the default
  floors adjacent segments are merged only when each had loss, so a merged
  period has at least two lossy bins and is not a burst.

One lossy bin is the whole rule. Every short burst of the read above had
exactly one; the gateway's longer periods had five or more. A looser rule
(two lossy bins with most of the loss in one) was considered and not
needed. Target Detail shades a burst over its bin only, with the text
"Loss burst (experimental) on UBA: 15 min at 30.7 % (inside a 3.5 h
segment averaging 2.2 %)"; any other degradation keeps its segment's span
and adds "peak 9 % at 14:45 UTC" to its text (always UTC: the query does
not depend on the dashboard's time zone, so a zone it cannot resolve
never hides the shading). Connectivity & Quality draws a
burst purple over its bin. Periods written before these fields existed
have none and read as before. The pass report (`status.json`) counts
`bursts` per target beside `monitor_deaf`.

Reading the shading across targets says where the loss is. The same period
on every destination and on the CPE target is the local link or the Pi;
on every destination but not on the CPE, it is beyond the first hop; on one
destination only, it is that destination.

## Shared degradation: the whole connection

The two detectors above answer for one destination each. Five destinations
with a period each are not one shared period, so once per pass the module
also asks the house-level question: did loss rise for the whole connection,
not one service?

1. Every ICMP target's loss is binned into 15 minutes over the loss window,
   whatever `INFERENCE_CATEGORIES` selects: that setting picks whom the
   per-target detectors analyze; the shared question is about all of them.
2. The monitor's own bins are left out: the radio was deaf, `smoking-pi
   upgrade` was restarting the stack, or the ISP gateway lost about as much
   as the destinations did (two bins running, or a fifth on its own). That
   is the first hop or closer, which the alerter's verdict already reports
   as such. One bin of margin on each side. An upgrade is read from the
   `upgrade` markers the command writes (`phase` = `start` and `end`); a
   start with no end within 30 minutes closes 30 minutes after it, and with
   no markers there are no upgrade bins. Upgrades used to read as first-hop
   cuts: 4–6 % on every destination and the gateway for the 2026-10-15
   upgrade on the reference Pi, 66 % for the 2026-10-18 one.
3. Only steady targets take part: one that reported in at least half the
   window's bins (a slot that comes and goes never weighs) and lost nothing
   in at least half of them (a host that rate-limits ICMP and always loses a
   third is not the network). A target that lost everything for six hours
   running is out while it stays dead.
4. Lost over sent across those targets gives one series, per bin, when at
   least 60 % of them (and at least eight) reported.
5. The degradation detector runs on that series, unchanged: 30 minutes or
   more averaging 2 % or more.

Each period is then qualified by **who runs the infrastructure** behind the
targets that lost. The module looks up each target's host (as the exporter
records it), its address and CNAME, and the origin AS from CAIDA's daily
RouteViews prefix-to-AS table, downloaded into the state volume and looked
up locally with [originet](https://github.com/estcarisimo/originet), with
the network's name from CAIDA's AS Organizations dataset (see
[The path to each destination](path.md#where-the-networks-come-from)); a
short curated table maps well-known networks to their operator and kind: a multi-tenant CDN
(Fastly, Cloudflare, Akamai, CloudFront, Imperva…), a content provider's own
network (Google, Meta, Apple, Microsoft), a cache embedded near the access
network (Netflix), cloud or origin servers, or an academic network. Any
other network is its own operator. Three names on one CDN are one operator.

- **shared**: at least a third of the voting operators lost 1 % or more in
  the period, and they are of at least two kinds.
- **dominated**: otherwise; `dominant` names the target that carried most of
  the loss (a destination going dead, one CDN having a bad evening).

The kinds qualify a period; they do not weight the series. Weighting each
operator equally lets an operator with a single target swing the result.

On the reference Pi, replayed over 52 days, this marked every evening of the
August to October loss as shared (four to six kinds of infrastructure each
time), called the periods one dead name carried `dominated`, and found
nothing on a CDN edge's route changes, the radio hangs or a quiet week.
Across 100 random sets of ten targets, the strong evenings were found by
99–100 % of them and a false period by 0–2 %: what matters is how many kinds
of infrastructure the targets reach, not which targets they are.

Results go to `shared_degradation` (one point per period, no target tag;
fields `scope`, `mean_loss_pct`, `coverage`, `voters`, `operators`,
`lossy_operator_share`, `kinds`, `dominant`, `dominant_share`, and the
attribution below).
Connectivity & Quality draws them in its episode timeline as the *Whole
connection* rows: dark red when shared, pale when one destination carried
it. With fewer than eight steady targets the module says nothing and leaves
earlier periods as they were.

The lookups are DNS queries, a few per target, cached in the state volume
(addresses for a day, owners for a week, a failure for an hour), the same
service the DNS observer and the resolver probe use. No probe is added. A
target whose lookups have not succeeded yet counts as no operator, so in
the first passes after the module starts, before every target is tagged
(64 lookups a pass), a period may read `dominated` and later `shared`.

**Who lost.** Each period also carries its attribution, read off the same
bins the detector used, as three short strings: `by_operator`, the three
operators that lost most (mean loss over the period, e.g.
`Fastly 9.0 %, Google 6.0 %, Meta 4.0 %`); `by_kind`, every kind of
infrastructure's mean loss; and `diverged`, the voting targets that stayed
out of it (lost at most a tenth of the pooled loss; five at most, then
`+N more`). Connectivity & Quality lists them under the timeline, in
*Whole connection: who lost in each shared loss period*. Untagged targets
are left out of the operators and kinds, as in the qualification.

## Shared path segments: a hypothesis of where

With the [path meter](path.md) on, each `shared` period also gets a
hypothesis of **where on the path** a common cause would sit: the router,
network (AS) or exchange point the destinations that lost cross and the
ones that did not, mostly, do not. It is a hypothesis, never a finding:
destinations that lost and cross one router are consistent with that
router, they do not show it. Nothing is probed for it; it reads the
periods above and the traces the path meter already stored.

1. **Who lost.** The period's voters by destination (one path counts once;
   a destination with several voters takes their mean loss over the period):
   *lost* at 1 % or more (the qualification's threshold), *did not* under
   0.5 %, and *in between*, which counts on neither side, so a destination
   at the threshold cannot tip the hypothesis.
2. **Their paths.** Each destination's last hourly trace at or before the
   period's end and no more than three hours before its start. The rounds
   the path meter traces [during an episode](path.md#during-a-loss-episode)
   are left out here: taken while the loss lasts, they lose hops to it, and
   they trace the destinations that lost first, so the two sides would be
   read from different evidence. They answer another question, where the
   paths go silent, read on Target Detail. One with
   none is *untraced*: it lowers the traced share, not the comparison. So
   does one whose path has no public hop (the gateway, a host on the LAN):
   it crosses nothing a cause beyond the house would sit on, and would make
   every remote segment look clean.
3. **Segments.** Every router a trace answered from, by its address; a
   private address (the home router, the access network's `10.x` routers)
   only names a router within the network that uses it, so it is named by
   the public hop before it (`10.248.2.13 after 136.25.220.1`). Every AS of
   the trace's AS path, and every exchange point a hop answered from. A hop
   that did not answer is no segment.
4. **What a trace cannot show.** A destination that does not cross a
   segment may still cross it unseen when its trace was silent at a TTL
   where the other paths saw that router, or, for an AS or an exchange, never reached the
   destination. Such a destination counts *against* the hypothesis.
5. **Scoring.** For each segment, the share of the destinations that lost
   crossing it, minus the share of the clean ones crossing it (or possibly
   crossing it). A segment every destination crosses, like the house's
   first hops or its provider, scores nothing: it is common to both sides.
   On a tie, a network goes before an exchange before a router (a network's
   routers may share the load).
6. **Chance.** The best of many segments is picked, and with 1–3 % loss
   which destinations cross the 1 % line is partly sampling noise. So each
   segment also carries `p_value`: the chance that as many of the
   destinations through it would have lost if losing had nothing to do
   with the path (hypergeometric; the clean ones that may cross it unseen
   count as crossing). Four of five lossy destinations sharing a network
   that none of three clean ones cross happens 7 % of the time by chance
   alone, and is no hypothesis.
7. **Confidence.** `strong`: 80 % or more of those that lost cross it and
   20 % or less of the clean ones do, with at least three on each side,
   60 % of the destinations traced and a chance under 1 %. `weak`: a gap of
   at least one half between the two shares, with at least three on each
   side and a chance under 5 %. The chance is per segment, not corrected
   for how many were tried (they overlap: a router lies inside its AS), so
   even a `strong` one is a lead to check. Otherwise `none`: nothing sets the destinations that lost apart. That is also what
   a loss everyone shared reads as: with fewer than three clean
   destinations there is nothing to contrast, and the text says so.

Every row also says what each side shares, the first hops all its traces
agree on (at least half answering, all with the same address) and the ASes
they all cross: "9 lost, 20 did not (29 traced). The 9 that lost share hops
1–5 and AS19165 AS16591 AS6939; the 20 that did not share hops 1–5 and
AS19165 AS16591. Hypothesis (strong): AS6939 (HURRICANE…), crossed by 9 of
9 that lost and 0 of 20 that did not."

Results go to `shared_segment`, one point per shared period that at least
one destination was traced for (no target tag): `confidence`, `segment`,
`segment_kind` (`as`, `ixp`, `router` or `none`), `hypothesis` (the text
above), `affected`, `unaffected`, `mild`, `traced`, `untraced`, `coverage`
(the traced share), `affected_through`, `unaffected_through`, `recall` and
`spill` (the two shares), `p_value`, `common_hops`/`common_as` and
`clean_common_hops`/`clean_common_as`, `path_changed` (destinations whose
path changed within the window read), and `alternatives` (the next two
segments). Connectivity & Quality lists them under *who lost*, in *Whole
connection: the path segment the destinations that lost share, a
hypothesis*. A `dominated` period is one destination's, and its own path is
its hypothesis: it gets no row.

Replayed on the staging Pi (17 steady destinations) over its 2, 5 and 6
October nights, the first shared nights it measured, with the paths it
first recorded three weeks later (the path meter did not run before
2026-10-22; the paths past the access network were stable since): every
destination lost 1–8 %, so it said `none` each night, "the 17 that lost
share hops 1–5 and AS19165 AS16591", the house's provider. That is what
those nights were: loss past the gateway on every destination, so on no
single route beyond the provider. Over the following week, with no shared
period, it wrote nothing.

## Shared persistent congestion: Jitterbug on an aggregate input

The same question for queues: did a floor rise and stay up for the whole
connection, not one route? Jitterbug runs **as published and unchanged**
(the same call as per target: BCP + KS), once per pass, on one stream built
across destinations:

1. Each steady target's pings (the voters of the shared degradation pass)
   minus that target's own floor: the 5th percentile of its 15-minute
   minimum RTT over the trailing 7 days. A destination 2 ms away and one
   170 ms away then share a scale, and a route change stays that route's.
2. Per 5-minute probe cycle, the ten deciles (5th to 95th percentile) of
   those relative pings, when at least eight voters answered. Jitterbug
   reads the ten values as the cycle's samples, placed at the distance of a
   typical voter: the median of the voters' floors, one constant per pass
   (`base_ms` in the status; 6.95 ms on the staging Pi). Jitterbug accepts
   only positive RTTs, and where a series sits is not neutral to its BCP:
   the Student-t likelihood's prior is centered on 0, and the same 3 ms
   step measured two change points at +10 ms and none at +100 ms. The base
   is read off the data, not tuned, so the stream looks like what Jitterbug
   would see on this connection's typical destination; the offline
   evaluation below used +10 ms on a connection whose floors are mostly
   under 15 ms. A sample still not positive (a route that got much
   shorter) is dropped and counted in the status (`dropped`).
3. The monitor's bins (the radio deaf, an upgrade, the gateway losing with
   the destinations) are left out, exactly as in the shared degradation
   pass.

### Where the stream sits (experimental; decided 2026-10-20)

The stream stays at the **median voter floor**, about 7 ms on the staging
Pi, and this is a deliberate choice, not an oversight. Jitterbug's BCP is
not shift-invariant: its Student-t likelihood has its prior centered on 0,
so where a series sits changes what it finds. Measured on one
synthetic series with a 3 ms step, shifted by a constant:

| Series offset | Change points found |
| --- | --- |
| +10 ms | 2 |
| +100 ms | 0 |
| +1000 ms | 0 |

Any offset is therefore a scale choice. Feeding Jitterbug the relative
values alone is not possible (it refuses RTTs that are not positive), and a
large constant would hide steps of a few milliseconds. The median voter
floor is read off the data, constant within a pass, and makes the stream
look like what Jitterbug would see on this connection's typical
destination. Both detectors stay as published; it is the input that is new, and the
whole row is experimental.

The input is new; the method is not. No parameter is tuned, and loss does
not feed it: evaluated offline on 52 days of the reference Pi, it found **no** shared
persistent congestion. The evenings of shared loss raised the median
relative floor by 0.25–0.55 ms, less than what Jitterbug calls a jump, so an
empty row is the expected answer on a connection whose trouble is loss, and
shared degradation is the signal for that.

The stream is built incrementally and kept in the state volume
(`aggregate.csv`, about 4 000 rows): each pass adds the cycles since the
last one, so it never re-reads two weeks of raw pings (see Cost). A cycle
is written once its 15-minute bin and the two after it are complete, since
the monitor rule looks two bins ahead, and it keeps the voters of the pass
that wrote it. A row of the file that does not read as numbers is dropped. The first pass fills the loss window (7 days, the span whose
monitor bins are known); the stream reaches the congestion window's 14 days
a week later. Deleting the volume starts it over; periods written before
the restarted stream's first cycle are kept.

Results go to `shared_congestion` (one point per period, no target tag;
fields `end`, `duration_s`, `confidence`, `jump_ms`, `ks_p`, `coverage`).
Connectivity & Quality draws it as the orange-dark *Whole connection* row,
labeled "Jitterbug (as published) on an aggregate input — experimental".

## What a period carries besides its detection

The detectors run on the raw series, as published. Two things are read off
other evidence and written beside each period, never fed back into it
(a degradation period also carries how its loss was spread, `lossy_bins`,
`peak_pct`, `peak_start` and `burst`: see *Bursts* above):

- **`class`: `loss` or `monitor_deaf`** (degradation only). A radio hang
  records 100 % loss on every destination for hours, and BCP on loss then
  finds a "degradation" of every target that is really the monitor being
  deaf — 22 of the first 51 periods on the reference Pi were this. A
  15-minute bin is deaf by the alerter's own test (`common/microcuts.py`:
  the uplink interface was not associated, or its received-packet counter
  did not move, for at least half the bin). The period is labeled
  `monitor_deaf` when at least half of its *loss* fell in deaf bins:
  `deaf_loss_share` is the sum of the period's per-bin mean loss (the
  series the detector segments) over its deaf bins, divided by the sum over
  all its bins. It is the share of the loss and not of the bins because the
  watchdog now ends a radio hang in 11–12 minutes, so a hang is one deaf
  bin, and hours later the change-point segmentation merges that bin with
  the clean hours around it: the second read of the reference Pi found 7
  of the 11 periods produced by two short hangs grown to 25–31 bins, one of
  them deaf (a bin share of 0.03–0.04), labeled ordinary loss of 6–8 h
  although every lost ping was in the deaf bin. By loss they read 1.00, and
  every other period 0.00. `deaf_share`, the share of the period's bins
  that were deaf, is still written beside it and means what it did; a
  period with no loss at all (a detected one always has some) is classed
  by it. The trade-off: a deaf bin carries up to 100 % loss, so a long, mild
  real degradation that the segmentation merges with one is labeled the
  monitor's when the deaf bin outweighs it (40 bins at 2.5 % beside one bin
  at 100 % read 0.5). `deaf_share` and `mean_loss_pct` beside the class
  show such a case. Target Detail shades `monitor_deaf` periods gray
  under *Monitor deaf (experimental)* and says so in the text; Connectivity
  & Quality draws them gray on the timeline and leaves them out of the
  degradation count. A wired host never has a deaf period.
- **`coverage`**: the share of the period's bins that had data — for a
  congestion period, bins with at least one answered ping (what Jitterbug
  reads; a bin of total loss is empty to it), for a degradation period,
  bins with at least one probe cycle (loss is recorded even when nothing
  answers). A congestion period that spans a gap of hours (a hang, a
  reboot) reads as such — "coverage 0.4" in its Target Detail annotation
  and Connectivity & Quality tooltip ("data in 40 % of the span" in a
  degradation's annotation) —
  instead of looking like a solid span; the first read of the reference Pi
  found a 49-hour period of a 3.7 ms jump that straddled two hangs.
  The deaf samples come from the same query the alerter uses, capped at
  20 000 rows (about 55 hours of deaf time at the default 10 s cadence);
  past that the service logs a warning and the newest deaf periods may read
  as loss.

## What a pass corrects and what it keeps

A period, once written, stays. Every pass rewrites what it finds well
inside its window, so a period found again is rewritten, not duplicated, a
start that moved by a bin or two moves with it, and one no longer found
disappears; but nothing is deleted or written in the window's first
**3 days for congestion** (per target and shared) and first **3 hours for
degradation** (per target and shared, and the path segment read for each
shared period). There the detectors lose what they found: Jitterbug needs
a stretch before a period to see the floor jump against, and BCP reports a
degradation whose start reaches the window's first bin as cut by the
window. A period the detector now places there keeps the record an
earlier pass wrote while it was comfortably inside the window, with that
pass's end and numbers; a start that moves across that line keeps its
record too, never a second one. So a period leaves the window as it was
last corrected and stays in InfluxDB, and the dashboards' 30-day ranges
show 30 days of periods from this change on. The two lengths come from
replaying the module's own code hourly over the reference Pi's raw pings
around every period that was erased: Jitterbug misses a congestion period
on and off until its start is up to 56 hours past the window's start, and
BCP loses a degradation in the window's first bin and, once, an hour in.
A window pinned by `INFERENCE_SINCE` (or Jitterbug's aggregate stream while
it is younger than 14 days) does not move until it reaches its full
length, so until then every period after its start is corrected; once it
slides it settles like the others.

Before this change (v2.52.1 and earlier) each pass deleted its whole
window and rewrote it, so a period was erased as soon as its start came
close enough to the window's start for the detector to lose it: on the
reference Pi, 12 of the 19 congestion periods and at least 8 degradation
periods written since October were gone, and the dashboards' longer ranges
showed at most the 14 (congestion) or ~7 (degradation) days of the window.
That history is lost; the raw pings are still there to replay it from.

## Pass history

Every pass writes one `inference_pass` point: `seconds`, `targets`,
`errors`, `congestion_periods`, `degradation_periods`, `shared_periods`,
`shared_congestion_periods`. The
[Diagnostics dashboard](diagnostics.md) (Advanced folder) charts the seconds
and failures per pass; `status.json` keeps only the last pass, and the
container's log does not survive an upgrade, so this is the record of what a
pass costs over time.

## Turning it on

Pro edition, InfluxDB backend:

```bash
sudo smoking-pi enable inference
```

(the other services stay as they are). The first pass starts two
minutes after the container. Then open **Target Detail** for a target: the
two annotations, *Persistent congestion (Jitterbug, experimental)* and
*Loss degradation (experimental)*, shade the periods found, and hovering a
region shows its numbers; on a CDN destination the congestion annotation
says *edge change likely (anycast/CDN destination)* ([Anycast
destinations](#anycast-destinations)). They can be switched off from the
dashboard's annotation toggles. Another, *Whole connection loss
(experimental)*, shades the house's [shared degradation](#shared-degradation-the-whole-connection)
periods on every target, so a target's own period is read inside or
outside one ([Target Detail](target-detail.md#what-the-inference-module-marks-on-the-charts)).
The [Connectivity & Quality](connectivity-quality.md)
dashboard draws every target's periods on one timeline, under the loss and
latency they are about, with each period's numbers in its tooltip. Since
periods are kept once their start leaves the corrected part of the window
(see *What a pass corrects and what it keeps*), the dashboards' longer
ranges show the history accumulated since that change (2026-11-09), not
only the last 14 days the detectors re-read.

`docker exec <project>-inference-1 python status.py` prints the last pass:
per target, the change points and periods found, and how long it took.

## Settings

All optional; empty means the default.

| Variable | Default | Meaning |
|---|---|---|
| `INFERENCE_INTERVAL` | `3600` | Seconds between passes |
| `INFERENCE_CONGESTION_DAYS` | `14` | Jitterbug's window, close to the 16 days of the paper's dataset |
| `INFERENCE_DEGRADATION_DAYS` | `7` | The loss window |
| `INFERENCE_SINCE` | — | `YYYY-MM-DD`: never look before this date. Set it after moving the Pi or changing provider: the old connection's history is not this one's baseline |
| `INFERENCE_CATEGORIES` | `topsites,custom,cpe` | Which ICMP targets, by InfluxDB category tag. Netflix OCAs (the appliance behind each one changes at the nightly refresh, which would read as a change point; `target_host` records when) and the DNS wizard's targets are left out by default |
| `INFERENCE_TARGETS` | — | Exactly these targets instead (comma-separated) |
| `INFERENCE_DEGRADATION_MIN_MINUTES` | `30` | Shortest degradation |
| `INFERENCE_DEGRADATION_LOSS_PCT` | `2` | Lowest mean loss of a degradation |

## Cost

Change-point detection grows with the square of the number of bins, which is
why the windows are bounded. On a laptop, one target costs about 1.5 s for
Jitterbug over 14 days and under 1 s for the loss over 7 days; expect several
times that on a Raspberry Pi, every hour, for each selected target. The
service runs at a lower CPU priority and is limited to one core, so the
measurements always come first. It only reads and writes InfluxDB; it sends
nothing to the network.

Each pass corrects the periods inside its window and keeps the ones near
its start (see [What a pass corrects and what it keeps](#what-a-pass-corrects-and-what-it-keeps)).
If a pass fails after clearing what it corrects, that part of the window
shows nothing until the next pass. `status.py` reports the time of the last
pass without errors (`last_ok`).


The shared detector adds one change-point run on one series per pass, plus
the DNS lookups above, which are cached. Its inputs are closed 15-minute
bins, and a bin that closed does not change, so they are kept in the state
volume too (`loss.csv` and `deaf.csv`, each with a `.json` beside it): a
pass reads only the last hour of loss bins and deaf samples and keeps the
rest, and reads the whole 7-day window again once a day (and on the first
pass, or when the window grew) to pick up points that arrived late.
Measured on the staging Pi (~23 ICMP targets, 672 bins, the pass replayed
in the running container):

| Per pass | Before | After |
| --- | --- | --- |
| Deaf samples (the alerter's query: a pivot InfluxDB cannot push down) | 4.5 s | 0.15 s |
| Loss and probe-cycle bins, hosts | 1.0 s | 0.15 s |
| Building the series | 0.8 s | 0.45 s |
| The change-point run itself | 3.5 s | 3.2 s |
| **In all** | **≈ 10 s** | **≈ 4 s** |

The first pass after a start still reads the whole window (and loads
PyTorch): about 20 s once. The cached and the full reads gave the same
voters, bins, monitor bins and periods, and the same 72 deaf bins over 30
and 60 days.

Jitterbug on the aggregate input was measured on a staging Pi (Pi 4, 4 GB,
~23 ICMP targets). Re-reading 14 days of every target's raw pings each pass
would cost 22 s and 662 MB (831 000 rows), so about a minute and 2 GB with
the ~65 targets of the reference Pi: hence the incremental stream. Each
hourly pass reads one hour of raw pings and 7 days of 15-minute minimums
(one storage-engine query; 0.8 s in InfluxDB for 66 targets on the
reference Pi) and adds 12 cycles in about 1 s; Jitterbug on the 14-day
stream takes about 6 s, 7.3 s in all. The first pass after an upgrade
fills 7 days a day at a time: about 30 s once, at most ~390 MB.

## Proving the shared path on staging

The shared detectors and the path meter's episode rounds only write when
the whole connection loses, and none had by the second production read
(2026-11-09): shared degradation and shared segments since v2.44.0, episode
traces since v2.51.0, never a point on the reference Pi. Their unit tests
prove each step on fakes; this proves that a real evening gets from the
bins to the dashboards. A maintainer tool, for a **staging Pi only**:

```bash
shared/scripts/inject-shared-loss.sh --host stagingpi --keep
# ... screenshots of Connectivity & Quality ...
shared/scripts/inject-shared-loss.sh --host stagingpi --cleanup
```

Run it from a checkout of the version under test (on a package install,
`/opt/smoking-pi/shared/scripts/`), with the inference and path meter
profiles on. It runs `shared/modules/inference/inject.py` inside the
inference container (copying the checkout's in when the image's differs or
has none) and:

1. **Plans an evening.** Three hours (`--hours`), ending two hours before
   the bin cache's last pass, so the evening is a closed part of the cache
   (`loss.csv` in the state volume) and not the tail a pass re-reads from
   InfluxDB. Who loses: the destinations whose hourly paths cross one
   network or exchange point, chosen so the period qualifies as `shared`
   (a third of the voting operators, two kinds) and the segment scorer can
   tell them apart; the operator with most targets loses most, so the
   attribution has one clear leader; the levels are scaled until the pooled
   loss is 5 %. With no such segment, every voter loses. `--csv FILE` takes
   the evening from a file instead, one row per 15-minute bin and target
   (`bin,target,loss`, the bin's start as epoch seconds or an ISO time,
   loss a ratio 0–1), moved to the same place in the window; targets the
   staging Pi does not measure are skipped. The file is copied into the
   container for the run and removed when the script exits. The plan refuses
   an evening on the monitor's own bins (a deaf radio, an upgrade), with
   fewer than eight voters, one that would read `dominated`, or one where
   shared points already exist (cleanup would delete them).
2. **Injects it** into the cache, after copying `loss.csv`/`loss.json`
   aside, and runs **one pass** as the service does (the shared detector and
   the segments; not the per-target detectors). The service's own hourly
   pass reads and saves the same cache with no lock, so the tool injects
   only when that pass is at least 25 minutes away (it says when, from the
   service's `status.json`), and cleanup waits for one that is running.
3. **Injects one cycle of widespread loss**: a `loss` point of 60 % for
   every target that reported in the last 15 minutes, the gateway excepted,
   at half a minute past a minute, a second no point holds. The path
   meter's episode check, which is the alerter's widespread rule, sees an
   episode under way within five minutes and traces it. It refuses while a
   real widespread run or episode traces are in the last hour, so the test's
   rounds are the only ones. The alerter is stopped meanwhile (it would
   alert), and started again once the cycle is deleted, also by
   `--cleanup` after a run that was killed.
4. **Verifies the three outputs** in InfluxDB against the plan: one
   `shared_degradation` period over the evening (within 30 minutes at each
   end) with the planned scope, the planned leader of `by_operator`, the
   planned `kinds` and a pooled loss within half to one and a half the
   planned; the `shared_segment` hypothesis at the period's start with the
   planned confidence and segment; a widespread run ending at the cycle's
   own step, and `trigger=episode` traces between the cycle and its
   deletion, each with its `episode`.
5. **Cleans up**: deletes the injected cycle, restores the cache, and
   deletes the `shared_degradation` and `shared_segment` points within an
   hour of the evening and the `trigger=episode` traces from the cycle to
   its deletion. When a service pass saved the cache meanwhile, its newer
   tail stays and only the evening's bins come back from the copy. A
   cleanup that fails says so and exits non-zero.
   `--keep` deletes the injected cycle only and leaves the evening and its
   outputs for screenshots (restored alone, the cache would let the next
   hourly pass correct the period away); `--cleanup` finishes later.

**The guard.** The script refuses unless the host's name contains
`staging`, `--host` repeats it exactly (type it: the reference Pi is
`smokingpi`, and a command copied to the wrong terminal stops there), and
the Docker daemon it talks to is this host's own (`docker info`), so a
`DOCKER_HOST` or a docker context pointing at another Pi stops it too.
`inject.py` refuses to run without `--staging` and a daemon name containing
`staging` in `INJECT_HOST`, which only the script passes; a bats suite
(`packaging/tests/inject-shared-loss.bats`) proves the refusals. Never run
it on a Pi whose history matters: the injected loss is indistinguishable
from measured loss until cleanup deletes it.

The assistant's automations read the same data: run it between two runs of
the hourly OpenClaw heartbeat (or after its last one of the day), and keep
`--keep` short.

**On the staging Pi, 2026-11-09 (v2.53.0, 22 voters),** the first run planted
the evening on the Equinix Internet Exchange Dallas: Apple, Wikimedia and
Imperva's `uba.ar` lost 29–44 % for three hours, a third of the nine voting
operators, three kinds. All three outputs came out as planned, the first
points either measurement had ever held:

- `shared_degradation`: one period, 20:45 to 23:45 UTC (the evening was
  21:00 to 00:00; the detector placed it one bin early), `shared`, pooled
  loss 4.5 %, `by_operator` *Apple 38.5 %, Imperva 33.9 %, WIKIMEDIA
  26.1 %*, kinds `cdn,content_network,other`;
- `shared_segment`: `strong`, *exchange Equinix Internet Exchange Dallas,
  crossed by 3 of 3 that lost and 0 of 19 that did not* (22 traced,
  p = 0.003);
- the widespread cycle read as 77 of 77 targets lossy, and the path meter's
  next round traced six destinations, `trigger=episode`, each with its
  `episode`.

![The injected evening on Connectivity & Quality: the Whole connection row
in the episode timeline, and the two Whole connection tables with their
first rows](img/inference-staging-injection.png)

The raw loss charts above them do not show the evening: it was injected
into the bin cache only, which is what the shared pass reads. That run also
found the plan blind to `upgrade` markers (an upgrade at 23:00 fell inside
the evening and the pass left those bins out); the plan now refuses an
evening on them, as it does one on a deaf radio. A second run took the
evening from a CSV (every voter losing 1.5–3 %, the five Fastly-fronted
ones 4–8 %, a synthetic file placed 19:30–22:30 UTC clear of the upgrades):
`shared`, `by_operator` led by *Fastly 6.7 %*, all four kinds, and a
`shared_segment` of confidence `none` (*22 lost, 0 did not*: everyone's
loss sets no path apart, as the real October nights read); five episode
traces; and the full cleanup left no point and, with no service pass in
between, the cache byte for byte as it was. In both runs the period started one bin before the evening: a
change point at bin *i* opens the new segment at bin *i*, as in Jitterbug
(see the degradation detector above), which is why the check allows two
bins at each end.

The path meter's own ledger (`episodes.json`) keeps the test's rounds for a
day, so `smoking-pi budget` counts their probes until they roll off.

## Limits

- **InfluxDB only.** With `TSDB_TYPE=clickhouse` the service stays idle and
  says so in its status.
- **ICMP only.** DNS response times are a different signal: a resolver under
  load answers more slowly without any queue on the path, and on a Pi the
  DNS probe's timer resolution used to be the kernel tick (4 ms on a
  250 Hz kernel), which left the minimum on a few fixed steps that
  Jitterbug's minimum cannot move between. The probe now reads dig at
  microsecond resolution (`dig-usec`, see [DNS probes](dns-probes.md)), but
  the history before that change is still quantized, so a window that
  spans it mixes the two.
- **Shared persistent congestion reads the aggregate input only.** It
  says whether the voters' floors rose together; a queue on one route is
  the per-target detector's. Its stream keeps the voters each cycle was
  written with, so a target that starts or stops voting changes the stream
  from then on, not backwards.
- **Upgrades are left out of the shared passes only.** A per-target
  degradation period that covers an upgrade still reads `loss`: its
  `class` names the radio (`monitor_deaf`), and an upgrade is not that.
- **Path segments are a hypothesis.** They compare paths, they do not test
  a router: a segment crossed by every destination that lost may be where
  the loss was, or merely on the way to every destination that was busy
  that evening. The path read is the destination's last trace at or before
  the period's end, taken up to three hours before its start; with hourly
  traces a route that moved during the period may be read as it was before
  (`path_changed` counts the destinations whose path changed within that
  window).
- **Not in the alert path.** Alerts and other ways of passing these results
  on come later, once each detector has been validated on real connections.
- The image is larger than the others: the detectors need PyTorch (the
  CPU-only build).
