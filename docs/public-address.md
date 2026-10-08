# The Overview page and the public address

Grafana opens on **Smoking Pi – Overview** (Pro, InfluxDB). It answers, in a
few seconds, where the Pi is connected, whether the link is healthy, and
which targets need a look:

- **Where it is connected:** the uplink (interface and whether it is
  wireless), the Wi-Fi network (SSID), the public address the Internet
  sees, the network (AS) that announces it, an approximate location, and
  who resolves DNS for the house ([Which resolver answers](public-resolver.md)).
- **Right now:** how many targets answered in the last 15 minutes on each
  layer (ICMP ping, DNS, TCP handshake, HTTP/1.1–3), how many are losing
  packets, the Wi-Fi signal, the round trip to the ISP's gateway (the
  first hop past the home router that answers), and how many measurements
  were written in the last hour. The DNS wizard's targets are counted
  apart: many CDNs drop ICMP, so a silent wizard target is usually a probe
  choice rather than an outage, as in [alerting](alerting.md).
- **Over the selected range** (24 hours by default): ICMP latency and loss
  per category of destination, the ISP gateway's latency against the Wi-Fi
  signal, and counts of ISP gateway cut windows, Wi-Fi drops, uplink
  changes and resolver changes. Uplink and resolver changes are marked on the
  charts. A jump in every category at once is the house or the ISP; one
  category alone is its destinations.
- **Targets that need a look:** every target and layer, the lossiest first
  and then the ones furthest above their own usual latency (their 24-hour
  median). Comparing each target with itself is what lets a ping and an
  HTTP fetch sort together. A click opens [Target
  Detail](target-detail.md) for that target.
- **Links** to the detailed dashboards.

With ClickHouse, Grafana keeps its own home page: this one reads InfluxDB.

## The public address

Every `PUBLIC_IP_INTERVAL` (15 minutes), from the SmokePing container,
`public_ip.py` asks Google's authoritative DNS server directly for
`o-o.myaddr.l.google.com` (TXT). The answer is the address the question came
from, which is the house's public address. It asks once over IPv4 and once
over IPv6; a family with no route here writes nothing, so most houses show
"no IPv6".

- **Network:** the AS that announces the address, from Team Cymru's DNS
  service (the same lookup [resolver identity](public-resolver.md) uses).
- **Approximate location:** city, region and country from
  [ipinfo.io](https://ipinfo.io). This is geolocation *by address*: often
  the ISP's point of presence, sometimes another city, never the house. It
  is asked only when the address changes or once a day. IPinfo also returns
  coordinates, a postal code and a reverse hostname; none of them is kept.

A change of address or network writes the old one as `previous`, like the
other collectors.

## Settings

In the env file (`smoking-pi config set`):

| Key | Default | |
|---|---|---|
| `PUBLIC_IP_INTERVAL` | `900` | Seconds between checks (60 at least) |
| `PUBLIC_IP_GEO` | `true` | `false` (or `0`, `off`, `no`, in any case) turns the location lookup off: the address is then never sent to ipinfo.io |
| `IPINFO_TOKEN` | empty | Optional; without one, IPinfo's free tier is far above one lookup a day |

What leaves the house: one DNS query per family to Google's authoritative
server per cycle, a Team Cymru lookup when the address or its network is new
to the day, and, unless `PUBLIC_IP_GEO=false`, one HTTPS request a day to
ipinfo.io carrying the address. Grafana sits behind its login; the address
and location are in InfluxDB like every other measurement.
