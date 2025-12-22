#!/bin/bash

# SmokePing Container Management Script
# Unified container lifecycle management across all editions

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

# Script version
VERSION="1.0.0"

# Default values
ACTION=""
EDITION="auto"
INCLUDE_VOLUMES=false
DRY_RUN=false
SHOW_LOGS=false
SERVICE=""
VERBOSE=false

# Help text
show_help() {
    cat << EOF
${CYAN}SmokePing Container Management Script v${VERSION}${NC}
${CYAN}═══════════════════════════════════════════════${NC}

${WHITE}USAGE:${NC}
    $0 --action <ACTION> [OPTIONS]

${WHITE}ACTIONS:${NC}
    ${GREEN}start${NC}      Start containers
    ${GREEN}stop${NC}       Stop containers
    ${GREEN}restart${NC}    Restart containers
    ${GREEN}remove${NC}     Remove containers
    ${GREEN}status${NC}     Show container status
    ${GREEN}logs${NC}       Show container logs

${WHITE}OPTIONS:${NC}
    ${BLUE}--edition${NC}    Edition to manage [auto|basic|standard|pro] (default: auto)
    ${BLUE}--service${NC}    Target specific service (optional)
    ${BLUE}--volumes${NC}    Include volumes when removing containers
    ${BLUE}--logs${NC}       Show logs after start/restart operations
    ${BLUE}--dry-run${NC}    Show what would be executed without running
    ${BLUE}--verbose${NC}    Enable verbose output
    ${BLUE}--help${NC}       Show this help message

${WHITE}EXAMPLES:${NC}
    $0 --action start                          # Start containers (auto-detect edition)
    $0 --action stop --edition pro             # Stop Pro edition containers
    $0 --action restart --logs                 # Restart and show logs
    $0 --action remove --volumes --edition std # Remove Standard containers and volumes
    $0 --action status --service smokeping     # Show status of smokeping service only
    $0 --action logs --service grafana         # Show Grafana logs

${WHITE}EDITIONS SUPPORTED:${NC}
    ${YELLOW}basic${NC}      SmokePing only
    ${YELLOW}standard${NC}   SmokePing + Web Admin + Config Manager + PostgreSQL  
    ${YELLOW}pro${NC}        Full stack with Grafana + InfluxDB + all services

EOF
}

# Logging functions
log_info() {
    echo -e "${BLUE}ℹ${NC}  $1"
}

log_success() {
    echo -e "${GREEN}✅${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}⚠${NC}  $1"
}

log_error() {
    echo -e "${RED}❌${NC} $1" >&2
}

log_verbose() {
    if [ "$VERBOSE" = true ]; then
        echo -e "${PURPLE}🔍${NC} $1"
    fi
}

# Detect SmokePing edition
detect_edition() {
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

# Parse command line arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --action)
                ACTION="$2"
                shift 2
                ;;
            --edition)
                EDITION="$2"
                shift 2
                ;;
            --service)
                SERVICE="$2"
                shift 2
                ;;
            --volumes)
                INCLUDE_VOLUMES=true
                shift
                ;;
            --logs)
                SHOW_LOGS=true
                shift
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            --verbose)
                VERBOSE=true
                shift
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                echo "Use --help for usage information"
                exit 1
                ;;
        esac
    done

    # Validate required arguments
    if [ -z "$ACTION" ]; then
        log_error "Action is required. Use --action <ACTION>"
        echo "Use --help for usage information"
        exit 1
    fi

    # Validate action
    case "$ACTION" in
        start|stop|restart|remove|status|logs)
            ;;
        *)
            log_error "Invalid action: $ACTION"
            echo "Valid actions: start, stop, restart, remove, status, logs"
            exit 1
            ;;
    esac
}

# Execute command with dry-run support
execute_command() {
    local cmd="$1"
    local description="$2"
    
    if [ "$DRY_RUN" = true ]; then
        echo -e "${CYAN}[DRY-RUN]${NC} $description"
        echo -e "${PURPLE}Command:${NC} $cmd"
    else
        log_verbose "$description"
        log_verbose "Executing: $cmd"
        eval "$cmd"
    fi
}

# Check if docker-compose is available
check_docker_compose() {
    if ! command -v docker-compose &> /dev/null; then
        log_error "docker-compose is not installed or not in PATH"
        exit 1
    fi
    
    if [ ! -f "docker-compose.yml" ]; then
        log_error "docker-compose.yml not found in current directory"
        log_info "Make sure you're in the correct edition directory"
        exit 1
    fi
}

# Wait for InfluxDB readiness (Pro edition only)
wait_for_influxdb() {
    if [ "$EDITION" != "pro" ] || [ "$DRY_RUN" = true ]; then
        return 0
    fi
    
    log_info "Waiting for InfluxDB to be ready..."
    local max_attempts=30
    local attempt=0
    
    while [ $attempt -lt $max_attempts ]; do
        if docker-compose ps influxdb 2>/dev/null | grep -q "Up.*healthy\|Up.*starting"; then
            log_success "InfluxDB is ready"
            return 0
        fi
        echo -n "."
        sleep 2
        attempt=$((attempt + 1))
    done
    
    log_warning "InfluxDB may not be fully ready after ${max_attempts} attempts"
}

# Synchronize InfluxDB token (Pro edition only)
sync_influx_token() {
    if [ "$EDITION" != "pro" ] || [ "$DRY_RUN" = true ]; then
        return 0
    fi
    
    log_info "Synchronizing InfluxDB token..."
    if [ -f "./sync-influx-token.sh" ]; then
        execute_command "./sync-influx-token.sh || true" "Running InfluxDB token synchronization"
    else
        log_warning "sync-influx-token.sh not found, skipping token synchronization"
    fi
}

