#!/usr/bin/env python3
"""
Netflix OCA Fetcher
Discovers Netflix Open Connect Appliance servers and updates targets configuration
"""

import json
import logging
import subprocess
import sys
import tempfile
import ipaddress
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import yaml

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration paths
CONFIG_DIR = Path(__file__).parent.parent / "config"
SOURCES_FILE = CONFIG_DIR / "sources.yaml"
TARGETS_FILE = CONFIG_DIR / "targets.yaml"



class OCAFetcher:
    """Fetches Netflix OCA servers using the Netflix-OCA-Servers-Locator"""
    
    def __init__(self):
        pass
        
    def run_oca_locator(self) -> Optional[Dict]:
        """Run the Netflix OCA locator tool and return JSON output"""
        logger.info("Running Netflix OCA locator tool...")
        
        # Check if the OCA locator repository exists
        oca_repo_path = Path("/app/oca-locator")
        if not oca_repo_path.exists():
            logger.error("Netflix OCA locator repository not found at /app/oca-locator")
            return None
        
        try:
            # Use the working Python module command that we tested
            logger.info("Running Netflix OCA locator via Python module...")
            result = subprocess.run([
                "python3", "-m", "src.netflix_oca_locator", "main", "--output", "json", "--quiet"
            ], cwd=str(oca_repo_path), capture_output=True, text=True, timeout=120)
            
            if result.returncode == 0:
                # The tool outputs to oca_results.json in the working directory
                output_file = oca_repo_path / "oca_results.json"
                if output_file.exists():
                    with open(output_file, 'r') as f:
                        data = json.load(f)
                    logger.info(f"Successfully ran Netflix OCA locator, found {data.get('total_ocas', 0)} OCA servers")
                    return data
                else:
                    logger.error("Netflix OCA locator completed but no output file found")
            else:
                logger.warning(f"Netflix OCA locator failed: {result.stderr}")
        
        except subprocess.TimeoutExpired:
            logger.error("Netflix OCA locator timed out after 120 seconds")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON output from Netflix OCA locator: {e}")
        except Exception as e:
            logger.error(f"Error running Netflix OCA locator: {e}")
        
        return None
    
    def fetch_oca_servers(self) -> Optional[Dict]:
        """Run the OCA locator and return the JSON output"""
        logger.info("Fetching Netflix OCA servers...")
        
        # Try the new direct approach first
        oca_data = self.run_oca_locator()
        if oca_data:
            logger.info("Successfully fetched OCA data using direct command approach")
            return oca_data
        
        logger.warning("Direct OCA locator approach failed, falling back to legacy method")
        return None
    
    def parse_oca_data(self, data: Dict, max_targets: int = 10) -> List[Dict]:
        """Parse OCA data and convert to SmokePing target format"""
        targets = []
        
        if not data or 'oca_servers' not in data:
            logger.warning("No OCA servers found in data")
            return targets
        
        logger.info(f"Found {len(data['oca_servers'])} real Netflix OCA servers")
        
        for idx, server in enumerate(data['oca_servers'][:max_targets]):
            try:
                ip = server.get('ip_address')
                domain = server.get('domain', '')
                
                if not ip:
                    logger.warning(f"OCA server {idx} missing IP address")
                    continue
                
                # Determine if it's IPv4 or IPv6
                ip_obj = ipaddress.ip_address(ip)
                probe = "FPing" if ip_obj.version == 4 else "FPing6"
                
                # Get city name directly from the server data
                city_name = server.get('city', 'Unknown')
                
                # Extract location code from domain name (e.g., ord003 -> ORD)
                location_code = "Unknown"
                if domain:
                    # Extract location code from domain like "ipv4-c115-ord003-ix.1.oca.nflxvideo.net"
                    parts = domain.split('-')
                    for part in parts:
                        if len(part) >= 6 and part[:3].isalpha() and part[3:].isdigit():
                            location_code = part[:3].upper()  # e.g., "ord" -> "ORD" 
                            break
                
                # Handle city name extraction - remove state suffix if present
                if city_name and ', ' in city_name:
                    city_name = city_name.split(',')[0].strip()  # "Chicago, IL" -> "Chicago"
                
                # Create meaningful name from domain  
                if domain:
                    # Extract cache identifier from domain (e.g., c115)
                    cache_id = "unknown"
                    for part in domain.split('-'):
                        if part.startswith('c') and part[1:].isdigit():
                            cache_id = part
                            break
                    # Use format: Chicago_ORD_c730_1 (SmokePing safe)
                    name = f"{city_name}_{location_code}_{cache_id}_{idx+1}".replace(' ', '_')
                else:
                    name = f"{city_name}_{location_code}_{idx+1}".replace(' ', '_')
                
                # Create title in format: domain (City CODE/cache_id)
                if domain and city_name != "Unknown":
                    title = f"{domain} ({city_name} {location_code}/{cache_id}_{idx+1})"
                else:
                    title = f"{domain}" if domain else f"Netflix OCA {location_code}"
                
                # Log real OCA server detection
                logger.info(f"Real Netflix OCA server {name}: {domain} -> {ip} (IPv{ip_obj.version}) -> {probe}")
                
                target = {
                    'name': name,
                    'host': ip,  # Use IP address for monitoring
                    'title': title,
                    'probe': probe,
                    'metadata': {
                        'domain': domain,
                        'asn': server.get('asn', ''),
                        'city': city_name,  # Clean city name without state
                        'location_code': location_code,
                        'type': 'real_oca',
                        'iata_code': server.get('iata_code', ''),
                        'latitude': server.get('latitude'),
                        'longitude': server.get('longitude'),
                        'raw_city': server.get('city', ''),  # Keep original data for reference
                        'cache_id': cache_id
                    }
                }
                
                targets.append(target)
                logger.debug(f"Added real OCA target: {name} ({ip})")
                
            except ValueError as e:
                logger.warning(f"Invalid IP address {ip}: {e}")
                continue
            except Exception as e:
                logger.warning(f"Error processing OCA server {idx}: {e}")
                continue
        
        logger.info(f"Parsed {len(targets)} real Netflix OCA targets")
        return targets
    
    def get_fallback_oca_targets(self) -> List[Dict]:
        """Return fallback OCA targets when Docker approach fails"""
        logger.info("Using fallback OCA targets")
        
        # These are real Netflix infrastructure servers and domains
        # This is a basic fallback when the dynamic discovery fails
        fallback_servers = [
            {
                'host': 'netflix.com',
                'name': 'netflix_main',
                'title': 'Netflix Main Site',
                'location': 'Global CDN',
                'probe': 'FPing'
            },
            {
                'host': 'fast.com',
                'name': 'netflix_fast',
                'title': 'Netflix Speed Test (Fast.com)',
                'location': 'Global CDN',
                'probe': 'FPing'
            },
            {
                'host': 'openconnect.netflix.com',
                'name': 'netflix_openconnect',
                'title': 'Netflix Open Connect',
                'location': 'Global CDN',
                'probe': 'FPing'
            },
            {
                'host': 'api.netflix.com',
                'name': 'netflix_api',
                'title': 'Netflix API Server',
                'location': 'Global CDN',
                'probe': 'FPing'
            },
            {
                'host': '198.38.96.0',
                'name': 'netflix_oca_ip1',
                'title': 'Netflix OCA Server (Direct IP)',
                'location': 'US',
                'probe': 'FPing'
            }
        ]
        
        targets = []
        for server in fallback_servers:
            try:
                # Validate hostname/IP address
                host = server['host']
                try:
                    # Check if it's an IP address
                    ip_obj = ipaddress.ip_address(host)
                    # It's a valid IP, use it directly
                    probe = "FPing" if ip_obj.version == 4 else "FPing6"
                    server['probe'] = probe
                    logger.info(f"OCA target {server['name']}: Direct IP {host} (IPv{ip_obj.version}) -> {probe}")
                except ValueError:
                    # It's a hostname, try to resolve it to validate and get IP info
                    try:
                        import socket
                        resolved_ip = socket.gethostbyname(host)
                        # Check what IP version it resolved to
                        try:
                            resolved_obj = ipaddress.ip_address(resolved_ip)
                            logger.info(f"OCA target {server['name']}: Hostname {host} -> {resolved_ip} (IPv{resolved_obj.version})")
                        except ValueError:
                            logger.info(f"OCA target {server['name']}: Hostname {host} -> {resolved_ip}")
                        # Hostname resolves, keep the specified probe
                    except socket.gaierror:
                        logger.warning(f"Cannot resolve hostname: {host}")
                        continue
                
                target = {
                    'name': server['name'],
                    'host': server['host'],
                    'title': server['title'],
                    'probe': server['probe'],
                    'metadata': {
                        'location': server['location'],
                        'type': 'fallback',
                        'domain': server['host'] if not server['host'].replace('.', '').isdigit() else ''
                    }
                }
                targets.append(target)
                logger.debug(f"Added fallback OCA target: {server['name']} ({server['host']})")
                
            except Exception as e:
                logger.warning(f"Error processing fallback server {server}: {e}")
                continue
        
        logger.info(f"Generated {len(targets)} fallback OCA targets with domain names")
        
        # Log summary of IPv4/IPv6 distribution
        ipv4_count = sum(1 for t in targets if t['probe'] == 'FPing')
        ipv6_count = sum(1 for t in targets if t['probe'] == 'FPing6')
        logger.info(f"Fallback OCA IPv4/IPv6 distribution: {ipv4_count} IPv4 targets, {ipv6_count} IPv6 targets")
        
        return targets
    
    def update_targets(self, oca_targets: List[Dict]) -> bool:
        """Update the targets.yaml file with new OCA servers"""
        try:
            # Load current targets
            with open(TARGETS_FILE, 'r') as f:
                targets_config = yaml.safe_load(f)
            
            # Log target replacement process
            old_oca_targets = targets_config.get('active_targets', {}).get('netflix_oca', [])
            old_count = len(old_oca_targets)
            new_count = len(oca_targets)
            
            if old_count > 0:
                logger.info(f"Replacing {old_count} existing Netflix OCA targets with {new_count} new targets")
                for old_target in old_oca_targets:
                    logger.debug(f"Removing old OCA target: {old_target.get('name', 'unknown')} ({old_target.get('host', 'unknown')})")
            else:
                logger.info(f"Adding {new_count} new Netflix OCA targets (no existing targets to replace)")
            
            # Update OCA targets (this replaces the entire netflix_oca list)
            targets_config['active_targets']['netflix_oca'] = oca_targets
            
            # Log new targets being added
            for new_target in oca_targets:
                logger.info(f"Added new OCA target: {new_target.get('name', 'unknown')} ({new_target.get('host', 'unknown')}) - {new_target.get('title', 'unknown')}")
            
            # Update metadata
            targets_config['metadata']['last_updated'] = datetime.now().isoformat()
            
            # Calculate total targets
            total = sum(len(v) for v in targets_config['active_targets'].values() 
                       if isinstance(v, list))
            targets_config['metadata']['total_targets'] = total
            
            # Estimate bandwidth (rough calculation)
            # Assuming 10 pings every 300s with 64 bytes each
            bandwidth_per_target = (10 * 64 * 8) / 300  # bits per second
            total_bandwidth_mbps = (total * bandwidth_per_target) / 1_000_000
            targets_config['metadata']['bandwidth_estimate_mbps'] = round(total_bandwidth_mbps, 2)
            
            # Write updated configuration
            with open(TARGETS_FILE, 'w') as f:
                yaml.dump(targets_config, f, default_flow_style=False, sort_keys=False)
            
            logger.info(f"Updated targets.yaml with {len(oca_targets)} OCA servers")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update targets file: {e}")
            return False
    
    def run(self) -> bool:
        """Main execution flow"""
        # Load sources configuration
        try:
            with open(SOURCES_FILE, 'r') as f:
                sources = yaml.safe_load(f)
            
            oca_config = sources.get('dynamic', {}).get('netflix_oca', {})
            
            if not oca_config.get('enabled', True):
                logger.info("Netflix OCA fetching is disabled in configuration")
                return True
            
            max_targets = oca_config.get('max_targets', 10)
            
        except Exception as e:
            logger.error(f"Failed to load sources configuration: {e}")
            return False
        
        # Fetch OCA servers
        logger.info("Starting OCA server discovery process...")
        oca_data = self.fetch_oca_servers()
        if not oca_data:
            logger.warning("Failed to fetch OCA servers via Docker - known issue with upstream repository")
            logger.info("Switching to fallback Netflix infrastructure targets")
            # Use fallback OCA targets
            oca_targets = self.get_fallback_oca_targets()
            if not oca_targets:
                logger.error("Failed to generate fallback OCA servers")
                return False
        else:
            logger.info("Successfully fetched dynamic OCA data, parsing targets...")
            # Parse the fetched data
            oca_targets = self.parse_oca_data(oca_data, max_targets)
            if not oca_targets:
                logger.warning("No valid OCA targets found in dynamic data, using fallback")
                oca_targets = self.get_fallback_oca_targets()
        
        # Update targets file
        if not self.update_targets(oca_targets):
            return False
        
        logger.info("Successfully completed OCA fetching process")
        return True


def main():
    """Main entry point"""
    fetcher = OCAFetcher()
    
    try:
        success = fetcher.run()
        sys.exit(0 if success else 1)
        
    except KeyboardInterrupt:
        logger.info("Process interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()