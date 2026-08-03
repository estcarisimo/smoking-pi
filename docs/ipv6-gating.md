# IPv6 Gating

SmokePing's `FPing6` probe only produces meaningful data when the host can
actually reach the global IPv6 internet. Without it, every IPv6 target charts a
flat 100% loss — which looks like an outage but only means there is no IPv6
here. config-manager therefore checks global IPv6 reachability and omits IPv6
targets from the generated `Targets` file when it is unavailable.

Targets are **not** modified in the database. They stay exactly as configured
and reappear on their own once a recheck sees IPv6 working.

## Why "does the host have an IPv6 address" is not the test

Linux reports several kinds of address as `scope global`, and most of them
cannot reach the internet:

| Prefix | Kind | Globally routable |
|--------|------|-------------------|
| `2000::/3` | Global unicast | **yes** |
| `fd00::/8` | ULA (RFC 4193), including Tailscale's `fd7a:115c:a1e0::/48` | no |
| `fe80::/10` | Link-local | no |

A router handing out a ULA prefix, or a Tailscale interface, is enough to make
`ip -6 addr show scope global` non-empty on a host with no IPv6 service at all.

## The check

Three signals, cheapest first — all must pass:

1. **A global unicast address** (`2000::/3`). ULA and link-local do not count.
2. **A default IPv6 route.** Without one the kernel rejects the packet outright
   (`connect: Network is unreachable`) and nothing leaves the host.
   `unreachable`/`prohibit`/`blackhole` default routes do not count.
3. **A reply from a probe host.** The only signal that proves end-to-end
   reachability rather than local configuration. Any one of the configured
   addresses replying is enough.

If steps 1 and 2 fail there is no point paying the ping timeout, so the probe
is skipped.

### Where it runs

The check executes **inside the SmokePing container**, via `docker exec`, not
in config-manager. SmokePing uses `network_mode: host`, so its namespace is
what its probes actually see. config-manager sits on a Docker bridge network
with no IPv6 whatsoever — a check made locally would report "no IPv6" on a
perfectly IPv6-capable host.

## When the verdict changes

The check runs at startup (before the first config generation) and then every
`IPV6_RECHECK_INTERVAL` seconds. Configuration is regenerated **only when the
verdict flips**, so a stable host does no repeated work:

- IPv6 starts working → targets return, SmokePing reloads.
- IPv6 stops working → targets drop out instead of charting 100% loss.

A check that cannot run at all (SmokePing container down, Docker socket
unavailable) is reported as an error and leaves the previous verdict in place —
a transient failure must never silently drop targets. Likewise, before the
first successful check the status is *unknown*, which keeps IPv6 targets.

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `IPV6_MODE` | `auto` | `auto` probes the host; `force` always emits IPv6 targets; `off` never does |
| `IPV6_PROBE_HOSTS` | `2001:4860:4860::8888,2606:4700:4700::1111` | Comma-separated addresses to ping |
| `IPV6_RECHECK_INTERVAL` | `900` | Seconds between background rechecks |

`force` is the escape hatch for a network the probe cannot classify (for
example, IPv6 reachable only through a proxy, or ICMP filtered outbound). The
recheck thread does not run unless the mode is `auto`.

## Inspecting the current verdict

```bash
curl -H "Authorization: Bearer $CONFIG_API_TOKEN" \
  http://localhost:5000/ipv6-status
```

```json
{
  "available": false,
  "reason": "no global IPv6 address on this host (only ULA/link-local, which cannot route to the IPv6 internet)",
  "addresses": [],
  "mode": "auto",
  "measurements_allowed": false,
  "recheck_interval": 900
}
```

Force an immediate re-check (regenerates config if the verdict flips):

```bash
curl -X POST -H "Authorization: Bearer $CONFIG_API_TOKEN" \
  http://localhost:5000/ipv6-status/refresh
```

When targets are being omitted, the generated `Targets` file says so near the
top, so the reason is visible where the effect is:

```
# IPv6 targets omitted: no global IPv6 address on this host ...
# They return automatically once a recheck sees global IPv6 working.
```

## Interaction with alerting

The alerter's `ipv6_down` rule fires when every IPv6 target is at 100% loss
while IPv4 is healthy. With gating enabled that rule now means what it says —
"IPv6 was working and broke" — because a host that never had IPv6 stops
producing IPv6 measurements altogether, and the incident clears once the
existing points age out of the 15-minute window.
