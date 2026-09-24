"""
Targets management routes
"""

from app.errors import error_response
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
import ipaddress
import re
import socket
from datetime import datetime
from app.services.config_api import ConfigAPIGateway

targets_bp = Blueprint('targets', __name__)

# Initialize config API gateway
config_api = ConfigAPIGateway()

# Target-name rule shared with the client-side validation in
# templates/targets/add.html and list.html (tests enforce parity):
# starts with a letter; letters, digits and underscores; max 30 chars.
NAME_PATTERN = r'^[a-zA-Z][a-zA-Z0-9_]{0,29}$'
NAME_RE = re.compile(NAME_PATTERN)


def validate_target_name(name):
    """Validate a SmokePing section name. Returns (valid, error)."""
    if not name:
        return False, "Name is required"
    if not NAME_RE.match(name):
        return False, (
            "Name must start with a letter and contain only letters, "
            "numbers and underscores (max 30 characters)"
        )
    return True, None


def validate_hostname(hostname):
    """Validate hostname or IP address (IPv4 and IPv6 aware)."""
    # Check if it's an IP address (v4 or v6)
    try:
        ipaddress.ip_address(hostname)
        return True, None
    except ValueError:
        pass

    # Check if it's a resolvable hostname. getaddrinfo covers A and AAAA
    # records, unlike gethostbyname which is IPv4-only.
    try:
        socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        return True, None
    except socket.gaierror:
        return False, f"Cannot resolve hostname: {hostname}"
    except Exception:
        current_app.logger.warning("Hostname check failed for %r", hostname,
                                   exc_info=True)
        return False, "Hostname could not be checked"

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
    probes = ['FPing', 'FPing6', 'DNS']
    try:
        # Check if database is available
        using_database = config_api.is_database_available()
        service_status = config_api.get_service_status()

        if using_database:
            # Use database mode - show ALL targets (every category, including
            # inactive ones so they stay visible and can be reactivated).
            try:
                db_result = config_api.get_all_targets_from_db()
                all_custom_targets = db_result.get('targets', [])
            except Exception as e:
                current_app.logger.error(f"Database error: {e}")
                all_custom_targets = []
            try:
                probes_result = config_api.get_probes_from_db()
                db_probes = [p['name'] for p in probes_result.get('probes', [])]
                if db_probes:
                    probes = db_probes
            except Exception as e:
                current_app.logger.warning(f"Failed to get probes: {e}")
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
        
    except Exception:
        current_app.logger.error("Failed to get targets", exc_info=True)
        all_custom_targets = []
        using_database = False
        service_status = {'status': 'error',
                          'error': 'config-manager unreachable; see web-admin log'}

    categories = sorted({
        t.get('category') for t in all_custom_targets if t.get('category')
    })

    return render_template('targets/list.html',
                         targets=all_custom_targets,
                         categories=categories,
                         probes=probes,
                         using_database=using_database,
                         service_status=service_status)

def _wants_json():
    """AJAX requests get JSON responses with per-field errors."""
    return (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or request.accept_mimetypes.best == 'application/json'
    )


# HTTP version chosen in the add form -> Curl sub-probe in the Probes file
# -> target-name suffix the exporters read the version back from (a target
# in the HTTP section without a suffix is tagged plain "http").
HTTP_VERSION_PROBES = {
    '1.1': ('CurlHTTP1', '_h1'),
    '2': ('CurlHTTP2', '_h2'),
    '3': ('CurlHTTP3', '_h3'),
}
TCP_PROBE = 'TCPPing'


