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

BAKED_PLUGINS_DIR="/opt/grafana-plugins"
PLUGINS_DIR="${GF_PATHS_PLUGINS:-/var/lib/grafana/plugins}"
CH_DASHBOARD_DIR="/etc/grafana/provisioning/dashboards-clickhouse"
# Writable provisioning tree used in ClickHouse mode (see below).
ACTIVE_PROV="${GF_PATHS_DATA:-/var/lib/grafana}/provisioning-active"
skip_datasource_selection=0

case "$TSDB_TYPE" in
    clickhouse)
        datasource_file="clickhouse.yaml"

        # The ClickHouse plugin is baked into the image at build time, but
        # /var/lib/grafana is a volume that shadows it at runtime. Stage it in
        # on first boot instead of downloading — a Pi that starts before its
        # network is up would otherwise come up with no datasource plugin.
        if [ -d "$BAKED_PLUGINS_DIR" ] && [ ! -d "$PLUGINS_DIR/grafana-clickhouse-datasource" ]; then
            echo "grafana-entrypoint: staging baked ClickHouse plugin into $PLUGINS_DIR"
            mkdir -p "$PLUGINS_DIR"
            cp -r "$BAKED_PLUGINS_DIR"/. "$PLUGINS_DIR"/ \
                || echo "grafana-entrypoint: warning — could not stage plugins" >&2
        fi

        # ClickHouse mode needs a provisioning tree the influxdb one cannot
        # supply: the ClickHouse dashboards live outside the scanned providers
        # directory, and the InfluxDB provider must NOT load (its dashboards
        # query a datasource that is not configured here).
        #
        # The repo bind-mounts provisioning/ read-only, and Docker cannot even
        # create a mountpoint for a file inside a read-only bind mount, so
        # neither the entrypoint nor a compose overlay can add a provider in
        # place. Build a writable tree instead and point Grafana at it.
        echo "grafana-entrypoint: building ClickHouse provisioning tree in $ACTIVE_PROV"
        rm -rf "$ACTIVE_PROV"
        mkdir -p "$ACTIVE_PROV/datasources" "$ACTIVE_PROV/dashboards"

        # Only the ClickHouse datasource here, so it can safely be the default.
        # The committed file keeps isDefault: false because influxdb mode
        # provisions both files from a read-only mount, and Grafana refuses to
        # start if two datasources claim default. Flip it in our own copy.
        sed 's/^\( *\)isDefault: false/\1isDefault: true/' \
            "$AVAILABLE_DIR/clickhouse.yaml" > "$ACTIVE_PROV/datasources/clickhouse.yaml"

        # Copy any non-dashboard providers (alerting, plugins, ...) verbatim.
        for extra in /etc/grafana/provisioning/*/; do
            name="$(basename "$extra")"
            case "$name" in
                datasources|dashboards|dashboards-clickhouse) continue ;;
            esac
            cp -r "$extra" "$ACTIVE_PROV/$name"
        done

        if [ -f "$CH_DASHBOARD_DIR/dashboard.yaml" ]; then
            cp "$CH_DASHBOARD_DIR/dashboard.yaml" \
               "$ACTIVE_PROV/dashboards/clickhouse-dashboards.yaml"
            echo "grafana-entrypoint: ClickHouse dashboard provider activated"
        else
            echo "grafana-entrypoint: WARNING — no dashboard.yaml in $CH_DASHBOARD_DIR;" \
                 "ClickHouse dashboards will not load" >&2
        fi

        export GF_PATHS_PROVISIONING="$ACTIVE_PROV"
        # The datasource copy below is redundant in this mode.
        skip_datasource_selection=1
        ;;
    *)
        datasource_file="influxdb.yaml"
        ;;
esac

if [ "$skip_datasource_selection" = "1" ]; then
    echo "grafana-entrypoint: datasources provisioned from $ACTIVE_PROV"
elif [ -w "$ACTIVE_DIR" ]; then
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
