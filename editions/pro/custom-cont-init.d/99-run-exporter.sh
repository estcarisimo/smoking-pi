#!/usr/bin/with-contenv bash

# SmokePing Exporter Runner
# Runs the appropriate exporter based on TSDB_TYPE
# Also runs CPE discovery (all editions) and microcut detector (InfluxDB only)
#
# IMPORTANT: this is an s6 cont-init script — it must exit quickly or the
# container's services (SmokePing, Apache) will not start. No foreground
# sleeps, and every background loop must be detached from our stdio (s6
# waits for the pipe to close, not just for the script to exit). The
# exporter/detector handle their own waiting for RRDs / discovery state.

# Detached runner: closes stdin and writes to the container log directly so
# cont-init's pipe is released immediately.
run_detached() {
    ( exec 0<&- >/proc/1/fd/1 2>/proc/1/fd/2; "$@" ) &
}

supervise_loop() {
    local name="$1"; shift
    while true; do
        "$@"
        echo "$name exited, restarting in 60 seconds..."
        sleep 60
    done
}

# Check if we have exporters directory
if [ ! -d "/exporters" ]; then
    echo "No exporters directory found, skipping exporter setup"
    exit 0
fi

# Set RRD directory
RRD_DIR="${RRD_DIR:-/data}"

# Ensure CPE_Targets file exists (SmokePing @include will fail without it)
touch /config/CPE_Targets 2>/dev/null || true

# Common dependencies are baked into the image at build time
# (shared/modules/smokeping/Dockerfile). Only install here as a fallback for
# images built before that change — avoids needing internet at every boot.
# (traceroute must be the full package; busybox's applet output is not
# reliably parseable, so check via apk, not `command -v`.)
missing_pkgs=""
for pkg in python3 py3-pip rrdtool traceroute; do
    apk info -e "$pkg" >/dev/null 2>&1 || missing_pkgs="$missing_pkgs $pkg"
done
if [ -n "$missing_pkgs" ]; then
    echo "Installing missing packages:$missing_pkgs"
    apk update && apk add --no-cache $missing_pkgs
fi

# ── CPE Discovery (runs for all editions) ──
if [ -f "/exporters/cpe_discovery.py" ]; then
    echo "Starting CPE discovery..."
    run_detached supervise_loop "CPE discovery" python3 /exporters/cpe_discovery.py
fi

# ── TSDB-specific exporters ──
case "${TSDB_TYPE}" in
    "influxdb")
        echo "Starting InfluxDB exporter..."
        if [ -f "/exporters/rrd2influx.py" ]; then
            # influxdb-client is baked into the image; install only if missing
            # (fallback for images built before the Dockerfile change)
            python3 -c "import influxdb_client" 2>/dev/null || \
                pip3 install --break-system-packages influxdb-client

            # Run RRD exporter in background with restart on failure
            run_detached supervise_loop "InfluxDB exporter" python3 /exporters/rrd2influx.py
        else
            echo "InfluxDB exporter script not found"
        fi

        # Start microcut detector (needs InfluxDB). It waits for CPE
        # discovery state internally — no sleep needed here.
        if [ -f "/exporters/microcut_detector.py" ]; then
            echo "Starting microcut detector..."
            run_detached supervise_loop "Microcut detector" python3 /exporters/microcut_detector.py
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
            run_detached supervise_loop "ClickHouse exporter" python3 /exporters/rrd2clickhouse.py
        else
            echo "ClickHouse exporter script not found"
        fi
        ;;

    *)
        echo "No exporter configured for TSDB_TYPE: ${TSDB_TYPE}"
        ;;
esac