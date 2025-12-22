#!/bin/bash
# Grafana custom entrypoint script
# Ensures admin password is properly set from environment variable

set -e

echo "Starting Grafana with custom initialization..."

# Function to configure datasources based on TSDB_TYPE
configure_datasources() {
    local tsdb_type="${TSDB_TYPE:-influxdb}"
    echo "Configuring datasources for TSDB_TYPE: $tsdb_type"
    echo "DEBUG: Full TSDB_TYPE environment: '$TSDB_TYPE'"
    
    # Determine which datasource configuration to use
    local datasource_file=""
    case "$tsdb_type" in
        "clickhouse")
            datasource_file="clickhouse.yaml"
            echo "Using ClickHouse datasource configuration"
            # Install ClickHouse plugin if needed
            if [ -n "${GF_INSTALL_PLUGINS}" ]; then
                export GF_INSTALL_PLUGINS="${GF_INSTALL_PLUGINS},grafana-clickhouse-datasource"
            else
                export GF_INSTALL_PLUGINS="grafana-clickhouse-datasource"
            fi
            ;;
        "influxdb"|*)
            datasource_file="influxdb.yaml"
            echo "Using InfluxDB datasource configuration"
            ;;
    esac
    
    # Remove non-selected datasource configurations and configure the selected one
    if [ -f "/etc/grafana/provisioning/datasources/$datasource_file" ]; then
        # Create a writable datasource directory
        mkdir -p /tmp/grafana-datasources
        
        # Copy the selected configuration to the writable location
        cp "/etc/grafana/provisioning/datasources/$datasource_file" "/tmp/grafana-datasources/datasource.yaml"
        echo "Using datasource configuration: $datasource_file"
        
        # Override the provisioning path to use our writable directory
        export GF_PATHS_PROVISIONING="/tmp/grafana-provisioning"
        mkdir -p /tmp/grafana-provisioning/datasources
        mkdir -p /tmp/grafana-provisioning/dashboards
        
        # Copy dashboards from read-only location based on TSDB_TYPE
        if [ "$tsdb_type" = "clickhouse" ]; then
            echo "ClickHouse mode: Loading ClickHouse-compatible dashboards"
            
            # Copy ClickHouse dashboards from read-only location
            if [ -d "/etc/grafana/provisioning/dashboards-clickhouse" ]; then
                cp -r /etc/grafana/provisioning/dashboards-clickhouse/* /tmp/grafana-provisioning/dashboards/ 2>/dev/null || true
                echo "Loaded ClickHouse dashboards from /etc/grafana/provisioning/dashboards-clickhouse"
            else
                echo "Warning: ClickHouse dashboards directory not found, creating minimal setup"
                # Create a custom dashboard provisioning configuration for ClickHouse mode
                cat > /tmp/grafana-provisioning/dashboards/dashboard.yaml << 'EOF'
apiVersion: 1

providers:
  - name: ClickHouse Info
    folder: ClickHouse Info
    type: file
    editable: true
    options:
      path: /tmp/grafana-provisioning/dashboards
      foldersFromFilesStructure: false
EOF
                
                # Create a minimal info dashboard for ClickHouse mode
                cat > /tmp/grafana-provisioning/dashboards/clickhouse-info.json << 'EOF'
{
  "uid": "clickhouse-info",
  "title": "ClickHouse Mode Information",
  "tags": ["info"],
  "style": "dark",
  "timezone": "browser",
  "editable": true,
  "panels": [
    {
      "type": "text",
      "title": "ClickHouse Mode Active",
      "gridPos": { "h": 10, "w": 24, "x": 0, "y": 0 },
      "options": {
        "content": "## SmokePing is running in ClickHouse mode\\n\\nClickHouse dashboards are available but were not found in the expected location.\\n\\n### Current Status:\\n- ✅ ClickHouse datasource is configured\\n- ✅ SmokePing is running and collecting data\\n- ⚠️ ClickHouse dashboards not loaded (check /etc/grafana/provisioning/dashboards-clickhouse)\\n\\n### ClickHouse Connection:\\nYou can verify the connection in **Configuration → Data Sources → ClickHouse**",
        "mode": "markdown"
      }
    }
  ],
  "schemaVersion": 37,
  "version": 1
}
EOF
            fi
            echo "ClickHouse dashboard setup completed"
        else
            echo "InfluxDB mode: Loading all dashboards"
            cp -r /etc/grafana/provisioning/dashboards/* /tmp/grafana-provisioning/dashboards/ 2>/dev/null || true
        fi
        
        # Use only the selected datasource configuration
        cp "/etc/grafana/provisioning/datasources/$datasource_file" "/tmp/grafana-provisioning/datasources/datasource.yaml"
        
        echo "Grafana will use provisioning from: /tmp/grafana-provisioning"
    else
        echo "Warning: Datasource configuration file not found: $datasource_file"
    fi
}

echo "Starting Grafana with custom initialization..."

# Configure datasources before starting Grafana
configure_datasources

# Function to wait for Grafana to be ready
wait_for_grafana() {
    echo "Waiting for Grafana to start..."
    for i in {1..30}; do
        if curl -s http://localhost:3000/api/health >/dev/null 2>&1; then
            echo "Grafana is ready!"
            return 0
        fi
        sleep 1
    done
    echo "Timeout waiting for Grafana to start"
    return 1
}

# Function to reset admin password
reset_admin_password() {
    if [ -n "$GF_SECURITY_ADMIN_PASSWORD" ]; then
        echo "Setting admin password from environment variable..."
        
        # Use Grafana CLI to reset the admin password
        # Run as grafana user if we're root, otherwise run directly
        if [ "$(id -u)" = "0" ]; then
            su-exec grafana grafana-cli admin reset-admin-password "$GF_SECURITY_ADMIN_PASSWORD" 2>/dev/null || {
                echo "Note: Password reset command returned non-zero (this is normal if password is already set correctly)"
            }
        else
            grafana-cli admin reset-admin-password "$GF_SECURITY_ADMIN_PASSWORD" 2>/dev/null || {
                echo "Note: Password reset command returned non-zero (this is normal if password is already set correctly)"
            }
        fi
        
        echo "Admin password configuration complete"
    else
        echo "GF_SECURITY_ADMIN_PASSWORD not set, skipping password configuration"
    fi
}

# Start Grafana in background
echo "Starting Grafana server in background..."
/run.sh &
GRAFANA_PID=$!

# Wait for Grafana to be ready
if wait_for_grafana; then
    # Reset admin password after Grafana is running
    reset_admin_password
else
    echo "Warning: Could not verify Grafana startup"
fi

# Bring Grafana back to foreground
echo "Grafana initialization complete, continuing with normal operation..."
wait $GRAFANA_PID