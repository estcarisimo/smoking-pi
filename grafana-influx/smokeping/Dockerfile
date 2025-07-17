# ─────────────────────────────────────────────────────────────
#  SmokePing Dockerfile — with RRD → InfluxDB exporter
#  (no credentials baked in; Compose/.env injects them at runtime)
# ─────────────────────────────────────────────────────────────

# 1) Base image
FROM debian:stable-slim

ENV DEBIAN_FRONTEND=noninteractive

# 2) Add Debian 11 (bullseye) repo *only* for echoping-ng
RUN echo 'deb http://deb.debian.org/debian bullseye main' >> /etc/apt/sources.list

# 3) Core build tools (purged after build to keep image small)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential autoconf automake \
        libssl-dev libpcre3-dev libpopt-dev wget ca-certificates && \
    apt-get purge -y build-essential autoconf automake \
        libssl-dev libpcre3-dev libpopt-dev && \
    apt-get autoremove -y && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/*

# -------------------------------------------------------------
# Install dnsutils (gives us /usr/bin/dig for probing DNS)
# -------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends dnsutils && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# -------------------------------------------------------------
# Build & install echoping-ng (DNS latency probe for SmokePing)
# -------------------------------------------------------------
RUN set -eux; \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential cmake git libssl-dev libpcre3-dev \
        libpopt-dev libidn2-0-dev ca-certificates && \
    git clone --depth 1 https://github.com/RaymiiOrg/echoping-ng.git /tmp/echoping-ng && \
    mkdir /tmp/echoping-ng/build && \
    cd    /tmp/echoping-ng/build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release && \
    make -j"$(nproc)" && make install && \
    ln -s /usr/local/bin/echoping-ng /usr/local/bin/echoping && \
    chmod 755 /usr/local/bin/echoping-ng /usr/local/bin/echoping && \
    ln -sf /usr/local/bin/echoping /usr/bin/echoping && \
    strip /usr/local/bin/echoping-ng || true && \
    apt-get purge -y --auto-remove \
        build-essential cmake git libssl-dev libpcre3-dev \
        libpopt-dev libidn2-0-dev && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/echoping-ng

# -------------------------------------------------------------
# Install SmokePing + runtime deps (no services started in build)
# -------------------------------------------------------------
RUN set -eux; \
    # Stub out service starts inside the image
    echo '#!/bin/sh\nexit 0' > /usr/sbin/policy-rc.d && chmod +x /usr/sbin/policy-rc.d; \
    ln -s /bin/true /usr/bin/systemctl; \
    \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        rrdtool librrds-perl \
        smokeping fping spawn-fcgi \
        fcgiwrap lighttpd && \
    rm /usr/sbin/policy-rc.d /usr/bin/systemctl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# -------------------------------------------------------------
# Configure Lighttpd & FastCGI for SmokePing
# -------------------------------------------------------------
RUN lighty-enable-mod cgi && \
    sed -i 's|server.document-root.*|server.document-root = "/usr/share/smokeping/www"|' \
        /etc/lighttpd/lighttpd.conf && \
    cat >> /etc/lighttpd/lighttpd.conf <<'EOF'
# --- SmokePing URL mappings ---------------------------------
alias.url += (
  "/smokeping/images/" => "/var/cache/smokeping/images/",  # graph PNGs
  "/smokeping/"        => "/usr/share/smokeping/www/",     # css, icons
  "/cgi-bin/"          => "/usr/lib/cgi-bin/"              # CGI entry
)
EOF

# SmokePing graph cache must be writable by www-data
RUN mkdir -p /var/cache/smokeping/images && chown -R www-data:www-data /var/cache/smokeping

# -------------------------------------------------------------
# EXTRA: Install Python + libraries for the RRD → Influx exporter
# -------------------------------------------------------------
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 python3-pip python3-rrdtool curl && \
    PIP_BREAK_SYSTEM_PACKAGES=1 pip3 install --no-cache-dir influxdb-client && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# -------------------------------------------------------------
# Copy SmokePing configs and exporter script
# -------------------------------------------------------------
# Your Targets/Probes configs
COPY config/Targets /etc/smokeping/config.d/Targets
COPY config/Probes  /etc/smokeping/config.d/Probes

# Exporter script (reads RRDs → posts to InfluxDB)
COPY exporter/rrd2influx.py /usr/local/bin/
RUN chmod +x /usr/local/bin/rrd2influx.py

# -------------------------------------------------------------
# Runtime housekeeping
# -------------------------------------------------------------
EXPOSE 80

RUN mkdir -p /var/run/smokeping && \
    chown smokeping:smokeping /var/run/smokeping

# Entry-point starts the exporter in background, then SmokePing
COPY docker-entrypoint.sh /usr/local/bin/
# Normalise line-endings and strip BOM, then mark executable
RUN sed -i -e '1s/^\xEF\xBB\xBF//' -e 's/\r$//' \
      /usr/local/bin/docker-entrypoint.sh && \
    chmod +x /usr/local/bin/docker-entrypoint.sh

CMD ["/usr/local/bin/docker-entrypoint.sh"]