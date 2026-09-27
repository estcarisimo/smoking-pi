#!/bin/bash
# Script to synchronize InfluxDB token with .env file
# This ensures Grafana can always access InfluxDB regardless of volume state

set -e

# The env file follows SMOKING_PI_ENV_FILE (docs/packaging.md, "Relocatable
# state"); containers are found through Compose, never by a guessed name
# (the project name is whatever COMPOSE_PROJECT_NAME says).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SMOKING_PI_ENV_FILE:-$SCRIPT_DIR/.env}"
compose() { (cd "$SCRIPT_DIR" && docker compose --env-file "$ENV_FILE" "$@"); }

echo "🔄 Synchronizing InfluxDB token..."

# Check if .env exists
if [ ! -f "$ENV_FILE" ]; then
    echo "❌ Error: .env file not found. Run ./setup.sh first"
    exit 1
fi

# Source the env file
source "$ENV_FILE"

# Check if InfluxDB is running
INFLUXDB=$(compose ps -q --status running influxdb 2>/dev/null | head -n1)
if [ -z "$INFLUXDB" ]; then
    echo "❌ Error: InfluxDB container is not running"
    exit 1
fi

token_works() {
    [ -n "$1" ] && docker exec "$INFLUXDB" influx bucket list --token "$1" --hide-headers >/dev/null 2>&1
}

# InfluxDB 2.9+ stores tokens hashed and can no longer show them, so asking
# whether it accepts the .env token is the only check that works everywhere.
if token_works "$INFLUX_TOKEN"; then
    echo "✅ Token is already synchronized"
    exit 0
fi

# Up to 2.8, `auth list` shows tokens in the clear. From 2.9 the column is
# blank and the fourth field is the user name, so a candidate is adopted only
# once InfluxDB accepts it -- never write a token that was not proven.
echo "📡 Retrieving active token from InfluxDB..."
ACTIVE_TOKEN=$(docker exec "$INFLUXDB" influx auth list --hide-headers 2>/dev/null | grep "admin's Token" | awk '{print $4}' || true)
token_works "$ACTIVE_TOKEN" || ACTIVE_TOKEN=""

if [ -z "$ACTIVE_TOKEN" ]; then
    # If we can't get the token, try using the admin password to create one
    echo "⚠️  No active token found, attempting to create one..."
    
    # First, setup influx CLI config
    docker exec "$INFLUXDB" influx setup \
        --force \
        --username admin \
        --password "${DOCKER_INFLUXDB_INIT_PASSWORD}" \
        --org "${INFLUX_ORG}" \
        --bucket "${INFLUX_BUCKET}" \
        --token "${INFLUX_TOKEN}" 2>/dev/null || true

    if token_works "$INFLUX_TOKEN"; then
        echo "✅ Token is already synchronized"
        exit 0
    fi
    echo "❌ InfluxDB rejects INFLUX_TOKEN from $ENV_FILE and holds no token"
    echo "   that can be recovered (2.9+ stores them hashed). Create one with"
    echo "   'influx auth create --all-access' and put it in INFLUX_TOKEN."
    exit 1
fi

if [ "$ACTIVE_TOKEN" != "$INFLUX_TOKEN" ]; then
    echo "🔧 Token mismatch detected!"
    echo "   ENV Token: ${INFLUX_TOKEN:0:10}..."
    echo "   Active Token: ${ACTIVE_TOKEN:0:10}..."
    
    # Update .env with the active token
    echo "📝 Updating .env file with active token..."
    sed -i "s|^INFLUX_TOKEN=.*|INFLUX_TOKEN=${ACTIVE_TOKEN}|" "$ENV_FILE"
    
    echo "🔄 Restarting Grafana to apply new token..."
    compose restart grafana
    
    echo "✅ Token synchronized successfully!"
else
    echo "✅ Token is already synchronized"
fi