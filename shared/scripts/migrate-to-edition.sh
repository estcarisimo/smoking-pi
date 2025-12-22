#!/bin/bash

# SmokePing Edition Migration Script
# Migrates data from current setup to new edition structure

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
NC='\033[0m'

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Function to backup current data
backup_current_data() {
    local backup_dir="$1"
    
    echo -e "${BLUE}📦 Creating backup of current data...${NC}"
    
    # Create backup directory
    mkdir -p "$backup_dir"/{volumes,configs,env}
    
    # Backup current .env files
    find "$ROOT_DIR" -name ".env" -not -path "*/editions/*" -exec cp {} "$backup_dir/env/" \; 2>/dev/null || true
    
    # Backup configuration files
    if [ -d "$ROOT_DIR/config-manager/config" ]; then
        cp -r "$ROOT_DIR/config-manager/config" "$backup_dir/configs/"
    fi
    
    # Backup Docker volumes
    echo -e "${BLUE}📦 Backing up Docker volumes...${NC}"
    
    # Get list of volumes
    local volumes=$(docker volume ls --format "{{.Name}}" | grep -E "(grafana|influx|postgres|smokeping)" || true)
    
    for volume in $volumes; do
        echo -e "  • Backing up volume: $volume"
        docker run --rm -v "$volume:/data" -v "$backup_dir/volumes:/backup" alpine tar czf "/backup/${volume}.tar.gz" -C /data . 2>/dev/null || true
    done
    
    echo -e "${GREEN}✅ Backup completed: $backup_dir${NC}"
}

# Function to migrate PostgreSQL data
migrate_postgres_data() {
    local backup_dir="$1"
    local target_edition="$2"
    
    if [[ "$target_edition" == "basic" ]]; then
        echo -e "${YELLOW}⚠️  Basic edition doesn't use PostgreSQL - skipping database migration${NC}"
        return 0
    fi
    
    echo -e "${BLUE}🗄️  Migrating PostgreSQL data...${NC}"
    
    local postgres_backup=""
    for f in "$backup_dir/volumes"/postgres*.tar.gz; do
        if [ -f "$f" ]; then
            postgres_backup="$f"
            break
        fi
    done
    
    if [ -n "$postgres_backup" ] && [ -f "$postgres_backup" ]; then
        echo -e "  • Found PostgreSQL backup: $(basename "$postgres_backup")"
        
        # Restore to new volume
        local new_volume_name="smokeping-${target_edition}-postgres-data"
        docker volume create "$new_volume_name" >/dev/null
        docker run --rm -v "$new_volume_name:/data" -v "$backup_dir/volumes:/backup" alpine tar xzf "/backup/$(basename "$postgres_backup")" -C /data
        
        echo -e "${GREEN}✅ PostgreSQL data migrated to: $new_volume_name${NC}"
    else
        echo -e "${YELLOW}⚠️  No PostgreSQL backup found - starting with fresh database${NC}"
    fi
}

# Function to migrate SmokePing data  
migrate_smokeping_data() {
    local backup_dir="$1"
    local target_edition="$2"
    
    echo -e "${BLUE}🎯 Migrating SmokePing data...${NC}"
    
    local smokeping_backup=""
    for f in "$backup_dir/volumes"/smokeping*.tar.gz; do
        if [ -f "$f" ]; then
            smokeping_backup="$f"
            break
        fi
    done
    
    if [ -n "$smokeping_backup" ] && [ -f "$smokeping_backup" ]; then
        echo -e "  • Found SmokePing backup: $(basename "$smokeping_backup")"
        
        # Restore to new volume
        local new_volume_name="smokeping-${target_edition}-data"
        docker volume create "$new_volume_name" >/dev/null
        docker run --rm -v "$new_volume_name:/data" -v "$backup_dir/volumes:/backup" alpine tar xzf "/backup/$(basename "$smokeping_backup")" -C /data
        
        echo -e "${GREEN}✅ SmokePing data migrated to: $new_volume_name${NC}"
    else
        echo -e "${YELLOW}⚠️  No SmokePing backup found - starting with fresh data${NC}"
    fi
}

# Function to migrate Grafana data (pro edition only)
migrate_grafana_data() {
    local backup_dir="$1"
    local target_edition="$2"
    
    if [[ "$target_edition" != "pro" ]]; then
        return 0
    fi
    
    echo -e "${BLUE}📊 Migrating Grafana data...${NC}"
    
    local grafana_backup=""
    for f in "$backup_dir/volumes"/grafana*.tar.gz; do
        if [ -f "$f" ]; then
            grafana_backup="$f"
            break
        fi
    done
    
    if [ -n "$grafana_backup" ] && [ -f "$grafana_backup" ]; then
        echo -e "  • Found Grafana backup: $(basename "$grafana_backup")"
        
        # Restore to new volume
        local new_volume_name="smokeping-pro-grafana-data"
        docker volume create "$new_volume_name" >/dev/null
        docker run --rm -v "$new_volume_name:/data" -v "$backup_dir/volumes:/backup" alpine tar xzf "/backup/$(basename "$grafana_backup")" -C /data
        
        echo -e "${GREEN}✅ Grafana data migrated to: $new_volume_name${NC}"
    else
        echo -e "${YELLOW}⚠️  No Grafana backup found - starting with fresh dashboards${NC}"
    fi
}

