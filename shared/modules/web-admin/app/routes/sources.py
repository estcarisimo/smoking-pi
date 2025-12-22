"""
Sources management routes - Top site picker
"""

from flask import Blueprint, render_template, request, jsonify, current_app
from pathlib import Path
import yaml
from datetime import datetime
from app.services.tranco import TrancoService
from app.services.crux import CruxService
from app.services.cloudflare import CloudflareService
from app.services.config_api import ConfigAPIGateway

sources_bp = Blueprint('sources', __name__)

# Initialize services
tranco_service = TrancoService()
crux_service = CruxService()
cloudflare_service = CloudflareService()
config_api = ConfigAPIGateway()

@sources_bp.route('/')
def index():
    """Top sites picker interface"""
    try:
        # Load sources configuration via config API
        sources_config = config_api.get_sources_config()
        dynamic_config = sources_config.get('dynamic', {})
        
        # Load current active targets via config API
        targets_config = config_api.get_targets_config()
        active_websites = [t['host'] for t in targets_config.get('active_targets', {}).get('top_sites', [])]
        
    except Exception as e:
        current_app.logger.error(f"Failed to load configuration: {e}")
        dynamic_config = {}
        active_websites = []
    
    context = {
        'tranco_config': dynamic_config.get('tranco', {}),
        'crux_config': dynamic_config.get('crux', {}),
        'cloudflare_config': dynamic_config.get('cloudflare_radar', {}),
        'active_websites': active_websites,
        'max_targets': 100  # Hard limit as per requirements
    }
    
    return render_template('sources/index.html', **context)

@sources_bp.route('/api/fetch/<source>')
def fetch_source(source):
    """Fetch top sites from a specific source"""
    country = request.args.get('country', 'global')
    requested_limit = int(request.args.get('limit', 100))
    offset = int(request.args.get('offset', 0))
    api_token = request.args.get('token')  # For Cloudflare
    
    # Apply service-specific limits
    if source == 'tranco':
        # Allow large limits for Tranco (up to 10M)
        limit = min(requested_limit, 10000000)
    elif source == 'cloudflare':
        # Cloudflare API max is 100
        limit = min(requested_limit, 100)
    else:
        # Keep reasonable limits for other services
        limit = min(requested_limit, 1000)
    
    try:
        if source == 'tranco':
            sites = tranco_service.get_top_sites(limit, country, offset)
        elif source == 'crux':
            sites = crux_service.get_top_sites(limit, country, offset)
        elif source == 'cloudflare':
            if not api_token:
                return jsonify({'error': 'API token required for Cloudflare Radar'}), 400
            sites = cloudflare_service.get_top_sites(limit, country, offset, api_token)
        else:
            return jsonify({'error': 'Unknown source'}), 400
        
        # Check if service returned empty list (indicates failure)
        if not sites and offset == 0:
            # Only treat as error if it's the first page
            return jsonify({
                'error': f'Failed to fetch data from {source.title()}. Please try again later.'
            }), 503  # Service Unavailable
        
        return jsonify({
            'source': source,
            'country': country,
            'sites': sites,
            'count': len(sites)
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@sources_bp.route('/api/update', methods=['POST'])
def update_targets():
    """Update active targets with selected sites (database-aware)"""
    data = request.get_json()
    
    if not data or 'sites' not in data:
        return jsonify({'error': 'No sites provided'}), 400
    
    selected_sites = data['sites']
    if len(selected_sites) > 100:
        return jsonify({'error': 'Maximum 100 sites allowed'}), 400
    
    try:
        # Use the database-aware method that automatically handles fallback
        result = config_api.update_targets_from_sites(selected_sites, 'top_sites')
        
        if result['success']:
            return jsonify(result)
        else:
            return jsonify(result), 500
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500