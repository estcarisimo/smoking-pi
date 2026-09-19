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
# - iw: nl80211 station/info/survey for the Wi-Fi link collector (wifi_link.py);
#   reads need no capability, the container just has to share the host netns
RUN apk add --no-cache python3 py3-pip rrdtool traceroute iw && \
    pip3 install --no-cache-dir --break-system-packages influxdb-client PyYAML

# A curl that speaks HTTP/3. Alpine's curl (and the official curlimages/curl)
# are built without a QUIC backend, so the HTTP/3 probe needs its own binary.
# stunnel/static-curl publishes fully static musl builds with ngtcp2/nghttp3;
# the tarball is pinned by version and sha256 per architecture. The HTTP/1.1
# and HTTP/2 probes use this same binary on purpose: one TLS stack for all
# three, so a difference between versions is the protocol, not the build.
ARG STATIC_CURL_VERSION=8.22.0
ARG STATIC_CURL_SHA256_AARCH64=cf94cbeaae1b3c1944a4a761ef04478f8f0b23e93a324b5ed009b2082eda11f3
ARG STATIC_CURL_SHA256_X86_64=dfb02460ba2abe513087538f12a3cf79b74b64a5ea3787ce8ac0cdb11251f884
RUN set -eu; \
    arch="$(uname -m)"; \
    case "$arch" in \
      aarch64) sha="$STATIC_CURL_SHA256_AARCH64" ;; \
      x86_64)  sha="$STATIC_CURL_SHA256_X86_64" ;; \
      *) echo "no pinned static curl for $arch" >&2; exit 1 ;; \
    esac; \
    apk add --no-cache --virtual .fetch xz; \
    cd /tmp; \
    curl -fsSL -o curl.tar.xz \
      "https://github.com/stunnel/static-curl/releases/download/${STATIC_CURL_VERSION}/curl-linux-${arch}-musl-${STATIC_CURL_VERSION}.tar.xz"; \
    echo "${sha}  curl.tar.xz" | sha256sum -c -; \
    tar -xJf curl.tar.xz curl; \
    install -m 0755 curl /usr/local/bin/curl-h3; \
    rm -f curl curl.tar.xz; \
    apk del .fetch; \
    /usr/local/bin/curl-h3 --version | grep -q HTTP3

# The edition-specific naming is handled by docker-compose.yml
# No additional changes needed as all customization happens via:
# - Volume mounts (config, scripts)
# - Environment variables
# - Custom entrypoints (Basic edition)
