#!/bin/bash

# SmokePing Basic Edition - Custom Entrypoint
# Converts YAML configuration to SmokePing format before starting

set -e

echo "SmokePing Basic Edition Starting..."
echo "=================================="

# Check if targets.yaml exists
if [ -f "/config/targets/targets.yaml" ]; then
    echo "Found YAML configuration file: /config/targets/targets.yaml"
    
    # Install Python3 and PyYAML if not available
    if ! command -v python3 &> /dev/null; then
        echo "Installing Python3 and PyYAML..."
        apk update --quiet
        apk add --quiet python3 py3-pip
        pip3 install --quiet PyYAML --break-system-packages
    elif ! python3 -c "import yaml" 2>/dev/null; then
        echo "Installing PyYAML..."
        apk add --quiet py3-pip
        pip3 install --quiet PyYAML --break-system-packages
    fi
    
    # Run the YAML to Targets converter
    echo "Converting YAML to SmokePing Targets format..."
    python3 /scripts/yaml2targets.py
    
    if [ $? -eq 0 ]; then
        echo "YAML conversion completed successfully!"
    else
        echo "ERROR: YAML conversion failed!"
        echo "Please check your targets.yaml file for errors."
        exit 1
    fi
else
    echo "No YAML configuration found. Using existing Targets file if available."
fi

echo ""
echo "Starting SmokePing..."

# Start the original LinuxServer entrypoint
exec /init "$@"