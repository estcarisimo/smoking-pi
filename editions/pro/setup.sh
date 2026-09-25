#!/bin/bash

# SmokePing Pro Edition Setup Script
# Supports both InfluxDB and ClickHouse time-series databases

set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

# Default database
DATABASE="influxdb"
# Where the stack's state lives (docs/packaging.md, "Relocatable state").
# Defaults keep everything beside this script, untracked by git.
ENV_FILE="${SMOKING_PI_ENV_FILE:-$SCRIPT_DIR/.env}"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --database)
            DATABASE="$2"
            shift 2
            ;;
        --env-file)
            ENV_FILE="$2"
            shift 2
            ;;
        influxdb|clickhouse)
            DATABASE="$1"
            shift
            ;;
        -h|--help)
            echo "SmokePing Pro Edition Setup"
            echo ""
            echo "Usage: $0 [DATABASE|OPTIONS]"
            echo ""
            echo "Arguments:"
            echo "  influxdb           Use InfluxDB (default)"
            echo "  clickhouse         Use ClickHouse"
            echo ""
            echo "Options:"
            echo "  --database <type>   Choose database: influxdb (default) or clickhouse"
            echo "  --env-file <path>   Where to write the env file (default: ./.env;"
            echo "                      also read from SMOKING_PI_ENV_FILE)"
            echo "  -h, --help         Show this help message"
            echo ""
            echo "Environment: SMOKING_PI_ENV_FILE, SMOKING_PI_CONFIG_DIR, SMOKING_PI_OUTPUT_DIR"
            echo "  relocate the env file, the YAML the stack edits and the generated"
            echo "  SmokePing config (docs/packaging.md). Defaults stay beside this script."
            echo ""
            echo "Examples:"
            echo "  $0                      # Use InfluxDB (default)"
            echo "  $0 clickhouse           # Use ClickHouse"
            echo "  $0 --database clickhouse # Use ClickHouse (alternative)"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Validate database choice
if [[ ! "$DATABASE" =~ ^(influxdb|clickhouse)$ ]]; then
    echo -e "${RED}Error: Invalid database '$DATABASE'. Must be 'influxdb' or 'clickhouse'${NC}"
    exit 1
fi

echo -e "${GREEN}🚀 SmokePing Pro Edition Setup${NC}"
echo -e "${GREEN}═══════════════════════════════════════${NC}"
echo -e "Database: ${BLUE}$DATABASE${NC}"
echo ""

# Generate passwords/environment
echo -e "${BLUE}📋 Setting up environment...${NC}"
"$ROOT_DIR/shared/scripts/generate-passwords.sh" --edition pro --target-dir "$SCRIPT_DIR" --env-file "$ENV_FILE"

# The directories the stack rewrites must exist before Compose mounts them
# (Compose would create them, owned by root, which is fine for the containers
# but not for an operator's /etc). Relative values are relative to this
# directory, as in the compose file.
cd "$SCRIPT_DIR"
mkdir -p "${SMOKING_PI_CONFIG_DIR:-./config-manager/config}" "${SMOKING_PI_OUTPUT_DIR:-./config-manager/output}"

# Set TSDB_TYPE in the generated .env (never mutate the tracked template).
# COMPOSE_PROFILES is persisted alongside it rather than only passed on the
# command line below: a profile that exists solely in this script's argv is
# forgotten by the next bare `docker compose up -d`, which then quietly runs
# without that service.
if [ -f "$ENV_FILE" ]; then
    sed -i "s/^TSDB_TYPE=.*/TSDB_TYPE=$DATABASE/" "$ENV_FILE"
    sed -i "s/^COMPOSE_PROFILES=.*/COMPOSE_PROFILES=$DATABASE/" "$ENV_FILE"
fi

# Choose compose file based on database. Every compose call below carries
# --env-file so a relocated env file is honored; with the default it is the
# same .env Compose would have read on its own.
if [ "$DATABASE" = "clickhouse" ]; then
    COMPOSE_ARGS=(--env-file "$ENV_FILE" -f docker-compose.yml -f docker-compose.clickhouse.yml)
    echo -e "${BLUE}🗄️ Using ClickHouse as time-series database${NC}"
else
    COMPOSE_ARGS=(--env-file "$ENV_FILE" -f docker-compose.yml)
    echo -e "${BLUE}🗄️ Using InfluxDB as time-series database${NC}"
fi
# Packaged mode: no source bind-mounts (docs/packaging.md, "Packaged mode").
# Last, so its !override wins over the ClickHouse overlay.
if [ "${SMOKING_PI_PACKAGED:-0}" = 1 ]; then
    COMPOSE_ARGS+=(-f docker-compose.packaged.yml)
fi

# Start services
echo -e "${BLUE}🐳 Starting services...${NC}"
cd "$SCRIPT_DIR"
if [ "$DATABASE" = "clickhouse" ]; then
    # For ClickHouse, we need to use the profile and override files
    COMPOSE_PROFILES=clickhouse docker compose "${COMPOSE_ARGS[@]}" up -d
