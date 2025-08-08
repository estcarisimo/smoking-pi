#!/bin/bash

set -e

# Generate random passwords and keys
INFLUX_PASSWORD=$(openssl rand -base64 32 | tr -d '=')
INFLUX_TOKEN=$(openssl rand -base64 32 | tr -d '=')
SECRET_KEY=$(openssl rand -base64 48 | tr -d '=')

# Detect timezone
if command -v timedatectl >/dev/null 2>&1; then
    DETECTED_TZ=$(timedatectl show --property=Timezone --value 2>/dev/null || echo "UTC")
elif [ -f /etc/timezone ]; then
    DETECTED_TZ=$(cat /etc/timezone 2>/dev/null | tr -d '\n' || echo "UTC")
else
    DETECTED_TZ="UTC"
fi

# Create .env file with generated passwords
cat > .env << EOF
# .env
# -------------------------------------------------------------------
# SmokePing + InfluxDB + Grafana stack
# -------------------------------------------------------------------
# InfluxDB connection
INFLUX_URL=http://influxdb:8086
INFLUX_TOKEN=${INFLUX_TOKEN}
INFLUX_ORG=smokingpi
INFLUX_BUCKET=latency

# SmokePing RRD directory inside its container
RRD_DIR=/var/lib/smokeping

# Timezone configuration (auto-detected: ${DETECTED_TZ})
TZ=${DETECTED_TZ}

# Web Admin Interface
SECRET_KEY=${SECRET_KEY}

# Generated passwords (do not commit to version control)
DOCKER_INFLUXDB_INIT_PASSWORD=${INFLUX_PASSWORD}
EOF

# Update docker-compose.yml to use environment variable instead of hardcoded password
sed -i 's/DOCKER_INFLUXDB_INIT_PASSWORD: admin123/DOCKER_INFLUXDB_INIT_PASSWORD: ${DOCKER_INFLUXDB_INIT_PASSWORD}/' docker-compose.yml

echo "Random passwords and configuration generated in .env file"
echo "InfluxDB admin password: ${INFLUX_PASSWORD}"
echo "InfluxDB token: ${INFLUX_TOKEN}"
echo "Web admin secret key: ${SECRET_KEY}"
echo "Timezone: ${DETECTED_TZ}"
echo ""
echo "IMPORTANT: Save these passwords securely!"
echo "IMPORTANT: Add .env to your .gitignore file to prevent committing secrets"