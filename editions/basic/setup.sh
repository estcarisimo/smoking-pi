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
# SMOKING_PI_ENV_FILE relocates the env file (docs/packaging.md, "Relocatable
# state"); the default is ./.env beside this script.
ENV_FILE="${SMOKING_PI_ENV_FILE:-$SCRIPT_DIR/.env}"
"$ROOT_DIR/shared/scripts/generate-passwords.sh" --edition basic --target-dir "$SCRIPT_DIR" --env-file "$ENV_FILE"

# Start services
echo -e "${BLUE}🐳 Starting SmokePing...${NC}"
cd "$SCRIPT_DIR"
docker compose --env-file "$ENV_FILE" up -d

echo -e "${YELLOW}⏳ Waiting for services to be ready...${NC}"
sleep 10

# Run by `smoking-pi install`, which ends on its own summary and on the
# address that works from the computer you are on: stop here.
if [ -n "${SMOKING_PI_INSTALL:-}" ]; then
    echo -e "${GREEN}✅ The Basic edition is up.${NC}"
    exit 0
fi

echo -e "${GREEN}✅ SmokePing Basic Edition is ready!${NC}"
echo -e "🌐 The address to open: smoking-pi url"
echo -e "📁 Configuration: Edit config/Targets to add monitoring targets"
echo -e "📊 View graphs and statistics through the web interface"
# The command, on the PATH from any directory (a clone only; the package
# and Homebrew install their own). Never fails the setup.
SMOKING_PI_HOME="$ROOT_DIR" SMOKING_PI_EDITION=basic "$ROOT_DIR/cli/smoking-pi" link --quiet || true
echo -e "💡 What is here, from any directory: smoking-pi"