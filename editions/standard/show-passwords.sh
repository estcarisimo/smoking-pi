#!/bin/bash

# SmokePing Standard Edition - Password Display Wrapper
# Calls the shared show-passwords script

# Get script directory and change to it
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Change to the edition directory so the script can find docker-compose.yml and .env
cd "$SCRIPT_DIR"

# Call the shared script
exec "$ROOT_DIR/shared/scripts/show-passwords.sh" "$@"