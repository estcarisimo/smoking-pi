FROM grafana/grafana-oss:10.4.2

# Switch to root to set permissions
USER root

# Copy custom entrypoint script and set permissions
COPY docker-entrypoint.sh /custom-entrypoint.sh
RUN chmod +x /custom-entrypoint.sh

# Optional: pre-provision datasources or dashboards
COPY provisioning/ /etc/grafana/provisioning/

# Switch back to grafana user
USER grafana

EXPOSE 3000

# Use custom entrypoint
ENTRYPOINT ["/custom-entrypoint.sh"]