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
            logger.error("Repository should be cloned during container build. Check Dockerfile.")
            return None
        
        try:
            # Check if the repository has the expected structure
            main_module = oca_repo_path / "src" / "netflix_oca_locator"
            if not main_module.exists():
                logger.error(f"Netflix OCA locator module not found at {main_module}")
                logger.error("Repository structure may have changed. Check the repository.")
                return None
            
            logger.info("Running Netflix OCA locator via CLI...")
            # Use the correct CLI command with JSON output
            result = subprocess.run([
                "python3", "-m", "netflix_oca_locator", "main", "--output", "json", "--quiet"
            ], cwd="/app", capture_output=True, text=True, timeout=120)
            
            if result.returncode == 0:
                logger.info("Netflix OCA locator completed successfully")
                logger.info(f"OCA locator output: {result.stdout}")
                
                # Look for output files in the working directory (/app)
                possible_outputs = [
                    Path("/app/oca_results.json"),
                    Path("/app/results.json"),
                    Path("/app/netflix_oca_results.json")
                ]
                
                for output_file in possible_outputs:
                    if output_file.exists():
                        logger.info(f"Found OCA results file: {output_file}")
                        with open(output_file, 'r') as f:
                            data = json.load(f)
                        logger.info(f"Successfully parsed OCA data, found {data.get('total_ocas', len(data.get('oca_servers', [])))} OCA servers")
                        return data
                
                logger.warning("Netflix OCA locator completed but no output file found")
                logger.warning(f"Checked paths: {[str(p) for p in possible_outputs]}")
                
            else:
                logger.error(f"Netflix OCA locator failed: {result.stderr}")
                logger.error(f"Return code: {result.returncode}")
                logger.error(f"Stdout: {result.stdout}")
        
        except subprocess.TimeoutExpired:
            logger.error("Netflix OCA locator timed out after 120 seconds")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON output from Netflix OCA locator: {e}")
        except Exception as e:
            logger.error(f"Error running Netflix OCA locator: {e}")
        
        logger.warning("Netflix OCA locator failed to produce valid results")
        return None
    
    def fetch_oca_servers(self) -> Optional[Dict]:
        """Run the OCA locator and return the JSON output"""
        logger.info("Fetching Netflix OCA servers...")
        
        # Run the OCA locator tool
        oca_data = self.run_oca_locator()
        if oca_data:
            logger.info("Successfully fetched OCA data using Netflix OCA Servers Locator")
            return oca_data
        
        logger.error("Netflix OCA locator failed - no OCA discovery method succeeded")
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
                logger.info(f"DEBUG: Step 1 - original city from server: '{city_name}'")
                
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
                logger.info(f"DEBUG: Step 2 - after removing state suffix: '{city_name}'")
                
                # Sanitize city name for SmokePing - remove all special characters including periods
                safe_city_name = city_name.replace('.', '').replace(' ', '_').replace(',', '').replace('-', '_').replace('(', '').replace(')', '')
                logger.info(f"DEBUG: Step 3 - after sanitization: '{safe_city_name}'")
                
                # Create meaningful name from domain  
                if domain:
                    # Extract cache identifier from domain (e.g., c115)
                    cache_id = "unknown"
                    for part in domain.split('-'):
                        if part.startswith('c') and part[1:].isdigit():
                            cache_id = part
                            break
                    # Use format: Chicago_ORD_c730_1 (SmokePing safe)
                    name = f"{safe_city_name}_{location_code}_{cache_id}_{idx+1}"
                    logger.info(f"DEBUG: Step 4 - final name: '{name}'")
                else:
                    name = f"{safe_city_name}_{location_code}_{idx+1}"
                    logger.info(f"DEBUG: Step 4 - final name (no domain): '{name}'")
                
                # Create title in format: domain (City CODE/cache_id)
                if domain and city_name != "Unknown":
                    title = f"{domain} ({city_name} {location_code}/{cache_id}_{idx+1})"
                else:
                    title = f"{domain}" if domain else f"Netflix OCA {location_code}"
                
                # Log real OCA server detection with debug info
                logger.info(f"Real Netflix OCA server {name}: {domain} -> {ip} (IPv{ip_obj.version}) -> {probe}")
                logger.info(f"Debug: original_city='{server.get('city')}', cleaned_city='{city_name}', safe_city='{safe_city_name}'")
                
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
        """DISABLED: No fallback targets per TODO-257"""
        logger.warning("Fallback OCA targets completely disabled per TODO-257")
        logger.warning("Netflix OCA section will remain empty when real OCA discovery fails")
        logger.warning("This prevents addition of generic Netflix domains (netflix.com, fast.com, etc.)")
        return []
    
    def generate_smokeping_config(self) -> bool:
        """Generate and deploy SmokePing configuration after target updates"""
        try:
            logger.info("Triggering SmokePing configuration regeneration...")
            
            # Run config generator with deployment to grafana-influx
            result = subprocess.run([
                sys.executable, 
                str(Path(__file__).parent / "config_generator.py"),
                "--deploy-to", "grafana-influx"
            ], 
            cwd=Path(__file__).parent.parent,
            capture_output=True, 
            text=True, 
            timeout=60
            )
            
            if result.returncode == 0:
                logger.info("Successfully regenerated and deployed SmokePing configuration")
                logger.debug(f"Config generator output: {result.stdout}")
                return True
            else:
                logger.error(f"Config generator failed: {result.stderr}")
                logger.error(f"Return code: {result.returncode}")
                logger.error(f"Stdout: {result.stdout}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error("Config generator timed out after 60 seconds")
            return False
        except Exception as e:
            logger.error(f"Error running config generator: {e}")
            return False
    
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
                if new_count > 0:
                    logger.info(f"Replacing {old_count} existing Netflix OCA targets with {new_count} new targets")
                else:
                    logger.info(f"Removing {old_count} existing Netflix OCA targets (no new targets found)")
                for old_target in old_oca_targets:
                    logger.debug(f"Removing old OCA target: {old_target.get('name', 'unknown')} ({old_target.get('host', 'unknown')})")
            else:
                if new_count > 0:
                    logger.info(f"Adding {new_count} new Netflix OCA targets (no existing targets to replace)")
                else:
                    logger.info("No existing Netflix OCA targets and no new targets found - keeping empty section")
            
            # Update OCA targets (this replaces the entire netflix_oca list)
            targets_config['active_targets']['netflix_oca'] = oca_targets
            
            # Log new targets being added
            if oca_targets:
                for new_target in oca_targets:
                    logger.info(f"Added new OCA target: {new_target.get('name', 'unknown')} ({new_target.get('host', 'unknown')}) - {new_target.get('title', 'unknown')}")
            else:
                logger.info("Netflix OCA section cleared - no real OCA servers found")
            
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
            
            # Trigger SmokePing configuration regeneration
            self.generate_smokeping_config()
            
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
        logger.info("Starting Netflix OCA server discovery process...")
        oca_data = self.fetch_oca_servers()
        if not oca_data:
            logger.error("Netflix OCA discovery failed - no real OCA servers found")
            logger.info("Netflix OCA section will be cleared (no fallback domains per TODO-257)")
            logger.info("Check container logs and network connectivity for OCA discovery issues")
            # Use empty targets list (no fallback)
            oca_targets = []
        else:
            logger.info("Successfully fetched real Netflix OCA data, parsing targets...")
            # Parse the fetched data
            oca_targets = self.parse_oca_data(oca_data, max_targets)
            if not oca_targets:
                logger.warning("No valid OCA targets found in discovery data - clearing Netflix OCA section")
                logger.warning("This may indicate an issue with OCA data parsing or filtering")
                oca_targets = []
            else:
                logger.info(f"Successfully parsed {len(oca_targets)} real Netflix OCA servers")
        
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