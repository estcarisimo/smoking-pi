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
"$ROOT_DIR/shared/scripts/generate-passwords.sh" --edition standard --target-dir "$SCRIPT_DIR"

# Start services
echo -e "${BLUE}🐳 Starting services...${NC}"
cd "$SCRIPT_DIR"
docker compose up -d

echo -e "${YELLOW}⏳ Waiting for services to be ready...${NC}"
sleep 15

# Check service health
echo -e "${BLUE}🔍 Checking service status...${NC}"
docker compose ps

echo -e "${GREEN}✅ SmokePing Standard Edition is ready!${NC}"
echo ""
echo -e "${CYAN}📊 Access Points:${NC}"
echo -e "  🌐 Web Admin: http://localhost:$(grep WEB_ADMIN_PORT .env | cut -d= -f2 || echo 8080)"
echo -e "     Username: $(grep WEB_ADMIN_USERNAME .env | cut -d= -f2 || echo admin)"
echo -e "     Password: See .env file or run: grep WEB_ADMIN_PASSWORD .env"
echo ""
echo -e "  📈 SmokePing: http://localhost:$(grep SMOKEPING_PORT .env | cut -d= -f2 || echo 8081)"
echo -e "     No authentication required"
echo ""
echo -e "${CYAN}💡 Tips:${NC}"
echo -e "  - View all credentials: cat .env"
echo -e "  - Check logs: docker compose logs"
echo -e "  - Stop services: docker compose down"
echo -e "  - Manage targets via Web Admin interface"