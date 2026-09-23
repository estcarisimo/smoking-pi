# Maintenance: stuck containers, volumes, cleanup

The `smoking-pi` command does the lifecycle
([Packaging](packaging.md#the-command)): `up`, `down`, `restart`, `status`,
`logs`, `upgrade`, `backup`, `restore`, `purge`. From a clone it is
`packaging/smoking-pi`; from the package, `/usr/bin/smoking-pi`.
Everything below is what to do when the stack is in a state the command
does not handle, and the raw Docker commands behind it. Names are never
guessed here: Compose labels every container, volume and network with the
project, and the project name is whatever `COMPOSE_PROJECT_NAME` in the
env file says (default: the edition directory's name — `basic`,
`standard`, `pro`).

## Where things are

```bash
smoking-pi paths            # home, edition, env file, config, output, images, data
smoking-pi status           # docker compose ps for the edition
docker volume ls --filter label=com.docker.compose.project=pro
docker network ls --filter label=com.docker.compose.project=pro
```

Basic and Standard declare fixed names for some resources
(`smokeping-basic`, `smokeping-standard-*`, volumes `smokeping-basic-*`,
`smokeping-standard-*`); Pro's are `<project>-<service>-1` and
`<project>_<volume>`. `docker compose ps`/`config` from the edition
directory shows the real ones — use those, not this page's examples.

## Stop and start

```bash
smoking-pi down             # stop and remove the containers; volumes stay
smoking-pi up               # start again with the recorded profiles
smoking-pi restart          # restart in place: docker compose restart
```

`restart` is not `down` then `up`. It restarts the running containers
without recreating them, so it does not pick up a changed compose file,
image or env file — for those, `down` then `up`.

**On Pro it does not re-sync the InfluxDB token.** That is
`manage-containers.sh`, not the command:

```bash
# from the repository root
shared/scripts/manage-containers.sh --action restart --edition pro
```

which restarts and then runs `sync-influx-token.sh`, so Grafana keeps
reaching InfluxDB after the token in the env file and the token in the
volume have diverged. If Grafana's InfluxDB panels are empty after a
restart while the data is arriving, that divergence is the first thing to
check — `editions/pro/sync-influx-token.sh` on its own fixes it without a
restart.

From a clone without the command, from the edition directory:

```bash
docker compose down
docker compose up -d
```

Pro with ClickHouse adds `-f docker-compose.yml -f
docker-compose.clickhouse.yml`; the packaged layout adds
`docker-compose.packaged.yml` last. The command and
`shared/scripts/manage-containers.sh` assemble that list from the env
file; by hand it is easy to forget the overlay and silently render the
InfluxDB stack.

## A container that will not stop

```bash
docker compose ps                         # the name, from the edition directory
docker kill <name> && docker rm -f <name> # last resort
smoking-pi up                             # recreate it
```

"Network … has active endpoints" on `down`: something outside Compose
(a tunnel from `create-tunnel.sh`, a one-off `docker run`) is attached.
`docker network inspect <project>_default --format '{{range .Containers}}{{.Name}} {{end}}'`
names it; stop that, then `down` again.

## Data: what is where, and what deletes it

- **Measurements** (RRD, InfluxDB/ClickHouse, PostgreSQL) live in Docker
  volumes. `down`, `apt remove`, `apt purge` and a `git pull` never touch
  them.
- **Secrets** are the env file (`.env` beside the edition, or
  `/etc/smoking-pi/env` packaged). The databases in the volumes were
  initialized with those secrets: deleting the file without the volumes
  makes the data unreadable, which is why `apt purge` keeps it
  ([Upgrading](upgrades.md#uninstalling)).
- **Config** (`config-manager/config`, or `/etc/smoking-pi/config`) is
  seeded on first start and edited by you; **output** is regenerated.

```bash
smoking-pi backup                # before anything below: pg_dumpall + every volume + env + config
smoking-pi purge                 # the volumes, after typing the project name
smoking-pi purge --config        # also the env file, config and output: a clean slate
```

Raw equivalents, if the command is unavailable — from the edition directory:

```bash
docker compose down -v           # containers AND this project's volumes
```

`docker compose down -v` skips volumes declared with a fixed `name:`
(Basic/Standard); `docker volume ls` shows what is left, `docker volume rm`
takes it. `docker system prune -a --volumes` removes **every** unused
image, network and volume on the host, not only this project's — never
on a machine that runs anything else.

## Disk

```bash
docker system df -v              # images, containers, volumes with sizes
docker compose logs --tail 0     # nothing; the log driver caps at LOG_MAX_SIZE x LOG_MAX_FILE per container (Pro)
du -sh /var/lib/docker/volumes/<volume>/_data   # as root
```

The RRD files grow to a fixed size per target and stop. InfluxDB and
ClickHouse retain what their retention policy says ([ClickHouse
backend](clickhouse.md) for the latter). PostgreSQL is small (targets
and config).

## Emergency recovery

1. `smoking-pi backup` if the volumes are readable at all (it stops the
   stack for the tarballs; `--online` if it must keep running).
2. `smoking-pi down`, then `smoking-pi up`. Most "stuck" states are a
   container that Docker's restart policy is looping; `docker compose
   logs <service> --tail 100` says why.
3. `smoking-pi upgrade` re-pulls (packaged) or rebuilds (clone) the
   images and recreates what changed; the doctor `--live` at the end
   says whether the deployed code is the code in the tree.
4. `smoking-pi restore <backup>` — after typing the project name it
   replaces the volumes' contents from the tarballs.

## Related

- [Packaging](packaging.md) — the command, the packaged layout, the images
- [Upgrading](upgrades.md) — PostgreSQL/InfluxDB/Grafana majors, uninstalling
- [Instrumentation doctor](doctor.md) — what the doctor checks
