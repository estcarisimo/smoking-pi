"""
Countries list routes
"""

from flask import Blueprint, render_template, jsonify
from app.services.crux import CruxService
from app.services.cloudflare import CloudflareService

countries_bp = Blueprint('countries', __name__)

# Country name mappings for display
COUNTRY_NAMES = {
    'ar': 'Argentina',
    'au': 'Australia', 
    'br': 'Brazil',
    'ca': 'Canada',
    'ch': 'Switzerland',
    'cl': 'Chile',
    'cn': 'China',
    'co': 'Colombia',
    'de': 'Germany',
    'dk': 'Denmark',
    'es': 'Spain',
    'fi': 'Finland',
    'fr': 'France',
    'gb': 'United Kingdom',
    'id': 'Indonesia',
    'ie': 'Ireland',
    'il': 'Israel',
    'in': 'India',
    'it': 'Italy',
    'jp': 'Japan',
    'kr': 'South Korea',
    'mx': 'Mexico',
    'my': 'Malaysia',
    'nl': 'Netherlands',
    'no': 'Norway',
    'nz': 'New Zealand',
    'pe': 'Peru',
    'ph': 'Philippines',
    'pl': 'Poland',
    'pt': 'Portugal',
    'ru': 'Russia',
    'se': 'Sweden',
    'sg': 'Singapore',
    'th': 'Thailand',
    'tr': 'Turkey',
    'tw': 'Taiwan',
    'ua': 'Ukraine',
    'us': 'United States',
    've': 'Venezuela',
    'za': 'South Africa'
}

@countries_bp.route('/')
def index():
    """Countries list page"""
    crux_service = CruxService()
    cloudflare_service = CloudflareService()
    
    # Get available countries from both services
    crux_countries = set(crux_service.get_available_countries())
    cloudflare_countries = set(cloudflare_service._get_region_specific_sites('').keys() if hasattr(cloudflare_service, '_get_region_specific_sites') else [])
    
    # Union of all countries
    all_countries = sorted(crux_countries)
    
    # Prepare country data for display
    countries_data = []
    for code in all_countries:
        countries_data.append({
            'code': code,
            'name': COUNTRY_NAMES.get(code, code.upper()),
            'crux_available': code in crux_countries,
            'cloudflare_available': code in cloudflare_countries
        })
    
    return render_template('countries/index.html', countries=countries_data)

@countries_bp.route('/api/search')
def search():
    """Search countries API endpoint"""
    from flask import request
    
    query = request.args.get('q', '').lower()
    
    # Filter countries based on query
    filtered_countries = []
    for code, name in COUNTRY_NAMES.items():
        if query in code.lower() or query in name.lower():
            filtered_countries.append({
                'code': code,
                'name': name
            })
    
    return jsonify({'countries': filtered_countries})