<div align="center">
  <img src="img/logo.jpg" alt="Smoking Pi — a steaming raspberry pie" width="220"/>
</div>

# 🥧 Smoking Pi

Continuous network monitoring for your home or lab, in a box. Smoking Pi wraps [SmokePing](https://oss.oetiker.ch/smokeping/) in Docker Compose and grows with you: from a single container reading a YAML file, to a full stack with a web admin, Grafana dashboards, a time-series database, alerting that leads with a verdict, and an MCP server so an AI assistant can answer *"how's my internet?"* from months of recorded history instead of a live `ping`. Built for a Raspberry Pi (ARM64) and runs anywhere Docker does.

[![CI](https://github.com/estcarisimo/smoking-pi/actions/workflows/ci.yml/badge.svg)](https://github.com/estcarisimo/smoking-pi/actions/workflows/ci.yml)
[![Docs](https://github.com/estcarisimo/smoking-pi/actions/workflows/docs.yml/badge.svg)](https://estcarisimo.github.io/smoking-pi/)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/downloads/)
[![Docker Compose](https://img.shields.io/badge/docker-compose-2496ED.svg?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Raspberry Pi](https://img.shields.io/badge/raspberry%20pi-arm64-C51A4A.svg?logo=raspberrypi&logoColor=white)](https://www.raspberrypi.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## ✨ Features

- 📡 **Continuous measurement**: ICMP latency and loss to every target on a 300 s cycle, plus DNS resolution timing and IPv6 — recorded for months, not sampled once
- 🌐 **HTTP/1.1 vs 2 vs 3**: the same page fetched over each version by separate probes, version enforced, with the bare TCP handshake underneath as the floor — see [docs/http-probes.md](docs/http-probes.md)
- 🎚️ **Three editions, one setup script**: Basic (YAML), Standard (web admin + PostgreSQL + REST API), Pro (everything + Grafana + InfluxDB/ClickHouse)
- 📊 **Grafana dashboards**: per-target detail with every individual ping, side-by-side comparisons, percentiles, CPE microcut detection sampled every 10 s, and the Wi-Fi uplink itself (signal, bitrate, throughput, disconnects) when the Pi is on wireless
- 🚨 **Alerts that say what they mean**: every alert leads with a verdict — *is it me or the internet?* — carries the chart, and can be muted by asking in chat
- 🤖 **MCP server + agent skill**: ask an AI assistant about last night's outage; answers come with deep links into the exact Grafana view
- 🖼️ **Charts you can forward**: `get_chart` renders a PNG of any target — median with the spread of individual pings — for someone with no login here
- 🩺 **Instrumentation doctor**: static and live checks that the dashboards, exporters, containers and DNS actually agree with each other
- 🌐 **Remote access built in**: temporary Cloudflare tunnels with no account, or permanent ones with yours
- 🔐 **Secure defaults**: `setup.sh` generates every password and API token, the config API and MCP server bind to loopback, Grafana snapshots are off, and error responses never echo internals
- 🍿 **Netflix CDN monitoring**: discovers the Open Connect Appliances serving your network and tracks them

## 🚀 Quick Start

### Installation

You need Docker with the Compose plugin (`docker compose version`). On Raspberry Pi OS and Debian 12 install Docker from [Docker's repository](https://docs.docker.com/engine/install/debian/) first — Debian 12 ships no Compose v2.

**As a package** (Raspberry Pi OS, Debian 12/13, Ubuntu 22.04/24.04 — the hosts each release is checked on, [docs/packaging.md](docs/packaging.md#supported-hosts)):

```bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://estcarisimo.github.io/smoking-pi/apt/smoking-pi.gpg | sudo tee /etc/apt/keyrings/smoking-pi.gpg >/dev/null
echo "deb [signed-by=/etc/apt/keyrings/smoking-pi.gpg] https://estcarisimo.github.io/smoking-pi/apt ./" | sudo tee /etc/apt/sources.list.d/smoking-pi.list
sudo apt update && sudo apt install smoking-pi
sudo smoking-pi install            # edition, backend, optional services; then the stack
sudo systemctl enable --now smoking-pi
```

`smoking-pi install` generates every password and API token, detects your timezone, starts the containers, and prints the URLs and credentials. The step-by-step version of all of this — requirements, what each answer means, how to check it is really measuring, and what to do when it is not — is **[Getting started](https://estcarisimo.github.io/smoking-pi/getting-started/)**.

**Or from a clone** (the development path, and the way to run an unreleased branch):

```bash
git clone https://github.com/estcarisimo/smoking-pi.git
cd smoking-pi

# Pro edition (full stack, InfluxDB)
(cd editions/pro && ./setup.sh)

# Or start smaller (each line from the checkout root)
(cd editions/basic    && ./setup.sh)                        # SmokePing + YAML config
(cd editions/standard && ./setup.sh)                        # + web admin, PostgreSQL, REST API
(cd editions/pro      && ./setup.sh --database clickhouse)  # Pro with ClickHouse instead of InfluxDB
```

From a clone the images are built locally rather than pulled (on a release tag, that release's are pulled), and `setup.sh` makes `smoking-pi` a command in every directory (a link to the checkout's `packaging/smoking-pi` in `/usr/local/bin`, or `~/.local/bin` without a passwordless sudo). A clone set up before that: `./packaging/smoking-pi link` once, or the next `upgrade` does it.

**macOS, untested** (no Mac has run it yet — the formula exists, `Formula/smoking-pi.rb`; Docker Desktop required, and Pro's host-network measurements see Docker's Linux VM, not the Mac):

```bash
brew tap estcarisimo/smoking-pi https://github.com/estcarisimo/smoking-pi
brew install smoking-pi
smoking-pi install
```

`sudo smoking-pi upgrade` after every `apt upgrade` pulls that release's images; `backup`, `restore`, `purge`, `passwords`, `doctor` are the rest of the lifecycle (all under `sudo` when packaged — `/etc/smoking-pi` is root-only, which is where the secrets are) ([docs/packaging.md](docs/packaging.md#the-command)). The `.deb` is also attached to every [GitHub release](https://github.com/estcarisimo/smoking-pi/releases) for a one-off `apt install ./smoking-pi_<version>_all.deb`.

> `.env` files (from a clone) and `/etc/smoking-pi/env` (packaged) hold real secrets; never commit or share them.

### System Requirements

- Docker Engine with the Compose v2 plugin
- A Raspberry Pi 4/5 (ARM64) or any x86-64 Linux host. Pro with Grafana + InfluxDB is comfortable on a Pi 5 with 4 GB
- Outbound ICMP (the whole point) and, for the optional integrations, outbound HTTPS

### Choose Your Edition

| Edition | Perfect for | What you get | Setup time |
|---|---|---|---|
| 🟢 **[Basic](editions/basic/)** | Home users, one box | LinuxServer.io SmokePing, targets in a YAML file, classic web UI, RRD storage | 2 min |
| 🟡 **[Standard](editions/standard/)** | Small teams | + web admin with login, PostgreSQL as the source of truth, REST API, bulk target management | 5 min |
| 🔴 **[Pro](editions/pro/)** | Everything | + Grafana dashboards, InfluxDB or ClickHouse, IPv6 + DNS probes, alerting, MCP server, AI reports, doctor | 10 min |

Upgrade path is `Basic → Standard → Pro`; `shared/scripts/migrate-to-edition.sh` backs up and migrates your data between editions.

## 📖 Usage

### Basic Usage

From any directory (`sudo` first when installed by the package):

```bash
smoking-pi                               # what is installed, whether it runs, where to open it
smoking-pi --help                        # every command
smoking-pi passwords                     # URLs, usernames, health checks
smoking-pi passwords --show-secrets      # ...and the secret values themselves

# Container lifecycle
smoking-pi status
smoking-pi logs grafana
smoking-pi restart

# Plain Compose works too, from the edition directory
docker compose ps
docker compose logs -f smokeping
```

### Managing Targets

- **Basic**: edit `config/targets.yaml` and restart — it is validated on startup
- **Standard / Pro**: the web admin at `http://<host>:8080` (targets, sources, countries, bulk operations), or the config-manager REST API on `127.0.0.1:5000` with a bearer token. PostgreSQL is the source of truth; the YAML files are import/export only

```bash
# REST API, from the host. .env is not exported into your shell, so read the token out of it.
TOKEN=$(grep ^CONFIG_API_TOKEN editions/pro/.env | cut -d= -f2)
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:5000/targets
curl -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:5000/generate
```

### Ask Your Network How It's Doing

Pro ships an MCP server and a ready-made agent skill, so *"how was the week?"* is answered from recorded history rather than a live probe:

```bash
# Opt in by adding the profile to .env, so a later bare `docker compose up -d` keeps it
sed -i 's/^COMPOSE_PROFILES=.*/COMPOSE_PROFILES=influxdb,mcp/' editions/pro/.env
(cd editions/pro && docker compose up -d mcp-server)
claude mcp add --transport http smokeping http://127.0.0.1:8090/mcp   # token: smoking-pi passwords --show-secrets

# Install the OpenClaw skill so a Telegram chat can ask; re-run after any change (from the checkout root)
./shared/scripts/install-openclaw-skill.sh --reload
./shared/scripts/install-openclaw-skill.sh --check     # non-zero if the copy is stale
```

Tools: `get_latency_stats`, `get_loss_events`, `get_microcut_stats`, `system_status`, `get_chart` (a PNG, on request only), `mute_alerts` / `ack_incident`, and target management. Once `PUBLIC_BASE_HOST` (and optionally `TUNNEL_BASE_HOST`) is set, every answer carries deep links into the Grafana view for that target and window; until then, answers are numbers only, on purpose. See [docs/mcp-server.md](docs/mcp-server.md) and [docs/openclaw-integration.md](docs/openclaw-integration.md).

### Alerts

```bash
# Opt in (persisted in .env); log-only until NOTIFY_MODE points it somewhere
sed -i 's/^COMPOSE_PROFILES=.*/COMPOSE_PROFILES=influxdb,alerts/' editions/pro/.env
(cd editions/pro && docker compose up -d alerter)
# Deliver to a chat via OpenClaw, or to any webhook -- see docs/alerting.md
```

Rules: target down, high loss, CPE microcut bursts, exporter stale. Each alert leads with a verdict (*🌐 not you — 12 of 16 destinations affected but your local link is clean*), attaches the chart, and links home and from-anywhere. A daily digest makes a quiet day distinguishable from a dead monitor. Muting is done by asking the assistant, capped at 24 h, and read back to you. See [docs/alerting.md](docs/alerting.md).

### Remote Access

```bash
# From the checkout root. Temporary URLs, no account needed (*.trycloudflare.com)
./shared/scripts/create-tunnel.sh create
./shared/scripts/show-tunnel-urls.sh
./shared/scripts/create-tunnel.sh stop

# Permanent tunnel with your own Cloudflare account and domain
(cd shared/cloudflare-tunnel && cp .env.template .env)   # then add CLOUDFLARE_TUNNEL_TOKEN
(cd shared/cloudflare-tunnel && docker compose up -d)
```

Anything you put a tunnel in front of should have authentication in front of the tunnel — see [SECURITY.md](SECURITY.md). Guides: [quick tunnels](docs/quick-tunnels.md), [permanent tunnels](docs/cloudflare-tunnel-setup.md).

### 🐳 Docker Usage

Everything is Compose. Optional services are behind profiles so the default stack stays small. `setup.sh` writes `COMPOSE_PROFILES` into `.env`; edit that line to opt in, because a profile given only on the command line is forgotten by the next bare `docker compose up -d`:

| Profile | Adds | Needs |
|---|---|---|
| `influxdb` *(default via setup.sh)* | InfluxDB 2.x + the RRD→Influx exporter | — |
| `clickhouse` | ClickHouse + its exporter and dashboards (`-f docker-compose.clickhouse.yml`) | see [docs/clickhouse.md](docs/clickhouse.md) |
| `alerts` | The alerting engine and daily digest | `NOTIFY_MODE` + delivery settings |
| `mcp` | MCP server on `127.0.0.1:8090` | `MCP_API_TOKEN` |
| `ai` | AI health reports | `ANTHROPIC_API_KEY` |
| `dns` | DNS observer (AdGuard Home) on port 53, for the router to forward the house's DNS to | `smoking-pi dns enable`; see [docs/dns-observer.md](docs/dns-observer.md) |

```bash
# In editions/pro/.env
COMPOSE_PROFILES=influxdb,alerts,mcp

# then, from editions/pro
docker compose up -d

# Pro on ClickHouse (setup.sh --database clickhouse writes COMPOSE_PROFILES=clickhouse)
docker compose -f docker-compose.yml -f docker-compose.clickhouse.yml up -d

# Rebuild one service after pulling changes
docker compose build web-admin && docker compose up -d web-admin
```

## 🔧 Configuration

### Environment Variables

`setup.sh` writes `editions/<edition>/.env` from `.env.template`; every secret in the template ships empty (non-secret defaults such as `TZ=UTC` and `INFLUX_ORG=smokeping` are filled in) and each key is documented inline. The ones you are most likely to touch:

| Variable | Purpose |
|---|---|
| `TZ` | Timezone for the stack and for chart axes |
| `CONFIG_API_TOKEN`, `MCP_API_TOKEN` | Bearer tokens for the config-manager API and the MCP server |
| `WEB_ADMIN_USERNAME`, `WEB_ADMIN_PASSWORD_HASH` | Web admin login |
| `PUBLIC_BASE_HOST`, `TUNNEL_BASE_HOST` | Where readers reach Grafana/web-admin; enables deep links (at home / from anywhere) |
| `NOTIFY_MODE`, `OPENCLAW_*`, `ALERT_WEBHOOK_URL` | Alert delivery |
| `ALERT_CHARTS`, `CHART_THEME`, `CHART_HOURS` | Charts attached to alerts |
| `IPV6_MODE` | `auto` (gate on real global IPv6), `force`, or `off` |
| `ANTHROPIC_API_KEY` | AI reports and the web-admin assistant |

### Configuration Files

- **Basic**: `editions/basic/config/targets.yaml` is the whole configuration
- **Standard / Pro**: `editions/<edition>/config-manager/config/{targets,probes,sources}.yaml` (seeded from `shared/modules/config-manager/templates/` on first start; untracked; relocatable with `SMOKING_PI_CONFIG_DIR`, see `docs/packaging.md`) seed PostgreSQL on first start and serve as import/export afterwards — targets, probe definitions, and the top-sites sources (Tranco, CrUX, Cloudflare Radar) used to pick targets by country
- Generated SmokePing `Targets` / `Probes` are written by config-manager (a host directory in Pro, a named volume in Standard) and mounted into the SmokePing container; do not edit them by hand
- Grafana dashboards are provisioned from `shared/modules/grafana/provisioning/` — separate sets for InfluxDB and ClickHouse

## 🏗️ Architecture

Each edition is a Compose file that assembles services from `shared/modules/`; containers never import across each other, and the few pieces two containers share live in `shared/modules/common/`.

```text
smoking-pi/
├── editions/
│   ├── basic/                 # SmokePing + YAML
│   ├── standard/              # + config-manager, web-admin, PostgreSQL
│   └── pro/                   # + Grafana, InfluxDB/ClickHouse, alerter, MCP, AI, doctor
├── shared/
│   ├── modules/
│   │   ├── smokeping/         # LinuxServer.io SmokePing image, exporter hooks
│   │   ├── config-manager/    # Flask API: YAML ↔ PostgreSQL ↔ generated SmokePing config
│   │   ├── web-admin/         # Flask UI: targets, sources, countries, AI assistant
│   │   ├── grafana/           # Custom image with provisioned dashboards
│   │   ├── smokeping-exporters/  # RRD → InfluxDB / ClickHouse, CPE microcut detector, Wi-Fi link collector
│   │   ├── alerter/           # Rules, verdict, charts, digest, delivery
│   │   ├── mcp-server/        # MCP tools over the config API and InfluxDB
│   │   ├── ai-insights/       # Periodic AI health reports
│   │   ├── doctor/            # Static + live instrumentation checks
│   │   └── common/            # Flux helpers, chart renderer, deep links, mutes, OpenClaw client
│   ├── scripts/               # setup helpers, container management, tunnels, skill install
│   ├── docs/                  # maintenance (pending refresh)
│   └── cloudflare-tunnel/     # permanent tunnel Compose
├── docs/                      # the documentation site (mkdocs.yml): probes, alerting, MCP, tunnels, doctor, upgrades
└── examples/openclaw/         # the agent skill
```

**Data flow (Pro):** YAML → config-manager bootstraps PostgreSQL → generates SmokePing `Targets`/`Probes` → SmokePing writes RRDs → exporters push to InfluxDB/ClickHouse → Grafana, the alerter and the MCP server read the time series. Standard stops at the RRDs (SmokePing's own graphs); Basic converts the YAML straight to SmokePing config.

## 🧪 Development

### Setup Development Environment

```bash
git clone https://github.com/estcarisimo/smoking-pi.git
cd smoking-pi

# Each module is its own package; use uv or a venv per module
cd shared/modules/config-manager
uv sync            # or: python -m venv .venv && pip install -e ".[dev]"
```

### Running Tests

Every module with a `tests/` directory is discovered by CI. Tests mock the network, the database and Docker; none needs a running stack.

```bash
for m in alerter mcp-server web-admin config-manager doctor smokeping-exporters dns-observer; do
  (cd shared/modules/$m && pytest tests/ -q)
done
```

### Code Quality

```bash
# Lint (CI enforces the critical rules; modules configure ruff fully)
ruff check shared/modules editions

# The doctor: dashboards, exporters and compose defaults agree with each other
PYTHONPATH=shared/modules/doctor python -m doctor --repo-root . --verbose
# ...and against the running stack, from an edition directory
PYTHONPATH=../../shared/modules/doctor python -m doctor --repo-root ../.. --live
```

CI runs ruff, shell syntax, Compose config for every edition, Docker builds, the doctor, and module tests on Python 3.14; CodeQL runs on every PR through GitHub's default code-scanning setup rather than from the workflow file. Every change goes branch → PR → green CI → merge → deploy → smoke test; the [CHANGELOG](CHANGELOG.md) records the why, not just the what.

## 📊 Example Output

A SmokePing graph of ten days on a home line, as served by the Basic edition — the "smoke" is the spread of the individual pings around the median:

<div align="center">
  <img src="img/minimal.jpeg" alt="SmokePing graph: ten days of latency and loss" width="720"/>
</div>

An alert as it arrives in chat (Pro, `alerts` profile):

```text
🟡 warning — UBA
🌐 Not you — 12 of 16 destinations affected but your local link is clean
UBA: mean loss 22.5% over 15m
graph · per-ping · peers · edit · 🌐 anywhere
[chart attached]
```

And the answer to *"send me a picture of the gateway for the last week"* is a PNG: median latency with the spread of individual pings shaded, loss underneath on a fixed 0–100 axis, local time, and a footer naming the source — delivered into the chat so it can be forwarded to a friend or the ISP.

## 🤝 Contributing

Contributions are welcome. Please see the [Contributing Guidelines](CONTRIBUTING.md)
for setup instructions, the development workflow, and what to include in a bug report.
The workflow this repository follows for every change:

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/amazing-feature`)
3. Add or update tests next to the module you changed
4. Commit with a message that says why (`git commit -m 'feat: add amazing feature'`)
5. Push to the branch and open a Pull Request — CI must be green

### Project documentation

Everything under `docs/` is published at **[estcarisimo.github.io/smoking-pi](https://estcarisimo.github.io/smoking-pi/)** (MkDocs, built in CI, deployed from `main`).

| Document | Contents |
| --- | --- |
| [docs/getting-started.md](docs/getting-started.md) | Bare Pi to a measuring stack in seven steps: requirements, install, verification, troubleshooting |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development setup, workflow, PR expectations, the load-bearing oddities |
| [CHANGELOG.md](CHANGELOG.md) | Release history, with the reasoning behind each change |
| [AGENTS.md](AGENTS.md) | Guidance for AI coding agents working in this repo |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Community standards |
| [SECURITY.md](SECURITY.md) | Vulnerability reporting, scope, and what this stack assumes about your network |
| [docs/alerting.md](docs/alerting.md) | Rules, the verdict, charts, digest, muting, delivery, flap damping |
| [docs/mcp-server.md](docs/mcp-server.md) | MCP tools, deep links, on-request charts |
| [docs/openclaw-integration.md](docs/openclaw-integration.md) | Registering the MCP server and installing the skill for a chat assistant |
| [docs/remote-openclaw.md](docs/remote-openclaw.md) | OpenClaw on another machine: SSH tunnel, Tailscale/WireGuard, or Cloudflare Access — ranked by exposure |
| [docs/wifi.md](docs/wifi.md) | Wi-Fi uplink stats: what is recorded, from where, and how to query it |
| [docs/http-probes.md](docs/http-probes.md) | HTTP/1.1 vs 2 vs 3 fetch time and TCP handshake probes: how the version is enforced, where the data lands |
| [docs/cpe-last-mile.md](docs/cpe-last-mile.md) | Reading the physical last mile (optical/DSL/DOCSIS levels) from the CPE: what the gateway exposes, why it is parked |
| [docs/doctor.md](docs/doctor.md) | The instrumentation doctor: static and live checks |
| [docs/clickhouse.md](docs/clickhouse.md) | Running Pro on ClickHouse, and its traps |
| [docs/ipv6-gating.md](docs/ipv6-gating.md) | Why IPv6 targets disappear when there is no global IPv6 |
| [docs/ai-insights.md](docs/ai-insights.md) | AI health reports |
| [docs/upgrades.md](docs/upgrades.md) | Upgrading between versions |
| [docs/packaging.md](docs/packaging.md) | The package: the layout, the published images, the apt repository, the supported hosts, the lifecycle |
| [docs/release-acceptance.md](docs/release-acceptance.md) | The checklist every release goes through on the reference Raspberry Pi |
| [docs/quick-tunnels.md](docs/quick-tunnels.md) · [docs/cloudflare-tunnel-setup.md](docs/cloudflare-tunnel-setup.md) | Remote access: temporary tunnels without an account, permanent ones with yours |
| [docs/maintenance.md](docs/maintenance.md) | Stuck containers, volumes, cleanup — what the `smoking-pi` command does not handle, and the raw Docker behind it |
| [editions/basic](editions/basic/README.md) · [standard](editions/standard/README.md) · [pro](editions/pro/README.md) | Per-edition guides |

### Getting Help

- 📖 **Documentation**: the per-edition READMEs and `docs/`
- 🐛 **Issues**: GitHub Issues for bug reports
- 💬 **Discussions**: GitHub Discussions for questions
- 🔒 **Security**: privately, as described in [SECURITY.md](SECURITY.md) — never in a public issue

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## 🔗 Related Resources

- [SmokePing](https://oss.oetiker.ch/smokeping/) — the measurement engine, by Tobi Oetiker
- [LinuxServer.io SmokePing image](https://docs.linuxserver.io/images/docker-smokeping/) — the container the Basic edition and the Pro probes run on
- [Netflix Open Connect](https://openconnect.netflix.com/) — the CDN whose appliances the `netflix_oca` category tracks
- [Netflix OCA Locator](https://github.com/estcarisimo/Netflix-OCA-Servers-Locator) — the sibling project that discovers those appliances
- [Model Context Protocol](https://modelcontextprotocol.io/) — what the assistant integration speaks
- [Grafana](https://grafana.com/) · [InfluxDB](https://www.influxdata.com/) · [ClickHouse](https://clickhouse.com/)

## 🙏 Acknowledgements

- **Tobi Oetiker** for SmokePing and RRDtool, which still draw the best latency graph there is
- **LinuxServer.io** for a SmokePing image that is maintained
- **Tranco, the Chrome UX Report and Cloudflare Radar** for the top-sites lists that seed per-country targets
- **The Grafana, InfluxDB and ClickHouse communities** for the storage and the pictures

---

<div align="center">
  <b>Start with Basic, grow to Pro.</b><br>
  Continuous, honest network monitoring for everyone.
</div>
