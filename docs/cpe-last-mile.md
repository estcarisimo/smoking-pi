# CPE last-mile signal (dBm): the exploration, and why it is parked

The idea: the physical last mile — optical power on fiber, SNR and
attenuation on DSL, signal levels on DOCSIS — is where a good share of
residential faults live, and the CPE knows those numbers. If Smoking Pi
could read them, a latency spike could be labelled "your fiber dropped
3 dB at the same minute" instead of "something happened".

This page records what was checked on the reference Pi, what the gateway
does expose (documented below because it is useful for other things), and
the conclusion. Read-only checks only; nothing was changed on the router.

## What the reference deployment looks like

```
Pi (wlan0) ──Wi-Fi── Nest Wifi Pro (192.168.86.1) ──Ethernet── Fiber Jack (ONT) ──fiber── Google Fiber
                                                   WAN: public /23 via DHCP, gateway 136.25.220.1
```

- The router at 192.168.86.1 is a **Nest Wifi Pro** (`modelId: SIROCCO`).
  Its WAN is plain Ethernet with a public DHCP address, so the **Fiber Jack
  is a transparent bridge** in front of it.
- The first ISP hop, 136.25.220.1, is what `cpe_discovery.py` already
  targets as `CPE_IPv4`. That hop *is* fiber + ONT + OLT + the ISP's first
  router, so its latency series is the last mile in latency terms.

## What the gateway exposes

Ports open on 192.168.86.1 from the LAN: 80, 8080, 53. Closed: 22, 443,
7547 (TR-069 — that is the ISP's channel *to* the device, never a customer
one), 49152 (UPnP).

### `GET http://192.168.86.1/api/v1/status`

Unauthenticated, read-only, JSON. This is the local status API the Google
Home app uses for on-LAN diagnostics; it is the only endpoint under
`/api/v1/` that answers (`diagnostic-report`, `lan`, `wan`, `stats`,
`log`, `mesh`, `net` all return 404). What it carries:

| key | meaning | example on the reference Pi |
| --- | --- | --- |
| `wan.online`, `wan.ethernetLink` | WAN up, physical link up | `true`, `true` |
| `wan.ipMethod`, `wan.localIpAddress`, `wan.ipPrefixLength` | how the WAN address was obtained and what it is | `dhcp`, `136.25.221.231`, `23` |
| `wan.gatewayIpAddress` | the ISP's first hop | `136.25.220.1` |
| `wan.leaseDurationSeconds` | DHCP lease | `10800` |
| `wan.nameServers`, `dns.servers`, `dns.mode` | resolvers the router uses/hands out | `8.8.8.8`, `8.8.4.4`, `automatic` |
| `wan.captivePortal`, `wan.pppoeDetected`, `wan.invalidCredentials` | WAN diagnostics | all `false` |
| `system.uptime` | seconds since the router booted | `58293` |
| `system.lan0Link` | LAN port link | `false` (nothing wired) |
| `system.modelId`, `system.hardwareId`, `software.softwareVersion`, `software.updateChannel` | identity and firmware | `SIROCCO`, ..., `3.78.518349`, `stable-channel` |
| `system.ledAnimation`, `system.groupRole`, `setupState` | state | `CONNECTED`, `none`, `GWIFI_OOBE_COMPLETE` |

What it does **not** carry: anything about the physical layer, Wi-Fi
clients, per-port counters, or throughput. It is a "is the WAN up and how
did it get its address" endpoint.

Where it *would* be useful, if wanted later (none of this is built):

- `wan.online` flipping, or `system.uptime` resetting, is a router-side
  "the WAN went down / the router rebooted" marker — a cleaner root cause
  for a 100%-loss window than inferring it from every target failing at
  once. One request every 60 s to the LAN, no credentials.
- `wan.gatewayIpAddress` is exactly what `cpe_discovery.py` derives from
  a traceroute today; on this router it could be read directly.
- `wan.leaseDurationSeconds` and `localIpAddress` changing would explain
  a short outage as a DHCP renewal that changed the public address.

Port 8080 is the Family Wi-Fi "Wi-Fi is paused on this device" splash
page, not an API.

### The ONT

The Fiber Jack answers on none of the conventional CPE management
addresses from the LAN (192.168.100.1, 192.168.1.1, 192.168.1.254,
192.168.0.1, 10.0.0.1), and Google Fiber Jacks have no customer-facing
status page. The optical Rx/Tx power lives in OMCI, visible only to Google
Fiber's OLT. There is no path to it from the customer side.

## Conclusion

**Not feasible here, and the reason generalises.** PHY-layer numbers are
exposed to the customer only when the ISP's own device is the router:
many DSL CPEs, most DOCSIS modems (at 192.168.100.1), some ISP-branded
GPON ONT/routers. Whenever the customer supplies the router — Google
Fiber, AT&T Fiber with a BGW in passthrough, any "bring your own router"
setup — the physical layer is behind a wall. And where it is exposed, it
is a different page or TR-181 path per vendor and per firmware.

An honest scope for this as a project feature is "opportunistic,
per-vendor scrapers where a page exists", and the reference Pi has
nothing to build one against. It is parked: no sprint, no code. If
someone on a DOCSIS or DSL setup wants it, the shape would be a small
collector like `wifi_link.py` writing a `cpe_phy` measurement, with one
scraper module per vendor page, and the router-status fields above as the
vendor-neutral baseline.

What *is* measured on the last mile today: ICMP to the first ISP hop
(`CPE_IPv4`), the Wi-Fi hop from the Pi to the router
([wifi.md](wifi.md)), and now TCP/HTTP fetch times through the whole path
([http-probes.md](http-probes.md)).
