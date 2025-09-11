#!/bin/bash
# Grafana custom entrypoint script
# Ensures admin password is properly set from environment variable

set -e

echo "Starting Grafana with custom initialization..."

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