#!/usr/bin/with-contenv bash

# SmokePing Exporter Runner
# Runs the appropriate exporter based on TSDB_TYPE
# Also runs CPE discovery (all editions) and microcut detector (InfluxDB only)

# Wait for SmokePing to start and create RRD files
sleep 30

# Check if we have exporters directory
if [ ! -d "/exporters" ]; then
    echo "No exporters directory found, skipping exporter setup"
    exit 0
fi

# Set RRD directory
RRD_DIR="${RRD_DIR:-/data}"

# Ensure CPE_Targets file exists (SmokePing @include will fail without it)
touch /config/CPE_Targets 2>/dev/null || true

# Install common dependencies (traceroute needed by cpe_discovery.py;
# busybox's applet output is not reliably parseable)
apk update && apk add --no-cache python3 py3-pip rrdtool traceroute

# ── CPE Discovery (runs for all editions) ──
if [ -f "/exporters/cpe_discovery.py" ]; then
    echo "Starting CPE discovery..."
    while true; do
        python3 /exporters/cpe_discovery.py
        echo "CPE discovery exited, restarting in 60 seconds..."
        sleep 60
    done &
fi

# ── TSDB-specific exporters ──
case "${TSDB_TYPE}" in
    "influxdb")
        echo "Starting InfluxDB exporter..."
        if [ -f "/exporters/rrd2influx.py" ]; then
            # Install Python dependencies
            pip3 install --break-system-packages influxdb-client

            # Run RRD exporter in background with restart on failure
            while true; do
                python3 /exporters/rrd2influx.py
                echo "InfluxDB exporter crashed, restarting in 60 seconds..."
                sleep 60
            done &
        else
            echo "InfluxDB exporter script not found"
        fi

        # Start microcut detector (needs InfluxDB)
        if [ -f "/exporters/microcut_detector.py" ]; then
            echo "Starting microcut detector..."
            # Wait for CPE discovery to find targets first
            sleep 40
            while true; do
                python3 /exporters/microcut_detector.py
                echo "Microcut detector exited, restarting in 60 seconds..."
                sleep 60
            done &
        fi
        ;;

    "clickhouse")
        echo "Starting ClickHouse exporter..."
        if [ -f "/exporters/rrd2clickhouse.py" ]; then
            # Install system dependencies (rrdtool binary, pip, and build tools for lz4)
            apk add --no-cache gcc musl-dev python3-dev

            # Install Python dependencies directly (matching pyproject.toml)
            pip3 install --break-system-packages clickhouse-connect influxdb-client numpy pandas structlog pydantic pydantic-settings prometheus-client rich tqdm click

            # Run exporter in background with restart on failure
            while true; do
                python3 /exporters/rrd2clickhouse.py
                echo "ClickHouse exporter crashed, restarting in 60 seconds..."
                sleep 60
            done &
        else
            echo "ClickHouse exporter script not found"
        fi
        ;;

    *)
        echo "No exporter configured for TSDB_TYPE: ${TSDB_TYPE}"
        ;;
esac