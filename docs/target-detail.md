# One target, every layer

The **Target Detail** dashboard answers "show me everything about this one
destination". Pick a target from the dropdown and one page shows what
Smoking Pi measures for it, layer by layer:

| Layer | Measurement | What it tells you |
| --- | --- | --- |
| ICMP | `latency` | The network path, with no application in the way |
| TCP handshake | `tcp_latency` | SYN / SYN-ACK to port 443: the transport floor under HTTP |
| HTTP/1.1, HTTP/2, HTTP/3 | `http_latency` | A HEAD request over each version, and how often one failed |
| DNS | `dns_latency` | A query answered by this target, when the target is a resolver |

The tiles at the top give the median and the loss for each layer over the
selected range. A layer the target has no probe for, or whose probe wrote
nothing in that range, says **not measured**,
so you can see at a glance what exists and what does not. Below the tiles,
each layer has its latency over time, where the line is the median of each
measurement cycle and the shaded band is the 10th to 90th percentile of that
cycle's individual pings, plus its loss. HTTP draws one line per version
instead of a band.

Open it from the dashboard list (*SmokePing dashboards*), or with the
**Target detail** link at the top of the Overview, the latency, resolver,
per-ping and HTTP-by-version dashboards: those carry the target you are
looking at.

## How targets pair across probes

Each probe writes its own target name, and the dashboard pairs them by the
name without its probe suffix, ignoring case:

- `Google` (ICMP), `Google_tcp443` (TCP), `Google_h1`, `Google_h2`,
  `Google_h3` (HTTP) are all **Google**.
- The DNS wizard's targets, `W_<service>_icmp`, `_tcp`, `_h1`, `_h2` and
  `_h3`, are all **W_&lt;service&gt;**.

The link from another dashboard can carry any of these names, including a
probe's full name (`Google_h2`) or another case (`google`): the dashboard
strips the suffix itself and shows the whole target.

What the pairing cannot do:

- A target added with a different name on another probe (for example `goog`
  for ICMP and `Google_h2` for HTTP) shows up as two entries. Give related
  targets the same base name when you add them.
- An IPv6 twin has its own name (`Google6`), so it is its own entry.
- Two targets with the same name in one measurement (the same name in two
  categories, or two names that differ only in case) are drawn together, and
  their bands mix. Keep names unique.
- The DNS wizard's targets have no DNS layer of their own, so their DNS tile
  always says "not measured"; the wizard's dashboard covers their DNS.

The dashboard reads InfluxDB. With the ClickHouse backend it is not
available yet.
