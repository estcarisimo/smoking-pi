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
    # The template is beside the edition's compose file (target dir); the
    # env file itself may live elsewhere (--env-file / SMOKING_PI_ENV_FILE).
    local template_file=$3
    
    if [ ! -f "$template_file" ]; then
        echo -e "${RED}Error: Template file $template_file not found${NC}"
        return 1
    fi
    
    if [ -f "$env_file" ]; then
        echo -e "${YELLOW}Warning: $env_file already exists. Creating backup...${NC}"
        cp "$env_file" "${env_file}.backup.$(date +%s)"
        chmod 600 "${env_file}.backup."* 2>/dev/null
        # Keep only the 3 most recent backups
        ls -t "${env_file}.backup."* 2>/dev/null | tail -n +4 | xargs -r rm -f
    fi
    
    echo -e "${CYAN}🔐 Generating passwords for $edition edition...${NC}"
    
    # Start with the template, created 0600 before any secret is written
    # into it: a cp leaves the template's 0644 for as long as the seds run.
    install -m 600 "$template_file" "$env_file"
    
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
            
            # The config-manager API token was left empty (= unauthenticated)
            # while the README called the APIs bearer-protected. web-admin
            # reads the same variable, so generating it costs nothing.
            local config_api_token=$(generate_hex_key 32)

            sed -i "s/TZ=UTC/TZ=$escaped_tz/" "$env_file"
            sed -i "s/POSTGRES_PASSWORD=/POSTGRES_PASSWORD=$postgres_pass/" "$env_file"
            sed -i "s/WEB_ADMIN_PASSWORD=/WEB_ADMIN_PASSWORD=$web_admin_pass/" "$env_file"
            sed -i "s/SECRET_KEY=/SECRET_KEY=$secret_key/" "$env_file"
            sed -i "s/^CONFIG_API_TOKEN=.*/CONFIG_API_TOKEN=$config_api_token/" "$env_file"
            ;;
        "pro")
            # Full password generation for pro edition
            local postgres_pass=$(generate_password 32)
            local grafana_pass=$(generate_password 32)
            local grafana_secret=$(generate_password 48)
            local web_admin_pass=$(generate_password 32)
            local secret_key=$(generate_hex_key 32)
            # API bearer tokens. Empty means unauthenticated for both, and
            # the MCP server exposes every mutation the config API has, so
            # neither should ship empty. hex keeps them sed- and URL-safe.
            local config_api_token=$(generate_hex_key 32)
            local mcp_api_token=$(generate_hex_key 32)
            local dns_admin_pass=$(generate_hex_key 24)
            
            # Get database type from env file or use default
            local tsdb_type=$(grep "^TSDB_TYPE=" "$env_file" | cut -d= -f2 || echo "influxdb")
            
            # Common replacements
            sed -i "s/TZ=UTC/TZ=$escaped_tz/" "$env_file"
            sed -i "s/POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$postgres_pass/" "$env_file"
            sed -i "s/GF_SECURITY_ADMIN_PASSWORD=.*/GF_SECURITY_ADMIN_PASSWORD=$grafana_pass/" "$env_file"
            sed -i "s/GF_SECURITY_SECRET_KEY=.*/GF_SECURITY_SECRET_KEY=$grafana_secret/" "$env_file"
            sed -i "s/WEB_ADMIN_PASSWORD=.*/WEB_ADMIN_PASSWORD=$web_admin_pass/" "$env_file"
            sed -i "s/SECRET_KEY=.*/SECRET_KEY=$secret_key/" "$env_file"
            sed -i "s/^CONFIG_API_TOKEN=.*/CONFIG_API_TOKEN=$config_api_token/" "$env_file"
            sed -i "s/^MCP_API_TOKEN=.*/MCP_API_TOKEN=$mcp_api_token/" "$env_file"
            sed -i "s/^DNS_ADMIN_PASSWORD=.*/DNS_ADMIN_PASSWORD=$dns_admin_pass/" "$env_file"
            
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

# Main function
main() {
    local edition=""
    local target_dir=""
    local env_file="${SMOKING_PI_ENV_FILE:-}"
    
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
            --env-file)
                env_file="$2"
                shift 2
                ;;
            -h|--help)
                echo "Usage: $0 --edition <basic|standard|pro> [--target-dir <directory>] [--env-file <path>]"
                echo ""
                echo "Options:"
                echo "  --edition      Edition to generate passwords for (basic|standard|pro)"
                echo "  --target-dir   Edition directory holding .env.template (default: current directory)"
                echo "  --env-file     Where to write the env file (default: <target-dir>/.env;"
                echo "                 also read from SMOKING_PI_ENV_FILE)"
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
    
    local template_file="$target_dir/.env.template"
    if [ -z "$env_file" ]; then
        env_file="$target_dir/.env"
    fi
    mkdir -p "$(dirname "$env_file")"
    
    echo -e "${GREEN}🚀 SmokePing Password Generator${NC}"
    echo -e "${GREEN}═══════════════════════════════${NC}"
    echo -e "Edition: ${BLUE}$edition${NC}"
    echo -e "Target:  ${BLUE}$target_dir${NC}"
    echo -e "Env:     ${BLUE}$env_file${NC}"
    
    # Generate passwords
    create_env_file "$edition" "$env_file" "$template_file"
    
    # The values are never printed: an install transcript, a terminal
    # scrollback or a pasted issue is the last place a password should
    # live. `smoking-pi passwords --show-secrets` reads them on request,
    # and refuses a pipe or a file (see show-passwords.sh).
    echo -e "   Not printed here. To read them: smoking-pi passwords --show-secrets"
}

# Run main function with all arguments
main "$@"