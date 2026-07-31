# SmokePing Docker Image
# Based on LinuxServer.io's well-maintained SmokePing image
# This wrapper allows custom naming per edition
#
# Pinned tag (was :latest). LinuxServer's version tags follow the upstream
# SmokePing release: version-2.9.0-r0 = SmokePing 2.9.0, Alpine package rev 0.
# Bump deliberately when upgrading.

FROM linuxserver/smokeping:version-2.9.0-r0

# Add metadata labels for identification
LABEL maintainer="SmokePing Multi-Edition Project"
LABEL description="SmokePing network latency monitoring"
LABEL base.image="linuxserver/smokeping:version-2.9.0-r0"

# Bake exporter runtime dependencies into the image so cont-init scripts
# (editions/pro/custom-cont-init.d/99-run-exporter.sh, basic's entrypoint
# YAML converter) don't have to apk/pip install on every container start
# (which required internet at boot and added >70s to startup).
# - traceroute: full version; busybox's applet output is not reliably parseable
# - influxdb-client: for the RRD -> InfluxDB exporter
# - PyYAML: for the Basic edition YAML -> Targets converter
RUN apk add --no-cache python3 py3-pip rrdtool traceroute && \
    pip3 install --no-cache-dir --break-system-packages influxdb-client PyYAML

# The edition-specific naming is handled by docker-compose.yml
# No additional changes needed as all customization happens via:
# - Volume mounts (config, scripts)
# - Environment variables
# - Custom entrypoints (Basic edition)
