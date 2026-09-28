# One target, every layer

The **Target Detail** dashboard answers "show me everything about this one
destination". Pick a target from the dropdown and one page shows what
Smoking Pi measures for it, layer by layer:

| Layer | Measurement | What it tells you |
| --- | --- | --- |
| ICMP | `latency` | The network path, with no application in the way |
| TCP handshake | `tcp_latency` | SYN / SYN-ACK to port 443: the transport floor under HTTP |
| HTTP/1.1, HTTP/2, HTTP/3 | `http_latency` | The page fetched with each version, and how often a fetch failed |
| DNS | `dns_latency` | A query answered by this target, when the target is a resolver |

The tiles at the top give the median and the loss for each layer over the
selected range. A layer the target has no probe for says **not measured**,
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

A target added with a different name on another probe (for example `goog`
for ICMP and `Google_h2` for HTTP) shows up as two entries. Give related
targets the same base name when you add them.

The dashboard reads InfluxDB. With the ClickHouse backend it is not
available yet.
