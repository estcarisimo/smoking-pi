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