def _add_target_error_response(errors, name, hostname, title, target_type,
                               dns_query, http_version=''):
    """Render the add-target failure response (JSON for AJAX, HTML otherwise)."""
    if _wants_json():
        return jsonify({'success': False, 'errors': errors}), 400
    for error in errors.values():
        flash(error, 'error')
    return render_template('targets/add.html',
                         name=name, hostname=hostname, title=title,
                         target_type=target_type, dns_query=dns_query,
                         http_version=http_version)


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
        http_version = request.form.get('http_version', '').strip()

        using_database = config_api.is_database_available()

        # Validate inputs -- errors keyed by field for inline display
        errors = {}
        valid, error = validate_target_name(name)
        if not valid:
            errors['name'] = error

        if target_type == 'http' and http_version not in HTTP_VERSION_PROBES:
            errors['http_version'] = "Choose an HTTP version (1.1, 2 or 3)"

        # For DNS targets, hostname is optional (will use default DNS if blank)
        if target_type == 'dns':
            if hostname:  # Only validate if provided
                valid, error = validate_hostname(hostname)
                if not valid:
                    errors['hostname'] = error
        else:
            # For ICMP targets, hostname is required
            if not hostname:
                errors['hostname'] = "Hostname/IP is required"
            else:
                valid, error = validate_hostname(hostname)
                if not valid:
                    errors['hostname'] = error

        # DNS-specific validation
        if target_type == 'dns':
            if not dns_query:
                errors['dns_query'] = "DNS query domain is required for DNS targets"
            elif not dns_query.replace('-', '').replace('.', '').isalnum():
                errors['dns_query'] = "DNS query domain contains invalid characters"

        if not title:
            title = name

        if errors:
            return _add_target_error_response(
                errors, name, hostname, title, target_type, dns_query,
                http_version)
        
        # Determine probe type and target category
        if target_type == 'dns':
            probe = 'DNS'
            target_category = 'dns_resolvers'
        elif target_type == 'http':
            probe, suffix = HTTP_VERSION_PROBES[http_version]
            target_category = 'http'
            # The suffix is what tells the dashboards which version a series
            # is; add it rather than let the target land as plain "http".
            if not name.endswith(suffix):
                name += suffix
        elif target_type == 'tcp':
            probe = TCP_PROBE
            target_category = 'tcp'
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
                    return _add_target_error_response(
                        {'_form': f"Category '{target_category}' not found in database"},
                        name, hostname, title, target_type, dns_query,
                    http_version)

                probe_id = probes.get(probe)
                if not probe_id:
                    return _add_target_error_response(
                        {'_form': f"Probe '{probe}' not found in database"},
                        name, hostname, title, target_type, dns_query,
                    http_version)

                # Check for duplicates
                existing_targets = config_api.get_all_targets_from_db()
                if any(t['name'] == name for t in existing_targets.get('targets', [])):
                    return _add_target_error_response(
                        {'name': f"Target with name '{name}' already exists"},
                        name, hostname, title, target_type, dns_query,
                    http_version)
                
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
                
                # Create target in database (config-manager regenerates the
                # SmokePing config automatically in database mode)
                result = config_api.create_target_in_db(target_data)
                warning = reload_warning(result)
                success_message = warning or (
                    f"Target '{name}' added. SmokePing picked it up; its "
                    f"first measurement arrives within one step (usually "
                    f"5 minutes)."
                )
                if _wants_json():
                    return jsonify({
                        'success': True,
                        'reloaded': warning is None,
                        'message': success_message,
                        'redirect': url_for('targets.list_targets'),
                    })
                flash(success_message, 'warning' if warning else 'success')
                return redirect(url_for('targets.list_targets'))

            except Exception:
                current_app.logger.error("Database error creating target",
                                         exc_info=True)
                return _add_target_error_response(
                    {'_form': "Failed to create target; see web-admin log"},
                    name, hostname, title, target_type, dns_query,
                    http_version)
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
                return _add_target_error_response(
                    {'name': f"Target with name '{name}' already exists"},
                    name, hostname, title, target_type, dns_query,
                    http_version)
        
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
                    return _add_target_error_response(
                        {'_form': "Failed to save configuration"},
                        name, hostname, title, target_type, dns_query,
                    http_version)
            except Exception as e:
                current_app.logger.error(f"Failed to update targets via API: {e}")
                return _add_target_error_response(
                    {'_form': "Failed to save configuration"},
                    name, hostname, title, target_type, dns_query,
                    http_version)

            # Regenerate the SmokePing config so the new target is picked up
            # without a separate Apply step
            # The gateway never raises; it reports. The message used to say
            # "config regenerated automatically" whatever it reported.
            generated = config_api.generate_config()
            if not generated.get('success'):
                warning = (f"Target '{name}' saved, but the SmokePing "
                           f"configuration was not regenerated; use Apply.")
            else:
                warning = reload_warning(generated)
            success_message = warning or (
                f"Target '{name}' added. SmokePing picked it up; its first "
                f"measurement arrives within one step (usually 5 minutes)."
            )
            if _wants_json():
                return jsonify({
                    'success': True,
                    'reloaded': warning is None,
                    'message': success_message,
                    'redirect': url_for('targets.list_targets'),
                })
            flash(success_message, 'warning' if warning else 'success')
            return redirect(url_for('targets.list_targets'))
    
    # Get available categories and probes for form
    categories = ['custom', 'dns_resolvers']
    probes = ['FPing', 'FPing6', 'DNS']
    
    # A suggestion from the dashboard's "Your connection" card arrives as
    # query parameters; the form shows them and validates them on submit
    # like anything typed. Only the form's own fields are taken.
    prefill = {key: request.args.get(key, '').strip()
               for key in ('name', 'hostname', 'title', 'target_type', 'dns_query')
               if request.args.get(key)}
    return render_template('targets/add.html',
                         categories=categories,
                         probes=probes,
                         using_database=config_api.is_database_available(),
                         **prefill)

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
        flash("Error deleting target; see web-admin log", 'error')
        return error_response(500, 'Error deleting target', e, success=False)


