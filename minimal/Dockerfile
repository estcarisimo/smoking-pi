# ─────────────────────────────────────────────────────────────
#  SmokePing Dockerfile (with bundled custom configuration)
# ─────────────────────────────────────────────────────────────

# 1) Base image
FROM debian:stable-slim

ENV DEBIAN_FRONTEND=noninteractive

# 2) Add Bullseye repo *only* for echoping
RUN echo 'deb http://deb.debian.org/debian bullseye main' >> /etc/apt/sources.list

# 3) Install SmokePing and its dependencies
# ─────────────────────────────────────────────────────────────
# Install SmokePing, fping, spawn-fcgi, fcgiwrap, lighttpd
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        autoconf automake \
        libssl-dev libpcre3-dev libpopt-dev wget ca-certificates && \
    # trim build toolchain & caches
    apt-get purge -y build-essential autoconf automake \
        libssl-dev libpcre3-dev libpopt-dev && \
    apt-get autoremove -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/*

# ─────────────────────────────────────────────────────────────
# Install dnsutils
# puts /usr/bin/dig in the image
RUN apt-get update && apt-get install -y dnsutils  

# ─────────────────────────────────────────────────────────────
# Build & install echoping-ng (DNS latency probe for SmokePing)
# ─────────────────────────────────────────────────────────────
RUN set -eux; \
    # 1) install build dependencies
    apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential cmake git libssl-dev libpcre3-dev \
        libpopt-dev libidn2-0-dev ca-certificates && \
    \
    # 2) clone the fork and build
    git clone --depth 1 https://github.com/RaymiiOrg/echoping-ng.git /tmp/echoping-ng && \
    mkdir   /tmp/echoping-ng/build && \
    cd      /tmp/echoping-ng/build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release && \
    make -j"$(nproc)" && \
    make install && \
    ln -s /usr/local/bin/echoping-ng /usr/local/bin/echoping && \
    chmod 755 /usr/local/bin/echoping-ng /usr/local/bin/echoping && \
    ln -sf /usr/local/bin/echoping /usr/bin/echoping && \
    strip /usr/local/bin/echoping-ng || true && \
    \
    # 3) wipe build tool-chain & caches to slim the layer
    apt-get purge -y --auto-remove \
        build-essential cmake git libssl-dev libpcre3-dev \
        libpopt-dev libidn2-0-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/echoping-ng


# ─────────────────────────────────────────────────────────────
# Install SmokePing + runtime deps (no service starts in build)
# ─────────────────────────────────────────────────────────────
RUN set -eux; \
    # 1) stub out service starts
    echo '#!/bin/sh\nexit 0' > /usr/sbin/policy-rc.d && chmod +x /usr/sbin/policy-rc.d; \
    ln -s /bin/true /usr/bin/systemctl; \
    \
    # 2) install everything Smokeping needs
    apt-get update && \
    # rrdtool + Perl bindings required by the CGI
    apt-get install -y --no-install-recommends \
        rrdtool librrds-perl \
        smokeping fping spawn-fcgi \
        fcgiwrap lighttpd && \
    \
    # 3) clean up the stubs
    rm /usr/sbin/policy-rc.d /usr/bin/systemctl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# ─────────────────────────────────────────────────────────────
# Configure fcgiwrap + lighttpd for CGI
# ─────────────────────────────────────────────────────────────

# ───── Configure Lighttpd and make SmokePing cache writable ─────
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



# make the cache writable for the FastCGI process (www-data)
RUN mkdir -p /var/cache/smokeping/images && \
    chown -R www-data:www-data /var/cache/smokeping

        
# ── copy target file *after* user exists ───────────────────────
COPY config/Targets /etc/smokeping/config.d/Targets
COPY config/Probes /etc/smokeping/config.d/Probes

# 6. Expose HTTP
EXPOSE 80

# Make sure SmokePing's piddir exists at runtime
RUN mkdir -p /var/run/smokeping \
 && chown smokeping:smokeping /var/run/smokeping

# 7. Default command: start fcgiwrap + lighttpd, then SmokePing (foreground)
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

CMD ["/usr/local/bin/docker-entrypoint.sh"]