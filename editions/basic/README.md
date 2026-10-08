# Basic edition

SmokePing on its own: the LinuxServer.io-based SmokePing image, targets in
a YAML file, SmokePing's classic web UI and RRD storage. No database, no
login, no API. It is for one box and one person; Standard adds a web admin,
PostgreSQL and a REST API, and Pro adds Grafana, a time-series database,
alerting and the rest ([Getting started](../../docs/getting-started.md),
step 3, compares them).

You do not run anything in this directory by hand. Install and operate the
stack with the `smoking-pi` command:

```bash
sudo smoking-pi install --edition basic   # once; from a clone, see Getting started
smoking-pi url                            # the address to open
smoking-pi status                         # what is running
smoking-pi restart                        # after editing config/targets.yaml
```

(`sudo` for a package install, whose env file lives under `/etc`; from a
clone, none.) `up`, `down`, `logs`, `backup`, `restore`, `upgrade` and the
rest are in `smoking-pi --help`.

## What is here

| File | Role |
| --- | --- |
| `docker-compose.yml` | The stack `smoking-pi` runs: SmokePing and the mDNS responder |
| `.env.template` | Every setting, documented; `install` writes the env file from it |
| `setup.sh` | Run by `smoking-pi install`; not meant to be run by hand |
| `docker-entrypoint.sh` | SmokePing's entrypoint: converts `config/targets.yaml` before starting |
| `scripts/yaml2targets.py` | The converter; a YAML error stops the container |
| `config/targets.yaml` | The whole configuration: what to measure |

`config/targets.yaml` groups hosts; a group or a host may set `probe`
(default `FPing`):

```yaml
targets:
  my_sites:                 # group id, no spaces
    title: "My sites"
    hosts:
      - name: MySite        # unique, no spaces
        host: www.example.com
        title: "My site"
```

## Read next

- [Getting started](../../docs/getting-started.md): install, first checks
- [Maintenance and cleanup](../../docs/maintenance.md): stop, start, volumes, disk
- [Upgrading](../../docs/upgrades.md) and [Packaging](../../docs/packaging.md)
- [Quick tunnels](../../docs/quick-tunnels.md): `smoking-pi tunnel`
