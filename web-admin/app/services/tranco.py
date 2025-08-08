"""
Tranco Top Sites Service
Fetches top websites from Tranco ranking list using two-step download process
"""

import logging
from typing import List, Optional
from pathlib import Path
import requests
from datetime import datetime, timedelta
import re
import zipfile
import io
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

class TrancoService:
    """Service for fetching Tranco top sites using two-step process"""
    
    def __init__(self):
        self.cache_dir = Path('/tmp/tranco_cache')
        self.cache_dir.mkdir(exist_ok=True)
        self.cache_duration = timedelta(days=1)  # Cache daily at UTC midnight
        self.base_url = "https://tranco-list.eu"
        
    def get_top_sites(self, limit: int = 100, country: str = 'global', offset: int = 0) -> List[str]:
        """
        Fetch top sites from Tranco using two-step download process
        Note: Tranco doesn't support country-specific lists
        """
        if country != 'global':
            logger.warning(f"Tranco doesn't support country-specific lists. Using global list.")
        
        try:
            # Check cache first (expires at UTC midnight)
            cache_file = self.cache_dir / 'tranco_daily.csv'
            if self._is_cache_valid(cache_file):
                logger.info(f"Using cached Tranco data (offset={offset}, limit={limit})")
                return self._read_cached_sites(cache_file, limit, offset)
            
            # Two-step download process as per TODO-220.md
            logger.info("Starting two-step Tranco download process")
            
            # Step 1: Land on tranco-list.eu and find latest list URL
            latest_url = self._find_latest_list_url()
            if not latest_url:
                logger.error("Failed to find latest Tranco list URL")
                logger.error("Returning fallback sites - Step 1 failed")
                return self._get_fallback_sites()[:limit]
            
            # Step 2: Download ZIP file from the latest website
            zip_data = self._download_tranco_zip(latest_url)
            if not zip_data:
                logger.error("Failed to download Tranco ZIP file")
                logger.error("Returning fallback sites - Step 2 failed")
                return self._get_fallback_sites()[:limit]
            
            # Extract and cache the CSV data
            csv_content = self._extract_csv_from_zip(zip_data)
            if not csv_content:
                logger.error("Failed to extract CSV from Tranco ZIP")
                logger.error("Returning fallback sites - CSV extraction failed")
                return self._get_fallback_sites()[:limit]
            
            # Cache the data
            with open(cache_file, 'w') as f:
                f.write(csv_content)
            
            # Parse and return top sites with offset
            return self._parse_sites(csv_content, limit, offset)
            
        except Exception as e:
            logger.error(f"Error in Tranco two-step download: {e}")
            logger.error(f"Returning fallback sites for Tranco (limit={limit}, offset={offset})")
            return self._get_fallback_sites()[:limit]
    
    def _read_cached_sites(self, cache_file: Path, limit: int, offset: int) -> List[str]:
        """Read sites from cached file with pagination"""
        try:
            with open(cache_file, 'r') as f:
                content = f.read()
            return self._parse_sites(content, limit, offset)
        except Exception as e:
            logger.error(f"Error reading cache: {e}")
            return self._get_fallback_sites()  # Return empty list
    
    def _parse_sites(self, content: str, limit: int, offset: int) -> List[str]:
        """Parse sites from content with pagination"""
        sites = []
        lines = content.strip().split('\n')
        
        logger.info(f"Tranco: Total lines in content: {len(lines)}, offset={offset}, limit={limit}")
        
        # Skip to offset and take limit
        selected_lines = lines[offset:offset + limit]
        logger.info(f"Tranco: Selected {len(selected_lines)} lines from {offset} to {offset + limit}")
        
        for line in selected_lines:
            if ',' in line:
                rank, domain = line.split(',', 1)
                sites.append(domain.strip())
        
        logger.info(f"Parsed {len(sites)} sites from Tranco (offset={offset}, requested_limit={limit})")
        return sites
    
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
    
    def _find_latest_list_url(self) -> Optional[str]:
        """Step 1: Find the latest Tranco list URL from main page"""
        try:
            response = requests.get(self.base_url, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Search for the download link - look for link to /latest_list
            latest_link = soup.find('a', href='/latest_list')
            if latest_link:
                logger.info(f"Found latest list link: {latest_link['href']}")
                return f"{self.base_url}/latest_list"
            
            # Alternative: search for "Download the" text
            download_text = soup.find(text=re.compile(r"Download the", re.IGNORECASE))
            if not download_text:
                logger.error("Could not find download link on Tranco homepage")
                return None
            
            # Find the parent element and look for a link
            parent = download_text.parent
            while parent and parent.name != 'html':
                link = parent.find('a', href=True)
                if link:
                    href = link['href']
                    # Make absolute URL if needed
                    if href.startswith('/'):
                        return f"{self.base_url}{href}"
                    elif href.startswith('http'):
                        return href
                parent = parent.parent
            
            logger.error("Could not find link for latest Tranco list")
            return None
            
        except Exception as e:
            logger.error(f"Error finding latest Tranco list URL: {e}")
            return None
    
    def _download_tranco_zip(self, latest_url: str) -> Optional[bytes]:
        """Step 2: Download ZIP file from the latest website"""
        try:
            response = requests.get(latest_url, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Find button with text containing "Download ZIP"
            zip_button = soup.find('a', text=re.compile(r"Download ZIP", re.IGNORECASE))
            
            if not zip_button:
                # Try finding by href pattern
                zip_button = soup.find('a', href=re.compile(r"/download_daily/"))
            
            if not zip_button or not zip_button.get('href'):
                logger.error("Could not find ZIP download button")
                logger.error(f"Page URL was: {latest_url}")
                return None
            
            zip_url = zip_button['href']
            # Make absolute URL if needed
            if zip_url.startswith('/'):
                zip_url = f"{self.base_url}{zip_url}"
            elif not zip_url.startswith('http'):
                # Relative to current page
                base_url = latest_url.rsplit('/', 1)[0]
                zip_url = f"{base_url}/{zip_url}"
            
            logger.info(f"Downloading Tranco ZIP from: {zip_url}")
            
            # Download the ZIP file (larger timeout for ZIP download)
            zip_response = requests.get(zip_url, timeout=25)
            zip_response.raise_for_status()
            
            return zip_response.content
            
        except Exception as e:
            logger.error(f"Error downloading Tranco ZIP: {e}")
            return None
    
    def _extract_csv_from_zip(self, zip_data: bytes) -> Optional[str]:
        """Extract CSV content from ZIP file"""
        try:
            with zipfile.ZipFile(io.BytesIO(zip_data)) as zip_file:
                # Find CSV file in ZIP
                csv_files = [name for name in zip_file.namelist() if name.endswith('.csv')]
                if not csv_files:
                    logger.error("No CSV file found in ZIP")
                    return None
                
                # Use the first CSV file
                csv_filename = csv_files[0]
                logger.info(f"Extracting {csv_filename} from Tranco ZIP")
                
                with zip_file.open(csv_filename) as csv_file:
                    return csv_file.read().decode('utf-8')
                    
        except Exception as e:
            logger.error(f"Error extracting CSV from ZIP: {e}")
            return None

    def _get_fallback_sites(self) -> List[str]:
        """Return empty list - no fallback sites per TODO-223"""
        logger.error("ERROR: Tranco service failed - returning empty list (no fallback sites)")
        return []