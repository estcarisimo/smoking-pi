#!/bin/bash

# PostgreSQL Verification Script for SmokePing Pro Edition
# Comprehensive PostgreSQL health checking and troubleshooting

set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Load environment variables
if [ -f "$SCRIPT_DIR/.env" ]; then
    source "$SCRIPT_DIR/.env"
else
    echo -e "${RED}❌ .env file not found. Run ./setup.sh first.${NC}"
    exit 1
fi

# Database configuration
DB_HOST="localhost"
DB_PORT="5432"
DB_NAME="smokeping_targets"
DB_USER="smokeping"
DB_PASSWORD="${POSTGRES_PASSWORD}"

echo -e "${PURPLE}🔍 PostgreSQL Verification for SmokePing Pro Edition${NC}"
echo -e "${PURPLE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Function to check if container is running
check_container_status() {
    local container_name="pro-postgres-1"
    
    echo -e "${BLUE}📦 Checking PostgreSQL container status...${NC}"
    
    if docker ps --format "{{.Names}}" | grep -q "^${container_name}$"; then
        local status=$(docker ps --format "table {{.Names}}\t{{.Status}}" | grep "$container_name" | awk '{print $2, $3, $4}')
        echo -e "   ${GREEN}✅ Container running: ${status}${NC}"
        
        # Check health status
        local health=$(docker inspect "$container_name" --format='{{.State.Health.Status}}' 2>/dev/null || echo "no-healthcheck")
        if [ "$health" = "healthy" ]; then
            echo -e "   ${GREEN}✅ Health check: ${health}${NC}"
        elif [ "$health" = "unhealthy" ]; then
            echo -e "   ${RED}❌ Health check: ${health}${NC}"
        else
            echo -e "   ${YELLOW}⚠️  Health check: ${health}${NC}"
        fi
        return 0
    else
        echo -e "   ${RED}❌ Container not running${NC}"
        return 1
    fi
}

# Function to check PostgreSQL connectivity
check_postgres_connection() {
    echo -e "${BLUE}🔗 Testing PostgreSQL connection...${NC}"
    
    # Test connection using docker exec
    if docker exec pro-postgres-1 pg_isready -U "$DB_USER" -d "$DB_NAME" -h localhost -p 5432 >/dev/null 2>&1; then
        echo -e "   ${GREEN}✅ PostgreSQL is accepting connections${NC}"
        return 0
    else
        echo -e "   ${RED}❌ PostgreSQL connection failed${NC}"
        return 1
    fi
}

# Function to test database schema
check_database_schema() {
    echo -e "${BLUE}🗄️ Verifying database schema...${NC}"
    
    local query="SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name;"
    
    if tables=$(docker exec pro-postgres-1 psql -U "$DB_USER" -d "$DB_NAME" -t -c "$query" 2>/dev/null); then
        echo -e "   ${GREEN}✅ Database schema accessible${NC}"
        echo -e "   ${CYAN}📋 Tables found:${NC}"
        echo "$tables" | while read -r table; do
            if [ -n "$(echo "$table" | xargs)" ]; then
                echo -e "      • $(echo "$table" | xargs)"
            fi
        done
        return 0
    else
        echo -e "   ${RED}❌ Cannot access database schema${NC}"
        return 1
    fi
}

# Function to check initial data
check_initial_data() {
    echo -e "${BLUE}📊 Checking initial data population...${NC}"
    
    # Check target_categories
    local categories_count=$(docker exec pro-postgres-1 psql -U "$DB_USER" -d "$DB_NAME" -t -c "SELECT COUNT(*) FROM target_categories;" 2>/dev/null | xargs || echo "0")
    echo -e "   Target categories: ${categories_count}"
    
    # Check probes
    local probes_count=$(docker exec pro-postgres-1 psql -U "$DB_USER" -d "$DB_NAME" -t -c "SELECT COUNT(*) FROM probes;" 2>/dev/null | xargs || echo "0")
    echo -e "   Probes configured: ${probes_count}"
    
    # Check targets
    local targets_count=$(docker exec pro-postgres-1 psql -U "$DB_USER" -d "$DB_NAME" -t -c "SELECT COUNT(*) FROM targets;" 2>/dev/null | xargs || echo "0")
    echo -e "   Targets configured: ${targets_count}"
    
    if [ "$categories_count" -gt 0 ] && [ "$probes_count" -gt 0 ]; then
        echo -e "   ${GREEN}✅ Initial data populated${NC}"
        return 0
    else
        echo -e "   ${YELLOW}⚠️  Initial data may be missing${NC}"
        return 1
    fi
}

