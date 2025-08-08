"""
API routes for AJAX operations
"""

from flask import Blueprint, jsonify, request, current_app, render_template_string
from pathlib import Path
import yaml
import subprocess
from datetime import datetime
import docker
import re
from app.services.config_api import ConfigAPIGateway

api_bp = Blueprint('api', __name__)

# Initialize config API gateway
config_api = ConfigAPIGateway()

@api_bp.route('/status')
def get_status():
    """Get system status via config API"""
    try:
        # Get status from config-manager API
        status = config_api.get_service_status()
        
        # Get targets data for total count
        targets_config = config_api.get_targets_config()
        total_targets = sum(
            len(v) for v in targets_config.get('active_targets', {}).values() 
            if isinstance(v, list)
        )
        last_updated = targets_config.get('metadata', {}).get('last_updated', 'Never')
        
        # Add web-admin specific information
        return jsonify({
            'smokeping_running': status.get('smokeping', {}).get('running', False),
            'total_targets': total_targets,
            'last_updated': last_updated,
            'config_manager_available': config_api.is_available(),
            'service_status': status.get('status', 'unknown')
        })
        
    except Exception as e:
        current_app.logger.error(f"Status check failed: {e}")
        return jsonify({
            'smokeping_running': False,
            'total_targets': 0,
            'last_updated': 'Error',
            'config_manager_available': False,
            'error': str(e)
        }), 500

@api_bp.route('/apply', methods=['POST'])
def apply_configuration():
    """Apply configuration changes to SmokePing via config API"""
    try:
        # Generate SmokePing configuration via config-manager API
        result = config_api.generate_config()
        
        if result.get('success'):
            # Also restart SmokePing service
            restart_result = config_api.restart_smokeping_service()
            
            return jsonify({
                'success': True,
                'message': f"Configuration applied successfully. {restart_result.get('message', '')}"
            })
        else:
            return jsonify({
                'success': False,
                'error': result.get('message', 'Configuration generation failed')
            }), 500
            
    except Exception as e:
        current_app.logger.error(f"Apply configuration failed: {e}")
        return jsonify({
            'success': False,
            'error': f'Configuration application failed: {str(e)}'
        }), 500

