# Upgrading the stateful images

Most base-image bumps here are safe and Dependabot can be trusted with them:
a new `python:3.14-slim` or `grafana-oss` patch changes what runs, not what is
stored, and a green CI build is real evidence.

Two images are different, because they own volumes: **postgres** and
**influxdb**. For those, a major bump is a data migration wearing a version
string, and **CI cannot validate it** — the Docker build matrix does not build
the postgres image at all, and no CI job has an existing volume to start
against. A green check on such a PR means nothing was tested. Dependabot is
configured to leave these alone (`.github/dependabot.yml`); do them by hand,
deliberately, using the procedures below.

## PostgreSQL

PostgreSQL is this project's **config source of truth** — the YAML files are
import/export only. Losing the volume means losing every target definition.

Two independent things break on a major bump, and the second is worse:

1. **The data directory is version-locked.** Pointed at a PG15 directory, PG18
   exits immediately:

   ```
   FATAL:  database files are incompatible with server
   DETAIL: The data directory was initialized by PostgreSQL version 15,
           which is not compatible with this version 18.6.
   ```

   Loud, and safe — nothing is damaged.

2. **PG18 moved its default `PGDATA`**, from `/var/lib/postgresql/data` to
   `/var/lib/postgresql/18/docker`. Our compose mounts the volume at the old
   path, so the entrypoint does not find a cluster where it now looks, reports
   *"Database is uninitialized"*, and — given a password — would **initialise a
   brand new empty cluster**, leaving the real data orphaned in the volume.

   This one **fails open**: the container comes up healthy, config-manager
   bootstraps into an empty database, and the symptom is every target quietly
   disappearing rather than an error. Assume this mode, not the first.

### Procedure

Dump before touching anything. The dump is the rollback.

```bash
cd editions/pro
docker compose exec postgres pg_dumpall -U "$POSTGRES_USER" \
  > ~/pg-backup-$(date +%F).sql
```

Then either restore into a fresh volume of the new major:

```bash
docker compose down postgres
docker volume rm pro_postgres-data          # only after the dump is verified
# bump the FROM in shared/modules/postgres/Dockerfile, then:
docker compose build postgres && docker compose up -d postgres
docker compose exec -T postgres psql -U "$POSTGRES_USER" < ~/pg-backup-YYYY-MM-DD.sql
```

or run `pg_upgrade` with both binaries present. The dump/restore path is
slower and much harder to get wrong; this database is small.

Afterwards, confirm the data actually arrived — a healthy container proves
nothing here:

```bash
curl -s -H "Authorization: Bearer $CONFIG_API_TOKEN" \
  http://127.0.0.1:5000/targets | jq '.total'
```

If `PGDATA` moved again, also update the volume's mount point in
`editions/*/docker-compose.yml` to match the new default rather than relying
on it.

## Grafana

Grafana migrates its own database on first boot of a new version, and
**there is no downgrade**. Once 13.x has migrated `grafana-data`, going back
to 12.x needs a volume restore.

It is otherwise low-risk: 13.0.2 boots clean on this stack (verified against a
fresh volume — healthy, migrations completed, no plugin errors with
`GF_INSTALL_PLUGINS` empty). The dashboards are provisioned from files, so
they are not at risk; only Grafana's own state is.

```bash
docker run --rm -v pro_grafana-data:/from -v "$PWD":/to alpine \
  tar czf /to/grafana-data-$(date +%F).tgz -C /from .
```

Then bump `shared/modules/grafana/Dockerfile`, rebuild, and check
`docker compose logs grafana` for `migrations completed` before assuming it
worked.

## Verifying any upgrade

`doctor --live` is the check that the thing you built is the thing that is
running — it compares the sha256 of every deployed `.py` against the
repository, which is how a masked build failure gets caught rather than
silently serving a three-week-old image:

```bash
PYTHONPATH=shared/modules/doctor python -m doctor --repo-root . --live
```
