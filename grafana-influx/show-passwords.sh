#!/bin/bash

# SmokePing Full Stack - Enhanced Password Display Script
# Shows all credentials, access information, and system status

# Colors for better readability
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${WHITE}     🔑 SmokePing Stack Credentials          ${CYAN}║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"
echo

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo -e "${RED}❌ Error: .env file not found!${NC}"
    echo -e "${YELLOW}The system will auto-generate passwords on first run.${NC}"
    echo -e "${GREEN}Just run: docker-compose up -d${NC}"
    echo
    
    # Check if .passwords-generated exists from zero-touch deployment
    if [ -f ".passwords-generated" ]; then
        echo -e "${GREEN}✅ Found generated passwords from initial deployment:${NC}"
        echo
        cat .passwords-generated
        rm -f .passwords-generated  # Remove after showing
    fi
    exit 1
fi

# Load environment variables
source .env

# Get server IP addresses
SERVER_IP=$(hostname -I | awk '{print $1}')
EXTERNAL_IP=$(curl -s -m 2 ifconfig.me 2>/dev/null || echo "Unable to detect")

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${WHITE}📊 Service Access URLs${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}Local Access:${NC}"
echo -e "  SmokePing:   http://localhost:8081/cgi-bin/smokeping.cgi"
echo -e "  Web Admin:   http://localhost:8080"
echo -e "  Grafana:     http://localhost:3000"
echo -e "  InfluxDB:    http://localhost:8086"
echo
echo -e "${GREEN}Network Access (from other devices):${NC}"
echo -e "  SmokePing:   http://${SERVER_IP}:8081/cgi-bin/smokeping.cgi"
echo -e "  Web Admin:   http://${SERVER_IP}:8080"
echo -e "  Grafana:     http://${SERVER_IP}:3000"
echo -e "  InfluxDB:    http://${SERVER_IP}:8086"

if [ "$EXTERNAL_IP" != "Unable to detect" ]; then
    echo
    echo -e "${GREEN}External Access (if port forwarding enabled):${NC}"
    echo -e "  Web Admin:   http://${EXTERNAL_IP}:8080"
fi

