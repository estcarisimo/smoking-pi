#!/usr/bin/with-contenv bash

# SmokePing Exporter Runner
# Runs the appropriate exporter based on TSDB_TYPE

# Wait for SmokePing to start and create RRD files
sleep 30

# Check if we have exporters directory
if [ ! -d "/exporters" ]; then
    echo "No exporters directory found, skipping exporter setup"
    exit 0
fi

# Set RRD directory
RRD_DIR="${RRD_DIR:-/data}"

# Run appropriate exporter based on TSDB_TYPE
case "${TSDB_TYPE}" in
    "influxdb")
        echo "Starting InfluxDB exporter..."
        if [ -f "/exporters/rrd2influx.py" ]; then
            # Install system dependencies (rrdtool binary and pip)
            apk update && apk add --no-cache python3 py3-pip rrdtool
            
            # Install Python dependencies directly
            pip3 install --break-system-packages influxdb-client
            
            # Run exporter in background with restart on failure
            while true; do
                python3 /exporters/rrd2influx.py
                echo "InfluxDB exporter crashed, restarting in 60 seconds..."
                sleep 60
            done &
        else
            echo "InfluxDB exporter script not found"
        fi
        ;;
        
    "clickhouse")
        echo "Starting ClickHouse exporter..."
        if [ -f "/exporters/rrd2clickhouse.py" ]; then
            # Install system dependencies (rrdtool binary, pip, and build tools for lz4)
            apk update && apk add --no-cache python3 py3-pip rrdtool gcc musl-dev python3-dev
            
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