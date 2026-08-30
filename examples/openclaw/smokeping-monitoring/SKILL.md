---
name: smokeping-monitoring
description: Answer any question about the home internet connection, network health, latency, packet loss, outages, microcuts, or "how is my connection / cómo está mi conexión / qué onda la red" using the smokeping MCP tools, which hold this Pi's continuous measurement history. ALSO use when the user names the monitoring host or project (smokingpi, smoking-pi, smokeping, "the Pi") and asks how things are. NOT for: running live ping/curl/speed tests in a shell — the measurement already exists and covers the past, which a live probe cannot.
---

# SmokePing Monitoring

You have tools from the `smokeping` MCP server covering a Raspberry Pi that has
been continuously measuring this home network for as long as it has been
running. Use them to answer questions about latency, loss, and outages, and to
manage what is monitored.

**Use the MCP tools, not the shell.** This host also gives you a terminal, and
it is tempting to answer "how is my internet?" with `ping`, `curl`, or a speed
test. Don't. A shell probe describes this one instant, is thrown away, competes
with the measurement this Pi is already taking, and will disagree with the
graphs the user is looking at. The tools answer from months of recorded
history — including the moment last night when the user noticed something and
nobody was watching.

The names on this host all sound alike — the machine, the project, and the MCP
server. If the user says "smokingpi", "smoking-pi", "smokeping", or "the Pi"
and asks how things are, they mean **this measurement history**, so reach for
these tools rather than inspecting the machine itself.

## Tune these four things before installing

This ships with one real deployment's values so it works as written rather than
as a fill-in-the-blanks form. Four of them are **yours to change**, and the
wrong value makes the agent confidently wrong rather than broken — which is
harder to notice:

1. **The host's names**, in the `description:` above and in the paragraph
   below. OpenClaw uses the description to decide whether to load this skill at
   all, so it must contain the words *you* use for the machine. Add your own
   ("the router box", "casa"), and add the language you ask in — the trigger
   phrases are matched, not translated.
2. **The CPE loss floor** under *Reading the numbers correctly* (10%, p99 ≈
   30%, "real cut" at 50%+). Every home gateway rate-limits ICMP differently.
   Read yours off a quiet week of `get_microcut_stats` and use that; a floor
   set too low turns normal into a nightly false alarm, and one set too high
   hides a failing line.
3. **The timezone** in the report rules ("pm CT"). Use the one the person
   reading the answers lives in, not the Pi's.
4. **The target names** used as examples (`CloudflareDNS`, `CPE_Gateway`,
   `Chicago_ORD_c122_1`) — these come from the sample `targets.yaml`. Swap in
   three of your own from `list_targets`, keeping one long ugly one: it is
   there to show that the name is a key to be quoted exactly, not prose.

Everything else — the false-alarm list, the report shape, the link rules — is
deployment-independent and should be left alone.

## Tools

**Reading**

- `system_status` — service health and target counts. Start here when someone
  asks an open question like "how's the network?"
- `get_latency_stats(target, hours)` — median latency and loss over a window.
- `get_loss_events(target, hours)` — discrete loss episodes rather than averages.
- `get_microcut_stats(hours)` — sub-second CPE dropouts, sampled far more
  finely than the 5-minute target probes.
- `list_targets` — what is currently monitored.

Responses carry a `links` object when deep links are configured: `graph`,
`per_ping_detail`, `compare_with_peers`, `edit`. Keys ending in `_tunnel` are
the same page reached from outside the home network — see *Links*, below.

**Writing** (confirm with the user in chat before calling)

- `add_target`, `remove_target`, `toggle_target` — change what is monitored.
- `apply_config` — regenerate SmokePing config and reload. Needed for changes
  to take effect.

## Reading the numbers correctly

**Loss units differ by measurement.** `latency` and `dns_latency` report loss
as a ratio 0–1; `cpe_latency` reports a percent 0–100. Do not compare them
directly or quote one as the other.

**Probes run every 5 minutes.** A single missed cycle is one data point, not a
trend. Do not describe a target as "down" on one bad sample — look for a run of
them. Microcut data is the exception: it samples every 10 seconds.

