#!/bin/bash
# Script to synchronize InfluxDB token with .env file
# This ensures Grafana can always access InfluxDB regardless of volume state

set -e

ENV_FILE="./.env"

echo "🔄 Synchronizing InfluxDB token..."

# Check if .env exists
if [ ! -f "$ENV_FILE" ]; then
    echo "❌ Error: .env file not found. Run ./setup.sh first"
    exit 1
fi

# Source the env file
source "$ENV_FILE"

# Check if InfluxDB is running
if ! docker ps | grep -q "pro-influxdb-1"; then
    echo "❌ Error: InfluxDB container is not running"
    exit 1
fi

# Try to get the actual token from InfluxDB
echo "📡 Retrieving active token from InfluxDB..."
ACTIVE_TOKEN=$(docker exec pro-influxdb-1 influx auth list --hide-headers 2>/dev/null | grep "admin's Token" | awk '{print $4}' || true)

if [ -z "$ACTIVE_TOKEN" ]; then
    # If we can't get the token, try using the admin password to create one
    echo "⚠️  No active token found, attempting to create one..."
    
    # First, setup influx CLI config
    docker exec pro-influxdb-1 influx setup \
        --force \
        --username admin \
        --password "${DOCKER_INFLUXDB_INIT_PASSWORD}" \
        --org "${INFLUX_ORG}" \
        --bucket "${INFLUX_BUCKET}" \
        --token "${INFLUX_TOKEN}" 2>/dev/null || true
    
    ACTIVE_TOKEN="${INFLUX_TOKEN}"
fi

if [ -n "$ACTIVE_TOKEN" ] && [ "$ACTIVE_TOKEN" != "$INFLUX_TOKEN" ]; then
    echo "🔧 Token mismatch detected!"
    echo "   ENV Token: ${INFLUX_TOKEN:0:10}..."
    echo "   Active Token: ${ACTIVE_TOKEN:0:10}..."
    
    # Update .env with the active token
    echo "📝 Updating .env file with active token..."
    sed -i "s|^INFLUX_TOKEN=.*|INFLUX_TOKEN=${ACTIVE_TOKEN}|" "$ENV_FILE"
    
    echo "🔄 Restarting Grafana to apply new token..."
    docker-compose restart grafana
    
    echo "✅ Token synchronized successfully!"
else
    echo "✅ Token is already synchronized"
fi