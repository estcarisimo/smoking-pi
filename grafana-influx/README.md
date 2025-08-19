# SmokePing Full Stack - Advanced Network Monitoring

<div align="center">
  <img src="../img/logo.jpg" alt="Smoking Pi Logo" width="150"/>
</div>

Complete network monitoring solution with **SmokePing**, **InfluxDB**, **Grafana**, **PostgreSQL**, **Web Admin**, and **Config Manager** – all containerized with database-first architecture, zero-touch deployment, and enhanced DNS monitoring.

## 🎯 What's Included

* **SmokePing** – Core latency monitoring with RRD data collection and DNS resolution timing
* **InfluxDB 2.x** – Modern time-series database with dual measurements (`latency` + `dns_latency`)
* **Grafana** – Professional dashboards with PostgreSQL-driven template variables and percentile analysis
* **PostgreSQL** – Centralized target management database with normalized schema and active/inactive status
* **Web Admin** – Database-first target management interface with bulk operations and migration tools
* **Config Manager** – Database-aware configuration system with automatic YAML fallback
* **RRD→InfluxDB Exporter** – Real-time data synchronization for all target categories

## 🚀 Quick Start

```bash
cd grafana-influx
./init-passwords-docker.sh  # Generate secure credentials (including PostgreSQL)
docker-compose up -d         # Deploy full stack with PostgreSQL database
```

**Access Points:**
- SmokePing: http://localhost:8081 - Classic SmokePing web interface
- Web Admin: http://localhost:8080 - Target management and configuration
- Grafana: http://localhost:3000 (admin/admin) - Professional dashboards
- InfluxDB: http://localhost:8086 - Time-series database API

## ✨ Latest Improvements

### 🆕 **PostgreSQL Database Integration**
- **Database-First Architecture**: All targets stored in normalized PostgreSQL database with proper relationships
- **Active/Inactive Status**: Toggle targets on/off without deletion, with web interface controls
- **Seamless Migration**: Zero-downtime migration from existing YAML configurations to database
- **RESTful API**: Full CRUD operations for programmatic target management with validation
- **Hybrid Fallback**: Intelligent detection with automatic YAML compatibility mode
- **Grafana Integration**: Dashboard template variables now populate directly from PostgreSQL

### **Enhanced Dashboard Organization**
- **3 Separate Folders**: Side-by-Side Pings, Individual Pings, DNS Resolution Times
- **Template Variable Fixes**: Dashboard dropdowns now properly populate with target names
- **DNS Monitoring**: Dedicated dashboards for DNS resolution time analysis with percentiles

### **Robust Data Pipeline**
- **Dual Measurements**: `latency` measurement for ping data, `dns_latency` for DNS resolution
- **Complete Export Coverage**: Handles all target categories (TopSites, Custom, Netflix, DNS)
- **Real-time Synchronization**: Python exporter processes all RRD files every 60 seconds

### **DNS Resolution Monitoring**
- **Multiple Resolvers**: Google DNS, Cloudflare, Quad9 with dedicated dashboard
- **Resolution Time Analysis**: Median, percentiles (P10-P90), and individual ping measurements
- **Separate Data Classification**: DNS targets automatically classified for specialized dashboards

## 📋 Table of Contents

