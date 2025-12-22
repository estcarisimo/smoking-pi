# SmokePing Docker Image
# Based on LinuxServer.io's well-maintained SmokePing image
# This wrapper allows custom naming per edition

FROM linuxserver/smokeping:latest

# Add metadata labels for identification
LABEL maintainer="SmokePing Multi-Edition Project"
LABEL description="SmokePing network latency monitoring"
LABEL base.image="linuxserver/smokeping:latest"

# The edition-specific naming is handled by docker-compose.yml
# No additional changes needed as all customization happens via:
# - Volume mounts (config, scripts)
# - Environment variables
# - Custom entrypoints (Basic edition)