# Main migration function
migrate_to_edition() {
    local target_edition="$1"
    local backup_timestamp=$(date +%Y%m%d_%H%M%S)
    local backup_dir="$ROOT_DIR/backups/migration-$backup_timestamp"
    
    echo -e "${PURPLE}🔄 SmokePing Edition Migration${NC}"
    echo -e "${PURPLE}═══════════════════════════════════${NC}"
    echo -e "Target Edition: ${GREEN}$target_edition${NC}"
    echo -e "Backup Location: ${BLUE}$backup_dir${NC}"
    
    # Validate edition
    if [[ ! -d "$ROOT_DIR/editions/$target_edition" ]]; then
        echo -e "${RED}❌ Error: Edition '$target_edition' not found${NC}"
        exit 1
    fi
    
    # Create backup
    backup_current_data "$backup_dir"
    
    # Stop current services
    echo -e "${BLUE}🛑 Stopping current services...${NC}"
    
    # Try to stop services in various locations
    for compose_file in "$ROOT_DIR/grafana-influx/docker-compose.yml" "$ROOT_DIR/docker-compose.yml"; do
        if [ -f "$compose_file" ]; then
            echo -e "  • Stopping services in $(dirname "$compose_file")"
            (cd "$(dirname "$compose_file")" && docker-compose down) 2>/dev/null || true
        fi
    done
    
    # Migrate data based on edition
    migrate_postgres_data "$backup_dir" "$target_edition"
    migrate_smokeping_data "$backup_dir" "$target_edition"
    migrate_grafana_data "$backup_dir" "$target_edition"
    
    # Generate new configuration
    echo -e "${BLUE}⚙️  Setting up $target_edition edition...${NC}"
    cd "$ROOT_DIR/editions/$target_edition"
    
    # Generate passwords for new edition
    "$ROOT_DIR/shared/scripts/generate-passwords.sh" --edition "$target_edition" --target-dir .
    
    echo -e "${GREEN}✅ Migration completed successfully!${NC}"
    echo -e "\n${YELLOW}📋 Next Steps:${NC}"
    echo -e "  1. Review the configuration: $ROOT_DIR/editions/$target_edition/.env"
    echo -e "  2. Start services: cd editions/$target_edition && docker-compose up -d"
    echo -e "  3. Verify data migration worked correctly"
    echo -e "  4. Backup location: $backup_dir"
    echo -e "\n${PURPLE}🎯 Edition Features:${NC}"
    
    case $target_edition in
        "basic")
            echo -e "  • Simple SmokePing monitoring"
            echo -e "  • LinuxServer.io container"
            echo -e "  • YAML configuration"
            echo -e "  • Web interface: http://localhost:8080"
            ;;
        "standard")
            echo -e "  • PostgreSQL database"
            echo -e "  • Web admin interface"
            echo -e "  • Config manager API"
            echo -e "  • Web admin: http://localhost:8080"
            echo -e "  • SmokePing: http://localhost:8081"
            ;;
        "pro")
            echo -e "  • Full monitoring stack"
            echo -e "  • Grafana dashboards"
            echo -e "  • InfluxDB time-series database"
            echo -e "  • All standard edition features"
            echo -e "  • Web admin: http://localhost:8080"
            echo -e "  • SmokePing: http://localhost:8081"  
            echo -e "  • Grafana: http://localhost:3000"
            ;;
    esac
}

# Main function
main() {
    local target_edition=""
    
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            -h|--help)
                echo "Usage: $0 <basic|standard|pro>"
                echo ""
                echo "Migrates from current SmokePing setup to specified edition."
                echo ""
                echo "Editions:"
                echo "  basic     - Simple SmokePing with YAML config"
                echo "  standard  - PostgreSQL + Web Admin + Config Manager"
                echo "  pro       - Full stack with Grafana + InfluxDB"
                echo ""
                echo "This script will:"
                echo "  1. Backup current data and configuration"
                echo "  2. Stop running services"
                echo "  3. Migrate data to new edition structure"
                echo "  4. Generate new passwords and configuration"
                exit 0
                ;;
            basic|standard|pro)
                target_edition="$1"
                shift
                ;;
            *)
                echo -e "${RED}❌ Error: Invalid edition '$1'${NC}"
                echo "Valid editions: basic, standard, pro"
                echo "Use --help for more information"
                exit 1
                ;;
        esac
    done
    
    if [ -z "$target_edition" ]; then
        echo -e "${RED}❌ Error: Edition required${NC}"
        echo "Usage: $0 <basic|standard|pro>"
        echo "Use --help for more information"
        exit 1
    fi
    
    # Confirmation
    echo -e "${YELLOW}⚠️  This will migrate your current setup to $target_edition edition.${NC}"
    echo -e "${YELLOW}   A backup will be created before making any changes.${NC}"
    echo -e "\nContinue? (y/N): "
    read -r response
    
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        echo -e "${BLUE}Migration cancelled.${NC}"
        exit 0
    fi
    
    # Run migration
    migrate_to_edition "$target_edition"
}

# Run main with all arguments
main "$@"