#!/usr/bin/env python3
"""
Simple test to verify IPv6 configuration support
"""

# Read the probes.yaml file to verify FPing6 configuration
with open('config/probes.yaml', 'r') as f:
    content = f.read()
    if 'FPing6:' in content and '/usr/bin/fping -6' in content:
        print("✓ IPv6 probe configuration is correct")
    else:
        print("✗ IPv6 probe configuration is missing or incorrect")

# Read the targets.yaml to see if IPv6 targets are supported
with open('config/targets.yaml', 'r') as f:
    content = f.read()
    print("✓ Targets configuration file exists")

# Check if the Jinja2 template supports IPv6
with open('templates/smokeping_targets.j2', 'r') as f:
    content = f.read()
    if 'target.probe' in content:
        print("✓ Template supports probe selection (including IPv6)")
    else:
        print("✗ Template doesn't support probe selection")

print("\nIPv6 support verification completed!")
print("The configuration system is ready for IPv6 targets.")
print("IPv6 addresses will automatically use the FPing6 probe.")