@api_bp.route('/bandwidth')
def get_bandwidth_estimate():
    """Get current bandwidth usage estimate via config API"""
    try:
        targets_config = config_api.get_targets_config()
        
        total_targets = sum(
            len(v) for v in targets_config.get('active_targets', {}).values() 
            if isinstance(v, list)
        )
        
        # Calculate bandwidth (10 pings every 300s with 64 bytes each)
        bandwidth_per_target = (10 * 64 * 8) / 300  # bits per second
        total_bandwidth_bps = total_targets * bandwidth_per_target
        
        return jsonify({
            'total_targets': total_targets,
            'bandwidth_bps': total_bandwidth_bps,
            'bandwidth_kbps': total_bandwidth_bps / 1000,
            'bandwidth_mbps': total_bandwidth_bps / 1_000_000
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@api_bp.route('/validate-hostname', methods=['POST'])
def validate_hostname():
    """Validate a hostname or IP address with IPv6 reachability info"""
    data = request.get_json()
    hostname = data.get('hostname', '').strip()
    
    if not hostname:
        return jsonify({'valid': False, 'error': 'Hostname is required'})
    
    # Import validation and IPv6 functions from targets module
    from app.routes.targets import (
        validate_hostname as do_validate,
        check_ipv6_global_reachability,
        check_ipv6_destination_global
    )
    
    valid, error = do_validate(hostname)
    
    # Add IPv6 reachability information
    ipv6_info = {
        'has_ipv6': False,
        'ipv6_global_reachable': False,
        'destination_ipv6_global': False,
        'recommended_probe': 'FPing'
    }
    
    if valid:
        try:
            import socket
            import ipaddress
            
            # Check if it's an IPv6 address or has IPv6 records
            try:
                ip = ipaddress.ip_address(hostname)
                if ip.version == 6:
                    ipv6_info['has_ipv6'] = True
                    ipv6_info['destination_ipv6_global'] = check_ipv6_destination_global(hostname)
            except ValueError:
                # It's a hostname, check for IPv6 records
                try:
                    socket.getaddrinfo(hostname, None, socket.AF_INET6)
                    ipv6_info['has_ipv6'] = True
                    ipv6_info['destination_ipv6_global'] = check_ipv6_destination_global(hostname)
                except:
                    pass
            
            # Check local IPv6 reachability
            ipv6_info['ipv6_global_reachable'] = check_ipv6_global_reachability()
            
            # Recommend probe based on capabilities
            if ipv6_info['has_ipv6'] and ipv6_info['ipv6_global_reachable'] and ipv6_info['destination_ipv6_global']:
                ipv6_info['recommended_probe'] = 'FPing6'
            else:
                ipv6_info['recommended_probe'] = 'FPing'
                
        except Exception as e:
            current_app.logger.warning(f"IPv6 info collection failed: {e}")
    
    return jsonify({
        'valid': valid,
        'error': error,
        'hostname': hostname,
        'ipv6_info': ipv6_info
    })

@api_bp.route('/smokeping/logs')
def get_smokeping_logs():
    """Get SmokePing container logs (last 200 lines)"""
    try:
        # Try Docker SDK first
        try:
            client = docker.from_env()
            container = client.containers.get('grafana-influx-smokeping-1')
            
            # Get last 200 lines of logs
            logs = container.logs(tail=200, timestamps=True).decode('utf-8')
            
            # Parse logs to highlight errors
            log_lines = []
            for line in logs.split('\n'):
                if line.strip():
                    level = 'info'
                    if any(keyword in line.upper() for keyword in ['ERROR', 'FATAL', 'CRITICAL']):
                        level = 'error'
                    elif any(keyword in line.upper() for keyword in ['WARNING', 'WARN']):
                        level = 'warning'
                    
                    log_lines.append({
                        'text': line,
                        'level': level
                    })
            
            # Get container status
            status = container.status
            exit_code = container.attrs.get('State', {}).get('ExitCode', 0)
            
            return jsonify({
                'success': True,
                'logs': log_lines[-200:],  # Ensure max 200 lines
                'status': status,
                'exit_code': exit_code,
                'container_id': container.short_id
            })
            
        except (docker.errors.NotFound, docker.errors.DockerException, FileNotFoundError) as docker_error:
            # Log the specific Docker error
            current_app.logger.warning(f"Docker SDK error: {type(docker_error).__name__}: {str(docker_error)}")
            # Fallback to docker CLI
            result = subprocess.run([
                'docker', 'logs', '--tail', '200', '-t', 'grafana-influx-smokeping-1'
            ], capture_output=True, text=True)
            
            if result.returncode != 0:
                return jsonify({
                    'success': False,
                    'error': 'SmokePing container not found or not accessible'
                }), 404
            
            # Parse CLI output
            log_lines = []
            for line in result.stdout.split('\n'):
                if line.strip():
                    level = 'info'
                    if any(keyword in line.upper() for keyword in ['ERROR', 'FATAL', 'CRITICAL']):
                        level = 'error'
                    elif any(keyword in line.upper() for keyword in ['WARNING', 'WARN']):
                        level = 'warning'
                    
                    log_lines.append({
                        'text': line,
                        'level': level
                    })
            
            # Get container status via CLI
            status_result = subprocess.run([
                'docker', 'inspect', '-f', '{{.State.Status}}', 'grafana-influx-smokeping-1'
            ], capture_output=True, text=True)
            
            status = status_result.stdout.strip() if status_result.returncode == 0 else 'unknown'
            
            return jsonify({
                'success': True,
                'logs': log_lines[-200:],
                'status': status,
                'exit_code': 0,
                'container_id': 'unknown'
            })
            
    except Exception as e:
        current_app.logger.error(f"Error fetching SmokePing logs: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Failed to fetch logs: {str(e)}'
        }), 500

@api_bp.route('/smokeping/restart', methods=['POST'])
def restart_smokeping():
    """Restart SmokePing container"""
    try:
        # Try Docker SDK first
        try:
            client = docker.from_env()
            container = client.containers.get('grafana-influx-smokeping-1')
            
            # Restart the container
            container.restart()
            
            return jsonify({
                'success': True,
                'message': 'SmokePing container restarted successfully'
            })
            
        except (docker.errors.NotFound, docker.errors.DockerException, FileNotFoundError) as docker_error:
            # Log the specific Docker error
            current_app.logger.warning(f"Docker SDK error on restart: {type(docker_error).__name__}: {str(docker_error)}")
            # Fallback to docker CLI
            result = subprocess.run([
                'docker', 'restart', 'grafana-influx-smokeping-1'
            ], capture_output=True, text=True)
            
            if result.returncode != 0:
                return jsonify({
                    'success': False,
                    'error': f'Failed to restart SmokePing: {result.stderr}'
                }), 500
            
            return jsonify({
                'success': True,
                'message': 'SmokePing container restarted successfully'
            })
            
    except Exception as e:
        current_app.logger.error(f"Error restarting SmokePing: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Failed to restart SmokePing: {str(e)}'
        }), 500

