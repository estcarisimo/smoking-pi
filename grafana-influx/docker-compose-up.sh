#!/bin/bash
# Wrapper script for docker-compose up that ensures token synchronization
# This guarantees Grafana can always access InfluxDB

set -e

echo "🚀 Starting SmokePing Stack with Token Synchronization"
echo "======================================================"

# Ensure passwords are initialized
if [ ! -f ".env" ]; then
    echo "📝 No .env file found, running initialization..."
    ./init-passwords-docker.sh
fi

# Start the stack
echo "🐳 Starting Docker containers..."
docker-compose up -d

# Wait for InfluxDB to be ready
echo "⏳ Waiting for InfluxDB to be ready..."
max_attempts=30
attempt=0
while [ $attempt -lt $max_attempts ]; do
    if docker exec grafana-influx_influxdb_1 influx ping 2>/dev/null; then
        echo "✅ InfluxDB is ready"
        break
    fi
    echo -n "."
    sleep 2
    attempt=$((attempt + 1))
done

if [ $attempt -eq $max_attempts ]; then
    echo "⚠️  Warning: InfluxDB may not be fully ready"
fi

# Synchronize the token
echo ""
echo "🔄 Synchronizing InfluxDB token..."
./sync-influx-token.sh || true

echo ""
echo "✅ Stack is running!"
echo ""
echo "📊 Access points:"
echo "   Grafana: http://localhost:3000 (admin/see ./show-passwords.sh)"
echo "   Web Admin: http://localhost:8080 (admin/see ./show-passwords.sh)"
echo "   SmokePing: http://localhost:8081"
echo ""
echo "💡 Run ./show-passwords.sh to see all credentials"