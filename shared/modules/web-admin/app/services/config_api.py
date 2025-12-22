"""
Config Manager API Client
Handles communication with the config-manager REST API
"""

import logging
import requests
from typing import Dict, Any, Optional
from datetime import datetime
import json

logger = logging.getLogger(__name__)


class ConfigManagerClient:
    """Client for config-manager REST API"""
    
    def __init__(self, base_url: str = "http://config-manager:5000"):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'web-admin/1.0'
        })
        
    def _make_request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """Make HTTP request with error handling"""
        url = f"{self.base_url}{endpoint}"
        
        try:
            logger.info(f"{method.upper()} {url}")
            start_time = datetime.now()
            
            response = self.session.request(method, url, timeout=30, **kwargs)
            
            duration = (datetime.now() - start_time).total_seconds()
            logger.info(f"{method.upper()} {url} - {response.status_code} ({duration:.2f}s)")
            
            return response
            
        except requests.exceptions.Timeout:
            logger.error(f"Request to {url} timed out")
            raise ConnectionError("Config manager request timed out")
        except requests.exceptions.ConnectionError:
            logger.error(f"Failed to connect to {url}")
            raise ConnectionError("Cannot connect to config manager")
        except requests.exceptions.RequestException as e:
            logger.error(f"Request to {url} failed: {e}")
            raise ConnectionError(f"Config manager request failed: {e}")
    
    def health_check(self) -> Dict[str, Any]:
        """Check if config-manager is healthy"""
        try:
            response = self._make_request('GET', '/health')
            if response.status_code == 200:
                return response.json()
            else:
                raise ConnectionError(f"Health check failed: {response.status_code}")
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            raise
    
    def get_status(self) -> Dict[str, Any]:
        """Get config-manager status"""
        try:
            response = self._make_request('GET', '/status')
            if response.status_code == 200:
                return response.json()
            else:
                error_data = response.json() if response.text else {'error': 'Unknown error'}
                raise RuntimeError(f"Failed to get status: {error_data}")
        except Exception as e:
            logger.error(f"Failed to get status: {e}")
            raise
    
    def get_config(self, config_type: str = 'all') -> Dict[str, Any]:
        """Get configuration from config-manager"""
        try:
            endpoint = f'/config/{config_type}' if config_type != 'all' else '/config'
            response = self._make_request('GET', endpoint)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 400:
                error_data = response.json()
                raise ValueError(error_data.get('error', 'Invalid request'))
            else:
                error_data = response.json() if response.text else {'error': 'Unknown error'}
                raise RuntimeError(f"Failed to get config: {error_data}")
                
        except Exception as e:
            logger.error(f"Failed to get {config_type} config: {e}")
            raise
    
    def update_config(self, config_type: str, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update configuration via config-manager"""
        try:
            response = self._make_request(
                'PUT', 
                f'/config/{config_type}',
                json=config_data
            )
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 400:
                error_data = response.json()
                raise ValueError(error_data.get('error', 'Invalid configuration'))
            else:
                error_data = response.json() if response.text else {'error': 'Unknown error'}
                raise RuntimeError(f"Failed to update config: {error_data}")
                
        except Exception as e:
            logger.error(f"Failed to update {config_type} config: {e}")
            raise
    
    def generate_config(self) -> Dict[str, Any]:
        """Generate SmokePing configuration"""
        try:
            response = self._make_request('POST', '/generate')
            
            if response.status_code == 200:
                return response.json()
            else:
                error_data = response.json() if response.text else {'error': 'Unknown error'}
                raise RuntimeError(f"Failed to generate config: {error_data}")
                
        except Exception as e:
            logger.error(f"Failed to generate config: {e}")
            raise
    
    def restart_smokeping(self) -> Dict[str, Any]:
        """Restart SmokePing service"""
        try:
            response = self._make_request('POST', '/restart')
            
            if response.status_code == 200:
                return response.json()
            else:
                error_data = response.json() if response.text else {'error': 'Unknown error'}
                raise RuntimeError(f"Failed to restart SmokePing: {error_data}")
                
        except Exception as e:
            logger.error(f"Failed to restart SmokePing: {e}")
            raise
    
    def refresh_oca(self) -> Dict[str, Any]:
        """Refresh OCA data"""
        try:
            response = self._make_request('POST', '/oca/refresh')
            
            if response.status_code == 200:
                return response.json()
            else:
                error_data = response.json() if response.text else {'error': 'Unknown error'}
                raise RuntimeError(f"Failed to refresh OCA: {error_data}")
                
        except Exception as e:
            logger.error(f"Failed to refresh OCA: {e}")
            raise
    
    # Database-specific methods for target management
    def get_targets(self, active_only: bool = False, category: str = None) -> Dict[str, Any]:
        """Get targets from database"""
        try:
            params = {}
            if active_only:
                params['active_only'] = 'true'
            if category:
                params['category'] = category
            
            response = self._make_request('GET', '/targets', params=params)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 400:
                error_data = response.json()
                raise ValueError(error_data.get('error', 'Database not available'))
            else:
                error_data = response.json() if response.text else {'error': 'Unknown error'}
                raise RuntimeError(f"Failed to get targets: {error_data}")
                
        except Exception as e:
            logger.error(f"Failed to get targets from database: {e}")
            raise
    
    def create_target(self, target_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create new target in database"""
        try:
            response = self._make_request('POST', '/targets', json=target_data)
            
            if response.status_code == 201:
                return response.json()
            elif response.status_code == 400:
                error_data = response.json()
                raise ValueError(error_data.get('error', 'Invalid target data'))
            else:
                error_data = response.json() if response.text else {'error': 'Unknown error'}
                raise RuntimeError(f"Failed to create target: {error_data}")
                
        except Exception as e:
            logger.error(f"Failed to create target: {e}")
            raise
    
    def update_target(self, target_id: int, target_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update target in database"""
        try:
            response = self._make_request('PUT', f'/targets/{target_id}', json=target_data)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                raise ValueError('Target not found')
            elif response.status_code == 400:
                error_data = response.json()
                raise ValueError(error_data.get('error', 'Invalid target data'))
            else:
                error_data = response.json() if response.text else {'error': 'Unknown error'}
                raise RuntimeError(f"Failed to update target: {error_data}")
                
        except Exception as e:
            logger.error(f"Failed to update target {target_id}: {e}")
            raise
    
    def delete_target(self, target_id: int) -> Dict[str, Any]:
        """Delete target from database"""
        try:
            response = self._make_request('DELETE', f'/targets/{target_id}')
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                raise ValueError('Target not found')
            else:
                error_data = response.json() if response.text else {'error': 'Unknown error'}
                raise RuntimeError(f"Failed to delete target: {error_data}")
                
        except Exception as e:
            logger.error(f"Failed to delete target {target_id}: {e}")
            raise
    
    def toggle_target(self, target_id: int) -> Dict[str, Any]:
        """Toggle target active status"""
        try:
            response = self._make_request('POST', f'/targets/{target_id}/toggle')
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                raise ValueError('Target not found')
            else:
                error_data = response.json() if response.text else {'error': 'Unknown error'}
                raise RuntimeError(f"Failed to toggle target: {error_data}")
                
        except Exception as e:
            logger.error(f"Failed to toggle target {target_id}: {e}")
            raise
    
    def get_categories(self) -> Dict[str, Any]:
        """Get all target categories"""
        try:
            response = self._make_request('GET', '/categories')
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 400:
                error_data = response.json()
                raise ValueError(error_data.get('error', 'Database not available'))
            else:
                error_data = response.json() if response.text else {'error': 'Unknown error'}
                raise RuntimeError(f"Failed to get categories: {error_data}")
                
        except Exception as e:
            logger.error(f"Failed to get categories: {e}")
            raise
    
    def get_probes(self) -> Dict[str, Any]:
        """Get all probes"""
        try:
            response = self._make_request('GET', '/probes')
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 400:
                error_data = response.json()
                raise ValueError(error_data.get('error', 'Database not available'))
            else:
                error_data = response.json() if response.text else {'error': 'Unknown error'}
                raise RuntimeError(f"Failed to get probes: {error_data}")
                
        except Exception as e:
            logger.error(f"Failed to get probes: {e}")
            raise


class ConfigAPIGateway:
    """API Gateway for config management operations"""
    
    def __init__(self):
        self.client = ConfigManagerClient()
        
    def is_available(self) -> bool:
        """Check if config-manager is available"""
        try:
            self.client.health_check()
            return True
        except:
            return False
    
    def get_targets_config(self) -> Dict[str, Any]:
        """Get targets configuration"""
        try:
            config = self.client.get_config('targets')
            return config.get('targets', {})
        except Exception as e:
            logger.error(f"Failed to get targets config: {e}")
            # Fallback to local file access if API fails
            return self._fallback_get_targets()
    
    def update_targets_config(self, targets_data: Dict[str, Any]) -> bool:
        """Update targets configuration"""
        try:
            result = self.client.update_config('targets', targets_data)
            return result.get('success', False)
        except Exception as e:
            logger.error(f"Failed to update targets config: {e}")
            # Fallback to local file access if API fails
            return self._fallback_update_targets(targets_data)
    
    def get_sources_config(self) -> Dict[str, Any]:
        """Get sources configuration"""
        try:
            config = self.client.get_config('sources')
            return config.get('sources', {})
        except Exception as e:
            logger.error(f"Failed to get sources config: {e}")
            # Fallback to local file access if API fails
            return self._fallback_get_sources()
    
    def restart_smokeping_service(self) -> Dict[str, str]:
        """Restart SmokePing service"""
        try:
            result = self.client.restart_smokeping()
            return {
                'success': str(result.get('success', False)),
                'message': result.get('message', 'Service restart attempted')
            }
        except Exception as e:
            logger.error(f"Failed to restart SmokePing via API: {e}")
            # Fallback to direct docker command
            return self._fallback_restart_smokeping()
    
    def generate_config(self) -> Dict[str, Any]:
        """Generate SmokePing configuration"""
        try:
            result = self.client.generate_config()
            return {
                'success': result.get('success', False),
                'message': result.get('message', 'Configuration generation attempted')
            }
        except Exception as e:
            logger.error(f"Failed to generate config via API: {e}")
            # Return error - no fallback for config generation
            return {
                'success': False,
                'message': f'Configuration generation failed: {str(e)}'
            }
    
    def refresh_oca_data(self) -> Dict[str, Any]:
        """Refresh OCA data"""
        try:
            result = self.client.refresh_oca()
            return {
                'success': result.get('success', False),
                'message': result.get('message', 'OCA refresh attempted')
            }
        except Exception as e:
            logger.error(f"Failed to refresh OCA via API: {e}")
            # Return error - no fallback for OCA refresh
            return {
                'success': False,
                'message': f'OCA refresh failed: {str(e)}'
            }
    
    def get_service_status(self) -> Dict[str, Any]:
        """Get comprehensive service status"""
        try:
            return self.client.get_status()
        except Exception as e:
            logger.error(f"Failed to get service status: {e}")
            return {
                'status': 'error',
                'error': str(e),
                'config_manager_available': False
            }
    
    # Database-aware methods for target management
    def is_database_available(self) -> bool:
        """Check if database is available"""
        try:
            status = self.get_service_status()
            return status.get('using_database', False)
        except:
            return False
    
    def get_all_targets_from_db(self, active_only: bool = False, category: str = None) -> Dict[str, Any]:
        """Get targets from database"""
        try:
            return self.client.get_targets(active_only=active_only, category=category)
        except Exception as e:
            logger.error(f"Failed to get targets from database: {e}")
            # No fallback for database operations
            raise
    
    def create_target_in_db(self, target_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create new target in database"""
        try:
            return self.client.create_target(target_data)
        except Exception as e:
            logger.error(f"Failed to create target in database: {e}")
            raise
    
    def update_target_in_db(self, target_id: int, target_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update target in database"""
        try:
            return self.client.update_target(target_id, target_data)
        except Exception as e:
            logger.error(f"Failed to update target in database: {e}")
            raise
    
    def delete_target_from_db(self, target_id: int) -> Dict[str, Any]:
        """Delete target from database"""
        try:
            return self.client.delete_target(target_id)
        except Exception as e:
            logger.error(f"Failed to delete target from database: {e}")
            raise
    
    def toggle_target_in_db(self, target_id: int) -> Dict[str, Any]:
        """Toggle target active status in database"""
        try:
            return self.client.toggle_target(target_id)
        except Exception as e:
            logger.error(f"Failed to toggle target in database: {e}")
            raise
    
    def get_categories_from_db(self) -> Dict[str, Any]:
        """Get all target categories from database"""
        try:
            return self.client.get_categories()
        except Exception as e:
            logger.error(f"Failed to get categories from database: {e}")
            raise
    
    def get_probes_from_db(self) -> Dict[str, Any]:
        """Get all probes from database"""
        try:
            return self.client.get_probes()
        except Exception as e:
            logger.error(f"Failed to get probes from database: {e}")
            raise
    
    def update_targets_from_sites(self, sites: list, category: str = 'top_sites') -> Dict[str, Any]:
        """Update targets from selected sites (database-aware)"""
        if not self.is_database_available():
            # Use YAML fallback method
            return self._update_targets_yaml_fallback(sites, category)
        else:
            # Use database method
            return self._update_targets_database(sites, category)
    
    def _normalize_domain_name(self, domain: str) -> str:
        """Normalize domain name for consistent target naming"""
        # Remove protocol if present
        domain = domain.replace('http://', '').replace('https://', '')
        # Remove www prefix for comparison
        domain = domain.replace('www.', '')
        # Clean domain name for SmokePing compatibility
        name = domain.replace('.', '_').replace('-', '_')
        # Ensure name doesn't start with digit
        if name and name[0].isdigit():
            name = f"site_{name}"
        return name[:20]  # Limit name length
    
    def _identify_conflicting_targets(self, existing_targets: Dict[str, Any], new_sites: list) -> list:
        """Identify existing targets that conflict with new site selections"""
        conflicting_ids = []
        
        # Normalize new sites for comparison
        new_normalized = set()
        for site in new_sites:
            normalized = self._normalize_domain_name(site)
            new_normalized.add(normalized)
            # Also add the exact site domain
            new_normalized.add(site.replace('www.', ''))
        
        # Check existing targets for conflicts
        for target in existing_targets.get('targets', []):
            target_host = target.get('host', '')
            target_name = target.get('name', '')
            
            # Normalize target host for comparison
            normalized_host = self._normalize_domain_name(target_host)
            base_host = target_host.replace('www.', '')
            
            # Check if this target conflicts with any new site
            if (normalized_host in new_normalized or 
                base_host in new_normalized or
                target_name in new_normalized):
                conflicting_ids.append(target['id'])
        
        return conflicting_ids
    
    def _preserve_existing_targets(self, existing_targets: Dict[str, Any], new_sites: list) -> list:
        """Get list of existing target IDs that should be preserved (not deactivated)"""
        conflicting_ids = self._identify_conflicting_targets(existing_targets, new_sites)
        
        # Return IDs of targets that should be preserved (active targets that don't conflict)
        preserve_ids = []
        for target in existing_targets.get('targets', []):
            if (target.get('is_active') and 
                target['id'] not in conflicting_ids):
                preserve_ids.append(target['id'])
        
        return preserve_ids
    
    def _update_targets_database(self, sites: list, category: str) -> Dict[str, Any]:
        """Update targets using smart merge (preserves existing, adds new)"""
        try:
            # Get category ID
            categories = self.get_categories_from_db()
            category_mapping = {cat['name']: cat['id'] for cat in categories.get('categories', [])}
            category_id = category_mapping.get(category)
            
            if not category_id:
                return {'success': False, 'message': f'Category {category} not found'}
            
            # Get default probe
            probes = self.get_probes_from_db()
            default_probe = None
            for probe in probes.get('probes', []):
                if probe.get('is_default'):
                    default_probe = probe
                    break
            
            if not default_probe:
                return {'success': False, 'message': 'No default probe found'}
            
            # Get existing targets for this category
            existing_targets = self.get_all_targets_from_db(category=category)
            
            # Smart merge: Only deactivate conflicting targets, preserve others
            conflicting_ids = self._identify_conflicting_targets(existing_targets, sites)
            preserved_count = 0
            deactivated_count = 0
            
            # Deactivate only conflicting targets
            for target_id in conflicting_ids:
                try:
                    target_info = next((t for t in existing_targets.get('targets', []) if t['id'] == target_id), None)
                    if target_info and target_info.get('is_active'):
                        self.toggle_target_in_db(target_id)
                        deactivated_count += 1
                        logger.info(f"Deactivated conflicting target: {target_info.get('name')} ({target_info.get('host')})")
                except Exception as e:
                    logger.warning(f"Failed to deactivate conflicting target {target_id}: {e}")
            
            # Count preserved targets
            for target in existing_targets.get('targets', []):
                if target.get('is_active') and target['id'] not in conflicting_ids:
                    preserved_count += 1
            
            # Create or reactivate targets for new sites
            created_count = 0
            reactivated_count = 0
            
            for idx, site in enumerate(sites[:100]):  # Limit to 100
                # Use normalized naming
                name = self._normalize_domain_name(site)
                
                target_data = {
                    'name': name,
                    'host': site,
                    'title': site,
                    'category_id': category_id,
                    'probe_id': default_probe['id'],
                    'is_active': True
                }
                
                # Check if target already exists (including inactive ones)
                existing_target = None
                for target in existing_targets.get('targets', []):
                    target_host = target.get('host', '')
                    target_name = target.get('name', '')
                    
                    # Check for exact match or normalized match
                    if (target_host == site or 
                        target_name == name or
                        self._normalize_domain_name(target_host) == name):
                        existing_target = target
                        break
                
                if existing_target:
                    # Reactivate existing target if it's inactive
                    if not existing_target.get('is_active'):
                        try:
                            self.toggle_target_in_db(existing_target['id'])
                            reactivated_count += 1
                            logger.info(f"Reactivated existing target: {existing_target.get('name')} ({existing_target.get('host')})")
                        except Exception as e:
                            logger.warning(f"Failed to reactivate target {existing_target['id']}: {e}")
                    # If already active, it's preserved (counted above)
                else:
                    # Create new target
                    try:
                        self.create_target_in_db(target_data)
                        created_count += 1
                        logger.info(f"Created new target: {name} ({site})")
                    except Exception as e:
                        logger.warning(f"Failed to create target for {site}: {e}")
            
            total_active = preserved_count + created_count + reactivated_count
            
            return {
                'success': True,
                'message': f'Smart merge complete: {preserved_count} preserved, {created_count} created, {reactivated_count} reactivated, {deactivated_count} deactivated',
                'total_targets': total_active,
                'preserved': preserved_count,
                'created': created_count,
                'reactivated': reactivated_count,
                'deactivated': deactivated_count
            }
            
        except Exception as e:
            logger.error(f"Failed to update targets in database: {e}")
            return {'success': False, 'message': f'Database update failed: {str(e)}'}
    
    def _update_targets_yaml_fallback(self, sites: list, category: str) -> Dict[str, Any]:
        """Fallback to YAML method for updating targets with smart merge"""
        try:
            # Load current targets via config API
            targets_data = self.get_targets_config()
            
            # Ensure active_targets structure exists
            if 'active_targets' not in targets_data:
                targets_data['active_targets'] = {}
            if category not in targets_data['active_targets']:
                targets_data['active_targets'][category] = []
            
            existing_targets = targets_data['active_targets'][category]
            
            # Convert sites to normalized names for comparison
            new_sites_normalized = {}
            for site in sites:
                normalized = self._normalize_domain_name(site)
                new_sites_normalized[normalized] = site
            
            # Smart merge: preserve existing targets that don't conflict
            preserved_targets = []
            conflicting_targets = []
            
            for existing_target in existing_targets:
                existing_host = existing_target.get('host', '')
                existing_name = existing_target.get('name', '')
                existing_normalized = self._normalize_domain_name(existing_host)
                
                # Check if this target conflicts with new selections
                conflicts = False
                for norm_name, site in new_sites_normalized.items():
                    if (existing_normalized == norm_name or 
                        existing_name == norm_name or
                        existing_host == site or
                        existing_host.replace('www.', '') == site.replace('www.', '')):
                        conflicts = True
                        conflicting_targets.append(existing_target)
                        break
                
                if not conflicts:
                    preserved_targets.append(existing_target)
            
            # Create new targets for selected sites
            new_targets = []
            created_count = 0
            reactivated_count = 0
            
            for site in sites:
                name = self._normalize_domain_name(site)
                
                # Check if this site already exists in preserved targets
                already_exists = False
                for preserved in preserved_targets:
                    if (preserved.get('host') == site or 
                        preserved.get('name') == name or
                        self._normalize_domain_name(preserved.get('host', '')) == name):
                        already_exists = True
                        break
                
                # Check if this site exists in conflicting targets (to reactivate)
                reactivated = False
                for conflicting in conflicting_targets:
                    if (conflicting.get('host') == site or 
                        conflicting.get('name') == name or
                        self._normalize_domain_name(conflicting.get('host', '')) == name):
                        # Reactivate this target
                        new_targets.append(conflicting)
                        reactivated_count += 1
                        reactivated = True
                        break
                
                if not already_exists and not reactivated:
                    # Create new target
                    new_targets.append({
                        'name': name,
                        'host': site,
                        'title': site,
                        'probe': 'FPing',
                        'category': category
                    })
                    created_count += 1
            
            # Combine preserved and new targets
            final_targets = preserved_targets + new_targets
            preserved_count = len(preserved_targets)
            deactivated_count = len(conflicting_targets) - reactivated_count
            
            # Update category section with smart-merged targets
            targets_data['active_targets'][category] = final_targets
            
            # Update metadata
            if 'metadata' not in targets_data:
                targets_data['metadata'] = {}
            targets_data['metadata']['last_updated'] = datetime.now().isoformat()
            total_targets = sum(
                len(v) for v in targets_data['active_targets'].values() 
                if isinstance(v, list)
            )
            targets_data['metadata']['total_targets'] = total_targets
            
            # Save configuration via config API
            success = self.update_targets_config(targets_data)
            
            if success:
                return {
                    'success': True,
                    'message': f'YAML smart merge complete: {preserved_count} preserved, {created_count} created, {reactivated_count} reactivated, {deactivated_count} deactivated',
                    'total_targets': len(final_targets),
                    'preserved': preserved_count,
                    'created': created_count,
                    'reactivated': reactivated_count,
                    'deactivated': deactivated_count
                }
            else:
                return {
                    'success': False,
                    'message': 'Failed to update YAML configuration'
                }
        
        except Exception as e:
            logger.error(f"YAML fallback failed: {e}")
            return {'success': False, 'message': f'YAML update failed: {str(e)}'}
    
    def _fallback_get_targets(self) -> Dict[str, Any]:
        """Fallback method to get targets from local config"""
        try:
            import yaml
            from pathlib import Path
            
            config_file = Path('/app/config/targets.yaml')
            if config_file.exists():
                with open(config_file, 'r') as f:
                    return yaml.safe_load(f)
            else:
                logger.warning("Targets config file not found")
                return {}
        except Exception as e:
            logger.error(f"Fallback get targets failed: {e}")
            return {}
    
    def _fallback_update_targets(self, targets_data: Dict[str, Any]) -> bool:
        """Fallback method to update targets in local config"""
        try:
            import yaml
            from pathlib import Path
            
            config_file = Path('/app/config/targets.yaml')
            with open(config_file, 'w') as f:
                yaml.dump(targets_data, f, default_flow_style=False, sort_keys=False)
            return True
        except Exception as e:
            logger.error(f"Fallback update targets failed: {e}")
            return False
    
    def _fallback_get_sources(self) -> Dict[str, Any]:
        """Fallback method to get sources from local config"""
        try:
            import yaml
            from pathlib import Path
            
            config_file = Path('/app/config/sources.yaml')
            if config_file.exists():
                with open(config_file, 'r') as f:
                    return yaml.safe_load(f)
            else:
                logger.warning("Sources config file not found")
                return {}
        except Exception as e:
            logger.error(f"Fallback get sources failed: {e}")
            return {}
    
    def _fallback_restart_smokeping(self) -> Dict[str, str]:
        """Fallback method to restart SmokePing directly"""
        try:
            import subprocess
            
            result = subprocess.run([
                'docker', 'restart', 'grafana-influx-smokeping-1'
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                return {
                    'success': 'True',
                    'message': 'SmokePing restarted successfully (fallback)'
                }
            else:
                return {
                    'success': 'False',
                    'message': f'SmokePing restart failed: {result.stderr}'
                }
        except Exception as e:
            logger.error(f"Fallback restart failed: {e}")
            return {
                'success': 'False',
                'message': f'SmokePing restart failed: {str(e)}'
            }