**The CPE has a permanent ICMP loss floor.** Home gateways rate-limit ICMP
replies, so the CPE shows steady single-digit loss with nothing wrong. On this
deployment the floor sits near 10% (p99 ≈ 30%). Only treat a CPE window as a
real cut when loss is far above that — roughly 50%+. *(Tunable #2 — these
three numbers are this gateway's, not a universal constant.)*

## Things that look broken but are not

Check these before reporting an outage; they are the usual false alarms:

- **Hosts that do not answer ICMP at all.** Some sites drop ping entirely and
  chart a flat 100% loss forever. A dead-flat line with no variance is a
  monitoring artifact, not an outage — real loss fluctuates. Bare `amazon.com`
  behaves this way; `www.amazon.com` responds normally.
- **IPv6 with no IPv6 service.** If the host has no global IPv6, IPv6 targets
  are automatically omitted from monitoring, so they should not appear at all.
  If you do see IPv6 targets at 100% loss, that means IPv6 was working and
  broke — worth reporting.
- **A perfectly constant value.** Any metric that is identical across every
  sample for hours is almost always a measurement bug rather than a network
  condition. Say so rather than inventing a network explanation.
- **Targets that exist to fail.** A deployment that has tested its alerting
  usually still carries the target it tested with — a deliberate blackhole
  address, flat at 100% forever. It is the alerting self-test, not the
  connection. Name it in the *ignored* line so the reader knows it was seen
  and dismissed, and never let it into the verdict.

## Answering well

Lead with the answer, then the evidence. "Everything looks normal — all
targets under 20 ms, no loss in the last 6 hours" beats a table nobody asked
for.

Quote real numbers with their window ("12% loss over the last hour"), never a
bare adjective. When a question is about a specific target, query that target
rather than summarising everything.

If the data does not support a conclusion, say what is missing instead of
guessing. "Only two samples since the restart — not enough to tell yet" is a
better answer than a confident wrong one.

These messages are usually read on a phone. Keep them short, skip the preamble,
and do not paste raw JSON.

## Write in the reader's language

**Answer in the language the person wrote in.** Everything in this file — the
template below, the section names, the example phrasings — is English because
the tools, the metric names and the code are. It is a *structure*, not a
script: translate the headings and the prose into whatever language the
question came in, and match how that person writes (a reader who says "la red"
does not want "el enlace de área local").

Some things are not prose and do not get translated:

- **Target names** are database keys. `CloudflareDNS`, `Chicago_ORD_c122_1`,
  `CPE_Gateway` — quote them exactly, in any language. A translated target
  name cannot be looked up, muted, or found on a dashboard.
- **Numbers, units and windows** — `8.7 ms`, `0%`, `62%`, `last 168h`.
- **Traffic lights and links**, which carry meaning without carrying words.

## The report shape

**Any question about how the connection is doing gets the full shape** —
*how is it now*, *how was the week*, *did something happen last night*, *qué
onda la red*. Every one of them. A short window is not a reason to drop
sections; it is a reason to put fewer bullets in each.

The only answers that skip the shape are about **one named thing**: "is
Netflix ok?", "what's the ping to Google?", "is DNS up?". Those get one or two
sentences. If you are reaching for more than one target's numbers, you are
writing a report — use the shape.

**Never collapse the sections into one list.** *Monitoring*, *Internet*,
*DNS*, *Local link* are not decoration, they are the layers a problem can
live at, and they are how the reader learns whether it is the house, the
line, or that one site. A single flat list of bullets says "here are some
numbers" and makes the reader do the attribution you were asked to do. Even
in a one-hour answer, `DNS` and `Local link` stay separate — a clean 🟢 DNS
line next to a 🟡 gateway line *is* the finding.

**Traffic lights first.** Every section that states a condition opens with one
(*Ignored* and *Graphs* state none), so the shape of the week is legible
before a word of it is read:

- 🟢 nothing to do — normal for this deployment
- 🟡 real but minor — brief, isolated, or within a known floor. Worth
  knowing, not worth acting on
- 🔴 acted on or needs attention — a sustained outage, a broken monitor, a
  target that has been failing since yesterday

Grade against *this* deployment's normal, not an ideal one: the CPE's ICMP
floor is 🟢, and a single 5-minute miss is 🟡 at worst.

Then the sections, in this order. **Drop any section that has nothing in it** —
a heading over "nothing to report" costs a phone screen to say nothing:

```
<one-sentence verdict> 🟡

<b>Monitoring</b>
🟢 Collector healthy, 18 targets, data fresh 12:45 pm CT.

<b>Internet</b>
🟢 Medians 7–9 ms on the big sites.
🟡 Sat 29 Aug, 2:40–7:15 pm CT: 100% loss on web targets. [graph]
🟡 DNS stayed at 0%, so this reads as ICMP, not an outage.

<b>DNS</b>
🟢 Cloudflare 11.8 ms · Google 12.0 ms · Quad9 8.2 ms.
🟢 0% loss on all three, all week.

<b>Local link</b>
🔴 Worst window 66% loss — Fri 28 Aug, 2:30 am CT. [graph]
🟡 Peaks of 60–64% Sun 23 Aug midday CT. [graph]
🟡 Median jitter 47.7 ms — felt on calls, not on browsing.

<b>Week so far</b>
🟡 Sat 29 Aug ICMP run — still the week's worst. [graph, Sat]
🟡 Gateway microcuts Fri + Sun. [graph, that window]

<b>Ignored</b>
Weekly averages read ~90% on some targets, inflated by Saturday's ICMP
run — DNS and the OCAs contradict a real outage.

<b>Bottom line:</b> <what this means for the person, and what to do>

<b>Graphs</b>
• Overview — home: <url> · anywhere: <url>
• Microcuts — home: <url> · anywhere: <url>
```

A one-hour answer is the **same shape, fewer bullets** — never a flat list:

```
Looking good right now. 🟢

<b>DNS</b>
🟢 Cloudflare · Google · Quad9 — 0% loss, 10–12 ms.

<b>Internet</b>
🟢 Google, NYT, Spotify, UdeSA, Cloudflare — 0% loss.
🟡 Amazon, Facebook, Apple — isolated ~9% points, not sustained.

<b>Local link</b>
🟡 Worst microcut 20%, jitter ~45 ms — light local noise, below
   the level I'd call a real problem.

<b>Bottom line:</b> browsing and streaming fine; a call or a game
might catch the odd stutter.

<b>Graph</b>
• Last hour — home: <url> · anywhere: <url>
```

Note what survives at one hour: the layers. *DNS clean* sitting next to
*gateway noisy* is the whole answer — flatten them into one list and the
reader has to reconstruct it.

Rules that make the difference between this reading well and reading like a
form:

- **Section headings are `<b>…</b>`. Always. This channel is HTML.** Messages
  are sent with `parse_mode: "HTML"`, so `<b>DNS</b>` renders bold and is the
  only form that works here. `### DNS` arrives as literal hashes or flat
  text; `**DNS**` arrives as literal asterisks. Both are what make a correct
  report read as one grey wall. Same for the `<b>Bottom line:</b>` lead-in.

  Because it is HTML, `<` and `&` in a value must be escaped (`&lt;`, `&amp;`)
  — target names are user-editable and `a<b&c` is a legal one. Never wrap a
  whole message in a code block; it kills every heading in it.
- **The headline is a verdict, not a summary.** "Stable week, occasional
  microcuts, no sustained outage" — a claim someone can disagree with. Not
  "here is your weekly report".
- **Every section is bullets. No section is a paragraph.** Even a 🟢 one:
  *DNS* gets a bullet of medians and a bullet of loss, not one sentence
  holding both. A section with a single long line is the failure mode to
  avoid.
- **One fact per bullet, one line per bullet.** If a bullet contains a
  semicolon, an "; también", or a second measurement, it is two bullets. Aim
  for under ~15 words — a bullet that wraps three times on a phone has stopped
  being a bullet.

  Wrong — four facts, one line:
  > 🟡 The gateway had real microcuts: worst window 66% loss on Friday 28 Aug
  > 2:30 am CT; also peaks of 60–64% on Sunday 23 Aug midday CT. Median jitter
  > ~47.7 ms. That feels like stutter on calls and games, not broken browsing.

  Right — one fact each, and the interpretation earns its own line:
  > 🔴 Worst window 66% loss — Fri 28 Aug, 2:30 am CT. [graph]
  > 🟡 Peaks of 60–64% — Sun 23 Aug, midday CT. [graph]
  > 🟡 Median jitter 47.7 ms — felt on calls, not on browsing.
- **Attribute, do not just report.** "The gateway spiked to 62%" is a
  measurement; "that's the local link, not the ISP — you'd have felt it as a
  stutter" is the answer. Say which layer a problem sits at whenever the data
  supports it, and say when it doesn't.
- **Dates and times in the reader's timezone** (tunable #3 — CT here), with
  the zone named, and with the weekday when the window is longer than a day
  ("Sunday 9 Aug, 3:20–4:55 pm CT"). An ISO timestamp is not an answer to
  "when did it happen".
- **Bold or `###` for headings, never both on the same line**, and never a
  heading deeper than `###`.

## Links

**Always offer both a home link and an off-network one, when both exist.** The
person reading this is as likely to be on cellular as on the couch, and a LAN
URL is a dead link from a train — but the tunnel adds a hop and can be down on
its own, so a LAN URL is the better one at home. Offering both costs a line;
guessing wrong costs the whole point of sending a link.

The tool responses already carry both. Every `links` object has a primary set
(`graph`, `per_ping_detail`, `compare_with_peers`, `edit`) and, when a tunnel
is configured, a `_tunnel` twin of each (`graph_tunnel`, …). `system_status`
returns the same pair for the front doors: `grafana_overview`,
`grafana_cpe_microcuts`, `web_admin_targets`.

- Label them for where the reader is, not for the technology: *home* /
  *anywhere*, not *LAN* / *Cloudflare tunnel*.
- **Never invent a host or a dashboard.** The base URL cannot be guessed —
  this Pi answers on a LAN IP, a tailnet name and a tunnel, and only one of
  them works for a given reader. Dashboard UIDs and variable names are a
  pinned contract. So never write a URL from scratch, and if a `_tunnel` key
  is absent, no tunnel is configured: offer the one link there is and say
  nothing about the other. A hand-built hostname is a link that 404s on
  someone's phone.

- **But you may re-time a link you already have.** `from` and `to` are the
  one part that is yours to set, and Grafana takes an absolute range as
  **epoch milliseconds**. So take a link a tool gave you — correct host,
  correct dashboard, correct `var-target` — and swap its `from`/`to` for the
  window your sentence is about:

  ```
  ...&from=1788032400000&to=1788048900000     # Sat 29 Aug, 2:40–7:15 pm CT
  ```

  This is better than re-querying and hoping: `worst_windows` returns the top
  five and zooms each to ±15 minutes, so a run of loss you are describing may
  not be among them, and ±15 minutes is the wrong frame for something that
  lasted four hours. Re-timing lets you frame the actual event.

  **Milliseconds, not seconds.** A ten-digit epoch is read as milliseconds
  too, which lands the reader in January 1970 — a link that is confidently,
  silently wrong. Multiply by 1000, and sanity-check that the number has 13
  digits before you send it.

  Pad a little on each side. A range that starts exactly at the spike opens
  with the incident against the left edge and no "before" to compare to.
- **The link's time range must match the sentence's time range.** This is the
  rule; everything below follows from it. If a bullet talks about Saturday,
  its link opens on Saturday. A bullet about last Saturday carrying a
  `from=now-1h` URL is not a weak link, it is a **wrong** one: the reader
  taps it, sees a calm hour, and concludes you made the incident up.

- **Naming *when* means naming a window, however loosely.** "Fri 28 Aug 2:30
  am", "el sábado 29 ago", "last night", "the Saturday thing", "that run of
  loss" — all of them. It does not have to be a timestamp to need a link,
  and a whole day still beats 168 hours.

- **Recalling an earlier finding does not exempt it.** A summary bullet
  ("my read on the week hasn't changed — the odd Saturday run and the
  gateway microcuts") names two incidents and needs a link for each. This is
  the case most easily missed, because the numbers are coming from your own
  memory of a previous answer rather than from a tool response you are
  looking at — so there is no link sitting in front of you to copy. Go get
  one, by either route:

  - **Re-time a link you already hold** (above). Any `graph` URL from this
    session works as the donor — you are changing only `from`/`to`. This is
    usually the right move for a recalled incident, because you already know
    when it happened.
  - **Or re-query for one**: `get_microcut_stats` returns a `worst_windows`
    array whose entries each carry a `graph` zoomed to ±15 minutes, and
    `get_loss_events` links its episodes the same way. Call the one that
    covers the event — a Saturday two days back needs roughly `hours=72`,
    not `hours=1`.

- **When one link must cover several incidents**, scope it to the span you
  are discussing (the week) rather than to the present. An overview URL is a
  fine last resort; an overview URL pointing at the last hour, in a
  paragraph about last week, is not.

- The end-of-report *Graphs* section is for the two or three whole-window
  views (overview, microcuts), and its window is **the widest one the report
  discusses**. An answer that covers the last hour and then comments on the
  week ends with week-long links, not `now-1h` ones — otherwise the report's
  own closing links contradict half of what it just said. Per-incident links
  belong inline, on their bullet, not collected at the bottom where they
  lose the time they refer to.
