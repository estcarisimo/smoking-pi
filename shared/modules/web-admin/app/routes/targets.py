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
    """Target management interface - database-aware"""
    try:
        # Check if database is available
        using_database = config_api.is_database_available()
        service_status = config_api.get_service_status()
        
        if using_database:
            # Use database mode - get all targets
            try:
                db_result = config_api.get_all_targets_from_db()
                all_targets = db_result.get('targets', [])
                # Separate by category for display
                custom_targets = [t for t in all_targets if t.get('category') == 'custom']
                dns_targets = [t for t in all_targets if t.get('category') == 'dns_resolvers']
                all_custom_targets = custom_targets + dns_targets
            except Exception as e:
                current_app.logger.error(f"Database error: {e}")
                all_custom_targets = []
        else:
            # Use YAML fallback
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
        current_app.logger.error(f"Failed to get targets: {e}")
        all_custom_targets = []
        using_database = False
        service_status = {'status': 'error', 'error': str(e)}
    
    return render_template('targets/list.html', 
                         targets=all_custom_targets,
                         using_database=using_database,
                         service_status=service_status)

@targets_bp.route('/add', methods=['GET', 'POST'])
def add_target():
    """Add a new custom target - database-aware"""
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        hostname = request.form.get('hostname', '').strip()
        title = request.form.get('title', '').strip()
        target_type = request.form.get('target_type', 'icmp').strip()
        dns_query = request.form.get('dns_query', '').strip()
        force_probe = request.form.get('force_probe', '').strip()
        
        using_database = config_api.is_database_available()
        
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
        
        # Handle database vs YAML mode
        if using_database:
            try:
                # Get categories and probes from database
                categories_result = config_api.get_categories_from_db()
                probes_result = config_api.get_probes_from_db()
                
                categories = {cat['name']: cat['id'] for cat in categories_result.get('categories', [])}
                probes = {probe['name']: probe['id'] for probe in probes_result.get('probes', [])}
                
                # Get category and probe IDs
                category_id = categories.get(target_category)
                if not category_id:
                    flash(f"Category '{target_category}' not found in database", 'error')
                    return render_template('targets/add.html', name=name, hostname=hostname, title=title,
                                         target_type=target_type, dns_query=dns_query)
                
                probe_id = probes.get(probe)
                if not probe_id:
                    flash(f"Probe '{probe}' not found in database", 'error')
                    return render_template('targets/add.html', name=name, hostname=hostname, title=title,
                                         target_type=target_type, dns_query=dns_query)
                
                # Check for duplicates
                existing_targets = config_api.get_all_targets_from_db()
                if any(t['name'] == name for t in existing_targets.get('targets', [])):
                    flash(f"Target with name '{name}' already exists", 'error')
                    return render_template('targets/add.html', name=name, hostname=hostname, title=title,
                                         target_type=target_type, dns_query=dns_query)
                
                # Create target data for database
                target_data = {
                    'name': name,
                    'host': hostname if hostname else '8.8.8.8',
                    'title': title,
                    'category_id': category_id,
                    'probe_id': probe_id,
                    'is_active': True
                }
                
                if target_type == 'dns':
                    target_data['lookup'] = dns_query
                
                # Create target in database
                result = config_api.create_target_in_db(target_data)
                flash(f"Successfully added target '{name}'", 'success')
                return redirect(url_for('targets.list_targets'))
                
            except Exception as e:
                current_app.logger.error(f"Database error creating target: {e}")
                flash(f"Failed to create target: {str(e)}", 'error')
                return render_template('targets/add.html', name=name, hostname=hostname, title=title,
                                     target_type=target_type, dns_query=dns_query)
        else:
            # YAML fallback mode
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
        
            # Create new target for YAML
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
    
    # Get available categories and probes for form
    categories = ['custom', 'dns_resolvers']
    probes = ['FPing', 'FPing6', 'DNS']
    
    return render_template('targets/add.html', 
                         categories=categories, 
                         probes=probes,
                         using_database=config_api.is_database_available())

