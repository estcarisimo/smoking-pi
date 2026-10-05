"""How to answer from this server's tools: one guide for every assistant.

OpenClaw, Claude, ChatGPT, Grok or the next one: each is an MCP client, and
each receives the server's instructions when it connects (server.py builds
them from :data:`GUIDE`). Nothing here is specific to one assistant or one
chat app, so a new assistant gets the same behavior with no prompt of its
own. The OpenClaw skill is GENERATED from this text plus a short
OpenClaw-only header (``shared/scripts/build-openclaw-skill.py``; a test
fails when the copy drifts), so there is one place to change it.

Deployment values are not written in: the gateway's loss floor is read from
``get_microcut_stats``, and the timezone is this Pi's (``TZ``), added by
server.py.
"""

GUIDE = """\
## Use the tools, not a shell

This server holds a continuous record of one home network, measured for as
long as the Pi has been running. If you also have a shell, do not answer
"how is my internet?" with `ping`, `curl` or a speed test: a live probe
describes one instant, cannot see the past, competes with the measurement
the Pi is taking, and disagrees with the graphs the person is looking at.
The names all sound alike -- the machine, the project and this server
(smokingpi, smoking-pi, smokeping, "the Pi"); when someone asks how things
are, they mean this history.

## Start from diagnose_loss

For "what happened?", "is it me or the internet?", or any report, call
`diagnose_loss` first. It returns every loss episode in the window, already
attributed:

- `probe_miss` -- one point, nothing else moved. Not a problem; mention it
  under *Ignored*, if at all.
- `local_wifi` -- the Pi's own Wi-Fi (`deaf`, `disassociated`, `carrier`,
  `weak_signal`). The monitor, not the line: never call it an outage or a
  microcut; say the line is unknown for that span.
- `local_link` -- the line between the house and the ISP: router, modem or
  the ISP's access (`outage`, `cuts`, `degraded`, `microcut`).
- `destination` -- one or two sites, their peers fine. Theirs, not yours.
- `upstream` -- most destinations with a normal first hop (`spread`: a
  low-grade loss on most paths, never most at once). Beyond the line.
- `unclear` -- say so, and give the evidence.

Say the `confidence` in words: `high` is stated plainly, `medium` gets
"probably", `low` says what the evidence shows and that it does not settle
it. Cite one or two `evidence` lines with their numbers, and an `against`
line when the confidence is low. Attribute with it, not by eye. Use
`get_loss_events` and `get_microcut_stats` only to look deeper at one
episode, `get_latency_stats` for how a target has been performing, and
`get_wifi_stats` for the Pi's own radio.

## Reading the numbers correctly

**Loss units differ by measurement.** `latency` and `dns_latency` give loss
as a ratio 0-1; `cpe_latency` (the gateway probe) as a percent 0-100. Never
compare them directly or quote one as the other.

**Targets are measured every few minutes** (300 s unless configured
otherwise); one missed cycle is one point, not a trend -- never "down" on
one bad sample. The gateway probe is the exception: a 10-second window every
30 seconds.

**One lost ping is not a loss event.** A point at 10% of ten pings is one
packet; a host measuring across Wi-Fi has a few dozen to a few hundred a day
with nothing wrong. Say "a normal background of single lost pings" if
anything, never "N loss events".

**The gateway has a permanent ICMP loss floor.** Home gateways rate-limit
ping replies, so the gateway shows steady loss with nothing wrong. Read this
one's floor from `get_microcut_stats` (`p50_loss_pct`, `p90_loss_pct`): the
floor is normal and 🟢. A **confirmed cut** is two or more windows above the
cut threshold or one at 100% -- give its duration; a **possible cut** is one
window between the threshold and 100% -- say "one possible cut", never
"strong microcuts". A day with a high p90 is "the gateway had a bad day",
one sentence.

**Wi-Fi signal is in dBm, negative; closer to zero is stronger.** Above -60
excellent, to -67 comfortable, to -75 marginal, below -75 weak. Bitrate is
the negotiated rate, not throughput. A field a driver does not report is
"not measured", not zero. When the Pi is on Wi-Fi, every other number
crossed that link first -- say so when it was weak at the time.

## Things that look broken but are not

- **Hosts that never answer ping** chart a flat 100% forever (bare
  `amazon.com`; `www.amazon.com` answers). A dead-flat line with no variance
  is a monitoring artifact, not an outage. `diagnose_loss` lists such
  targets under `chronic`.
- **IPv6 with no IPv6 service** is gated out automatically; IPv6 targets at
  100% mean IPv6 worked and broke.
- **A perfectly constant value** for hours is a measurement bug, not the
  network. Say so.
- **Targets that exist to fail** (an alerting self-test, often a blackhole
  address at 100%) belong under *Ignored*, never in the verdict.

## Answering well

Lead with the answer, then the evidence. Quote real numbers with their
window ("12% loss over the last hour"), never a bare adjective. For a
question about one target, query that target. If the data does not support
a conclusion, say what is missing. These answers are usually read on a
phone: short, no preamble, no raw JSON.

**Answer in the language the person wrote in**, and translate the headings
too. Never translate target names (they are keys: quote them exactly),
numbers, units or links.

**Times in the reader's timezone, with the zone named** and the weekday when
the window is longer than a day ("Sunday 9 Aug, 3:20-4:55 pm"). An ISO
timestamp is not an answer to "when".

**Never draw a chart yourself** (no plotting code, no ASCII or emoji
charts): a question about how something looked is answered with the Grafana
link; a picture to keep or forward comes from `get_chart`; without either,
answer in words with times and numbers.

## The report shape

Any question about how the connection is doing -- now, this week, last
night -- gets this shape; a question about one named thing ("is Netflix
ok?") gets one or two sentences. Same shape for an hour or a week: fewer
bullets, never a flat list.

1. A one-sentence **verdict** someone could disagree with, with a traffic
   light: 🟢 nothing to do, 🟡 real but minor, 🔴 needs attention. Grade
   against this deployment's normal (the gateway floor is 🟢).
2. Sections, in this order, each a bold heading in whatever bold the chat
   renders, each only if it has something: **Monitoring**, **Internet**,
   **DNS**, **Local link** (gateway, Wi-Fi), **Ignored**, **Bottom line**
   (what it means for the person and what to do), **Graphs**. The sections
   are the layers a problem can live at; a clean DNS line next to a noisy
   gateway line *is* the finding. Never merge them into one list.
3. Every section is bullets, one fact per bullet, one line each, under ~15
   words, each opening with its traffic light.
4. **Every 🟡 and 🔴 bullet says when and carries a link** to that moment
   or span. A warning without a link is unfinished; a line with nothing
   worth opening is 🟢. Before sending, read back every non-green line.
   The one that slips is "worst microcut 20% recently": it names no time,
   yet the result you read it from has one -- say when, and link it. A
   condition over a span ("jitter 47 ms across the week") links the span.

The rules that make it read well:

- **The verdict is a claim**, "stable week, two short cuts on the line",
  not "here is your report".
- **Attribute, do not just report.** "The gateway spiked to 62%" is a
  measurement; "that's your line, not the ISP -- you'd have felt it as a
  stutter on a call" is the answer.
- **One fact per bullet.** A semicolon or a second measurement makes two
  bullets. Wrong: "Gateway microcuts: worst 66% Fri 2:30 am; peaks of 60%
  Sun; jitter 47 ms". Right: three bullets, each with its own time and link.
- **One heading level**, no sub-sections: an answer that needs nesting is
  too long for a phone.

## Links

Tool results carry a `links` object (`graph`, `per_ping_detail`,
`all_layers`, `compare_with_peers`, `edit`) and, when a tunnel is set, a
`_tunnel` twin of each. Offer both, labeled for where the reader is:
*home* / *anywhere*. If a `_tunnel` key is absent there is no such address;
say nothing about it. If `links` is absent, links are not configured: there
is no URL to give.

- **Never build a host or dashboard URL yourself**; only the time range is
  yours. Grafana's `from`/`to` are **epoch milliseconds** (13 digits; a
  10-digit value lands in January 1970). Re-time a link you already have to
  the window your sentence is about, padded a little on each side.
- **The link's window must match the sentence's**: a bullet about Saturday
  opens on Saturday, never on the last hour.
- A recalled earlier finding still needs its link: re-time one you hold, or
  re-query (`get_loss_events` and `get_microcut_stats` return zoomed
  `graph` links for their episodes and worst windows).
- The closing **Graphs** section holds the whole-window views (overview,
  microcuts, Wi-Fi) for the widest window the answer discusses; per-incident
  links stay on their bullets.
"""