@api_bp.route('/ocas/refresh', methods=['POST'])
def refresh_ocas():
    """Refresh Netflix OCA servers via config API"""
    try:
        result = config_api.refresh_oca_data()
        
        if result.get('success'):
            return jsonify({
                'success': True,
                'message': result.get('message', 'OCAs refreshed successfully')
            })
        else:
            return jsonify({
                'success': False,
                'error': result.get('message', 'OCA refresh failed')
            }), 500
            
    except Exception as e:
        current_app.logger.error(f"Error refreshing OCAs: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Failed to refresh OCAs: {str(e)}'
        }), 500

@api_bp.route('/ocas/status')
def get_oca_status():
    """Get current OCA status and last refresh time"""
    config_dir = Path(current_app.config['CONFIG_DIR'])
    
    try:
        with open(config_dir / 'targets.yaml', 'r') as f:
            targets_data = yaml.safe_load(f)
        
        oca_targets = targets_data.get('active_targets', {}).get('netflix_oca', [])
        metadata = targets_data.get('metadata', {})
        
        return jsonify({
            'oca_count': len(oca_targets),
            'last_updated': metadata.get('last_updated', 'Never'),
            'enabled': True  # Can be extended to check sources.yaml
        })
    except Exception as e:
        return jsonify({
            'oca_count': 0,
            'last_updated': 'Never',
            'enabled': False,
            'error': str(e)
        })

@api_bp.route('/targets/<category>')
def get_category_targets(category):
    """Get HTML fragment for a specific target category"""
    try:
        # Get current targets via API
        targets_data = config_api.get_targets_config()
        active_targets = targets_data.get('active_targets', {})
        
        # Get targets for the specified category
        category_targets = active_targets.get(category, [])
        
        # Define the HTML template for the target list
        template = '''
        {% if targets %}
            <ul class="list-unstyled mb-0">
                {% for target in targets[:10] %}
                <li>
                    <i class="bi bi-dot"></i>
                    <strong>{{ target.name }}</strong> - {{ target.host }}
                    {% if target.probe != 'FPing' %}
                        <span class="badge bg-secondary">{{ target.probe }}</span>
                    {% endif %}
                </li>
                {% endfor %}
                {% if targets|length > 10 %}
                <li class="text-muted">
                    <i class="bi bi-three-dots"></i>
                    and {{ targets|length - 10 }} more...
                </li>
                {% endif %}
            </ul>
        {% else %}
            <p class="text-muted mb-0">No targets configured</p>
        {% endif %}
        '''
        
        # Render the template with the targets
        html = render_template_string(template, targets=category_targets)
        
        response = jsonify({
            'success': True,
            'html': html,
            'count': len(category_targets)
        })
        
        # Add cache-busting headers
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        
        return response
        
    except Exception as e:
        current_app.logger.error(f"Error getting category targets: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@api_bp.route('/targets/delete', methods=['DELETE'])
def delete_target():
    """Delete a target from the configuration"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'error': 'No data provided'
            }), 400
        
        category = data.get('category')
        target_name = data.get('name')
        target_host = data.get('host')
        
        if not all([category, target_name, target_host]):
            return jsonify({
                'success': False,
                'error': 'Category, name, and host are required'
            }), 400
        
        # Get current targets configuration
        targets_data = config_api.get_targets_config()
        if not targets_data:
            return jsonify({
                'success': False,
                'error': 'Failed to load targets configuration'
            }), 500
        
        active_targets = targets_data.get('active_targets', {})
        category_targets = active_targets.get(category, [])
        
        # Find and remove the target
        target_found = False
        updated_targets = []
        
        for target in category_targets:
            if target.get('name') == target_name and target.get('host') == target_host:
                target_found = True
                current_app.logger.info(f"Deleting target: {target_name} ({target_host}) from category {category}")
            else:
                updated_targets.append(target)
        
        if not target_found:
            return jsonify({
                'success': False,
                'error': f'Target "{target_name}" not found in category "{category}"'
            }), 404
        
        # Update the targets data
        targets_data['active_targets'][category] = updated_targets
        
        # Update metadata
        targets_data.setdefault('metadata', {})
        targets_data['metadata']['last_updated'] = datetime.now().isoformat()
        
        # Recalculate total targets
        total_targets = sum(
            len(v) for v in targets_data.get('active_targets', {}).values() 
            if isinstance(v, list)
        )
        targets_data['metadata']['total_targets'] = total_targets
        
        # Save the updated configuration via API
        result = config_api.update_targets_config(targets_data)
        
        if result:
            return jsonify({
                'success': True,
                'message': f'Target "{target_name}" deleted successfully from {category}'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to save configuration'
            }), 500
            
    except Exception as e:
        current_app.logger.error(f"Error deleting target: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Failed to delete target: {str(e)}'
        }), 500