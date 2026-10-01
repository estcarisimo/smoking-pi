"""
Dashboard route - Main overview page
"""

import time

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


# The SmokePing section cpe_discovery.py writes its targets into.
CPE_SECTION = 'CPE'


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
        # Measured but in no target list: the ISP's first hop(s).
        'automatic': sum(
            1 for t in body.get('targets', []) if t.get('section') == CPE_SECTION
        ),
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
        'public_resolver': summarize_public_resolver(body.get('public_resolver') or {}),
    }


# The observer's states (dns-observer/health.py) as the card colours them.
DNS_OBSERVER_BADGES = {
    'observing': 'success', 'quiet': 'info', 'partial': 'info',
    'upstream_fallback': 'warning', 'upstream_failing': 'warning',
    'idle': 'secondary', 'starting': 'secondary', 'stopped': 'secondary',
    'not_receiving': 'warning', 'server_down': 'danger', 'down': 'danger',
}


def summarize_dns_observer(body):
    """What the dashboard's DNS observer card needs from /dns/observer.

    Not shown where the edition has no observer; an invitation while it is
    off; its state, reason and fix once it runs.
    """
    if not body.get('available'):
        return {'show': False}
    if not body.get('enabled'):
        return {'show': True, 'enabled': False}
    state = body.get('state') or 'down'
    until = body.get('observed_until')
    canary = body.get('canary') or {}
    return {
        'show': True,
        'enabled': True,
        'state': state,
        'label': state.replace('_', ' '),
        'badge': DNS_OBSERVER_BADGES.get(state, 'secondary'),
        'live': bool(body.get('live')),
        'reason': body.get('reason') or '',
        'fix': body.get('fix') or '',
        'observed_age': humanize_age(int(time.time() - until)) if until else None,
        'canary': canary if canary.get('enabled') else None,
        'coverage': body.get('coverage') or '',
    }


def summarize_public_resolver(paths):
    """The card's DNS line: who answers through the router and, when it
    runs, through the DNS observer. Owner names come from the ASN registry
    ('GOOGLE - Google LLC, US'); the part before ' - ' is shown."""
    out = []
    for path, label in (('router', 'through your router'), ('observer', 'through the DNS observer')):
        entry = paths.get(path) or {}
        if not entry.get('ok'):
            continue
        owners = []
        for o in entry.get('owners') or []:
            name = str(o.get('name') or '')
            short = name.split(' - ', 1)[-1].split(',')[0].strip() if ' - ' in name else name
            owners.append(f"{short or 'unknown'} (AS{o.get('asn')})")
        out.append({
            'path': path,
            'label': label,
            'owners': ' and '.join(owners) or 'an unknown network',
            'ecs': entry.get('ecs') or '',
            'egress': entry.get('egress') or [],
        })
    return out


# Share of a ceiling at which the card turns yellow; over 100% it is red.
BUDGET_WARN_PCT = 75


def summarize_budget(body):
    """What the dashboard's budget card needs from /budget (budget.py).

    The configured cost, not metered traffic: samples per hour and an
    approximate MB/day per probe against the two ceilings, most expensive
    first, so what to cut is at the top.
    """
    if not body.get('available'):
        return {'available': False, 'reason': body.get('reason', 'unknown')}
    ceiling = body.get('ceiling') or {}
    used = body.get('used') or {}
    mb_ceiling = ceiling.get('mb_per_day') or 0
    samples_ceiling = ceiling.get('samples_per_hour') or 0
    mb = body.get('mb_per_day') or 0
    samples = body.get('samples_per_hour') or 0

    def gauge(pct):
        pct = pct or 0
        badge = ('danger' if pct > 100 else
                 'warning' if pct >= BUDGET_WARN_PCT else 'success')
        return {'pct': pct, 'width': min(pct, 100), 'badge': badge}

    return {
        'available': True,
        'complete': body.get('complete', True),
        'over': bool(body.get('over')),
        'targets': body.get('targets', 0),
        'mb_per_day': mb,
        # The same daily volume as an average rate, for the top-row card.
        'kbps': round(mb * 8e6 / 86400 / 1000, 1),
        'mb_ceiling': mb_ceiling,
        'mb_headroom': round(max(mb_ceiling - mb, 0), 1),
        'samples_per_hour': samples,
        'samples_ceiling': samples_ceiling,
        'samples_headroom': round(max(samples_ceiling - samples, 0)),
        'bandwidth': gauge(used.get('bandwidth_pct')),
        'samples': gauge(used.get('samples_pct')),
        'unpriced': body.get('unpriced') or [],
        'by_probe': body.get('by_probe') or [],
        'measured': summarize_measured(body.get('measured'), mb_ceiling),
    }


def summarize_measured(measured, mb_ceiling):
    """The uplink meter beside the estimate (budget.py measured()): what
    the Pi's uplink really carried, whole-Pi, over the last 24 h. None
    before the meter's first interval, and on Basic/Standard."""
    if not isinstance(measured, dict) or measured.get('mb_per_day') is None:
        return None
    mb = measured.get('mb_per_day') or 0
    pct = measured.get('pct_of_ceiling')
    provisional = bool(measured.get('provisional'))
    if provisional:
        badge = 'secondary'  # minutes of data: shown, not judged
    else:
        badge = ('danger' if (pct or 0) > 100 else
                 'warning' if (pct or 0) >= BUDGET_WARN_PCT else 'info')
    return {
        'interface': measured.get('interface') or 'the uplink',
        'mb_per_day': mb,
        'kbps': measured.get('kbps'),
        'rx_mb': measured.get('rx_mb') or 0,
        'tx_mb': measured.get('tx_mb') or 0,
        'hours': measured.get('hours'),
        # What it rests on, as a person says it: a new meter has minutes.
        # Kept in step with config-manager's budget.covered() (containers
        # cannot import across each other).
        'covered': (f"{measured.get('hours')} h" if (measured.get('hours') or 0) >= 1
                    else f"{measured.get('minutes') or 0} min"),
        'stale': bool(measured.get('stale')),
        'pct': pct,
        'provisional': provisional,
        'width': min(pct or 0, 100),
        'badge': badge,
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
        'dns_observer': summarize_dns_observer(config_api.get_dns_observer()),
        'budget': summarize_budget(config_api.get_budget()),
        'using_database': using_database,
        'smokeping_running': smokeping_running,
        'target_counts': target_counts,
        'total_targets': sum(target_counts.values()),
        'last_updated': targets_data.get('metadata', {}).get('last_updated', 'Never'),
        'active_targets': targets_data.get('active_targets', {})
    }
    
    return render_template('dashboard.html', **context)