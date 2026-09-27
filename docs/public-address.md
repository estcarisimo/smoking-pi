# The Overview page and the public address

Grafana opens on **Smoking Pi – Overview** (Pro, InfluxDB). It answers, in a
few seconds, where the Pi is connected and what it is measuring:

- **Where it is connected:** the uplink (interface and whether it is
  wireless), the Wi-Fi network (SSID), the public address the Internet
  sees, the network (AS) that announces it, an approximate location, and
  who resolves DNS for the house ([Which resolver answers](public-resolver.md)).
- **What it is measuring:** how many targets answered in the last 15
  minutes on each layer (ICMP ping, DNS, TCP handshake, HTTP/1.1–3), how
  many are losing packets, and how many measurements were written in the
  last hour. The DNS wizard's targets are counted apart: many CDNs drop
  ICMP, so a silent wizard target is usually a probe choice rather than an
  outage, as in [alerting](alerting.md).
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
| `PUBLIC_IP_GEO` | `1` | `0` turns the location lookup off: the address is then never sent to ipinfo.io |
| `IPINFO_TOKEN` | empty | Optional; without one, IPinfo's free tier is far above one lookup a day |

What leaves the house: one DNS query per family to Google's authoritative
server per cycle, a Team Cymru lookup when the address or its network is new
to the day, and, unless `PUBLIC_IP_GEO=0`, one HTTPS request a day to
ipinfo.io carrying the address. Grafana sits behind its login; the address
and location are in InfluxDB like every other measurement.
