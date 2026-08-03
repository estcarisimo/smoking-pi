# ClickHouse Backend

An alternative to InfluxDB for the Pro edition. InfluxDB stays the default;
ClickHouse is fully wired but less travelled.

```bash
cd editions/pro
./setup.sh --database clickhouse
# or, on an existing deployment:
COMPOSE_PROFILES=clickhouse docker compose \
  -f docker-compose.yml -f docker-compose.clickhouse.yml up -d
```

## How the pieces fit

- **Schema** — `smokeping.latency`, one row per RRD sample, created by the
  exporter (see below).
- **Exporter** — `rrd2clickhouse.py` reads the SmokePing RRDs and inserts rows.
- **Datasource** — the official `grafana-clickhouse-datasource` plugin, baked
  into the Grafana image at build time.
- **Dashboards** — eight ClickHouse variants under
  `provisioning/dashboards-clickhouse/`, provisioned only in ClickHouse mode.

## Things that are not obvious

### The schema is created by the exporter, not the init hook

`shared/modules/clickhouse/init/` is mounted at
`/docker-entrypoint-initdb.d`, but **it never runs**. Docker seeds a fresh
named volume from the image, and the official ClickHouse image ships a
populated `/var/lib/clickhouse`; its entrypoint sees "Database directory
appears to contain a database" and skips the init hook entirely. Nothing in the
logs marks this as a problem — you simply get no `smokeping` database.

The exporter therefore applies `CREATE DATABASE / TABLE IF NOT EXISTS` on
connect. That is idempotent and works whether or not the hook ever fires. The
SQL files are kept as documentation of the intended schema.

### The datasource takes host/port, not url

The official plugin reads `host` and `port` from `jsonData` and **ignores** the
top-level `url` field that most Grafana datasources use. Configure only `url`
and its health check fails with `[config] invalid server host. Either empty or
not set` — which reads like a networking problem and is not one.

### ClickHouse mode builds its own provisioning tree

`editions/pro` bind-mounts `provisioning/` read-only, and Docker cannot create
a mountpoint for a file inside a read-only bind mount — so neither the
entrypoint nor a compose overlay can drop a provider file into the scanned
directory. In ClickHouse mode the entrypoint instead builds a writable tree at
`/var/lib/grafana/provisioning-active` containing only the ClickHouse
datasource and the ClickHouse dashboard provider, and points
`GF_PATHS_PROVISIONING` at it. The InfluxDB dashboards are deliberately absent
— they query a datasource that is not configured in this mode.

### Loss is a percent, and the denominator comes from the RRD

`packet_loss` is 0–100. The RRD `loss` data source is a **count** of lost
pings, and probes disagree on how many they send (FPing 10, DNS 5), so the
exporter reads the count of `ping1..pingN` data sources from each RRD and
divides by that. This matches `rrd2influx.py`, which stores the same quantity
as a 0–1 ratio.

`category` and `measurement_type` use the same vocabulary as the InfluxDB
exporter — `topsites` / `netflix` / `dns` / `custom`, and `latency` /
`dns_latency` — not the raw RRD directory names. Dashboard predicates must
match that vocabulary; filtering on `category = 'DNS_Resolvers'` returns
nothing.

## Verifying a deployment

```bash
# schema present
docker compose exec clickhouse clickhouse-client \
  --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" \
  --query "SHOW TABLES FROM smokeping"

# rows arriving
docker compose exec clickhouse clickhouse-client \
  --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" \
  --query "SELECT count(), max(timestamp) FROM smokeping.latency"

# datasource health (expects "Data source is working")
curl -s -u "admin:$GF_SECURITY_ADMIN_PASSWORD" \
  http://localhost:3000/api/datasources/uid/clickhouse/health
```

Grafana's boot log should show:

```
grafana-entrypoint: building ClickHouse provisioning tree in /var/lib/grafana/provisioning-active
grafana-entrypoint: ClickHouse dashboard provider activated
grafana-entrypoint: datasources provisioned from /var/lib/grafana/provisioning-active
```

## Switching back to InfluxDB

Set `TSDB_TYPE=influxdb` and start with the `influxdb` profile. The
ClickHouse provisioning tree is rebuilt only in ClickHouse mode, so nothing
needs cleaning up. Data does not migrate between backends.
