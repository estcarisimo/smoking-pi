#!/bin/bash

# SmokePing Password Generation Utility
# Generates secure passwords for all editions

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m' 
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Function to generate secure passwords
generate_password() {
    local length=${1:-32}
    # Use URL-safe base64 to avoid problematic characters (/, +)
    openssl rand -base64 $length | tr -d '=' | tr '/' '_' | tr '+' '-' | head -c $length
}

# Function to generate hex keys
generate_hex_key() {
    local length=${1:-32}
    openssl rand -hex $length
}

# Function to detect timezone
detect_timezone() {
    if command -v timedatectl &> /dev/null; then
        timedatectl show --property=Timezone --value 2>/dev/null || echo "UTC"
    elif [ -f /etc/timezone ]; then
        cat /etc/timezone
    else
        echo "UTC"
    fi
}

# Function to create .env file
create_env_file() {
    local edition=$1
    local env_file=$2
    local template_file="${env_file}.template"
    
    if [ ! -f "$template_file" ]; then
        echo -e "${RED}Error: Template file $template_file not found${NC}"
        return 1
    fi
    
    if [ -f "$env_file" ]; then
        echo -e "${YELLOW}Warning: $env_file already exists. Creating backup...${NC}"
        cp "$env_file" "${env_file}.backup.$(date +%s)"
    fi
    
    echo -e "${CYAN}🔐 Generating passwords for $edition edition...${NC}"
    
    # Start with template
    cp "$template_file" "$env_file"
    
    # Detect timezone
    local detected_tz=$(detect_timezone)
    echo -e "${BLUE}🌍 Detected timezone: $detected_tz${NC}"
    
    # Escape timezone for sed (handles forward slashes)
    local escaped_tz=$(echo "$detected_tz" | sed 's/\//\\\//g')
    
    # Replace values based on edition
    case $edition in
        "basic")
            sed -i "s/TZ=UTC/TZ=$escaped_tz/" "$env_file"
            ;;
        "standard")
            local postgres_pass=$(generate_password 32)
            local web_admin_pass=$(generate_password 32)
            local secret_key=$(generate_hex_key 32)
            
            sed -i "s/TZ=UTC/TZ=$escaped_tz/" "$env_file"
            sed -i "s/POSTGRES_PASSWORD=/POSTGRES_PASSWORD=$postgres_pass/" "$env_file"
            sed -i "s/WEB_ADMIN_PASSWORD=/WEB_ADMIN_PASSWORD=$web_admin_pass/" "$env_file"
            sed -i "s/SECRET_KEY=/SECRET_KEY=$secret_key/" "$env_file"
            ;;
        "pro")
            # Full password generation for pro edition
            local postgres_pass=$(generate_password 32)
            local grafana_pass=$(generate_password 32)
            local grafana_secret=$(generate_password 48)
            local web_admin_pass=$(generate_password 32)
            local secret_key=$(generate_hex_key 32)
            
            # Get database type from env file or use default
            local tsdb_type=$(grep "^TSDB_TYPE=" "$env_file" | cut -d= -f2 || echo "influxdb")
            
            # Common replacements
            sed -i "s/TZ=UTC/TZ=$escaped_tz/" "$env_file"
            sed -i "s/POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$postgres_pass/" "$env_file"
            sed -i "s/GF_SECURITY_ADMIN_PASSWORD=.*/GF_SECURITY_ADMIN_PASSWORD=$grafana_pass/" "$env_file"
            sed -i "s/GF_SECURITY_SECRET_KEY=.*/GF_SECURITY_SECRET_KEY=$grafana_secret/" "$env_file"
            sed -i "s/WEB_ADMIN_PASSWORD=.*/WEB_ADMIN_PASSWORD=$web_admin_pass/" "$env_file"
            sed -i "s/SECRET_KEY=.*/SECRET_KEY=$secret_key/" "$env_file"
            
            # Database-specific replacements
            if [ "$tsdb_type" = "influxdb" ]; then
                local influx_token=$(generate_password 64)
                local influx_admin_pass=$(generate_password 32)
                sed -i "s/INFLUX_TOKEN=.*/INFLUX_TOKEN=$influx_token/" "$env_file"
                sed -i "s/DOCKER_INFLUXDB_INIT_PASSWORD=.*/DOCKER_INFLUXDB_INIT_PASSWORD=$influx_admin_pass/" "$env_file"
            elif [ "$tsdb_type" = "clickhouse" ]; then
                local clickhouse_pass=$(generate_password 32)
                sed -i "s/CLICKHOUSE_PASSWORD=.*/CLICKHOUSE_PASSWORD=$clickhouse_pass/" "$env_file"
            fi
            ;;
    esac
    
    # Set secure permissions
    chmod 600 "$env_file"
    
    echo -e "${GREEN}✅ Passwords generated and saved to $env_file${NC}"
}

