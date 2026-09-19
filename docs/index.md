# Smoking Pi

<div align="center" markdown>
<img src="https://raw.githubusercontent.com/estcarisimo/smoking-pi/main/img/logo.jpg" alt="Smoking Pi — a steaming raspberry pie" width="200"/>
</div>

Continuous network monitoring for your home or lab, in a box. Smoking Pi wraps
[SmokePing](https://oss.oetiker.ch/smokeping/) in Docker Compose and grows with
you: from a single container reading a YAML file, to a full stack with a web
admin, Grafana dashboards, a time-series database, alerting that leads with a
verdict, and an MCP server so an AI assistant can answer *"how's my internet?"*
from months of recorded history instead of a live `ping`. Built for a
Raspberry Pi (ARM64); runs anywhere Docker does.

The code, issues and releases live at
[github.com/estcarisimo/smoking-pi](https://github.com/estcarisimo/smoking-pi).
This site is the reference for what the stack does and why it is built the way
it is; the [README](https://github.com/estcarisimo/smoking-pi#readme) is the
quick start.

## Three editions

| Edition | For | What you get |
|---|---|---|
| **Basic** | One box, one person | LinuxServer.io SmokePing, targets in a YAML file, the classic web UI, RRD storage |
| **Standard** | A small team | + web admin with login, PostgreSQL as the source of truth, a REST API, bulk target management |
| **Pro** | Everything | + Grafana dashboards, InfluxDB or ClickHouse, IPv6 and DNS probes, HTTP and TCP probes, Wi-Fi uplink stats, alerting, MCP server, AI reports, the doctor |

Each edition is a directory under `editions/` with its own `setup.sh`, which
generates every password and API token, detects the timezone, starts the
containers and prints the URLs and credentials. Nothing is published to a
package registry: install from a clone.

```bash
git clone https://github.com/estcarisimo/smoking-pi.git
cd smoking-pi/editions/pro
./setup.sh
```

## What is measured

- **Latency and loss** to your targets, every 300 s, with every individual
  ping kept (the "smoke"), over IPv4 and, when the host really has it, IPv6
  ([IPv6 gating](ipv6-gating.md)).
- **DNS resolution time** against public resolvers.
- **The same page over HTTP/1.1, HTTP/2 and HTTP/3**, each version enforced
  rather than requested, with the bare TCP handshake underneath as the floor
  ([HTTP and TCP probes](http-probes.md)).
- **The first hop**: the CPE sampled every 10 s for microcuts, and, when the
  Pi is on wireless, the Wi-Fi link itself — signal, bitrate, throughput,
  disconnects ([Wi-Fi uplink stats](wifi.md)). What the gateway will not tell
  you about the physical last mile is written down too
  ([the last mile from the CPE](cpe-last-mile.md)).
- **Netflix's Open Connect appliances** serving your network, discovered and
  tracked.

## What happens with it

- **Alerts that say what they mean.** Every alert leads with a verdict — *is
  it me or the internet?* — carries the chart, links into the exact Grafana
  view, and can be muted by asking in chat. A daily digest makes a quiet day
  distinguishable from a dead monitor ([Alerting](alerting.md)).
- **An assistant that has read the history.** The MCP server exposes latency,
  loss, microcut and Wi-Fi statistics, system status and on-request charts to
  any MCP client; the OpenClaw skill turns that into *"how was the line last
  night?"* ([MCP server](mcp-server.md), [OpenClaw integration](openclaw-integration.md),
  [OpenClaw on another machine](remote-openclaw.md)).
- **Reports** written by a model from the same data, when you want prose
  instead of a chart ([AI health reports](ai-insights.md)).
- **An instrumentation doctor** that checks the dashboards, exporters,
  containers and DNS actually agree with each other, statically in CI and live
  on the box ([Instrumentation doctor](doctor.md)).

## Operating it

Remote access goes through Cloudflare tunnels, temporary ones with no account
([Quick tunnels](quick-tunnels.md)) or permanent ones with yours
([Permanent Cloudflare tunnels](cloudflare-tunnel-setup.md)); put
authentication in front of anything you expose. Upgrades between versions are
in [Upgrading](upgrades.md), the alternative time-series backend and its traps
in [ClickHouse backend](clickhouse.md), and the question of whether this stack
can be an `apt install` — answered, and parked — in [Packaging](packaging.md).

The [Changelog](changelog.md) says, release by release, what was wrong, what
it cost, and what changed.
