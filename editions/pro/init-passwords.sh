#!/bin/bash

set -e

# Generate random passwords
INFLUX_PASSWORD=$(openssl rand -base64 32 | tr -d '=')
INFLUX_TOKEN=$(openssl rand -base64 32 | tr -d '=')

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

# Generated passwords (do not commit to version control)
DOCKER_INFLUXDB_INIT_PASSWORD=${INFLUX_PASSWORD}
EOF

# Update docker-compose.yml to use environment variable instead of hardcoded password
sed -i 's/DOCKER_INFLUXDB_INIT_PASSWORD: admin123/DOCKER_INFLUXDB_INIT_PASSWORD: ${DOCKER_INFLUXDB_INIT_PASSWORD}/' docker-compose.yml

echo "Random passwords generated and configured in .env file"
echo "InfluxDB admin password: ${INFLUX_PASSWORD}"
echo "InfluxDB token: ${INFLUX_TOKEN}"
echo ""
echo "IMPORTANT: Save these passwords securely!"
echo "IMPORTANT: Add .env to your .gitignore file to prevent committing secrets"