echo
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${WHITE}🔐 InfluxDB Credentials${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${PURPLE}Organization:${NC} ${INFLUX_ORG}"
echo -e "  ${PURPLE}Bucket:${NC}       ${INFLUX_BUCKET}"
echo -e "  ${PURPLE}Admin User:${NC}   admin"

if [ -n "$DOCKER_INFLUXDB_INIT_PASSWORD" ]; then
    echo -e "  ${PURPLE}Admin Pass:${NC}   ${YELLOW}$DOCKER_INFLUXDB_INIT_PASSWORD${NC}"
else
    echo -e "  ${RED}Admin Pass:   Not set in .env file${NC}"
fi

if [ -n "$INFLUX_TOKEN" ]; then
    echo -e "  ${PURPLE}API Token:${NC}    ${YELLOW}$INFLUX_TOKEN${NC}"
else
    echo -e "  ${RED}API Token:    Not set in .env file${NC}"
fi

echo
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${WHITE}🌐 Grafana Credentials${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${PURPLE}Username:${NC}     admin"
echo -e "  ${PURPLE}Password:${NC}     admin ${YELLOW}(change on first login!)${NC}"

echo
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${WHITE}🗄️ PostgreSQL Database${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
if [ -n "$POSTGRES_PASSWORD" ]; then
    echo -e "  ${PURPLE}Database:${NC}     smokeping_targets"
    echo -e "  ${PURPLE}Username:${NC}     smokeping"
    echo -e "  ${PURPLE}Password:${NC}     ${YELLOW}$POSTGRES_PASSWORD${NC}"
    echo -e "  ${PURPLE}Host:${NC}         localhost:5432 (container: postgres:5432)"
    if [ -n "$DATABASE_URL" ]; then
        echo -e "  ${PURPLE}URL:${NC}          ${YELLOW}$DATABASE_URL${NC}"
    fi
else
    echo -e "  ${RED}PostgreSQL:   Not configured in .env file${NC}"
fi

echo
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${WHITE}🔧 Web Admin Interface${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
if [ -n "$SECRET_KEY" ]; then
    echo -e "  ${PURPLE}Secret Key:${NC}   ${YELLOW}$SECRET_KEY${NC}"
else
    echo -e "  ${RED}Secret Key:   Not set in .env file${NC}"
fi

echo
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${WHITE}🌍 System Configuration${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${PURPLE}Timezone:${NC}     ${TZ:-UTC}"
echo -e "  ${PURPLE}InfluxDB URL:${NC} ${INFLUX_URL:-http://influxdb:8086}"
echo -e "  ${PURPLE}RRD Dir:${NC}      ${RRD_DIR}"

# Check if this was auto-generated
if grep -q "Auto-generated by zero-touch deployment" .env 2>/dev/null; then
    GENERATION_DATE=$(grep "Generated:" .env | sed 's/# Generated: //')
    echo -e "  ${PURPLE}Generated:${NC}    ${GENERATION_DATE}"
fi

echo
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${WHITE}🚀 Service Status${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if command -v docker-compose >/dev/null 2>&1; then
    if [ -f "docker-compose.yml" ]; then
        # Get container status
        COMPOSE_OUTPUT=$(docker-compose ps 2>/dev/null)
        
        # Check each service
        check_service() {
            SERVICE=$1
            if echo "$COMPOSE_OUTPUT" | grep -q "$SERVICE.*Up"; then
                echo -e "  ${GREEN}✅ $SERVICE is running${NC}"
            elif echo "$COMPOSE_OUTPUT" | grep -q "$SERVICE.*Exit"; then
                EXIT_CODE=$(echo "$COMPOSE_OUTPUT" | grep "$SERVICE" | awk '{print $NF}')
                echo -e "  ${RED}❌ $SERVICE exited (code: $EXIT_CODE)${NC}"
            else
                echo -e "  ${YELLOW}⚠️  $SERVICE not found${NC}"
            fi
        }
        
        check_service "smokeping"
        check_service "influxdb"
        check_service "grafana"
        check_service "web-admin"
        check_service "config-manager"
        check_service "postgres"
        
        # Check init-passwords separately as it should exit
        if echo "$COMPOSE_OUTPUT" | grep -q "init-passwords.*Exit 0"; then
            echo -e "  ${GREEN}✅ init-passwords completed successfully${NC}"
        fi
        
    else
        echo -e "  ${RED}docker-compose.yml not found${NC}"
    fi
else
    echo -e "  ${RED}docker-compose not found${NC}"
fi

echo
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${WHITE}📋 Quick Commands${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${CYAN}View logs:${NC}        docker-compose logs -f [service]"
echo -e "  ${CYAN}Check status:${NC}     docker-compose ps"
echo -e "  ${CYAN}Restart service:${NC}  docker-compose restart [service]"
echo -e "  ${CYAN}Stop all:${NC}         docker-compose down"
echo -e "  ${CYAN}Update & restart:${NC} docker-compose pull && docker-compose up -d"
echo -e "  ${CYAN}View real-time:${NC}   docker-compose logs -f --tail=50"
echo
echo -e "${YELLOW}PostgreSQL Commands:${NC}"
echo -e "  ${CYAN}Database status:${NC}  curl -s http://localhost:5000/status"
echo -e "  ${CYAN}List targets:${NC}     curl -s http://localhost:5000/targets"
echo -e "  ${CYAN}Toggle target:${NC}    curl -X POST http://localhost:5000/targets/{id}/toggle"
echo -e "  ${CYAN}Run migration:${NC}    docker exec grafana-influx-config-manager-1 python3 /app/scripts/migrate_yaml_to_db.py"

echo
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${WHITE}💡 Tips & Troubleshooting${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  • ${YELLOW}Change Grafana admin password on first login${NC}"
echo -e "  • ${YELLOW}Keep the InfluxDB API token secure${NC}"
echo -e "  • ${YELLOW}Access web admin from any device on your network${NC}"
echo -e "  • ${YELLOW}Check service health: curl http://localhost:8080/api/status${NC}"

# Check for common issues
echo
echo -e "${WHITE}🔍 Health Checks:${NC}"

# Check if ports are accessible
check_port() {
    PORT=$1
    SERVICE=$2
    if nc -z localhost $PORT 2>/dev/null; then
        echo -e "  ${GREEN}✅ Port $PORT ($SERVICE) is accessible${NC}"
    else
        echo -e "  ${RED}❌ Port $PORT ($SERVICE) is not accessible${NC}"
    fi
}

check_port 8080 "Web Admin"
check_port 3000 "Grafana"
check_port 8086 "InfluxDB"
check_port 8081 "SmokePing"

# Check if .env has been modified from defaults
if grep -q "supersecrettoken\|your-secret-key-here" .env 2>/dev/null; then
    echo
    echo -e "${RED}⚠️  WARNING: Default passwords detected!${NC}"
    echo -e "${YELLOW}   Run 'docker-compose down && rm .env && docker-compose up -d'${NC}"
    echo -e "${YELLOW}   to regenerate secure passwords.${NC}"
fi

echo
echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${WHITE}        📘 Documentation & Support           ${CYAN}║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"
echo -e "  GitHub: ${BLUE}https://github.com/your-repo/smoking-pi${NC}"
echo -e "  Issues: ${BLUE}https://github.com/your-repo/smoking-pi/issues${NC}"
echo