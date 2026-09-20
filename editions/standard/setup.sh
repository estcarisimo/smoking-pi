#!/bin/bash

# SmokePing Standard Edition Setup Script

set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${GREEN}🚀 SmokePing Standard Edition Setup${NC}"
echo -e "${GREEN}═══════════════════════════════════════${NC}"

# Generate passwords/environment
echo -e "${BLUE}📋 Setting up environment...${NC}"
# SMOKING_PI_ENV_FILE relocates the env file (docs/packaging.md, "Relocatable
# state"); the default is ./.env beside this script.
ENV_FILE="${SMOKING_PI_ENV_FILE:-$SCRIPT_DIR/.env}"
"$ROOT_DIR/shared/scripts/generate-passwords.sh" --edition standard --target-dir "$SCRIPT_DIR" --env-file "$ENV_FILE"
# The YAML directory the stack rewrites must exist before Compose mounts it
# (relative values are relative to this directory, as in the compose file).
( cd "$SCRIPT_DIR" && mkdir -p "${SMOKING_PI_CONFIG_DIR:-./config-manager/config}" )

# Start services
echo -e "${BLUE}🐳 Starting services...${NC}"
cd "$SCRIPT_DIR"
docker compose --env-file "$ENV_FILE" up -d

echo -e "${YELLOW}⏳ Waiting for services to be ready...${NC}"
sleep 15

# Check service health
echo -e "${BLUE}🔍 Checking service status...${NC}"
docker compose --env-file "$ENV_FILE" ps

echo -e "${GREEN}✅ SmokePing Standard Edition is ready!${NC}"
echo ""
echo -e "${CYAN}📊 Access Points:${NC}"
echo -e "  🌐 Web Admin: http://localhost:$(grep WEB_ADMIN_PORT "$ENV_FILE" | cut -d= -f2 || echo 8080)"
echo -e "     Username: $(grep WEB_ADMIN_USERNAME "$ENV_FILE" | cut -d= -f2 || echo admin)"
echo -e "     Password: run ./show-passwords.sh (or grep WEB_ADMIN_PASSWORD $ENV_FILE)"
echo ""
echo -e "  📈 SmokePing: http://localhost:$(grep SMOKEPING_PORT "$ENV_FILE" | cut -d= -f2 || echo 8081)"
echo -e "     No authentication required"
echo ""
echo -e "${CYAN}💡 Tips:${NC}"
echo -e "  - View all credentials: ./show-passwords.sh"
echo -e "  - Check logs: docker compose logs"
echo -e "  - Stop services: docker compose down"
echo -e "  - Manage targets via Web Admin interface"