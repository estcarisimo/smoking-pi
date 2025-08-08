"""
Cloudflare Radar Top Sites Service
Fetches top websites from Cloudflare Radar using direct HTTP API calls
"""

import logging
from typing import List, Optional
from pathlib import Path
from datetime import datetime, timezone
import json
import requests

logger = logging.getLogger(__name__)

class CloudflareService:
    """Service for fetching Cloudflare Radar top sites"""
    
    def __init__(self):
        self.cache_dir = Path('/tmp/cloudflare_cache')
        self.cache_dir.mkdir(exist_ok=True)
        self.api_base_url = 'https://api.cloudflare.com/client/v4'
    
    def get_top_sites(self, limit: int = 100, country: str = 'global', offset: int = 0, api_token: Optional[str] = None) -> List[str]:
        """
        Fetch top sites from Cloudflare Radar API
        
        Args:
            limit: Number of sites to return
            country: Country code or 'global' for worldwide
            offset: Offset for pagination (not supported by API)
            api_token: Cloudflare API token
            
        Returns:
            List of domain names
        """
        if not api_token:
            logger.error("No API token provided for Cloudflare Radar")
            return []
        
        # Validate country code (2-letter ISO code or 'global')  
        if country != 'global':
            if not country or len(country) != 2 or not country.isalpha():
                logger.warning(f"Invalid country code '{country}', using global")
                country = 'global'
            else:
                # Convert to uppercase for API consistency
                country = country.upper()
        
        try:
            # Check cache first
            cache_file = self.cache_dir / f'cloudflare_{country}.json'
            if self._is_cache_valid(cache_file):
                logger.info(f"Using cached Cloudflare data")
                return self._read_cached_sites(cache_file, limit, offset)
            
            # Fetch from API - Cloudflare API max is 100 and doesn't support offset
            if offset > 0:
                logger.warning("Cloudflare API doesn't support pagination - returning empty list for offset > 0")
                return []
            
            fetch_limit = min(100, limit)  # API max is 100
            sites = self._fetch_from_api(api_token, fetch_limit, country)
            if not sites:
                logger.error("Failed to fetch data from Cloudflare API")
                return []
            
            # Cache the data
            self._cache_sites(cache_file, sites)
            
            logger.info(f"Successfully fetched {len(sites)} sites from Cloudflare")
            return sites
            
        except Exception as e:
            logger.error(f"Error in Cloudflare service: {e}")
            return []
    
    def _fetch_from_api(self, api_token: str, limit: int = 100, location: str = 'global') -> List[str]:
        """Fetch sites from Cloudflare Radar API using direct HTTP requests"""
        try:
            headers = {
                'Authorization': f'Bearer {api_token}',
                'Content-Type': 'application/json'
            }
            
            url = f'{self.api_base_url}/radar/ranking/top'
            params = {'limit': limit}
            
            # Add location parameter if not global
            if location != 'global':
                params['location'] = location  # Already uppercase from validation
            
            logger.info(f"Fetching sites from Cloudflare Radar API: {url} with params={params}")
            response = requests.get(url, headers=headers, params=params, timeout=30)
            logger.info(f"Request URL: {response.url}")
            
            logger.info(f"Cloudflare API response: status={response.status_code}")
            
            if response.status_code == 401:
                logger.error("Invalid API token for Cloudflare")
                return []
            elif response.status_code == 403:
                logger.error("API token does not have permission to access Cloudflare Radar")
                return []
            elif response.status_code == 429:
                logger.error("Rate limit exceeded for Cloudflare API")
                return []
            elif response.status_code != 200:
                logger.error(f"HTTP error {response.status_code}: {response.text}")
                return []
            
            # Parse JSON response
            data = response.json()
            
            if not data.get('success', False):
                errors = data.get('errors', [])
                logger.error(f"Cloudflare API returned errors: {errors}")
                return []
            
            # Extract domains from response
            sites = []
            result = data.get('result', {})
            top_0 = result.get('top_0', [])
            
            if not top_0:
                logger.error("No top_0 data in Cloudflare API response")
                return []
            
            for item in top_0:
                if isinstance(item, dict) and 'domain' in item:
                    sites.append(item['domain'])
            
            logger.info(f"Extracted {len(sites)} domains from Cloudflare API response")
            return sites
            
        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP request error: {e}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}")
            return []
        except Exception as e:
            logger.error(f"Error fetching from Cloudflare API: {e}")
            return []
    
    def _is_cache_valid(self, cache_file: Path) -> bool:
        """Check if cache is valid (expires at UTC midnight)"""
        if not cache_file.exists():
            return False
        
        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)
            
            cached_time = datetime.fromisoformat(data['timestamp'])
            now = datetime.now(timezone.utc)
            
            # Check if it's the same UTC day
            return (cached_time.date() == now.date() and 
                    cached_time.replace(tzinfo=timezone.utc).date() == now.date())
            
        except Exception as e:
            logger.error(f"Error checking cache validity: {e}")
            return False
    
    def _read_cached_sites(self, cache_file: Path, limit: int, offset: int) -> List[str]:
        """Read sites from cached file"""
        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)
            
            sites = data['sites']
            
            logger.info(f"Read {len(sites)} sites from cache")
            return sites
            
        except Exception as e:
            logger.error(f"Error reading cached sites: {e}")
            return []
    
    def _cache_sites(self, cache_file: Path, sites: List[str]) -> None:
        """Cache sites data"""
        try:
            cache_data = {
                'sites': sites,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'count': len(sites)
            }
            
            with open(cache_file, 'w') as f:
                json.dump(cache_data, f)
            
            logger.info(f"Cached {len(sites)} sites to {cache_file}")
            
        except Exception as e:
            logger.error(f"Error caching sites: {e}")