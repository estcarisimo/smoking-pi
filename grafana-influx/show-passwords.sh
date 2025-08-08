#!/bin/bash

# SmokePing Full Stack - Password Display Script
# Shows all credentials and access information

echo "=================================="
echo "🔑 SmokePing Full Stack Credentials"
echo "=================================="
echo

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo "❌ Error: .env file not found!"
    echo "Run ./init-passwords.sh first to generate credentials."
    exit 1
fi

# Load environment variables
source .env

echo "📊 Service Access URLs:"
echo "------------------------"
echo "SmokePing:   http://localhost:8081/cgi-bin/smokeping.cgi"
echo "Web Admin:   http://localhost:8080"
echo "Grafana:     http://localhost:3000"
echo "InfluxDB:    http://localhost:8086"
echo

echo "🔐 InfluxDB Credentials:"
echo "------------------------"
echo "Organization: ${INFLUX_ORG}"
echo "Bucket:       ${INFLUX_BUCKET}"
echo "Admin User:   admin"
if [ -n "$DOCKER_INFLUXDB_INIT_PASSWORD" ]; then
    echo "Admin Pass:   $DOCKER_INFLUXDB_INIT_PASSWORD"
else
    echo "Admin Pass:   Not set in .env file"
fi
if [ -n "$INFLUX_TOKEN" ]; then
    echo "API Token:    $INFLUX_TOKEN"
else
    echo "API Token:    Not set in .env file"
fi
echo

echo "🌐 Grafana Credentials:"
echo "-----------------------"
echo "Username:     admin"
echo "Password:     admin (default - change on first login)"
echo

echo "🔧 Web Admin:"
echo "-------------"
if [ -n "$SECRET_KEY" ]; then
    echo "Secret Key:   $SECRET_KEY"
else
    echo "Secret Key:   Not set in .env file"
fi
echo

echo "🌍 System Configuration:"
echo "------------------------"
echo "Timezone:     ${TZ:-UTC}"
echo "InfluxDB URL: ${INFLUX_URL:-http://influxdb:8086}"
echo

echo "📋 Quick Commands:"
echo "------------------"
echo "View logs:        docker-compose logs -f"
echo "Check status:     docker-compose ps"
echo "Restart services: docker-compose restart"
echo "Stop all:         docker-compose down"
echo "Update services:  docker-compose pull && docker-compose up -d"
echo

echo "💡 Tips:"
echo "--------"
echo "• Change Grafana admin password on first login"
echo "• Keep the InfluxDB API token secure"
echo "• Access web admin from any network device using your server IP"
echo "• Check service health with: curl http://localhost:8080/api/status"
echo

# Check if services are running
echo "🚀 Service Status:"
echo "------------------"
if command -v docker-compose >/dev/null 2>&1; then
    if [ -f "docker-compose.yml" ]; then
        docker-compose ps 2>/dev/null | grep -E "(Up|Exit|Dead)" || echo "Services not started. Run: docker-compose up -d"
    else
        echo "docker-compose.yml not found in current directory"
    fi
else
    echo "docker-compose not found. Please install Docker Compose."
fi

echo
echo "=================================="