#!/bin/bash

# SmokePing Tunnel URL Display Utility
# Shows all currently running tunnel URLs in a formatted display

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Function to get tunnel URL from container logs
get_tunnel_url() {
    local container_name=$1
    # tail, NOT head. A quick tunnel gets a BRAND NEW hostname every time
    # cloudflared reconnects -- a container restart, a daemon restart, a
    # dropped connection -- and every one of them stays in the log. `head -1`
    # therefore reports the first hostname the container ever had, which is
    # dead, while looking perfectly plausible. Observed with 12 distinct URLs
    # in one log, reporting #1 while #12 was live.
    docker logs "$container_name" 2>&1 \
        | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' \
        | tail -1 || echo ""
}

# How many hostnames this tunnel has burned through. Shown so a stale URL
# pasted into .env or a chat is a visible risk rather than a silent one.
count_tunnel_urls() {
    local container_name=$1
    docker logs "$container_name" 2>&1 \
        | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' \
        | sort -u | wc -l | tr -d ' '
}

# Function to check container status
get_container_status() {
    local container_name=$1
    docker inspect -f '{{.State.Status}}' "$container_name" 2>/dev/null || echo "not found"
}

# Function to format URL for display
format_url() {
    local url=$1
    local max_length=60
    
    if [ -z "$url" ]; then
        echo "Not available"
    elif [ ${#url} -gt $max_length ]; then
        echo "${url:0:$max_length}..."
    else
        echo "$url"
    fi
}

# Function to display tunnel information in a table format
display_tunnel_table() {
    local tunnels=("$@")
    
    # Table header
    echo -e "\n${BOLD}Service                  Status      URL${NC}"
    echo -e "────────────────────────────────────────────────────────────────────────────────"
    
    for tunnel_info in "${tunnels[@]}"; do
        IFS='|' read -r service container_name <<< "$tunnel_info"
        
        local status=$(get_container_status "$container_name")
        local url=$(get_tunnel_url "$container_name")
        
        # Format status with color
        case $status in
            "running")
                status_colored="${GREEN}✓ Running${NC}"
                ;;
            "not found")
                status_colored="${RED}✗ Not Found${NC}"
                ;;
            *)
                status_colored="${YELLOW}? $status${NC}"
                ;;
        esac
        
        # Format URL
        if [ -n "$url" ]; then
            url_colored="${CYAN}$(format_url "$url")${NC}"
        else
            url_colored="${YELLOW}Waiting for URL...${NC}"
        fi
        
        # Print row
        printf "%-24s %-20s %s\n" "$service" "$status_colored" "$url_colored"
    done
    
    echo -e "────────────────────────────────────────────────────────────────────────────────"
}

# Main function
main() {
    echo -e "${PURPLE}🌐 SmokePing Quick Tunnel Status${NC}"
    echo -e "${PURPLE}═══════════════════════════════════════${NC}"
    
    # Check if any tunnel containers are running
    local tunnel_count=$(docker ps --format "{{.Names}}" | grep -c "^tunnel-")
    
    if [ "$tunnel_count" -eq 0 ]; then
        echo -e "\n${YELLOW}ℹ️  No tunnels are currently running${NC}"
        echo -e "\nTo create tunnels, run:"
        echo -e "  ${CYAN}./shared/scripts/create-tunnel.sh${NC}"
        exit 0
    fi
    
    # Detect which services have tunnels
    local tunnels=()
    
    if docker ps --format "{{.Names}}" | grep -q "tunnel-smokeping"; then
        tunnels+=("SmokePing Interface|tunnel-smokeping")
    fi
    
    if docker ps --format "{{.Names}}" | grep -q "tunnel-webadmin"; then
        tunnels+=("Web Administration|tunnel-webadmin")
    fi
    
    if docker ps --format "{{.Names}}" | grep -q "tunnel-grafana"; then
        tunnels+=("Grafana Dashboard|tunnel-grafana")
    fi
    
    # Display the table
    display_tunnel_table "${tunnels[@]}"
    
    # Show additional information
    # Warn when a tunnel has rotated, because anything pasted into .env or a
    # chat from an earlier run is now a dead link that still looks valid.
    for tunnel_info in "${tunnels[@]}"; do
        IFS='|' read -r service container_name <<< "$tunnel_info"
        local seen
        seen=$(count_tunnel_urls "$container_name")
        if [ "${seen:-0}" -gt 1 ]; then
            echo -e "\n${YELLOW}⚠️  ${service} has used ${seen} different hostnames${NC}"
            echo -e "   Anything you saved earlier (.env, a bookmark, a chat) is dead."
            echo -e "   Re-copy the URL above, or switch to a named tunnel."
        fi
    done

    echo -e "\n${YELLOW}ℹ️  Quick Tunnel Information:${NC}"
    echo -e "   • URLs are temporary and will change on restart"
    echo -e "   • No authentication on tunnel level (services handle auth)"
    echo -e "   • For permanent URLs, use token-based tunnels instead"
    
    echo -e "\n${BLUE}📝 Tunnel Management:${NC}"
    echo -e "   • Stop all tunnels:  ${CYAN}./shared/scripts/create-tunnel.sh stop${NC}"
    echo -e "   • Recreate tunnels:  ${CYAN}./shared/scripts/create-tunnel.sh create${NC}"
}

# Run main function
main "$@"