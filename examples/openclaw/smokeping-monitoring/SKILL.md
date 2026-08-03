---
name: smokeping-monitoring
description: Answer questions about home network health using the smokeping MCP server — latency, packet loss, microcuts, and monitoring configuration.
---

# SmokePing Monitoring

You have tools from the `smokeping` MCP server covering a Raspberry Pi that
continuously measures home network quality. Use them to answer questions about
latency, loss, and outages, and to manage what is monitored.

Replace the placeholders below with your own deployment's values before
installing this skill.

## Tools

**Reading**

- `system_status` — service health and target counts. Start here when someone
  asks an open question like "how's the network?"
- `get_latency_stats(target, hours)` — median latency and loss over a window.
- `get_loss_events(target, hours)` — discrete loss episodes rather than averages.
- `get_microcut_stats(hours)` — sub-second CPE dropouts, sampled far more
  finely than the 5-minute target probes.
- `list_targets` — what is currently monitored.

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
real cut when loss is far above that — roughly 50%+.

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