# Function to check probe configuration
check_probe_configuration() {
    echo -e "${BLUE}🎯 Verifying probe configuration...${NC}"
    
    # Check FPing binary path
    local fping_path=$(docker exec pro-postgres-1 psql -U "$DB_USER" -d "$DB_NAME" -t -c "SELECT binary_path FROM probes WHERE name = 'FPing';" 2>/dev/null | xargs || echo "")
    
    if [ "$fping_path" = "/usr/sbin/fping" ]; then
        echo -e "   ${GREEN}✅ FPing binary path correct: ${fping_path}${NC}"
    elif [ "$fping_path" = "/usr/bin/fping" ]; then
        echo -e "   ${YELLOW}⚠️  FPing binary path outdated: ${fping_path}${NC}"
        echo -e "   ${YELLOW}   Should be: /usr/sbin/fping${NC}"
        return 1
    else
        echo -e "   ${RED}❌ FPing probe not found or misconfigured${NC}"
        return 1
    fi
    
    return 0
}

# Function to test application connectivity
check_application_connectivity() {
    echo -e "${BLUE}🔌 Testing application connectivity...${NC}"
    
    # Test config-manager connection
    if docker logs pro-config-manager-1 2>&1 | grep -q "Connected to PostgreSQL"; then
        echo -e "   ${GREEN}✅ Config-manager connected to PostgreSQL${NC}"
    elif docker logs pro-config-manager-1 2>&1 | grep -q "Using YAML fallback"; then
        echo -e "   ${YELLOW}⚠️  Config-manager using YAML fallback${NC}"
        return 1
    else
        echo -e "   ${YELLOW}⚠️  Config-manager connection status unclear${NC}"
    fi
    
    # Test web-admin connection
    if docker logs pro-web-admin-1 2>&1 | grep -q "Connected to database"; then
        echo -e "   ${GREEN}✅ Web-admin connected to PostgreSQL${NC}"
    else
        echo -e "   ${YELLOW}⚠️  Web-admin connection status unclear${NC}"
    fi
    
    return 0
}

# Function to show diagnostic information
show_diagnostics() {
    echo -e "${BLUE}🔍 Diagnostic Information${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    
    echo -e "${CYAN}Database Configuration:${NC}"
    echo -e "  Host: ${DB_HOST}:${DB_PORT}"
    echo -e "  Database: ${DB_NAME}"
    echo -e "  Username: ${DB_USER}"
    echo -e "  Password: [${#DB_PASSWORD} characters]"
    echo ""
    
    echo -e "${CYAN}Container Logs (last 10 lines):${NC}"
    docker logs pro-postgres-1 --tail 10 2>&1 || echo "Cannot access container logs"
    echo ""
}

# Function to provide troubleshooting suggestions
show_troubleshooting() {
    echo -e "${YELLOW}🛠️  Troubleshooting Suggestions${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "${CYAN}Quick Fixes:${NC}"
    echo -e "1. ${YELLOW}Reset PostgreSQL data:${NC}"
    echo -e "   docker-compose down"
    echo -e "   docker volume rm pro_postgres-data"
    echo -e "   docker-compose up -d"
    echo ""
    echo -e "2. ${YELLOW}Restart services in order:${NC}"
    echo -e "   docker-compose restart postgres"
    echo -e "   docker-compose restart config-manager"
    echo -e "   docker-compose restart web-admin"
    echo ""
    echo -e "3. ${YELLOW}Check container logs:${NC}"
    echo -e "   docker-compose logs postgres"
    echo -e "   docker-compose logs config-manager"
    echo ""
    echo -e "4. ${YELLOW}Manual database connection test:${NC}"
    echo -e "   docker exec -it pro-postgres-1 psql -U $DB_USER -d $DB_NAME"
    echo ""
}

# Main execution
main() {
    local exit_code=0
    
    # Run all checks
    check_container_status || exit_code=1
    echo ""
    
    check_postgres_connection || exit_code=1
    echo ""
    
    check_database_schema || exit_code=1
    echo ""
    
    check_initial_data || exit_code=1
    echo ""
    
    check_probe_configuration || exit_code=1
    echo ""
    
    check_application_connectivity || exit_code=1
    echo ""
    
    # Show diagnostics if there are issues
    if [ $exit_code -ne 0 ]; then
        show_diagnostics
        show_troubleshooting
    fi
    
    # Summary
    echo -e "${PURPLE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    if [ $exit_code -eq 0 ]; then
        echo -e "${GREEN}✅ PostgreSQL verification completed successfully${NC}"
    else
        echo -e "${RED}❌ PostgreSQL verification found issues${NC}"
    fi
    echo -e "${PURPLE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    
    exit $exit_code
}

# Run main function
main "$@"