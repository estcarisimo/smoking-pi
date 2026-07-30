"""
Chrome User Experience (CrUX) Top Sites Service
Fetches top websites from CrUX dataset
"""

import logging
import gzip
import csv
import re
from typing import List
from pathlib import Path
import requests
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

class CruxService:
    """Service for fetching CrUX top sites"""

    def __init__(self):
        self.cache_dir = Path('/tmp/crux_cache')
        self.cache_dir.mkdir(exist_ok=True)
        self.cache_duration = timedelta(days=1)  # Cache daily at UTC midnight
        # Updated URL from TODO-220.md
        self.global_url = "https://github.com/zakird/crux-top-lists/raw/refs/heads/main/data/global/current.csv.gz"
        # Per-country lists are monthly snapshots: data/country/<cc>/<YYYYMM>.csv.gz
        self.country_url_template = (
            "https://github.com/zakird/crux-top-lists/raw/refs/heads/main/"
            "data/country/{country}/{yyyymm}.csv.gz"
        )

    def get_top_sites(self, limit: int = 100, country: str = 'global', offset: int = 0) -> List[str]:
        """
        Fetch top sites from the CrUX dataset (global or per-country).
        """
        country = (country or 'global').lower()
        if country != 'global':
            return self._get_country_sites(country, limit, offset)

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

    def _get_country_sites(self, country: str, limit: int, offset: int) -> List[str]:
        """Fetch a per-country CrUX list (monthly snapshots on GitHub).

        Returns an empty list on failure so the API layer can surface an
        error instead of silently substituting unrelated sites.
        """
        if not re.fullmatch(r'[a-z]{2}', country):
            logger.error(f"Invalid CrUX country code: {country!r}")
            return []

        cache_file = self.cache_dir / f'crux_{country}.csv'
        if self._is_cache_valid(cache_file):
            logger.info(
                f"Using cached CrUX data for {country} "
                f"(offset={offset}, limit={limit})"
            )
            return self._read_cached_sites(cache_file, limit, offset)

        # Snapshots are published monthly with some lag: try the current
        # month first, then step back a few months.
        now = datetime.now(timezone.utc)
        year, month = now.year, now.month
        for _ in range(4):
            yyyymm = f"{year}{month:02d}"
            url = self.country_url_template.format(country=country, yyyymm=yyyymm)
            try:
                logger.info(f"Fetching CrUX country data from URL: {url}")
                response = requests.get(url, timeout=30)
                if response.status_code == 200 and response.content.startswith(b'\x1f\x8b'):
                    decompressed = gzip.decompress(response.content).decode('utf-8')
                    with open(cache_file, 'w') as f:
                        f.write(decompressed)
                    return self._read_cached_sites(cache_file, limit, offset)
                logger.info(
                    f"CrUX snapshot {yyyymm} for {country} not available "
                    f"(status={response.status_code})"
                )
            except Exception as e:
                logger.error(f"Error fetching CrUX {country}/{yyyymm}: {e}")
            month -= 1
            if month == 0:
                month = 12
                year -= 1

        logger.error(f"No CrUX snapshot found for country {country}")
        return []

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