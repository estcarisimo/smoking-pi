# Standard edition

SmokePing plus a web admin with a login, PostgreSQL as the source of truth
for the configuration, and the config-manager REST API that turns it into
SmokePing's `Targets` and `Probes`. Graphs are SmokePing's own, from its
RRD files; Pro adds Grafana, InfluxDB or ClickHouse, alerting, the MCP
server and more ([Getting started](../../docs/getting-started.md), step 3,
compares the editions).

You do not run anything in this directory by hand. Install and operate the
stack with the `smoking-pi` command:

```bash
sudo smoking-pi install --edition standard   # once; from a clone, see Getting started
smoking-pi url                               # the address to open, and the username
smoking-pi passwords --show-secrets          # the web admin password
smoking-pi status                            # what is running
smoking-pi budget                            # what the configured measurements cost
```

(`sudo` for a package install, whose env file lives under `/etc`; from a
clone, none.) Targets are edited in the web admin. `up`, `down`,
`restart`, `logs`, `config`, `backup`, `restore`, `upgrade` and the rest
are in `smoking-pi --help`.

## What is here

| File | Role |
| --- | --- |
| `docker-compose.yml` | The stack `smoking-pi` runs |
| `docker-compose.packaged.yml` | Added for a package install: drops the clone's source bind-mounts |
| `.env.template` | Every setting, documented; `install` writes the env file from it |
| `setup.sh` | Run by `smoking-pi install`; not meant to be run by hand |
| `custom-cont-init.d/` | SmokePing container start-up: link the generated config, guard the RRDs |
| `config-manager/config/` | Runtime state (gitignored): the YAML that seeds PostgreSQL, then import/export |

## Read next

- [Getting started](../../docs/getting-started.md): install, first checks
- [Maintenance and cleanup](../../docs/maintenance.md): stop, start, volumes, disk
- [Upgrading](../../docs/upgrades.md) and [Packaging](../../docs/packaging.md)
- [Measurement budget](../../docs/measurement-budget.md)
- [Quick tunnels](../../docs/quick-tunnels.md): `smoking-pi tunnel`