# Start containers
action_start() {
    log_info "Starting $EDITION edition containers..."
    
    # Ensure environment is set up
    if [ ! -f ".env" ]; then
        log_warning "No .env file found"
        log_info "Run the setup script first: ./setup.sh"
        if [ "$DRY_RUN" = false ]; then
            exit 1
        fi
    fi
    
    local cmd="docker-compose up -d"
    if [ -n "$SERVICE" ]; then
        cmd="$cmd $SERVICE"
    fi
    
    execute_command "$cmd" "Starting containers"
    
    if [ "$DRY_RUN" = false ]; then
        # Pro edition specific logic
        if [ "$EDITION" = "pro" ]; then
            wait_for_influxdb
            sync_influx_token
        fi
        
        # Show logs if requested
        if [ "$SHOW_LOGS" = true ]; then
            action_logs
        fi
    fi
}

# Stop containers
action_stop() {
    log_info "Stopping $EDITION edition containers..."
    
    local cmd="docker-compose stop"
    if [ -n "$SERVICE" ]; then
        cmd="$cmd $SERVICE"
    fi
    
    execute_command "$cmd" "Stopping containers"
}

# Restart containers
action_restart() {
    log_info "Restarting $EDITION edition containers..."
    
    local cmd="docker-compose restart"
    if [ -n "$SERVICE" ]; then
        cmd="$cmd $SERVICE"
    fi
    
    execute_command "$cmd" "Restarting containers"
    
    if [ "$DRY_RUN" = false ]; then
        # Pro edition specific logic
        if [ "$EDITION" = "pro" ]; then
            wait_for_influxdb
            sync_influx_token
        fi
        
        # Show logs if requested
        if [ "$SHOW_LOGS" = true ]; then
            action_logs
        fi
    fi
}

# Remove containers
action_remove() {
    log_info "Removing $EDITION edition containers..."
    
    # Confirm destructive operation
    if [ "$INCLUDE_VOLUMES" = true ] && [ "$DRY_RUN" = false ]; then
        echo -e "${YELLOW}⚠${NC}  This will remove containers AND volumes (data will be lost)"
        read -p "Are you sure? [y/N]: " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "Operation cancelled"
            exit 0
        fi
    fi
    
    # Stop containers first
    local stop_cmd="docker-compose stop"
    if [ -n "$SERVICE" ]; then
        stop_cmd="$stop_cmd $SERVICE"
    fi
    execute_command "$stop_cmd" "Stopping containers before removal"
    
    # Remove containers
    local rm_cmd="docker-compose rm -f"
    if [ -n "$SERVICE" ]; then
        rm_cmd="$rm_cmd $SERVICE"
    fi
    execute_command "$rm_cmd" "Removing containers"
    
    # Remove volumes if requested
    if [ "$INCLUDE_VOLUMES" = true ]; then
        local volume_cmd="docker-compose down -v"
        execute_command "$volume_cmd" "Removing volumes"
    fi
}

# Show container status
action_status() {
    log_info "Container status for $EDITION edition:"
    echo
    
    local cmd="docker-compose ps"
    if [ -n "$SERVICE" ]; then
        cmd="$cmd $SERVICE"
    fi
    
    execute_command "$cmd" "Showing container status"
}

# Show container logs
action_logs() {
    local cmd="docker-compose logs"
    
    # Add follow flag for better log viewing
    if [ -z "$SERVICE" ]; then
        cmd="$cmd --tail=50 -f"
    else
        cmd="$cmd --tail=50 -f $SERVICE"
    fi
    
    if [ "$DRY_RUN" = true ]; then
        echo -e "${CYAN}[DRY-RUN]${NC} Would show logs"
        echo -e "${PURPLE}Command:${NC} $cmd"
    else
        log_info "Showing logs (Press Ctrl+C to exit)"
        eval "$cmd"
    fi
}

# Main execution
main() {
    echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${WHITE}       SmokePing Container Manager           ${CYAN}║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"
    echo
    
    # Parse command line arguments
    parse_args "$@"
    
    # Auto-detect edition if needed
    if [ "$EDITION" = "auto" ]; then
        DETECTED_EDITION=$(detect_edition)
        if [ "$DETECTED_EDITION" = "unknown" ]; then
            log_error "Could not detect SmokePing edition"
            log_info "Use --edition to specify manually: basic, standard, or pro"
            exit 1
        fi
        EDITION="$DETECTED_EDITION"
    fi
    
    log_info "Managing $EDITION edition"
    if [ -n "$SERVICE" ]; then
        log_info "Targeting service: $SERVICE"
    fi
    if [ "$DRY_RUN" = true ]; then
        log_warning "DRY-RUN mode enabled - no changes will be made"
    fi
    echo
    
    # Check prerequisites
    check_docker_compose
    
    # Execute the requested action
    case "$ACTION" in
        start)
            action_start
            ;;
        stop)
            action_stop
            ;;
        restart)
            action_restart
            ;;
        remove)
            action_remove
            ;;
        status)
            action_status
            ;;
        logs)
            action_logs
            ;;
    esac
    
    if [ "$DRY_RUN" = false ] && [ "$ACTION" != "logs" ]; then
        echo
        log_success "Operation completed successfully!"
    fi
}

# Run main function with all arguments
main "$@"