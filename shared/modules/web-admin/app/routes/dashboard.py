"""
Dashboard route - Main overview page
"""

from flask import Blueprint, redirect, render_template, request, current_app, url_for
from app.services.config_api import ConfigAPIGateway

dashboard_bp = Blueprint('dashboard', __name__)

# Initialize config API gateway
config_api = ConfigAPIGateway()

def get_smokeping_status():
    """Check if SmokePing is running via the config-manager API"""
    try:
        status_data = config_api.get_service_status()
        if status_data and 'smokeping' in status_data:
            return status_data['smokeping'].get('running', False)
    except Exception as e:
        current_app.logger.warning(f"Failed to get SmokePing status via API: {e}")
    return False

def humanize_age(seconds):
    """'4 min', '3 h', '2 d' -- for the age of a target's last measurement."""
    if seconds is None:
        return 'never'
    if seconds < 90:
        return f'{seconds} s'
    if seconds < 90 * 60:
        return f'{round(seconds / 60)} min'
    if seconds < 36 * 3600:
        return f'{round(seconds / 3600)} h'
    return f'{round(seconds / 86400)} d'


def summarize_measurements(body):
    """What the dashboard's Measurements card needs from /measurements.

    "SmokePing: Running" above it means the container is up. This is the
    other half: whether each target's RRD is still being written.
    """
    if not body.get('available'):
        return {'available': False, 'reason': body.get('reason', 'unknown')}
    counts = body.get('counts', {})
    problems = [
        {**t, 'age': humanize_age(t.get('age_seconds'))}
        for t in body.get('targets', [])
        if t.get('state') != 'fresh'
    ]
    return {
        'available': True,
        'measuring': body.get('measuring', False),
        'total': body.get('total', 0),
        'fresh': counts.get('fresh', 0),
        'stale': counts.get('stale', 0),
        'missing': counts.get('missing', 0),
        'pending': counts.get('pending', 0),
        'problems': problems,
    }


def summarize_connection(body):
    """What the dashboard's "Your connection" card needs from /recommendations.

    Each suggestion becomes a link to the add form, pre-filled: accepting
    one goes through the same form and validation as any other target.
    """
    if not body.get('available'):
        return {'available': False, 'reason': body.get('reason', 'unknown')}
    items = []
    for item in body.get('items', []):
        suggest = item.get('suggest')
        items.append({
            **item,
            'add_url': url_for('targets.add_target', **suggest) if suggest else None,
        })
    uplink = body.get('uplink') or {}
    return {
        'available': True,
        'interface': uplink.get('interface'),
        'wireless': bool(uplink.get('wireless')),
        'entries': items,
        'suggested': body.get('suggested', 0),
    }


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
    # A new install's first login goes to the welcome tour, once. ?tour=off
    # is the way out when recording the outcome failed.
    if request.args.get('tour') != 'off' and config_api.tour_pending():
        return redirect(url_for('welcome.index'))
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

    # Data-source mode (database vs YAML fallback)
    try:
        service_status = config_api.get_service_status()
        using_database = bool(service_status.get('using_database', False))
    except Exception as e:
        current_app.logger.warning(f"Failed to get service status: {e}")
        using_database = False

    context = {
        'measurements': summarize_measurements(config_api.get_measurements()),
        'connection': summarize_connection(config_api.get_recommendations()),
        'using_database': using_database,
        'smokeping_running': smokeping_running,
        'target_counts': target_counts,
        'total_targets': bandwidth_info['total_targets'],
        'bandwidth_mbps': bandwidth_info['bandwidth_mbps'],
        'bandwidth_kbps': bandwidth_info['bandwidth_kbps'],
        'last_updated': targets_data.get('metadata', {}).get('last_updated', 'Never'),
        'active_targets': targets_data.get('active_targets', {})
    }
    
    return render_template('dashboard.html', **context)