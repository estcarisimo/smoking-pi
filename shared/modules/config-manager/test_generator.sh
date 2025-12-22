#!/bin/bash
# Test script for configuration generator

set -e

echo "Testing SmokePing Configuration Generator"
echo "========================================"

# Change to config-manager directory
cd "$(dirname "$0")"

# Install dependencies if needed
if ! python3 -c "import yaml, jinja2" 2>/dev/null; then
    echo "Installing Python dependencies..."
    pip3 install -r requirements.txt
fi

# Run the configuration generator
echo "Generating configuration files..."
python3 scripts/config_generator.py --verbose

# Check if output files were created
if [ -f output/Targets ] && [ -f output/Probes ]; then
    echo "✓ Configuration files generated successfully"
    echo ""
    echo "Generated files:"
    ls -la output/
    echo ""
    echo "Targets file preview:"
    head -20 output/Targets
else
    echo "✗ Failed to generate configuration files"
    exit 1
fi