1. [Quick Deployment](#1-quick-deployment)
2. [Architecture](#2-architecture)
3. [Professional Dashboards](#3-professional-dashboards)
4. [Web Administration](#4-web-administration)
5. [Configuration Management](#5-configuration-management)
6. [Data Management](#6-data-management)
7. [Environment Setup](#7-environment-setup)
8. [Troubleshooting](#8-troubleshooting)
9. [Comparison with Minimal](#9-comparison-with-minimal)

---

## 1. Quick Deployment

### Prerequisites
- Docker & Docker Compose installed
- Ports 8080, 8081, 3000, 8086 available

### Zero-Touch Setup
```bash
# Clone repository
git clone https://github.com/estcarisimo/smoking-pi.git
cd smoking-pi/grafana-influx

# Generate secure credentials and auto-detect timezone
./init-passwords.sh

# Deploy full stack
docker-compose up -d

# Check deployment status
docker-compose ps
```

### Verify Services
- **SmokePing**: http://localhost:8081/cgi-bin/smokeping.cgi
- **Web Admin**: http://localhost:8080
- **Grafana**: http://localhost:3000 (admin/admin)
- **InfluxDB**: http://localhost:8086

---

## 2. Architecture

```text
                    🔐 Init-Passwords (setup)
                    │ Generates credentials:
                    │ • INFLUX_TOKEN
                    │ • POSTGRES_PASSWORD  
                    │ • SECRET_KEY
                    │ • DOCKER_INFLUXDB_INIT_PASSWORD
                    ▼
┌─────────────┐  Config YAML   ┌──────────────┐
│Config Manager│──────────────▶│  SmokePing   │─┐
└─────────────┘                │   (8081)     │ │
       ▲                       └──────────────┘ │
       │ Database-first               │         │ RRD files
       │                              │         │
┌─────────────┐   ┌──────────────┐    │         ▼
│ Web Admin   │──▶│ PostgreSQL   │    │ ┌──────────────┐  Flux queries  ┌─────────────┐
│   (8080)    │   │   (5432)     │    │ │ InfluxDB 2.x │──────────────▶│  Grafana    │
└─────────────┘   └──────────────┘    │ │   (8086)     │                │  (3000)     │
                         ▲             │ └──────────────┘                └─────────────┘
                         │             │       ▲                                ▲
                         └─────────────┘       │ exporter.py               │ PostgreSQL
                                               └───────────────────────────┘ Template Vars
```

### 🔄 Data Flow
1. **Database Management**: PostgreSQL stores all monitoring targets with active/inactive status and metadata
2. **Dynamic Configuration**: Config manager generates SmokePing config from database in real-time
3. **Template Variables**: Grafana dashboards populate dropdowns directly from PostgreSQL
4. **Network Probing**: SmokePing monitors only active targets every 5 minutes → RRD storage
5. **Data Export**: Python exporter monitors RRD changes → pushes to InfluxDB with target categorization
6. **Professional Visualization**: Grafana dashboards with database-driven analytics and filtering
7. **Management**: Web Admin interface manages database targets → triggers automatic config regeneration

### 🎯 Key Features
- **Zero-Touch Deployment**: Automated setup with secure credential generation
- **Microservice Architecture**: Each component runs as independent container
- **Professional Dashboards**: Percentile analysis, comparison views, outage detection
- **Smart Data Routing**: DNS and latency data automatically separated
- **Target Discovery**: Integration with Netflix OCA, Tranco, Chrome UX top sites

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

## 📚 Additional Documentation

- **[Architecture Details](ARCHITECTURE.md)**: Complete system architecture and data flow diagrams
- **[DNS Monitoring Guide](DNS_MONITORING.md)**: DNS resolution monitoring setup and troubleshooting  
- **[Main Project README](../README.md)**: Overview of all deployment options
- **[Web Admin Guide](../web-admin/README.md)**: Target management interface
- **[Config Manager Guide](../config-manager/README.md)**: Configuration management system

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
| `DATABASE_URL`  | PostgreSQL connection string    | `postgresql://smokeping:${POSTGRES_PASSWORD}@postgres:5432/smokeping_targets` |
| `POSTGRES_PASSWORD` | PostgreSQL database password | Generated by `init-passwords.sh` |
| `RRD_DIR`       | Where SmokePing stores RRDs     | `/var/lib/smokeping`         |
| `TZ`            | Timezone for all services       | `UTC` (auto-detected)        |
| `SECRET_KEY`    | Flask web admin secret key      | `change-me-in-production`    |
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

# PostgreSQL database
DATABASE_URL=postgresql://smokeping:${POSTGRES_PASSWORD}@postgres:5432/smokeping_targets
POSTGRES_PASSWORD=<generated-secure-password>

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

## <a id="timezone"></a>🌍 Timezone Configuration

All services (SmokePing, InfluxDB, Grafana, Web Admin) are automatically configured to use the same timezone for consistent time handling.

### Automatic Detection

Run the timezone detection script to automatically configure your local timezone:

```bash
./detect-timezone.sh
```

This script will:
- Detect your system's timezone using multiple methods
- Update the `.env` file with the correct `TZ` setting
- Validate the timezone before applying

### Manual Configuration

To manually set a specific timezone, edit the `.env` file:

```bash
# Common timezone examples:
TZ=America/New_York           # Eastern Time (US)
TZ=America/Los_Angeles        # Pacific Time (US)
TZ=America/Chicago            # Central Time (US)
TZ=Europe/London              # UK Time
TZ=Europe/Berlin              # Central European Time
TZ=Europe/Paris               # Central European Time
TZ=Asia/Tokyo                 # Japan Time
TZ=Asia/Shanghai              # China Time
TZ=America/Argentina/Buenos_Aires  # Argentina Time
TZ=America/Sao_Paulo          # Brazil Time
TZ=UTC                        # Coordinated Universal Time
```

### How It Works

- **Host Timezone Sync**: Containers mount `/etc/localtime` and `/etc/timezone` from the host
- **Environment Variable**: `TZ` environment variable ensures consistent timezone across all services
- **InfluxDB Timestamps**: RRD timestamps are preserved and InfluxDB handles timezone conversion
- **Grafana Display**: Dashboards automatically display times in the configured timezone

### Verification

After setting the timezone, restart the stack and verify:

```bash
docker-compose down
docker-compose up -d

# Check container timezone
docker-compose exec smokeping date
docker-compose exec influxdb date
docker-compose exec grafana date
```

All containers should show the same timezone and time.

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

## 9. Comparison with Minimal

| Feature | Minimal | grafana-influx |
|---------|---------|----------------|
| SmokePing | ✅ | ✅ |
| Web Admin | Optional | ✅ |
| Config Manager | Optional | ✅ |
| InfluxDB | ❌ | ✅ |
| Grafana | ❌ | ✅ |
| Professional Dashboards | ❌ | ✅ |
| Historical Analysis | ❌ | ✅ |
| Resource Usage | Low | High |
| Setup Complexity | Simple | Zero-Touch |
| Use Case | Basic monitoring | Professional monitoring |

### When to Use Full Stack
- **Professional Monitoring**: Need advanced dashboards and analytics
- **Historical Analysis**: Long-term trend analysis and reporting
- **Team Collaboration**: Multiple users need access to monitoring data
- **Integration Requirements**: Need to integrate with other monitoring tools
- **Advanced Visualization**: Custom dashboards and complex queries

### When to Use Minimal
- **Resource Constraints**: Limited hardware (basic Raspberry Pi)
- **Simple Requirements**: Basic latency monitoring only
- **Traditional Setup**: Prefer classic SmokePing RRD interface

---

## Support & Maintenance

### Regular Maintenance
```bash
# Check service health
docker-compose ps
docker-compose logs --tail=50

# Update services
docker-compose pull
docker-compose up -d

# Backup data
./backup-data.sh  # Create this script based on troubleshooting section
```

### Performance Optimization
- **InfluxDB**: Adjust retention policies for long-term storage
- **Grafana**: Use dashboard caching for better performance  
- **SmokePing**: Tune probe intervals based on requirements
- **System**: Monitor disk usage and memory consumption

---

## License

MIT for scripts; SmokePing itself is GPLv2.

**Maintainer:** Esteban Carisimo