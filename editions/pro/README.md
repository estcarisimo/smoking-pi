# Pro edition

Everything: SmokePing with ICMP, DNS, HTTP/1.1, /2, /3 and TCP probes and
IPv6 gating; the web admin and PostgreSQL as the configuration's source of
truth; Grafana over InfluxDB (the default) or ClickHouse; Wi-Fi and uplink
statistics; the instrumentation doctor. Optional services, off until you
turn them on: the MCP server for assistants (`mcp`), alerting and a daily
digest (`alerts`), AI reports (`ai`), the experimental congestion inference
(`inference`) and the DNS observer (`dns`). [Getting started](../../docs/getting-started.md),
step 3, compares the editions.

You do not run anything in this directory by hand. Install and operate the
stack with the `smoking-pi` command:

```bash
sudo smoking-pi install                  # once (Pro is preselected); from a clone, see Getting started
smoking-pi url                           # the address to open
smoking-pi enable mcp alerts             # optional services; 'disable' turns them off
smoking-pi alerts --telegram             # where alerts go
smoking-pi connect --tailscale           # publish the MCP server for a remote assistant
smoking-pi doctor --live                 # do dashboards, exporters and data agree?
```

(`sudo` for a package install, whose env file lives under `/etc`; from a
clone, none.) `status`, `logs`, `restart`, `config`, `links`, `openclaw`,
`dns`, `budget`, `traffic`, `tunnel`, `backup`, `restore`, `upgrade` and
the rest are in `smoking-pi --help`.

## What is here

| File | Role |
| --- | --- |
| `docker-compose.yml` | The stack `smoking-pi` runs (InfluxDB backend, optional services by profile) |
| `docker-compose.clickhouse.yml` | Added when the backend is ClickHouse |
| `docker-compose.packaged.yml` | Added for a package install: drops the clone's source bind-mounts |
| `.env.template` | Every setting, documented; `install` writes the env file from it |
| `setup.sh` | Run by `smoking-pi install`; not meant to be run by hand |
| `sync-influx-token.sh` | Run by `smoking-pi restart`: checks Grafana's InfluxDB token still works |
| `verify-postgres.sh` | Run by `setup.sh`: checks PostgreSQL's health |
| `custom-cont-init.d/` | SmokePing container start-up: link the generated config, guard the RRDs, run the exporter |
| `config-manager/` | Runtime state (gitignored): `config/` YAML import/export, `output/` the generated SmokePing config |
| `DNS_MONITORING.md` | Notes on the DNS resolver probes and their dashboard |

## Read next

- [Getting started](../../docs/getting-started.md), [Maintenance and cleanup](../../docs/maintenance.md), [Upgrading](../../docs/upgrades.md), [Packaging](../../docs/packaging.md)
- [Alerting](../../docs/alerting.md), [MCP server](../../docs/mcp-server.md), [Connecting any assistant](../../docs/remote-connector.md), [OpenClaw](../../docs/openclaw-integration.md)
- [ClickHouse backend](../../docs/clickhouse.md), [DNS observer](../../docs/dns-observer.md), [Instrumentation doctor](../../docs/doctor.md)
- [Measurement budget](../../docs/measurement-budget.md), [HTTP and TCP probes](../../docs/http-probes.md), [Congestion inference](../../docs/inference.md)
- [Quick tunnels](../../docs/quick-tunnels.md) and [Cloudflare tunnels](../../docs/cloudflare-tunnel-setup.md)
