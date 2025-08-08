"""
Chrome User Experience (CrUX) Top Sites Service
Fetches top websites from CrUX dataset
"""

import logging
import gzip
import csv
from typing import List
from pathlib import Path
import requests
from datetime import datetime, timedelta
from io import StringIO

logger = logging.getLogger(__name__)

class CruxService:
    """Service for fetching CrUX top sites"""
    
    def __init__(self):
        self.cache_dir = Path('/tmp/crux_cache')
        self.cache_dir.mkdir(exist_ok=True)
        self.cache_duration = timedelta(days=1)  # Cache daily at UTC midnight
        # Updated URL from TODO-220.md
        self.global_url = "https://github.com/zakird/crux-top-lists/raw/refs/heads/main/data/global/current.csv.gz"
    
    def get_top_sites(self, limit: int = 100, country: str = 'global', offset: int = 0) -> List[str]:
        """
        Fetch top sites from CrUX dataset
        Note: Country-specific lists are not available per TODO-220.md
        """
        if country != 'global':
            logger.warning(f"CrUX doesn't support country-specific lists. Using global list.")
        
        try:
            # Use global URL only
            url = self.global_url
            
            # Check cache (expires at UTC midnight)
            cache_file = self.cache_dir / 'crux_global.csv'
            if self._is_cache_valid(cache_file):
                logger.info(f"Using cached CrUX data (offset={offset}, limit={limit})")
                return self._read_cached_sites(cache_file, limit, offset)
            
            # Download the data
            logger.info(f"Fetching CrUX data from URL: {url}")
            response = requests.get(url, timeout=30)
            logger.info(f"CrUX HTTP response: status={response.status_code}, content-length={len(response.content)}")
            
            if response.status_code == 404 and country != 'global':
                # Fallback to global if country not found
                logger.warning(f"Country {country} not found, using global list")
                return self.get_top_sites(limit, 'global')
            
            response.raise_for_status()
            
            # Check if response content looks like gzipped data
            if not response.content.startswith(b'\x1f\x8b'):
                logger.error(f"CrUX response doesn't appear to be gzipped data. First 50 bytes: {response.content[:50]}")
                return self._get_fallback_sites()[:limit]
            
            # Decompress and parse CSV
            logger.info("Attempting to decompress CrUX gzip data...")
            try:
                decompressed = gzip.decompress(response.content).decode('utf-8')
                logger.info(f"CrUX decompression successful. Decompressed size: {len(decompressed)} chars")
            except Exception as gzip_error:
                logger.error(f"CrUX gzip decompression failed: {gzip_error}")
                return self._get_fallback_sites()[:limit]
            
            # Cache the decompressed data
            with open(cache_file, 'w') as f:
                f.write(decompressed)
            
            # Read with pagination
            return self._read_cached_sites(cache_file, limit, offset)
            
        except Exception as e:
            logger.error(f"Error fetching CrUX data: {e}")
            return self._get_fallback_sites()[:limit]
    
    def _read_cached_sites(self, cache_file: Path, limit: int, offset: int = 0) -> List[str]:
        """Read sites from cached file with pagination"""
        all_domains = []
        try:
            logger.info(f"Reading CrUX cached file: {cache_file}")
            with open(cache_file, 'r') as f:
                csv_reader = csv.DictReader(f)
                row_count = 0
                for row in csv_reader:
                    row_count += 1
                    origin = row.get('origin', '')
                    if origin:
                        domain = self._extract_domain(origin)
                        if domain and domain not in all_domains:
                            all_domains.append(domain)
                    
                    # Stop processing if we have enough for this page + future pages
                    if len(all_domains) >= offset + limit + 1000:
                        break
                
                logger.info(f"CrUX: Processed {row_count} rows, found {len(all_domains)} unique domains")
            
            # Apply pagination
            start_idx = offset
            end_idx = offset + limit
            sites = all_domains[start_idx:end_idx]
            
            logger.info(f"Read {len(sites)} sites from CrUX cache (offset={offset}, limit={limit})")
            return sites
        except Exception as e:
            logger.error(f"Error reading CrUX cache: {e}")
            return []
    
    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL"""
        try:
            # Remove protocol
            if '://' in url:
                url = url.split('://', 1)[1]
            
            # Remove path
            if '/' in url:
                url = url.split('/', 1)[0]
            
            # Remove port
            if ':' in url:
                url = url.split(':', 1)[0]
            
            return url.lower()
        except:
            return ''
    
    
    def _is_cache_valid(self, cache_file: Path) -> bool:
        """Check if cache is valid (expires at UTC midnight)"""
        if not cache_file.exists():
            return False
        
        # Get file modification time
        file_mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
        now = datetime.now()
        
        # Cache is valid if file was created today (UTC)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return file_mtime >= today_start

    def _get_fallback_sites(self) -> List[str]:
        """Return fallback list of popular sites"""
        return [
            'google.com', 'youtube.com', 'facebook.com', 'amazon.com', 'wikipedia.org',
            'twitter.com', 'instagram.com', 'linkedin.com', 'reddit.com', 'netflix.com'
        ]