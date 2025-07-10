# SmokePing + InfluxDB + Grafana Using Docker

This example demonstrates a **multi-container** setup for:
1. **SmokePing** – to probe and collect latency data (stored in RRD files by default).
2. **InfluxDB** – a time-series database for storing your SmokePing data (once exported).
3. **Grafana** – for interactive dashboards and visualization of the time-series data in InfluxDB.
4. A **bridge/exporter** – a small Python-based container that periodically exports data from SmokePing’s RRD files to InfluxDB.

> **Important**: SmokePing natively stores data in RRD files. We need an **export** step to ingest those metrics into InfluxDB. Below is a minimal working example to illustrate how you could set this up.

---

## 1. Directory Structure

A suggested directory layout:


# 5. Usage Steps

1.	Clone or copy this repository layout locally.
2.	Edit the docker-compose.yml environment variables as needed (e.g., INFLUXDB_DB, INFLUXDB_ADMIN_PASSWORD).
3.	Place your custom SmokePing config in ./smokeping/config/smokeping_config.
4.	Build and start everything:

```bash
docker-compose up -d --build
```

```bash
docker-compose ps
```

6.	Access SmokePing at http://localhost:8080.
7.	Access Grafana at http://localhost:3000. Log in with admin / admin (default or as set in environment).
8.	Configure Grafana data source for InfluxDB:
    - Go to Configuration → Data sources in Grafana.
    - Add InfluxDB with:
        - URL: http://influxdb:8086
        - Database: smokeping
        - Basic Auth or credentials: admin / admin123 (as set in docker-compose.yml)
    - Save & Test.
9.	Create a Dashboard in Grafana:

- Pick “smokeping” as the measurement.
- Query fields like value, grouped by rrd_file tag, etc.
- You can build graphs, alerts, etc.# smoking-pi
