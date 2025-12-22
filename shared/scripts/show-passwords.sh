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

# Detect SmokePing edition
detect_edition() {
    # Check if docker-compose.yml exists in current directory
    if [ -f "docker-compose.yml" ]; then
        if grep -q "grafana" docker-compose.yml && grep -q "influxdb\|clickhouse" docker-compose.yml; then
            echo "pro"
        elif grep -q "postgres" docker-compose.yml && grep -q "config-manager" docker-compose.yml; then
            echo "standard"
        elif grep -q "smokeping" docker-compose.yml; then
            echo "basic"
        else
            echo "unknown"
        fi
    else
        echo "unknown"
    fi
}

EDITION=$(detect_edition)

echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${WHITE}    🔑 SmokePing ${EDITION^} Edition Credentials    ${CYAN}║${NC}"
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

# Detect time-series database type for pro edition
TSDB_TYPE=${TSDB_TYPE:-influxdb}

# Get server IP addresses
SERVER_IP=$(hostname -I | awk '{print $1}')
EXTERNAL_IP=$(curl -s -m 2 ifconfig.me 2>/dev/null || echo "Unable to detect")

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${WHITE}📊 Service Access URLs${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}Local Access:${NC}"

# Show services based on edition
case "$EDITION" in
    "basic")
        echo -e "  SmokePing:   http://localhost:8080"
        ;;
    "standard")
        echo -e "  SmokePing:   http://localhost:8081"
        echo -e "  Web Admin:   http://localhost:8080"
        ;;
    "pro")
        echo -e "  SmokePing:   http://localhost:8081"
        echo -e "  Web Admin:   http://localhost:8080"
        echo -e "  Grafana:     http://localhost:3000"
        if [ "$TSDB_TYPE" = "clickhouse" ]; then
            echo -e "  ClickHouse:  http://localhost:8123"
        else
            echo -e "  InfluxDB:    http://localhost:8086"
        fi
        ;;
    *)
        echo -e "  ${YELLOW}Unknown edition - please check docker-compose.yml${NC}"
        ;;
esac

echo
echo -e "${GREEN}Network Access (from other devices):${NC}"

case "$EDITION" in
    "basic")
        echo -e "  SmokePing:   http://${SERVER_IP}:8080"
        ;;
    "standard")
        echo -e "  SmokePing:   http://${SERVER_IP}:8081"
        echo -e "  Web Admin:   http://${SERVER_IP}:8080"
        ;;
    "pro")
        echo -e "  SmokePing:   http://${SERVER_IP}:8081"
        echo -e "  Web Admin:   http://${SERVER_IP}:8080"
        echo -e "  Grafana:     http://${SERVER_IP}:3000"
        if [ "$TSDB_TYPE" = "clickhouse" ]; then
            echo -e "  ClickHouse:  http://${SERVER_IP}:8123"
        else
            echo -e "  InfluxDB:    http://${SERVER_IP}:8086"
        fi
        ;;
esac

if [ "$EXTERNAL_IP" != "Unable to detect" ] && [ "$EDITION" != "basic" ]; then
    echo
    echo -e "${GREEN}External Access (if port forwarding enabled):${NC}"
    case "$EDITION" in
        "standard"|"pro")
            echo -e "  Web Admin:   http://${EXTERNAL_IP}:8080"
            ;;
    esac
fi