@targets_bp.route('/delete/<name>', methods=['POST'])
def delete_target(name):
    """Delete a custom target - database-aware"""
    try:
        using_database = config_api.is_database_available()
        
        if using_database:
            # Database mode - need to find target by name and delete by ID
            all_targets = config_api.get_all_targets_from_db()
            target_to_delete = next((t for t in all_targets.get('targets', []) if t['name'] == name), None)
            
            if not target_to_delete:
                flash(f"Target '{name}' not found", 'error')
                return jsonify({'success': False, 'error': 'Target not found'}), 404
            
            result = config_api.delete_target_from_db(target_to_delete['id'])
            flash(f"Successfully deleted target '{name}'", 'success')
            return jsonify({'success': True, 'message': f"Target '{name}' deleted successfully"})
        else:
            # YAML mode - original logic
            targets_data = config_api.get_targets_config()
            if not targets_data:
                flash("Failed to load configuration", 'error')
                return redirect(url_for('targets.list_targets'))
        
            # Search in both custom and dns_resolvers categories
            target_found = False
            for category in ['custom', 'dns_resolvers']:
                category_targets = targets_data.get('active_targets', {}).get(category, [])
                filtered_targets = [t for t in category_targets if t['name'] != name]
                
                if len(filtered_targets) != len(category_targets):
                    targets_data['active_targets'][category] = filtered_targets
                    target_found = True
                    break
            
            if not target_found:
                flash(f"Target '{name}' not found", 'error')
                return jsonify({'success': False, 'error': 'Target not found'}), 404
            
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
                config_api.generate_config()
            except Exception as config_error:
                current_app.logger.error(f"Failed to update configuration after delete: {config_error}")
            
            flash(f"Successfully deleted target '{name}'", 'success')
            return jsonify({'success': True, 'message': f"Target '{name}' deleted successfully"})
    
    except Exception as e:
        current_app.logger.error(f"Error deleting target: {str(e)}")
        flash(f"Error deleting target: {str(e)}", 'error')
        return jsonify({'success': False, 'error': str(e)}), 500

# New API endpoints for database management
@targets_bp.route('/api/targets')
def api_get_targets():
    """Get all targets"""
    try:
        active_only = request.args.get('active_only', 'false').lower() == 'true'
        category = request.args.get('category')
        
        if config_api.is_database_available():
            # Use database
            result = config_api.get_all_targets_from_db(
                active_only=active_only, 
                category=category
            )
            result['source'] = 'database'
        else:
            # Use YAML fallback
            targets_config = config_api.get_targets_config()
            
            # Convert YAML structure to match database format
            targets = []
            for cat_name, cat_targets in targets_config.get('active_targets', {}).items():
                if category and cat_name != category:
                    continue
                    
                for target in cat_targets:
                    if isinstance(target, dict):
                        targets.append({
                            'id': None,  # No ID in YAML mode
                            'name': target.get('name'),
                            'host': target.get('host'),
                            'title': target.get('title'),
                            'category': cat_name,
                            'probe': target.get('probe', 'FPing'),
                            'is_active': True,  # All YAML targets are active
                            'metadata': target.get('metadata')
                        })
            
            result = {
                'targets': targets,
                'total': len(targets),
                'source': 'yaml'
            }
        
        return jsonify(result)
        
    except Exception as e:
        current_app.logger.error(f"Failed to get targets: {e}")
        return jsonify({'error': str(e)}), 500

@targets_bp.route('/api/targets/<int:target_id>/toggle', methods=['POST'])
def api_toggle_target(target_id):
    """Toggle target active status (database only)"""
    if not config_api.is_database_available():
        return jsonify({'error': 'Database not available'}), 400
    
    try:
        result = config_api.toggle_target_in_db(target_id)
        return jsonify(result)
        
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        current_app.logger.error(f"Failed to toggle target {target_id}: {e}")
        return jsonify({'error': str(e)}), 500

@targets_bp.route('/api/status')
def api_get_status():
    """Get target management status"""
    try:
        status = config_api.get_service_status()
        return jsonify(status)
        
    except Exception as e:
        current_app.logger.error(f"Failed to get status: {e}")
        return jsonify({'error': str(e)}), 500