else
    COMPOSE_PROFILES=influxdb docker compose "${COMPOSE_ARGS[@]}" up -d
fi

# Wait for services to be ready
echo -e "${YELLOW}⏳ Waiting for services to be ready...${NC}"
sleep 10

# Wait for PostgreSQL to be ready
echo -e "${BLUE}🗄️ Checking PostgreSQL readiness...${NC}"
max_attempts=30
attempt=0
postgres_container=$(docker compose "${COMPOSE_ARGS[@]}" ps -q postgres)

while [ $attempt -lt $max_attempts ]; do
    if docker exec "$postgres_container" pg_isready -U smokeping -d smokeping_targets >/dev/null 2>&1; then
        echo -e "${GREEN}✅ PostgreSQL is ready${NC}"
        break
    fi
    echo -n "."
    sleep 2
    attempt=$((attempt + 1))
done

if [ $attempt -eq $max_attempts ]; then
    echo -e "\n${YELLOW}⚠️  Warning: PostgreSQL may not be fully ready${NC}"
fi

# InfluxDB-specific setup
if [ "$DATABASE" = "influxdb" ]; then
    # Wait for InfluxDB to be ready
    echo -e "${BLUE}🔄 Checking InfluxDB readiness...${NC}"
    max_attempts=30
    attempt=0
    container_name=$(docker compose "${COMPOSE_ARGS[@]}" ps -q influxdb)
    
    while [ $attempt -lt $max_attempts ]; do
        if docker exec "$container_name" influx ping 2>/dev/null; then
            echo -e "${GREEN}✅ InfluxDB is ready${NC}"
            break
        fi
        echo -n "."
        sleep 2
        attempt=$((attempt + 1))
    done
    
    if [ $attempt -eq $max_attempts ]; then
        echo -e "\n${YELLOW}⚠️  Warning: InfluxDB may not be fully ready${NC}"
    fi
    
    # Synchronize the token
    echo -e "${BLUE}🔑 Synchronizing InfluxDB token...${NC}"
    if [ -x "./sync-influx-token.sh" ]; then
        ./sync-influx-token.sh || echo -e "${YELLOW}⚠️  Token sync may have failed${NC}"
    fi
fi

# Check service health
echo -e "${BLUE}🔍 Checking service status...${NC}"
docker compose "${COMPOSE_ARGS[@]}" ps

# Verify PostgreSQL connection
echo -e "${BLUE}🔗 Verifying PostgreSQL connection...${NC}"
if docker exec "$postgres_container" pg_isready -U smokeping -d smokeping_targets >/dev/null 2>&1; then
    echo -e "${GREEN}✅ PostgreSQL connection verified${NC}"
    
    # Check if verify-postgres.sh exists and run basic verification
    if [ -x "./verify-postgres.sh" ]; then
        echo -e "${BLUE}🔍 Running PostgreSQL verification...${NC}"
        if ./verify-postgres.sh >/dev/null 2>&1; then
            echo -e "${GREEN}✅ PostgreSQL verification passed${NC}"
        else
            echo -e "${YELLOW}⚠️  PostgreSQL verification found issues. Run './verify-postgres.sh' for details.${NC}"
        fi
    fi
else
    echo -e "${RED}❌ PostgreSQL connection failed${NC}"
    echo -e "${YELLOW}💡 Run './verify-postgres.sh' for detailed diagnosis${NC}"
fi

echo ""
echo -e "${GREEN}✅ SmokePing Pro Edition is ready!${NC}"
echo ""
echo -e "${CYAN}📊 Access Points:${NC}"
echo -e "  🌐 Web Admin: http://localhost:8080"
echo -e "  📊 Grafana: http://localhost:3000"
echo -e "  📈 SmokePing: http://localhost:80"

if [ "$DATABASE" = "influxdb" ]; then
    echo -e "  💾 InfluxDB: http://localhost:8086"
elif [ "$DATABASE" = "clickhouse" ]; then
    echo -e "  💾 ClickHouse: http://localhost:8123"
fi

echo ""
# The command, on the PATH from any directory (a clone only; the package
# and Homebrew install their own). Never fails the setup.
SMOKING_PI_HOME="$ROOT_DIR" SMOKING_PI_EDITION=pro "$ROOT_DIR/packaging/smoking-pi" link --quiet || true
echo ""
echo -e "${CYAN}🔐 Credentials:${NC}"
echo -e "  Run: ${YELLOW}smoking-pi passwords${NC} for URLs and status"
echo -e "  Add ${YELLOW}--show-secrets${NC} to display the passwords and tokens themselves"
echo ""
echo -e "${CYAN}💡 Tips:${NC}"
echo -e "  - What is here, from any directory: smoking-pi"
echo -e "  - The address to open: smoking-pi url"
echo -e "  - View logs: smoking-pi logs [service]"
echo -e "  - Stop services: smoking-pi down"
echo -e "  - Is it measuring: smoking-pi doctor --live"