# Function to display generated credentials
show_credentials() {
    local edition=$1
    local env_file=$2
    
    if [ ! -f "$env_file" ]; then
        echo -e "${RED}Error: $env_file not found${NC}"
        return 1
    fi
    
    echo -e "\n${PURPLE}📋 Generated Credentials for $edition Edition:${NC}"
    echo -e "${PURPLE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    
    source "$env_file"
    
    case $edition in
        "basic")
            echo -e "${CYAN}🌐 SmokePing Web Interface:${NC}"
            echo -e "  URL: http://localhost:${SMOKEPING_PORT:-8080}"
            echo -e "  No authentication required"
            ;;
        "standard")
            echo -e "${CYAN}🌐 Web Admin Interface:${NC}"
            echo -e "  URL: http://localhost:${WEB_ADMIN_PORT:-8080}"
            echo -e "  Username: ${WEB_ADMIN_USERNAME:-admin}"
            echo -e "  Password: ${WEB_ADMIN_PASSWORD}"
            echo -e "\n${CYAN}📊 SmokePing Interface:${NC}"
            echo -e "  URL: http://localhost:${SMOKEPING_PORT:-8081}"
            echo -e "\n${CYAN}🗄️ PostgreSQL Database:${NC}"
            echo -e "  Database: ${POSTGRES_DB:-smokeping_targets}"
            echo -e "  Username: ${POSTGRES_USER:-smokeping}"
            echo -e "  Password: ${POSTGRES_PASSWORD}"
            ;;
        "pro")
            echo -e "${CYAN}🌐 Web Admin Interface:${NC}"
            echo -e "  URL: http://localhost:${WEB_ADMIN_PORT:-8080}"
            echo -e "  Username: ${WEB_ADMIN_USERNAME:-admin}"
            echo -e "  Password: ${WEB_ADMIN_PASSWORD}"
            echo -e "\n${CYAN}📊 Grafana Dashboard:${NC}"
            echo -e "  URL: http://localhost:${GRAFANA_PORT:-3000}"
            echo -e "  Username: ${GF_SECURITY_ADMIN_USER:-admin}"
            echo -e "  Password: ${GF_SECURITY_ADMIN_PASSWORD}"
            
            # Show database-specific credentials
            if [ "${TSDB_TYPE:-influxdb}" = "influxdb" ]; then
                echo -e "\n${CYAN}📈 InfluxDB:${NC}"
                echo -e "  URL: http://localhost:${INFLUXDB_PORT:-8086}"
                echo -e "  Token: ${INFLUX_TOKEN}"
                echo -e "  Admin Password: ${DOCKER_INFLUXDB_INIT_PASSWORD}"
            elif [ "${TSDB_TYPE:-influxdb}" = "clickhouse" ]; then
                echo -e "\n${CYAN}📈 ClickHouse:${NC}"
                echo -e "  URL: http://localhost:${CLICKHOUSE_PORT:-8123}"
                echo -e "  Username: ${CLICKHOUSE_USER:-smokeping}"
                echo -e "  Password: ${CLICKHOUSE_PASSWORD}"
            fi
            
            echo -e "\n${CYAN}🎯 SmokePing Interface:${NC}"
            echo -e "  URL: http://localhost:${SMOKEPING_PORT:-8081}"
            ;;
    esac
    
    echo -e "\n${YELLOW}💡 Tip: Save these credentials securely!${NC}"
}

# Main function
main() {
    local edition=""
    local target_dir=""
    
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --edition)
                edition="$2"
                shift 2
                ;;
            --target-dir)
                target_dir="$2"  
                shift 2
                ;;
            -h|--help)
                echo "Usage: $0 --edition <basic|standard|pro> [--target-dir <directory>]"
                echo ""
                echo "Options:"
                echo "  --edition      Edition to generate passwords for (basic|standard|pro)"
                echo "  --target-dir   Target directory (default: current directory)"
                echo "  -h, --help     Show this help message"
                exit 0
                ;;
            *)
                echo -e "${RED}Unknown option: $1${NC}"
                exit 1
                ;;
        esac
    done
    
    # Validate edition
    if [ -z "$edition" ]; then
        echo -e "${RED}Error: --edition is required${NC}"
        echo "Use --help for usage information"
        exit 1
    fi
    
    if [[ ! "$edition" =~ ^(basic|standard|pro)$ ]]; then
        echo -e "${RED}Error: Invalid edition '$edition'. Must be basic, standard, or pro${NC}"
        exit 1
    fi
    
    # Set target directory
    if [ -z "$target_dir" ]; then
        target_dir="."
    fi
    
    local env_file="$target_dir/.env"
    
    echo -e "${GREEN}🚀 SmokePing Password Generator${NC}"
    echo -e "${GREEN}═══════════════════════════════${NC}"
    echo -e "Edition: ${BLUE}$edition${NC}"
    echo -e "Target:  ${BLUE}$target_dir${NC}"
    
    # Generate passwords
    create_env_file "$edition" "$env_file"
    
    # Show credentials
    show_credentials "$edition" "$env_file"
    
    echo -e "\n${GREEN}✅ Password generation complete!${NC}"
    echo -e "${YELLOW}Next steps:${NC}"
    echo -e "  1. Review the generated .env file"
    echo -e "  2. Run: docker-compose up -d"
    echo -e "  3. Access your services using the credentials above"
}

# Run main function with all arguments
main "$@"