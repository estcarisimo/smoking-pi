#!/usr/bin/env python3
"""
Config Manager REST API
Provides REST interface for SmokePing configuration management
"""

import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
import yaml
import subprocess
import time

from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from werkzeug.exceptions import BadRequest

# Import our existing config generator and bootstrap
from scripts.config_generator import ConfigGenerator
from scripts.bootstrap import run_bootstrap, validate_all_configs

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Configuration paths
CONFIG_DIR = Path("/app/config")
OUTPUT_DIR = Path("/app/output")

# Run bootstrap to ensure config files exist
logger.info("Running configuration bootstrap...")
if not run_bootstrap():
    logger.error("Bootstrap failed - some config files may be missing")
else:
    logger.info("Bootstrap completed successfully")


class ConfigManagerAPI:
    """REST API for SmokePing configuration management"""
    
    def __init__(self):
        self.generator = ConfigGenerator()
        
    def get_config(self, config_type: str = 'all') -> Dict[str, Any]:
        """Get current configuration"""
        try:
            result = {}
            
            if config_type in ['all', 'targets']:
                with open(CONFIG_DIR / "targets.yaml", 'r') as f:
                    result['targets'] = yaml.safe_load(f)
            
            if config_type in ['all', 'probes']:
                with open(CONFIG_DIR / "probes.yaml", 'r') as f:
                    result['probes'] = yaml.safe_load(f)
                    
            if config_type in ['all', 'sources']:
                with open(CONFIG_DIR / "sources.yaml", 'r') as f:
                    result['sources'] = yaml.safe_load(f)
            
            # Add metadata
            result['metadata'] = {
                'last_modified': self._get_last_modified(),
                'generated_at': datetime.now().isoformat(),
                'config_type': config_type
            }
            
            return result
            
        except FileNotFoundError as e:
            raise ValueError(f"Configuration file not found: {e}")
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML configuration: {e}")
    
    def update_config(self, config_type: str, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update configuration"""
        try:
            config_file = CONFIG_DIR / f"{config_type}.yaml"
            
            # Validate the configuration data
            if config_type == 'targets':
                self._validate_targets_config(config_data)
            elif config_type == 'probes':
                self._validate_probes_config(config_data)
            elif config_type == 'sources':
                self._validate_sources_config(config_data)
            else:
                raise ValueError(f"Unknown configuration type: {config_type}")
            
            # Backup existing config
            backup_file = config_file.with_suffix(f'.yaml.backup.{int(time.time())}')
            if config_file.exists():
                backup_file.write_text(config_file.read_text())
                logger.info(f"Backed up {config_file} to {backup_file}")
            
            # Write new configuration
            with open(config_file, 'w') as f:
                yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)
            
            logger.info(f"Updated {config_type} configuration")
            
            # Generate and deploy new SmokePing configuration
            self._regenerate_smokeping_config()
            
            return {
                'success': True,
                'message': f'{config_type} configuration updated successfully',
                'updated_at': datetime.now().isoformat(),
                'backup_file': str(backup_file)
            }
            
        except Exception as e:
            logger.error(f"Failed to update {config_type} configuration: {e}")
            raise
    
    def generate_smokeping_config(self) -> Dict[str, Any]:
        """Generate SmokePing configuration files"""
        try:
            success = self.generator.run(deploy_to='grafana-influx')
            
            if success:
                return {
                    'success': True,
                    'message': 'SmokePing configuration generated and deployed',
                    'generated_at': datetime.now().isoformat()
                }
            else:
                raise RuntimeError("Configuration generation failed")
                
        except Exception as e:
            logger.error(f"Failed to generate SmokePing configuration: {e}")
            raise
    
    def restart_smokeping(self) -> Dict[str, Any]:
        """Restart SmokePing service"""
        try:
            # Try to restart via docker-compose
            result = subprocess.run([
                'docker', 'restart', 'grafana-influx-smokeping-1'
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                logger.info("SmokePing service restarted successfully")
                return {
                    'success': True,
                    'message': 'SmokePing service restarted',
                    'restarted_at': datetime.now().isoformat()
                }
            else:
                raise RuntimeError(f"Docker restart failed: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            raise RuntimeError("SmokePing restart timed out")
        except Exception as e:
            logger.error(f"Failed to restart SmokePing: {e}")
            raise
    
    def get_status(self) -> Dict[str, Any]:
        """Get service status"""
        try:
            # Check if config files exist
            configs_exist = {
                'targets': (CONFIG_DIR / "targets.yaml").exists(),
                'probes': (CONFIG_DIR / "probes.yaml").exists(), 
                'sources': (CONFIG_DIR / "sources.yaml").exists()
            }
            
            # Check if generated files exist
            generated_exist = {
                'targets_file': (OUTPUT_DIR / "Targets").exists(),
                'probes_file': (OUTPUT_DIR / "Probes").exists()
            }
            
            # Check SmokePing container status
            smokeping_status = self._check_smokeping_status()
            
            return {
                'status': 'healthy' if all(configs_exist.values()) else 'partial',
                'configs': configs_exist,
                'generated_files': generated_exist,
                'smokeping': smokeping_status,
                'last_check': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to get status: {e}")
            return {
                'status': 'error',
                'error': str(e),
                'last_check': datetime.now().isoformat()
            }
    
    def _validate_targets_config(self, config: Dict[str, Any]) -> None:
        """Validate targets configuration"""
        if 'active_targets' not in config:
            raise ValueError("Missing 'active_targets' in configuration")
        
        if 'metadata' not in config:
            config['metadata'] = {}
        
        # Update metadata
        config['metadata']['last_updated'] = datetime.now().isoformat()
        
        # Count total targets
        total_targets = 0
        for category, targets in config['active_targets'].items():
            if isinstance(targets, list):
                total_targets += len(targets)
                
                # Validate each target
                for target in targets:
                    if not isinstance(target, dict):
                        raise ValueError(f"Invalid target format in {category}")
                    if 'name' not in target or 'host' not in target:
                        raise ValueError(f"Target missing required fields (name, host) in {category}")
        
        config['metadata']['total_targets'] = total_targets
    
    def _validate_probes_config(self, config: Dict[str, Any]) -> None:
        """Validate probes configuration"""
        if 'probes' not in config:
            raise ValueError("Missing 'probes' in configuration")
        
        if not config['probes']:
            raise ValueError("At least one probe must be configured")
    
    def _validate_sources_config(self, config: Dict[str, Any]) -> None:
        """Validate sources configuration"""
        # Sources config is more flexible, just ensure it's valid YAML
        if not isinstance(config, dict):
            raise ValueError("Sources configuration must be a dictionary")
    
    def _regenerate_smokeping_config(self) -> None:
        """Regenerate and deploy SmokePing configuration"""
        try:
            self.generator.run(deploy_to='grafana-influx')
            logger.info("SmokePing configuration regenerated")
        except Exception as e:
            logger.error(f"Failed to regenerate SmokePing config: {e}")
            raise
    
    def _get_last_modified(self) -> str:
        """Get the last modification time of config files"""
        try:
            config_files = [
                CONFIG_DIR / "targets.yaml",
                CONFIG_DIR / "probes.yaml", 
                CONFIG_DIR / "sources.yaml"
            ]
            
            latest_time = 0
            for config_file in config_files:
                if config_file.exists():
                    latest_time = max(latest_time, config_file.stat().st_mtime)
            
            return datetime.fromtimestamp(latest_time).isoformat()
        except:
            return datetime.now().isoformat()
    
    def _check_smokeping_status(self) -> Dict[str, Any]:
        """Check SmokePing container status"""
        try:
            result = subprocess.run([
                'docker', 'ps', '--filter', 'name=grafana-influx-smokeping-1', '--format', '{{.Names}}'
            ], capture_output=True, text=True, timeout=10)
            
            logger.info(f"SmokePing status check - Return code: {result.returncode}, Stdout: {repr(result.stdout)}")
            
            if result.returncode == 0 and 'grafana-influx-smokeping-1' in result.stdout:
                logger.info("SmokePing detected as running")
                return {
                    'running': True,
                    'status': 'running',
                    'created': 'unknown'  # Could be enhanced to get creation time
                }
            else:
                logger.warning("SmokePing not detected as running")
                return {
                    'running': False,
                    'status': 'not_found'
                }
        except Exception as e:
            logger.error(f"Error checking SmokePing status: {e}")
            return {
                'running': False,
                'status': 'unknown'
            }


# Initialize API instance
api = ConfigManagerAPI()


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'config-manager',
        'timestamp': datetime.now().isoformat()
    })


@app.route('/status', methods=['GET'])
def get_status():
    """Get service status"""
    try:
        status = api.get_status()
        return jsonify(status)
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        return jsonify({
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500


@app.route('/config', methods=['GET'])
@app.route('/config/<config_type>', methods=['GET'])
def get_config(config_type='all'):
    """Get configuration"""
    try:
        config = api.get_config(config_type)
        return jsonify(config)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Failed to get config: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/config/<config_type>', methods=['PUT'])
def update_config(config_type):
    """Update configuration"""
    try:
        if not request.is_json:
            raise BadRequest("Content-Type must be application/json")
        
        config_data = request.get_json()
        if not config_data:
            raise BadRequest("Empty request body")
        
        result = api.update_config(config_type, config_data)
        return jsonify(result)
        
    except BadRequest as e:
        return jsonify({'error': str(e)}), 400
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Failed to update config: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/generate', methods=['POST'])
def generate_config():
    """Generate SmokePing configuration"""
    try:
        result = api.generate_smokeping_config()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Failed to generate config: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/restart', methods=['POST'])
def restart_smokeping():
    """Restart SmokePing service"""
    try:
        result = api.restart_smokeping()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Failed to restart SmokePing: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/oca/refresh', methods=['POST'])
def refresh_oca():
    """Refresh OCA (Open Connect Appliance) data"""
    try:
        # Run OCA fetcher script
        result = subprocess.run([
            'python3', '/app/scripts/oca_fetcher.py'
        ], capture_output=True, text=True, timeout=60)
        
        if result.returncode == 0:
            return jsonify({
                'success': True,
                'message': 'OCA data refreshed successfully',
                'output': result.stdout,
                'refreshed_at': datetime.now().isoformat()
            })
        else:
            raise RuntimeError(f"OCA refresh failed: {result.stderr}")
            
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'OCA refresh timed out'}), 500
    except Exception as e:
        logger.error(f"OCA refresh failed: {e}")
        return jsonify({'error': str(e)}), 500


@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404


@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({'error': 'Method not allowed'}), 405


@app.route('/docs')
def api_documentation():
    """API documentation endpoint"""
    return """
    <h1>SmokePing Config Manager API</h1>
    <p>REST API for managing SmokePing configuration</p>
    <h2>Available Endpoints:</h2>
    <ul>
        <li><strong>GET /health</strong> - Health check</li>
        <li><strong>GET /status</strong> - Service status</li>
        <li><strong>GET /config</strong> - Get all configurations</li>
        <li><strong>GET /config/{type}</strong> - Get specific configuration (targets, probes, sources)</li>
        <li><strong>PUT /config/{type}</strong> - Update specific configuration</li>
        <li><strong>POST /generate</strong> - Generate SmokePing configuration</li>
        <li><strong>POST /restart</strong> - Restart SmokePing service</li>
        <li><strong>POST /oca/refresh</strong> - Refresh OCA data</li>
    </ul>
    <p>For detailed API documentation with schemas, run the api_docs.py server on port 5001</p>
    """


@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500


if __name__ == '__main__':
    logger.info("Starting Config Manager REST API")
    app.run(host='0.0.0.0', port=5000, debug=False)