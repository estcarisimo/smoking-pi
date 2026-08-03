FROM grafana/grafana-oss:12.4.3

# Switch to root to install the entrypoint and provisioning files
USER root

COPY docker-entrypoint.sh /custom-entrypoint.sh
RUN chmod +x /custom-entrypoint.sh

# Bake the ClickHouse datasource plugin at BUILD time. Installing it at boot
# (the old GF_INSTALL_PLUGINS route) needs internet on every start and fails
# closed on a Pi that comes up before its network does.
RUN grafana cli --pluginsDir /var/lib/grafana/plugins \
        plugins install grafana-clickhouse-datasource \
    && mkdir -p /opt/grafana-plugins \
    && cp -r /var/lib/grafana/plugins/. /opt/grafana-plugins/ \
    && chown -R grafana:root /opt/grafana-plugins

# Provisioning files baked into the image. NOTE: editions/pro/docker-compose.yml
# bind-mounts provisioning/dashboards, provisioning/dashboards-clickhouse and
# provisioning/datasources read-only over these paths, so at runtime the repo
# copies win. The datasources-available/ staging dir below is only used when
# provisioning/datasources is writable (i.e. no read-only bind mount).
COPY provisioning/ /etc/grafana/provisioning/
RUN cp -r /etc/grafana/provisioning/datasources /etc/grafana/datasources-available \
    && chown -R grafana:root /etc/grafana/provisioning /etc/grafana/datasources-available

USER grafana

EXPOSE 3000

# The Alpine-based grafana-oss image ships curl; fall back to busybox wget just
# in case a base image change drops it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -sf http://localhost:3000/api/health || wget -q --spider http://localhost:3000/api/health || exit 1

ENTRYPOINT ["/custom-entrypoint.sh"]
