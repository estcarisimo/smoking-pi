"""
Dashboard route - Main overview page
"""

from flask import Blueprint, render_template, current_app
from pathlib import Path
import yaml
import subprocess
from app.services.config_api import ConfigAPIGateway

dashboard_bp = Blueprint('dashboard', __name__)

# Initialize config API gateway
config_api = ConfigAPIGateway()

def get_smokeping_status():
    """Check if SmokePing Docker container is running via config-manager API"""
    try:
        # First try to get status via config-manager API
        status_data = config_api.get_service_status()
        if status_data and 'smokeping' in status_data:
            return status_data['smokeping'].get('running', False)
    except Exception as e:
        current_app.logger.warning(f"Failed to get SmokePing status via API: {e}")
    
    # Fallback to direct Docker command check
    try:
        result = subprocess.run([
            'docker', 'ps', '--filter', 'name=grafana-influx-smokeping-1', '--format', '{{.Names}}'
        ], capture_output=True, text=True, timeout=10)
        
        # Check if the command succeeded and the container name is in output
        return result.returncode == 0 and 'grafana-influx-smokeping-1' in result.stdout
    except Exception as e:
        current_app.logger.error(f"Failed to check SmokePing status: {e}")
        return False

def calculate_bandwidth(targets_data):
    """Calculate estimated bandwidth usage"""
    total_targets = sum(
        len(v) for k, v in targets_data.get('active_targets', {}).items() 
        if isinstance(v, list)
    )
    
    # Assuming 10 pings every 300s with 64 bytes each
    bandwidth_per_target = (10 * 64 * 8) / 300  # bits per second
    total_bandwidth_mbps = (total_targets * bandwidth_per_target) / 1_000_000
    
    return {
        'total_targets': total_targets,
        'bandwidth_mbps': round(total_bandwidth_mbps, 3),
        'bandwidth_kbps': round(total_bandwidth_mbps * 1000, 1)
    }

@dashboard_bp.route('/')
def index():
    """Main dashboard view"""
    # Load current targets via API
    try:
        targets_data = config_api.get_targets_config()
        if not targets_data:
            targets_data = {'active_targets': {}, 'metadata': {}}
    except Exception as e:
        current_app.logger.error(f"Failed to get targets via API: {e}")
        targets_data = {'active_targets': {}, 'metadata': {}}
    
    # Get target counts by category
    target_counts = {}
    for category, targets in targets_data.get('active_targets', {}).items():
        if isinstance(targets, list):
            target_counts[category] = len(targets)
    
    # Calculate bandwidth
    bandwidth_info = calculate_bandwidth(targets_data)
    
    # Check SmokePing status
    smokeping_running = get_smokeping_status()
    
    context = {
        'smokeping_running': smokeping_running,
        'target_counts': target_counts,
        'total_targets': bandwidth_info['total_targets'],
        'bandwidth_mbps': bandwidth_info['bandwidth_mbps'],
        'bandwidth_kbps': bandwidth_info['bandwidth_kbps'],
        'last_updated': targets_data.get('metadata', {}).get('last_updated', 'Never'),
        'active_targets': targets_data.get('active_targets', {})
    }
    
    return render_template('dashboard.html', **context)