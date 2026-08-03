#!/bin/bash
# Grafana custom entrypoint.
#
# Responsibilities (all before hand-off):
#   1. Select the datasource provisioning file matching TSDB_TYPE, when possible.
#   2. Optionally reset the admin password on an existing database.
#   3. exec the stock /run.sh so Grafana is PID-adjacent to Docker's signal
#      handling — `docker stop` delivers SIGTERM straight to Grafana instead of
#      timing out and SIGKILLing a backgrounded process.
#
# Datasource selection mechanism
# ------------------------------
# The image keeps a pristine copy of all datasource YAMLs in
# /etc/grafana/datasources-available/. If /etc/grafana/provisioning/datasources
# is writable we clear it and copy in only the file for the selected TSDB.
#
# In editions/pro, docker-compose.yml bind-mounts the repo's
# provisioning/datasources directory READ-ONLY over that path, so the copy is
# impossible. In that case ALL datasource files present in the mount are
# provisioned (currently influxdb.yaml + clickhouse.yaml). This is deliberate
# and documented: influxdb.yaml carries isDefault: true, clickhouse.yaml
# isDefault: false, so there is no default-datasource conflict. In influxdb
# mode the ClickHouse datasource simply shows as "plugin not found" in the UI
# (its plugin is not installed); it does not affect the InfluxDB dashboards.

set -euo pipefail

echo "grafana-entrypoint: starting (TSDB_TYPE=${TSDB_TYPE:-influxdb})"

TSDB_TYPE="${TSDB_TYPE:-influxdb}"
AVAILABLE_DIR="/etc/grafana/datasources-available"
ACTIVE_DIR="/etc/grafana/provisioning/datasources"

case "$TSDB_TYPE" in
    clickhouse)
        # ClickHouse support is currently parked. Keep the plugin install so an
        # explicit clickhouse deployment still gets a working datasource, but
        # note that dashboard provisioning for ClickHouse is not wired up here:
        # provisioning/dashboards-clickhouse is mounted OUTSIDE the scanned
        # provisioning/dashboards directory and is therefore inactive.
        datasource_file="clickhouse.yaml"
        export GF_INSTALL_PLUGINS="${GF_INSTALL_PLUGINS:+${GF_INSTALL_PLUGINS},}grafana-clickhouse-datasource"
        echo "grafana-entrypoint: clickhouse mode (parked) — plugin will be installed by run.sh"
        ;;
    *)
        datasource_file="influxdb.yaml"
        ;;
esac

if [ -w "$ACTIVE_DIR" ]; then
    echo "grafana-entrypoint: activating $datasource_file in $ACTIVE_DIR"
    rm -f "$ACTIVE_DIR"/*.yaml "$ACTIVE_DIR"/*.yml 2>/dev/null || true
    cp "$AVAILABLE_DIR/$datasource_file" "$ACTIVE_DIR/$datasource_file"
else
    echo "grafana-entrypoint: $ACTIVE_DIR is read-only (bind mount) — all datasource files in it will be provisioned; isDefault is resolved in the YAML files themselves"
fi

# Re-apply the admin password on an existing database. On first boot Grafana
# itself honours GF_SECURITY_ADMIN_PASSWORD, so this only matters when the
# password in .env changed after the grafana.db volume was created.
# grafana-cli operates directly on the database, so it must run BEFORE the
# server starts.
# Grafana 11 deprecated the standalone `grafana-cli` binary and 13 removed it;
# the CLI now lives behind `grafana cli`. Prefer the subcommand and fall back
# to the old binary so this script also works on older base images. Losing
# this step silently would pin the admin password to whatever the database
# already held, so a failure here is an error, not a passing remark.
reset_admin_password() {
    if command -v grafana >/dev/null 2>&1; then
        grafana cli --homepath /usr/share/grafana \
            admin reset-admin-password "$1" >/dev/null 2>&1 && return 0
    fi
    if command -v grafana-cli >/dev/null 2>&1; then
        grafana-cli --homepath /usr/share/grafana \
            admin reset-admin-password "$1" >/dev/null 2>&1 && return 0
    fi
    return 1
}

if [ -n "${GF_SECURITY_ADMIN_PASSWORD:-}" ] && [ -f "${GF_PATHS_DATA:-/var/lib/grafana}/grafana.db" ]; then
    echo "grafana-entrypoint: syncing admin password on existing database"
    if ! reset_admin_password "$GF_SECURITY_ADMIN_PASSWORD"; then
        echo "grafana-entrypoint: ERROR — admin password reset failed; the" \
             "password in the database is unchanged and GF_SECURITY_ADMIN_PASSWORD" \
             "will NOT take effect (continuing)" >&2
    fi
fi

echo "grafana-entrypoint: handing off to /run.sh"
exec /run.sh
