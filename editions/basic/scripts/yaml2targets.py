#!/usr/bin/env python3
"""
YAML to SmokePing Targets Converter

Converts YAML configuration to native SmokePing Targets format.
This script runs automatically when the SmokePing Basic edition container starts.

Author: SmokePing Team
Version: 1.0.0
"""

import sys
import yaml
from pathlib import Path
from datetime import datetime


def load_yaml_config(yaml_path: Path) -> dict:
    """
    Load and parse YAML configuration file.
    
    Args:
        yaml_path: Path to the YAML configuration file
        
    Returns:
        Parsed YAML configuration as dictionary
        
    Raises:
        SystemExit: If YAML file cannot be loaded or parsed
    """
    try:
        with open(yaml_path, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Error: YAML configuration file not found: {yaml_path}")
        print("Please ensure targets.yaml exists in the config directory.")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error: Invalid YAML syntax in {yaml_path}")
        print(f"YAML Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: Failed to load YAML configuration: {e}")
        sys.exit(1)


def generate_smokeping_config(config: dict) -> str:
    """
    Generate SmokePing Targets configuration from YAML.
    
    Args:
        config: Parsed YAML configuration dictionary
        
    Returns:
        SmokePing Targets configuration as string
    """
    lines = []
    
    # Header
    lines.append("*** Targets ***")
    lines.append("")
    lines.append("probe = FPing")
    lines.append("")
    lines.append("menu = Top")
    lines.append("title = Using a Raspberry Pi and SmokePing to Monitor Networks")
    lines.append("remark = Latency to a few select sites and services in the Internet.")
    lines.append("")
    
    # Process each target group
    targets = config.get('targets', {})
    for group_name, group_config in targets.items():
        # Group header
        lines.append(f"+ {group_name}")
        lines.append(f"menu = {group_config.get('title', group_name.replace('_', ' ').title())}")
        lines.append(f"title = {group_config.get('title', group_name.replace('_', ' ').title())}")
        
        # Check if group has a specific probe
        group_probe = group_config.get('probe')
        if group_probe and group_probe != 'FPing':
            lines.append(f"probe = {group_probe}")
        
        lines.append("")
        
        # Process hosts in group
        hosts = group_config.get('hosts', [])
        for host in hosts:
            name = host.get('name', 'UnknownHost')
            host_addr = host.get('host', 'localhost')
            title = host.get('title', name)
            host_probe = host.get('probe', group_probe or 'FPing')
            lookup = host.get('lookup')
            
            lines.append(f"++ {name}")
            lines.append(f"menu = {title}")
            lines.append(f"title = {title}")
            if lookup:
                lines.append(f"lookup = {lookup}")
            lines.append(f"host = {host_addr}")
            if host_probe != 'FPing' and host_probe != group_probe:
                lines.append(f"probe = {host_probe}")
            lines.append("")
    
    return "\n".join(lines)


def backup_existing_config(targets_path: Path) -> None:
    """
    Create backup of existing Targets file if it exists.
    
    Args:
        targets_path: Path to the Targets configuration file
    """
    if targets_path.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = targets_path.with_suffix(f".backup_{timestamp}")
        
        try:
            targets_path.rename(backup_path)
            print(f"Backed up existing Targets file to: {backup_path.name}")
        except Exception as e:
            print(f"Warning: Could not backup existing Targets file: {e}")


def validate_yaml_structure(config: dict) -> bool:
    """
    Validate the structure of the YAML configuration.
    
    Args:
        config: Parsed YAML configuration dictionary
        
    Returns:
        True if valid, False otherwise
    """
    if not isinstance(config, dict):
        print("Error: YAML root must be a dictionary")
        return False
    
    if 'targets' not in config:
        print("Error: YAML must contain 'targets' section")
        return False
    
    targets = config['targets']
    if not isinstance(targets, dict):
        print("Error: 'targets' section must be a dictionary")
        return False
    
    if not targets:
        print("Warning: No target groups defined in YAML")
        return True
    
    for group_name, group_config in targets.items():
        if not isinstance(group_config, dict):
            print(f"Error: Target group '{group_name}' must be a dictionary")
            return False
        
        if 'hosts' not in group_config:
            print(f"Warning: Target group '{group_name}' has no hosts")
            continue
            
        hosts = group_config['hosts']
        if not isinstance(hosts, list):
            print(f"Error: Hosts in group '{group_name}' must be a list")
            return False
        
        for i, host in enumerate(hosts):
            if not isinstance(host, dict):
                print(f"Error: Host {i+1} in group '{group_name}' must be a dictionary")
                return False
            
            if 'name' not in host:
                print(f"Error: Host {i+1} in group '{group_name}' missing 'name' field")
                return False
            
            if 'host' not in host:
                print(f"Error: Host {i+1} in group '{group_name}' missing 'host' field")
                return False
    
    return True


def main() -> None:
    """Main conversion function."""
    # Define paths (mounted as /config/targets in docker-compose)
    config_dir = Path("/config/targets")
    yaml_path = config_dir / "targets.yaml"
    targets_path = Path("/config") / "Targets"
    
    print("SmokePing YAML to Targets Converter")
    print("===================================")
    print(f"Reading YAML config: {yaml_path}")
    
    # Load YAML configuration
    config = load_yaml_config(yaml_path)
    
    # Validate YAML structure
    if not validate_yaml_structure(config):
        sys.exit(1)
    
    # Generate SmokePing configuration
    smokeping_config = generate_smokeping_config(config)
    
    # Backup existing configuration
    backup_existing_config(targets_path)
    
    # Write new configuration
    try:
        with open(targets_path, 'w') as f:
            f.write(smokeping_config)
        print(f"Successfully generated: {targets_path}")
        
        # Show summary
        targets = config.get('targets', {})
        total_hosts = sum(len(group.get('hosts', [])) for group in targets.values())
        print(f"Configuration summary:")
        print(f"  - Target groups: {len(targets)}")
        print(f"  - Total hosts: {total_hosts}")
        
    except Exception as e:
        print(f"Error: Failed to write Targets file: {e}")
        sys.exit(1)
    
    print("Conversion completed successfully!")


if __name__ == "__main__":
    main()