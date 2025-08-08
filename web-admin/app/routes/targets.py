"""
Targets management routes
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from pathlib import Path
import yaml
import ipaddress
import socket
import subprocess
from datetime import datetime
from app.services.config_api import ConfigAPIGateway

targets_bp = Blueprint('targets', __name__)

# Initialize config API gateway
config_api = ConfigAPIGateway()

def validate_hostname(hostname):
    """Validate hostname or IP address"""
    # Check if it's an IP address
    try:
        ipaddress.ip_address(hostname)
        return True, None
    except ValueError:
        pass
    
    # Check if it's a valid hostname
    try:
        socket.gethostbyname(hostname)
        return True, None
    except socket.gaierror:
        return False, f"Cannot resolve hostname: {hostname}"
    except Exception as e:
        return False, str(e)

def check_ipv6_global_reachability():
    """Check if local host has IPv6 global connectivity"""
    try:
        # Test connectivity to known IPv6 servers
        test_servers = [
            ('2001:4860:4860::8888', 53),  # Google DNS
            ('2606:4700:4700::1111', 53),  # Cloudflare DNS
        ]
        
        for server, port in test_servers:
            try:
                sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
                sock.settimeout(3)
                sock.connect((server, port))
                sock.close()
                return True  # Successfully connected via IPv6
            except:
                continue
        
        return False  # No IPv6 connectivity
    except:
        return False

def check_ipv6_destination_global(hostname):
    """Check if destination has global IPv6 addresses"""
    try:
        # Parse as IP directly
        ip = ipaddress.ip_address(hostname)
        if ip.version == 6:
            return not (ip.is_link_local or ip.is_loopback or ip.is_private or ip.is_multicast)
    except ValueError:
        # It's a hostname, resolve all IPv6 addresses
        try:
            addr_info = socket.getaddrinfo(hostname, None, socket.AF_INET6)
            for info in addr_info:
                ip = ipaddress.ip_address(info[4][0])
                if not (ip.is_link_local or ip.is_loopback or ip.is_private or ip.is_multicast):
                    return True  # Has at least one global IPv6 address
        except:
            pass
    
    return False

def detect_ip_version(hostname, force_probe=None):
    """Detect if hostname resolves to IPv4 or IPv6 with optional force override"""
    # If user forces a specific probe, respect that choice
    if force_probe in ['FPing', 'FPing6']:
        return force_probe
    
    try:
        # Try to parse as IP directly
        ip = ipaddress.ip_address(hostname)
        if ip.version == 6:
            # Check IPv6 global reachability before recommending FPing6
            if check_ipv6_global_reachability() and check_ipv6_destination_global(hostname):
                return 'FPing6'
            else:
                return 'FPing'  # Fallback to IPv4 if IPv6 not globally reachable
        else:
            return 'FPing'
    except ValueError:
        # It's a hostname, check both IPv4 and IPv6 capabilities
        has_ipv4 = False
        has_ipv6 = False
        
        try:
            socket.getaddrinfo(hostname, None, socket.AF_INET)
            has_ipv4 = True
        except:
            pass
            
        try:
            socket.getaddrinfo(hostname, None, socket.AF_INET6)
            has_ipv6 = True
        except:
            pass
        
        # Prefer IPv6 if available and globally reachable
        if has_ipv6 and check_ipv6_global_reachability() and check_ipv6_destination_global(hostname):
            return 'FPing6'
        elif has_ipv4:
            return 'FPing'
        elif has_ipv6:
            return 'FPing6'  # Use IPv6 even if not globally reachable (user choice)
        else:
            return 'FPing'  # Default to IPv4

@targets_bp.route('/')
def list_targets():
    """List all custom targets (ICMP and DNS)"""
    try:
        targets_data = config_api.get_targets_config()
        
        # Get custom ICMP targets
        custom_targets = targets_data.get('active_targets', {}).get('custom', [])
        
        # Get DNS targets added via the custom target form
        dns_targets = targets_data.get('active_targets', {}).get('dns_resolvers', [])
        # Only include DNS targets that have a 'category' field (added via form)
        custom_dns_targets = [t for t in dns_targets if t.get('category') == 'dns_resolvers']
        
        # Combine both types
        all_custom_targets = custom_targets + custom_dns_targets
        
    except Exception as e:
        current_app.logger.error(f"Failed to get targets via API: {e}")
        all_custom_targets = []
    
    return render_template('targets/list.html', targets=all_custom_targets)

@targets_bp.route('/add', methods=['GET', 'POST'])
def add_target():
    """Add a new custom target"""
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        hostname = request.form.get('hostname', '').strip()
        title = request.form.get('title', '').strip()
        target_type = request.form.get('target_type', 'icmp').strip()
        dns_query = request.form.get('dns_query', '').strip()
        force_probe = request.form.get('force_probe', '').strip()
        
        # Validate inputs
        errors = []
        if not name:
            errors.append("Name is required")
        elif not name.replace('_', '').isalnum():
            errors.append("Name must be alphanumeric (underscores allowed)")
        
        # For DNS targets, hostname is optional (will use default DNS if blank)
        if target_type == 'dns':
            if hostname:  # Only validate if provided
                valid, error = validate_hostname(hostname)
                if not valid:
                    errors.append(error)
        else:
            # For ICMP targets, hostname is required
            if not hostname:
                errors.append("Hostname/IP is required")
            else:
                valid, error = validate_hostname(hostname)
                if not valid:
                    errors.append(error)
        
        # DNS-specific validation
        if target_type == 'dns':
            if not dns_query:
                errors.append("DNS query domain is required for DNS targets")
            elif not dns_query.replace('-', '').replace('.', '').isalnum():
                errors.append("DNS query domain contains invalid characters")
        
        if not title:
            title = name
        
        if errors:
            for error in errors:
                flash(error, 'error')
            return render_template('targets/add.html', 
                                 name=name, hostname=hostname, title=title, 
                                 target_type=target_type, dns_query=dns_query)
        
        # Determine probe type and target category
        if target_type == 'dns':
            probe = 'DNS'
            target_category = 'dns_resolvers'
        else:
            # Pass force_probe parameter if provided
            force_probe_param = force_probe if force_probe and force_probe != 'auto' else None
            probe = detect_ip_version(hostname, force_probe_param)
            target_category = 'custom'
        
        # Load current configuration via API
        try:
            targets_data = config_api.get_targets_config()
            if not targets_data:
                targets_data = {'active_targets': {target_category: []}, 'metadata': {}}
        except Exception as e:
            current_app.logger.error(f"Failed to get targets via API: {e}")
            targets_data = {'active_targets': {target_category: []}, 'metadata': {}}
        
        # Check for duplicates across all categories
        all_targets = []
        for category_targets in targets_data.get('active_targets', {}).values():
            if isinstance(category_targets, list):
                all_targets.extend(category_targets)
        
        if any(t['name'] == name for t in all_targets):
            flash(f"Target with name '{name}' already exists", 'error')
            return render_template('targets/add.html', 
                                 name=name, hostname=hostname, title=title,
                                 target_type=target_type, dns_query=dns_query)
        
        # Create new target
        new_target = {
            'name': name,
            'host': hostname if hostname else '8.8.8.8',  # Use Google DNS as default if blank
            'title': title,
            'probe': probe,
            'category': target_category
        }
        
        # Add DNS-specific fields for DNS targets
        if target_type == 'dns':
            new_target['lookup'] = dns_query
        
        # Ensure target category exists
        if target_category not in targets_data['active_targets']:
            targets_data['active_targets'][target_category] = []
        
        targets_data['active_targets'][target_category].append(new_target)
        
        # Update metadata
        if 'metadata' not in targets_data:
            targets_data['metadata'] = {}
        targets_data['metadata']['last_updated'] = datetime.now().isoformat()
        
        # Save configuration via API
        try:
            success = config_api.update_targets_config(targets_data)
            if not success:
                flash("Failed to save configuration", 'error')
                return render_template('targets/add.html', 
                                     name=name, hostname=hostname, title=title,
                                     target_type=target_type, dns_query=dns_query)
        except Exception as e:
            current_app.logger.error(f"Failed to update targets via API: {e}")
            flash("Failed to save configuration", 'error')
            return render_template('targets/add.html', 
                                 name=name, hostname=hostname, title=title,
                                 target_type=target_type, dns_query=dns_query)
        
        flash(f"Successfully added target '{name}'", 'success')
        return redirect(url_for('targets.list_targets'))
    
    return render_template('targets/add.html')

@targets_bp.route('/delete/<name>', methods=['POST'])
def delete_target(name):
    """Delete a custom target"""
    try:
        # Load current configuration via API
        targets_data = config_api.get_targets_config()
        if not targets_data:
            flash("Failed to load configuration", 'error')
            return redirect(url_for('targets.list_targets'))
        
        custom_targets = targets_data.get('active_targets', {}).get('custom', [])
        
        # Remove target
        targets_data['active_targets']['custom'] = [
            t for t in custom_targets if t['name'] != name
        ]
        
        # Update metadata
        if 'metadata' not in targets_data:
            targets_data['metadata'] = {}
        targets_data['metadata']['last_updated'] = datetime.now().isoformat()
        
        # Save configuration via API
        success = config_api.update_targets_config(targets_data)
        if not success:
            flash("Failed to save configuration", 'error')
            return redirect(url_for('targets.list_targets'))
        
        # Trigger configuration update (regenerate SmokePing config)
        try:
            # Use the same approach as in api.py for applying configuration
            result = subprocess.run([
                'docker', 'exec', 'grafana-influx-config-manager-1',
                'python3', '/app/scripts/config_generator.py',
                '--deploy-to', 'all'
            ], capture_output=True, text=True)
            
            if result.returncode != 0:
                current_app.logger.warning(f"Config generation after delete failed: {result.stderr}")
        except Exception as config_error:
            current_app.logger.error(f"Failed to update configuration after delete: {config_error}")
        
        flash(f"Successfully deleted target '{name}'", 'success')
        return jsonify({'success': True, 'message': f"Target '{name}' deleted successfully"})
    
    except Exception as e:
        current_app.logger.error(f"Error deleting target: {str(e)}")
        flash(f"Error deleting target: {str(e)}", 'error')
        return jsonify({'success': False, 'error': str(e)}), 500