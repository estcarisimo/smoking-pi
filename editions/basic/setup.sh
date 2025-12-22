#!/bin/bash

# SmokePing Basic Edition Setup Script

set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}🚀 SmokePing Basic Edition Setup${NC}"
echo -e "${GREEN}═══════════════════════════════════${NC}"

# Generate passwords/environment
echo -e "${BLUE}📋 Setting up environment...${NC}"
"$ROOT_DIR/shared/scripts/generate-passwords.sh" --edition basic --target-dir "$SCRIPT_DIR"

# Start services
echo -e "${BLUE}🐳 Starting SmokePing...${NC}"
cd "$SCRIPT_DIR"
docker-compose up -d

echo -e "${YELLOW}⏳ Waiting for services to be ready...${NC}"
sleep 10

echo -e "${GREEN}✅ SmokePing Basic Edition is ready!${NC}"
echo -e "🌐 Web Interface: http://localhost:$(grep SMOKEPING_PORT .env | cut -d= -f2 || echo 8080)"
echo -e "📁 Configuration: Edit config/Targets to add monitoring targets"
echo -e "📊 View graphs and statistics through the web interface"