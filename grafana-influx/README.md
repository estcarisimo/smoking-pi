# SmokePing 🔗 InfluxDB 📈 Grafana

Full‑stack latency monitoring with modern time‑series storage and rich dashboards – **all containerized, each service in its own image**.

---

## Table of Contents

1. [Architecture](#architecture)
2. [TL;DR — Quick start](#tldr)
3. [Directory layout](#layout)
4. [Building the images](#build)
5. [Running with Docker Compose](#run)
6. [Component reference](#components)
   * [SmokePing image](#smokeping)
   * [RRD → InfluxDB exporter](#exporter)
   * [InfluxDB image](#influxdb)
   * [Grafana image + provisioning](#grafana)
7. [Dashboards](#dashboards)
8. [Environment variables](#env)
9. [Troubleshooting & tips](#troubleshooting)

---

## <a id="architecture"></a> 🗺️ Architecture

```text
┌────────────┐   RRD files      ┌──────────────┐  Flux queries   ┌──────────────┐
│ SmokePing  │──────────────▶   │ InfluxDB 2.x │──────────────▶│   Grafana     │
│ (FCGI + CGI)│                 │ (latency bucket)│                │ dashboards    │
└───▲───┬────┘  exporter.py     └───────▲──────┘                └──────▲──────┘
    │   │                            HTTP 8086                      HTTP 3000
    │   └─ HTTP 80 / PNG graphs
    └───── probes (fping / echoping)
```

### 🔄 **Data Flow**
1. **Probing**: SmokePing runs FPing & DNS probes every 5 minutes
2. **Storage**: Results stored in RRD files with directory-based classification
3. **Export**: Python exporter monitors RRD changes and pushes to InfluxDB
4. **Visualization**: Grafana queries InfluxDB using Flux for real-time dashboards

### 🎯 **Key Features**
- **Dual Storage**: Classic RRD files **+** modern time-series database
- **Smart Classification**: Automatic separation of ping vs DNS latency data
- **Rich Dashboards**: Percentile analysis, side-by-side comparison, DNS monitoring
- **Filtered Views**: Dashboards show only relevant targets (no DNS in ping dashboards)

📋 **For detailed architecture diagrams and component interaction, see [ARCHITECTURE.md](ARCHITECTURE.md)**

---

## <a id="tldr"></a>🚀 TL;DR — Quick start

```bash
# Clone the repo & build everything once
$ git clone https://github.com/estcarisimo/smoking-pi.git
$ cd smoking-pi/grafana-influx

# Generate secure passwords first
$ ./init-passwords.sh

# Build & launch the full stack
$ docker compose build  # or: docker-compose build
$ docker compose up -d

# After ~30 sec…
🖥️  SmokePing  → http://localhost:8080/smokeping
📈  Grafana    → http://localhost:3000  (admin / admin)
```

---

## <a id="layout"></a>📁 Directory layout

```text
grafana-influx/
├─ smokeping/            # SmokePing image (+ exporter)
│  ├─ Dockerfile
│  ├─ config/            # Targets, Probes, etc.
│  ├─ exporter/          # RRD → Influx script
│  │  └─ rrd2influx.py
│  └─ docker-entrypoint.sh
├─ influxdb/             # InfluxDB image (thin wrapper around official)
│  └─ Dockerfile
├─ grafana/              # Grafana image with zero‑touch provisioning
│  ├─ Dockerfile
│  └─ provisioning/
│     ├─ datasources/
│     │   └─ influxdb.yaml
│     └─ dashboards/
│         ├─ dashboard.yaml
│         ├─ smokeping_latency.json
│         ├─ smokeping_latency_compare.json
│         └─ smokeping_resolvers.json
├─ docker-compose.yml
├─ init-passwords.sh     # Password generation script
├─ .env.template         # Environment template
├─ .gitignore           # Prevents committing secrets
└─ README.md            # you‑are‑here
```

---

## <a id="build"></a>🏗️ Building the images

```bash
# Build just one image
$ docker compose build smokeping

# Or build the trio
$ docker compose build
```

The **InfluxDB** and **Grafana** images are mere customisations of their official counterparts, so they download quickly.

---

## <a id="run"></a>▶️ Running with Docker Compose

```bash
# Spin up (detached) – creates named volumes for persistence
$ docker compose up -d

# Tails logs for all containers
$ docker compose logs -f
```

The first run initialises InfluxDB (organisation *smokingpi*, bucket *latency*) and Grafana (datasource + example dashboard auto‑provisioned).

Stop & delete everything:

```bash
$ docker compose down -v   # -v wipes the volumes ➜ fresh start
```

---

## <a id="components"></a>⚙️ Component reference

### <a id="smokeping"></a>1. SmokePing image (`smokeping/Dockerfile`)

A multi-stage Debian-based image that bundles:

- **SmokePing** with FPing and DNS probes
- **echoping-ng** for DNS latency monitoring
- **Lighttpd + fcgiwrap** for web interface
- **Python 3** with InfluxDB client for data export
- **Custom entrypoint** that coordinates all services

<details><summary>Show Dockerfile highlights</summary>

```Dockerfile
# Multi-stage build with dependency cleanup
FROM debian:stable-slim

# Install SmokePing + web server
RUN apt-get update && apt-get install -y --no-install-recommends \
    smokeping fping spawn-fcgi fcgiwrap lighttpd \
    python3 python3-pip python3-rrdtool

# Build echoping-ng from source for DNS monitoring
RUN git clone https://github.com/RaymiiOrg/echoping-ng.git && \
    cmake . && make install

# Copy configurations and exporter
COPY config/Targets /etc/smokeping/config.d/Targets
COPY config/Probes  /etc/smokeping/config.d/Probes
COPY exporter/rrd2influx.py /usr/local/bin/

EXPOSE 80
CMD ["/usr/local/bin/docker-entrypoint.sh"]
```

</details>

**Entry point orchestration:**

```bash
# Wait for InfluxDB to be healthy
# Start fcgiwrap for CGI execution
spawn-fcgi -s /var/run/fcgiwrap.socket -u www-data -g www-data /usr/sbin/fcgiwrap

# Start Lighttpd web server (background)
lighttpd -D -f /etc/lighttpd/lighttpd.conf &

# Launch RRD exporter (background)
/usr/local/bin/rrd2influx.py &

# Run SmokePing in foreground (PID 1)
exec smokeping --nodaemon
```

---

### <a id="exporter"></a> 2. RRD → InfluxDB exporter (`smokeping/exporter/rrd2influx.py`)

A Python script that monitors RRD files and exports data to InfluxDB in real-time:

**Key Features:**
- **File Monitoring**: Watches `/var/lib/smokeping/` for RRD changes
- **Smart Classification**: Routes data based on directory structure
  - `/var/lib/smokeping/resolvers/` → `dns_latency` measurement
  - `/var/lib/smokeping/` → `latency` measurement
- **Rich Data Export**: Exports median, loss, and all individual ping values
- **Batch Processing**: Efficient batched writes to InfluxDB
- **Error Handling**: Robust error handling with logging

**How it works:**
1. Scans RRD directory every 30 seconds
2. Reads new data points using `rrdtool fetch`
3. Converts to InfluxDB line protocol
4. Sends HTTP POST to InfluxDB API
5. Handles authentication via environment variables

**Testing the data flow:**

Use the InfluxDB UI to verify data:
1. Open http://localhost:8086 in your browser
2. Log in with the admin credentials from your `.env` file
3. Click Data Explorer → Script Editor
4. Paste a Flux query, e.g.:

```influxdb
from(bucket: "latency")
  |> range(start: -15m)          // last 15 minutes
  |> filter(fn: (r) => r._measurement == "latency")
  |> filter(fn: (r) => r.target == "Apple")
  |> yield(name: "raw")
```

---

### <a id="influxdb"></a>3. InfluxDB image (`influxdb/Dockerfile`)

A minimal wrapper around the official InfluxDB 2.7 image:

```Dockerfile
FROM influxdb:2.7

# Expose web & API ports
EXPOSE 8086
```

**Configuration:**
- **Auto-initialization**: Docker Compose sets up organization, bucket, and admin user
- **Persistence**: Data stored in named Docker volume `influxdb-data`
- **Security**: Admin password and API token generated by `init-passwords.sh`

**Environment Variables:**
- `DOCKER_INFLUXDB_INIT_MODE=setup` - Auto-setup on first run
- `DOCKER_INFLUXDB_INIT_USERNAME=admin` - Admin username
- `DOCKER_INFLUXDB_INIT_PASSWORD` - Generated admin password
- `DOCKER_INFLUXDB_INIT_ORG=smokingpi` - Organization name
- `DOCKER_INFLUXDB_INIT_BUCKET=latency` - Default bucket
- `DOCKER_INFLUXDB_INIT_ADMIN_TOKEN` - API token for applications

> ✨ **Note**: This simple wrapper could be replaced with the official `influxdb:2.7` image directly in `docker-compose.yml`, but having a dedicated Dockerfile allows for future customizations.

---

### <a id="grafana"></a>4. Grafana image + provisioning (`grafana/Dockerfile`)

Grafana OSS with zero-touch provisioning for datasources and dashboards:

```Dockerfile
FROM grafana/grafana-oss:10.4.2

# Pre-provision datasources and dashboards
COPY provisioning/ /etc/grafana/provisioning/

EXPOSE 3000
```

**Auto-Provisioning Configuration:**

**Datasource** (`grafana/provisioning/datasources/influxdb.yaml`):
```yaml
apiVersion: 1
envInterpolation: true

datasources:
  - name: InfluxDB
    uid: influxdb
    type: influxdb
    access: proxy
    url: http://influxdb:8086
    isDefault: true
    
    jsonData:
      version: Flux
      organization: ${INFLUX_ORG}
      defaultBucket: ${INFLUX_BUCKET}
      
    secureJsonData:
      token: ${INFLUX_TOKEN}
```

**Dashboard loader** (`grafana/provisioning/dashboards/dashboard.yaml`):
```yaml
apiVersion: 1

providers:
  - name: SmokePing
    folder: SmokePing
    type: file
    editable: true
    options:
      path: /etc/grafana/provisioning/dashboards
      updateIntervalSeconds: 30
```

**Pre-built dashboards**:
- `smokeping_latency.json` - Percentile analysis with loss graphs
- `smokeping_latency_compare.json` - Side-by-side mosaic comparison
- `smokeping_resolvers.json` - DNS resolver performance monitoring

---

## <a id="env"></a>🌐 Environment variables

| Variable        | Purpose                         | Default (docker‑compose.yml) |
| --------------- | ------------------------------- | ---------------------------- |
| `INFLUX_URL`    | Base URL of the InfluxDB server | `http://influxdb:8086`       |
| `INFLUX_TOKEN`  | API token with write perms      | Generated by `init-passwords.sh` |
| `INFLUX_ORG`    | InfluxDB organisation           | `smokingpi`                  |
| `INFLUX_BUCKET` | Bucket name                     | `latency`                    |
| `RRD_DIR`       | Where SmokePing stores RRDs     | `/var/lib/smokeping`         |
| `DOCKER_INFLUXDB_INIT_PASSWORD` | InfluxDB admin password | Generated by `init-passwords.sh` |

Set them in **`docker-compose.yml`** or in `.env`.

> **⚠️ Security Note:** Always run `./init-passwords.sh` before deploying to generate secure random passwords. The `.env` file is gitignored to prevent accidental commits of secrets.

### Complete `.env` file (copy‑me)

After running `./init-passwords.sh`, your `.env` file will contain:

```bash
# InfluxDB connection
INFLUX_URL=http://influxdb:8086
INFLUX_TOKEN=<generated-secure-token>
INFLUX_ORG=smokingpi
INFLUX_BUCKET=latency

# SmokePing RRD directory
RRD_DIR=/var/lib/smokeping

# Generated passwords
DOCKER_INFLUXDB_INIT_PASSWORD=<generated-secure-password>
```

### `docker-compose.yml` usage

The compose file uses environment variable substitution:
```yaml
environment:
  INFLUX_TOKEN: ${INFLUX_TOKEN}
  DOCKER_INFLUXDB_INIT_PASSWORD: ${DOCKER_INFLUXDB_INIT_PASSWORD}
```

Place the `.env` file in the same directory as `docker-compose.yml`; Docker Compose will load it automatically and substitute every `${VAR}` placeholder.

---

## <a id="dashboards"></a>🎛️ Dashboards

### Available Dashboards
- **`smokeping_latency.json`** - Main latency dashboard with percentiles and loss graphs
- **`smokeping_latency_compare.json`** - Mosaic comparison view showing all providers side-by-side
- **`smokeping_resolvers.json`** - DNS resolver performance monitoring

The shipped JSONs are seeds – duplicate & extend them via Grafana's UI: add loss %, min/max, per‑target drilldowns, alerts, etc.


---

## <a id="troubleshooting"></a>🛠️ Troubleshooting

### Common Issues

**Nothing in Grafana?**
- Check exporter logs: `docker compose logs smokeping`
- Verify InfluxDB connection: `docker compose logs influxdb`
- Check token/URL in `.env` file

**Dashboard shows no data?**
- Ensure SmokePing is running: `docker compose ps`
- Check RRD files exist: `docker exec -it grafana-influx-smokeping-1 ls -la /var/lib/smokeping/`
- Verify exporter is working: `docker compose logs smokeping | grep "exporter"`

**InfluxDB errors?**
- *413 request too large*: Lower batch size in exporter `WriteOptions`
- *Unauthorized*: Check `INFLUX_TOKEN` in `.env` matches database
- *Connection refused*: Ensure InfluxDB is healthy before starting other services

**Performance issues?**
- **Memory**: Increase Docker memory allocation for InfluxDB
- **Storage**: Monitor disk usage - InfluxDB data grows over time
- **Network**: High-frequency probing can impact network performance

### Maintenance

**Long‑term storage backup:**
```bash
# Backup InfluxDB data
docker run --rm -v influxdb-data:/data -v $(pwd):/backup \
  busybox tar czf /backup/influxdb-backup.tgz /data

# Backup Grafana configuration
docker run --rm -v grafana-data:/data -v $(pwd):/backup \
  busybox tar czf /backup/grafana-backup.tgz /data
```

**Reset everything:**
```bash
# Stop and remove all containers, networks, and volumes
docker compose down -v
# Remove all images
docker compose build --no-cache
# Fresh start
./init-passwords.sh && docker compose up -d
```

---

## License

MIT for scripts; SmokePing itself is GPLv2.