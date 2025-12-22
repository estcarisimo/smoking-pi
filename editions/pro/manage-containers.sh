#!/bin/bash

# SmokePing Pro Edition - Container Management Wrapper
# Calls the shared container management script

# Get script directory and change to it
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Change to the edition directory so the script can find docker-compose.yml and .env
cd "$SCRIPT_DIR"

# Call the shared script with all arguments
exec "$ROOT_DIR/shared/scripts/manage-containers.sh" "$@"