def reload_warning(result):
    """The sentence to add when config-manager saved a change but SmokePing
    did not confirm the reload; None otherwise.

    Only an explicit ``reloaded: false`` counts: an older config-manager
    does not send the field, and silence is not a failure.
    """
    if isinstance(result, dict) and result.get('reloaded') is False:
        return ("Saved, but SmokePing did not confirm the reload: "
                "restart SmokePing to start measuring the change.")
    return None


@targets_bp.route('/<int:target_id>/toggle', methods=['POST'])
def toggle_target(target_id):
    """Toggle a target's active state (database mode only)."""
    if not config_api.is_database_available():
        return jsonify({
            'success': False,
            'error': 'Toggling targets requires database mode',
        }), 400

    try:
        result = config_api.toggle_target_in_db(target_id)
        target = result.get('target', {})
        warning = reload_warning(result)
        return jsonify({
            'success': True,
            'is_active': target.get('is_active'),
            'reloaded': warning is None,
            'message': warning or result.get('message', 'Target toggled'),
        })
    except ValueError:
        return jsonify({'success': False, 'error': 'Target not found'}), 404
    except Exception as e:
        return error_response(500, f'Error toggling target {target_id}', e,
                              success=False)


@targets_bp.route('/<int:target_id>', methods=['PUT'])
def edit_target(target_id):
    """Edit a target's title/host/probe (database mode only)."""
    if not config_api.is_database_available():
        return jsonify({
            'success': False,
            'error': 'Editing targets requires database mode',
        }), 400

    data = request.get_json(silent=True) or {}
    update = {}

    title = (data.get('title') or '').strip()
    if title:
        update['title'] = title

    host = (data.get('host') or '').strip()
    if host:
        valid, error = validate_hostname(host)
        if not valid:
            return jsonify({
                'success': False,
                'errors': {'host': error},
            }), 400
        update['host'] = host

    probe = (data.get('probe') or '').strip()
    if probe:
        try:
            probes_result = config_api.get_probes_from_db()
            probe_ids = {
                p['name']: p['id'] for p in probes_result.get('probes', [])
            }
        except Exception as e:
            return error_response(500, 'Failed to get probes', e, success=False)
        if probe not in probe_ids:
            return jsonify({
                'success': False,
                'errors': {'probe': f"Unknown probe '{probe}'"},
            }), 400
        update['probe_id'] = probe_ids[probe]

    if not update:
        return jsonify({'success': False, 'error': 'Nothing to update'}), 400

    try:
        result = config_api.update_target_in_db(target_id, update)
        warning = reload_warning(result)
        return jsonify({'success': True, 'reloaded': warning is None,
                        'message': warning or 'Target updated'})
    except ValueError:
        return jsonify({'success': False, 'error': 'Target not found'}), 404
    except Exception as e:
        return error_response(500, f'Error updating target {target_id}', e,
                              success=False)


@targets_bp.route('/bulk-delete', methods=['POST'])
def bulk_delete_targets():
    """Delete multiple targets by id (database mode only)."""
    if not config_api.is_database_available():
        return jsonify({
            'success': False,
            'error': 'Bulk delete requires database mode',
        }), 400

    data = request.get_json(silent=True) or {}
    ids = data.get('ids')
    if not isinstance(ids, list) or not ids or not all(
        isinstance(i, int) for i in ids
    ):
        return jsonify({
            'success': False,
            'error': 'A non-empty list of integer target ids is required',
        }), 400

    deleted, failed = [], []
    for target_id in ids:
        try:
            config_api.delete_target_from_db(target_id)
            deleted.append(target_id)
        except Exception as e:
            current_app.logger.error(f"Bulk delete failed for {target_id}: {e}")
            failed.append(target_id)

    status = 200 if not failed else 207
    return jsonify({
        'success': not failed,
        'deleted': deleted,
        'failed': failed,
        'message': f"Deleted {len(deleted)} target(s)"
                   + (f", {len(failed)} failed" if failed else ""),
    }), status