# Show Grafana credentials only for Pro edition
if [ "$EDITION" = "pro" ]; then
    echo
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}🌐 Grafana Credentials${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${PURPLE}URL:${NC}          http://localhost:3000"
echo -e "  ${PURPLE}Username:${NC}     ${GF_SECURITY_ADMIN_USER:-admin}"
if [ -n "$GF_SECURITY_ADMIN_PASSWORD" ]; then
    echo -e "  ${PURPLE}Password:${NC}     ${YELLOW}${GF_SECURITY_ADMIN_PASSWORD}${NC}"
    
    # Check if Grafana container is running
    if docker-compose ps 2>/dev/null | grep -q "grafana.*Up"; then
        echo -e "  ${GREEN}✅ Grafana is running${NC}"
    else
        echo -e "  ${RED}❌ Grafana is not running${NC}"
        echo -e "     ${YELLOW}Run: docker-compose up -d grafana${NC}"
    fi
else
    echo -e "  ${PURPLE}Password:${NC}     admin ${YELLOW}(default - change on first login!)${NC}"
    echo -e "  ${RED}⚠️  Run ./init-passwords-docker.sh to generate secure password${NC}"
fi
echo
echo -e "  ${CYAN}Troubleshooting Grafana Login:${NC}"
echo -e "  • If password doesn't work, restart Grafana:"
echo -e "    ${YELLOW}docker-compose restart grafana${NC}"
echo -e "  • Wait 30 seconds after restart for password reset"
echo -e "  • For persistent issues, reset the volume:"
echo -e "    ${YELLOW}docker-compose down grafana${NC}"
echo -e "    ${YELLOW}docker volume rm grafana-influx_grafana-data${NC}"
echo -e "    ${YELLOW}docker-compose up -d grafana${NC}"

fi

# Show time-series database credentials for Pro edition
if [ "$EDITION" = "pro" ]; then
    echo
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    
    if [ "$TSDB_TYPE" = "clickhouse" ]; then
        echo -e "${WHITE}💾 ClickHouse Database${NC}"
        echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "  ${PURPLE}URL:${NC}          http://localhost:8123"
        echo -e "  ${PURPLE}Database:${NC}     ${CLICKHOUSE_DB:-smokeping}"
        echo -e "  ${PURPLE}Username:${NC}     ${CLICKHOUSE_USER:-smokeping}"
        if [ -n "$CLICKHOUSE_PASSWORD" ]; then
            echo -e "  ${PURPLE}Password:${NC}     ${YELLOW}$CLICKHOUSE_PASSWORD${NC}"
        else
            echo -e "  ${RED}Password:     Not set in .env file${NC}"
        fi
        
        # Test ClickHouse connectivity
        if curl -s "http://localhost:8123/ping" >/dev/null 2>&1; then
            echo -e "  ${GREEN}✅ ClickHouse server is responding${NC}"
            
            # Test database connection with credentials
            if curl -s -u "${CLICKHOUSE_USER:-smokeping}:${CLICKHOUSE_PASSWORD}" "http://localhost:8123/" --data "SELECT 1" >/dev/null 2>&1; then
                echo -e "  ${GREEN}✅ ClickHouse authentication works${NC}"
            else
                echo -e "  ${RED}❌ ClickHouse authentication failed${NC}"
                echo -e "     ${YELLOW}⚠️  Grafana dashboards may show connection errors${NC}"
            fi
        else
            echo -e "  ${RED}❌ ClickHouse server is not responding${NC}"
            echo -e "     ${YELLOW}⚠️  Check if ClickHouse container is running${NC}"
            echo -e "     ${CYAN}🛠️  QUICK FIX:${NC}"
            echo -e "     ${YELLOW}docker-compose -f docker-compose.yml -f docker-compose.clickhouse.yml up -d${NC}"
        fi
    else
        echo -e "${WHITE}💾 InfluxDB Database${NC}"
        echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "  ${PURPLE}URL:${NC}          http://localhost:8086"
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
            
            # Test InfluxDB connectivity
            if curl -s -H "Authorization: Token $INFLUX_TOKEN" http://localhost:8086/api/v2/buckets?org=$INFLUX_ORG >/dev/null 2>&1; then
                echo -e "  ${GREEN}✅ InfluxDB token is valid${NC}"
            else
                echo -e "  ${RED}❌ InfluxDB token authentication failed${NC}"
                echo -e "     ${YELLOW}⚠️  Grafana dashboards will show 'unauthorized access' errors${NC}"
                echo ""
                echo -e "     ${CYAN}🛠️  QUICK FIX:${NC}"
                echo -e "     ${YELLOW}docker-compose down${NC}"
                echo -e "     ${YELLOW}docker volume rm grafana-influx_influxdb-data${NC}"
                echo -e "     ${YELLOW}docker-compose up -d${NC}"
                echo ""
                echo -e "     ${CYAN}🔍 For detailed diagnosis:${NC} ${YELLOW}./verify-influxdb.sh${NC}"
            fi
        else
            echo -e "  ${RED}API Token:    Not set in .env file${NC}"
        fi
    fi

fi

# Show PostgreSQL credentials for Standard and Pro editions
if [ "$EDITION" = "standard" ] || [ "$EDITION" = "pro" ]; then
    echo
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}🗄️ PostgreSQL Database${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
if [ -n "$POSTGRES_PASSWORD" ]; then
    echo -e "  ${PURPLE}Database:${NC}     smokeping_targets"
    echo -e "  ${PURPLE}Username:${NC}     smokeping"
    echo -e "  ${PURPLE}Password:${NC}     ${YELLOW}$POSTGRES_PASSWORD${NC}"
    echo -e "  ${PURPLE}Host:${NC}         localhost:5432 (container: postgres:5432)"
    
    # Test PostgreSQL connectivity from config-manager
    if docker exec grafana-influx_postgres_1 psql -U smokeping -d smokeping_targets -c "SELECT 1;" >/dev/null 2>&1; then
        # Check if database has targets
        TARGET_COUNT=$(docker exec grafana-influx_postgres_1 psql -U smokeping -d smokeping_targets -t -c "SELECT COUNT(*) FROM targets;" 2>/dev/null | xargs)
        if [ -n "$TARGET_COUNT" ] && [ "$TARGET_COUNT" -gt 0 ]; then
            echo -e "  ${GREEN}✅ PostgreSQL connection works ($TARGET_COUNT targets found)${NC}"
        else
            echo -e "  ${YELLOW}⚠️  PostgreSQL works but database is empty (0 targets)${NC}"
            echo -e "     ${YELLOW}Config-manager may be in YAML fallback mode${NC}"
            echo ""
            echo -e "     ${CYAN}🔍 For diagnosis:${NC} ${YELLOW}./verify-postgres.sh${NC}"
        fi
    else
        echo -e "  ${RED}❌ PostgreSQL connection failed${NC}"
        echo -e "     ${YELLOW}⚠️  Config-manager will use YAML fallback mode${NC}"
        echo -e "     ${YELLOW}⚠️  Grafana template variables will fail${NC}"
        echo ""
        echo -e "     ${CYAN}🛠️  QUICK FIX:${NC}"
        echo -e "     ${YELLOW}docker-compose down${NC}"
        echo -e "     ${YELLOW}docker volume rm grafana-influx_postgres-data${NC}"
        echo -e "     ${YELLOW}docker-compose up -d${NC}"
        echo ""
        echo -e "     ${CYAN}🔍 For detailed diagnosis:${NC} ${YELLOW}./verify-postgres.sh${NC}"
    fi
    
    if [ -n "$DATABASE_URL" ]; then
        echo -e "  ${PURPLE}URL:${NC}          ${YELLOW}$DATABASE_URL${NC}"
    fi
else
    echo -e "  ${RED}PostgreSQL:   Not configured in .env file${NC}"
fi

fi

# Show Web Admin credentials for Standard and Pro editions
if [ "$EDITION" = "standard" ] || [ "$EDITION" = "pro" ]; then
    echo
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}🔧 Web Admin Interface${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${PURPLE}Login URL:${NC}    http://localhost:8080/auth/login"
if [ -n "$WEB_ADMIN_USERNAME" ]; then
    echo -e "  ${PURPLE}Username:${NC}     ${YELLOW}${WEB_ADMIN_USERNAME}${NC}"
else
    echo -e "  ${PURPLE}Username:${NC}     admin ${YELLOW}(default)${NC}"
fi
if [ -n "$WEB_ADMIN_PASSWORD" ]; then
    echo -e "  ${PURPLE}Password:${NC}     ${YELLOW}${WEB_ADMIN_PASSWORD}${NC}"
else
    echo -e "  ${RED}Password:     Not set in .env file${NC}"
fi
if [ -n "$SECRET_KEY" ]; then
    echo -e "  ${PURPLE}Secret Key:${NC}   ${YELLOW}$SECRET_KEY${NC}"
else
    echo -e "  ${RED}Secret Key:   Not set in .env file${NC}"
fi

fi

echo
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${WHITE}🌍 System Configuration${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${PURPLE}Timezone:${NC}     ${TZ:-UTC}"

# Show additional config based on edition
if [ "$EDITION" = "pro" ]; then
    if [ "$TSDB_TYPE" = "clickhouse" ]; then
        echo -e "  ${PURPLE}ClickHouse URL:${NC} ${CLICKHOUSE_URL:-http://clickhouse:8123}"
        echo -e "  ${PURPLE}Database Type:${NC}  ClickHouse"
    else
        echo -e "  ${PURPLE}InfluxDB URL:${NC} ${INFLUX_URL:-http://influxdb:8086}"
        echo -e "  ${PURPLE}Database Type:${NC}  InfluxDB"
    fi
    echo -e "  ${PURPLE}RRD Dir:${NC}      ${RRD_DIR}"
fi

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
        
        # Check services based on edition
        check_service "smokeping"
        
        case "$EDITION" in
            "standard")
                check_service "postgres"
                check_service "config-manager"
                check_service "web-admin"
                ;;
            "pro")
                check_service "postgres"
                check_service "config-manager"
                check_service "web-admin"
                if [ "$TSDB_TYPE" = "clickhouse" ]; then
                    check_service "clickhouse"
                else
                    check_service "influxdb"
                fi
                check_service "grafana"
                ;;
        esac
        
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
echo -e "  ${CYAN}Run migration:${NC}    docker exec \$(curl -s http://localhost:5000/api/containers/config-manager | grep -o '\"container_name\":\"[^\"]*\"' | cut -d'\"' -f4) python3 /app/scripts/migrate_yaml_to_db.py"

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
if [ "$EDITION" = "pro" ]; then
    if [ "$TSDB_TYPE" = "clickhouse" ]; then
        check_port 8123 "ClickHouse"
    else
        check_port 8086 "InfluxDB"
    fi
fi
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