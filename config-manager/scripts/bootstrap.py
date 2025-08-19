#!/usr/bin/env python3
"""
Bootstrap Module for Config Manager
Handles first-run setup and config file recovery per TODO-218.md
"""

import logging
import shutil
import subprocess
import sys
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Setup logging
logger = logging.getLogger(__name__)

# Configuration paths
BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = BASE_DIR / "config"
TEMPLATE_DIR = BASE_DIR / "templates"
OUTPUT_DIR = BASE_DIR / "output"

# Required configuration files
REQUIRED_CONFIGS = [
    "targets.yaml",
    "probes.yaml", 
    "sources.yaml"
]


class ConfigBootstrap:
    """Handles bootstrap and recovery of configuration files"""
    
    def __init__(self):
        self.config_dir = CONFIG_DIR
        self.template_dir = TEMPLATE_DIR
        
    def ensure_directories(self) -> None:
        """Ensure all required directories exist"""
        for directory in [self.config_dir, self.template_dir, OUTPUT_DIR]:
            directory.mkdir(exist_ok=True, parents=True)
            logger.debug(f"Ensured directory exists: {directory}")
    
    def check_missing_configs(self) -> List[str]:
        """Return list of missing configuration files"""
        missing = []
        for config_name in REQUIRED_CONFIGS:
            config_path = self.config_dir / config_name
            if not config_path.exists():
                missing.append(config_name)
        return missing
    
    def validate_config_file(self, config_name: str) -> bool:
        """Validate that a config file exists and contains valid YAML"""
        try:
            config_path = self.config_dir / config_name
            if not config_path.exists():
                return False
                
            with open(config_path, 'r') as f:
                yaml.safe_load(f)  # Will raise exception if invalid YAML
            
            return True
            
        except yaml.YAMLError as e:
            logger.warning(f"Invalid YAML in {config_name}: {e}")
            return False
        except Exception as e:
            logger.error(f"Error validating {config_name}: {e}")
            return False
    
    def copy_template(self, config_name: str) -> bool:
        """Copy template file to config directory"""
        try:
            template_path = self.template_dir / config_name
            config_path = self.config_dir / config_name
            
            if not template_path.exists():
                logger.error(f"Template not found: {template_path}")
                return False
            
            # Create backup if config exists
            if config_path.exists():
                backup_path = config_path.with_suffix(
                    f'.yaml.backup.{int(datetime.now().timestamp())}'
                )
                shutil.copy2(config_path, backup_path)
                logger.info(f"Backed up existing {config_name} to {backup_path.name}")
            
            # Copy template
            shutil.copy2(template_path, config_path)
            logger.info(f"Bootstrap → created {config_path}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to copy template {config_name}: {e}")
            return False
    
    def generate_smokeping_config(self) -> bool:
        """Generate SmokePing configuration files using config generator"""
        try:
            logger.info("Generating SmokePing configuration files...")
            
            # Run config generator with deployment to grafana-influx
            result = subprocess.run([
                sys.executable, 
                str(BASE_DIR / "scripts" / "config_generator.py"),
                "--deploy-to", "grafana-influx"
            ], 
            cwd=BASE_DIR,
            capture_output=True, 
            text=True, 
            timeout=60
            )
            
            if result.returncode == 0:
                logger.info("Successfully generated and deployed SmokePing configuration")
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
    
    def update_targets_metadata(self) -> None:
        """Update targets.yaml metadata after bootstrap"""
        try:
            targets_path = self.config_dir / "targets.yaml"
            if not targets_path.exists():
                return
                
            with open(targets_path, 'r') as f:
                targets_config = yaml.safe_load(f)
            
            # Update metadata
            if 'metadata' not in targets_config:
                targets_config['metadata'] = {}
                
            targets_config['metadata']['last_updated'] = datetime.now().isoformat()
            targets_config['metadata']['bootstrap_completed'] = True
            
            # Calculate total targets
            total_targets = 0
            for category, targets in targets_config.get('active_targets', {}).items():
                if isinstance(targets, list):
                    total_targets += len(targets)
            
            targets_config['metadata']['total_targets'] = total_targets
            
            # Write back
            with open(targets_path, 'w') as f:
                yaml.dump(targets_config, f, default_flow_style=False, sort_keys=False)
                
            logger.debug(f"Updated targets metadata: {total_targets} targets")
            
        except Exception as e:
            logger.error(f"Failed to update targets metadata: {e}")
    
    def bootstrap_missing_configs(self, force: bool = False) -> Dict[str, bool]:
        """Bootstrap all missing or invalid configuration files"""
        results = {}
        
        # Ensure directories exist
        self.ensure_directories()
        
        # Check each required config
        for config_name in REQUIRED_CONFIGS:
            needs_bootstrap = False
            
            if force:
                needs_bootstrap = True
                logger.info(f"Force bootstrap requested for {config_name}")
            elif not (self.config_dir / config_name).exists():
                needs_bootstrap = True
                logger.info(f"Missing config file: {config_name}")
            elif not self.validate_config_file(config_name):
                needs_bootstrap = True
                logger.warning(f"Invalid config file: {config_name}")
            
            if needs_bootstrap:
                results[config_name] = self.copy_template(config_name)
                if results[config_name]:
                    logger.info(f"Successfully bootstrapped {config_name}")
                else:
                    logger.error(f"Failed to bootstrap {config_name}")
            else:
                results[config_name] = True  # Already exists and valid
                logger.debug(f"Config file OK: {config_name}")
        
        # Update targets metadata if targets.yaml was bootstrapped
        if results.get('targets.yaml', False):
            self.update_targets_metadata()
        
        return results
    
    def run_bootstrap(self, force: bool = False) -> bool:
        """Main bootstrap entry point"""
        logger.info("Starting configuration bootstrap...")
        
        # Check what's missing
        missing = self.check_missing_configs()
        if missing and not force:
            logger.info(f"Missing config files: {missing}")
        elif force:
            logger.info("Force bootstrap requested for all configs")
        elif not missing:
            logger.info("All config files present, checking validity...")
        
        # Bootstrap configs
        results = self.bootstrap_missing_configs(force=force)
        
        # Report results
        success_count = sum(1 for success in results.values() if success)
        total_count = len(results)
        
        if success_count == total_count:
            logger.info(f"Bootstrap completed successfully ({success_count}/{total_count} configs OK)")
            
            # Generate SmokePing configuration
            if self.generate_smokeping_config():
                logger.info("SmokePing configuration generated and deployed successfully")
            else:
                logger.warning("SmokePing configuration generation failed, but bootstrap was successful")
                
            return True
        else:
            logger.error(f"Bootstrap completed with errors ({success_count}/{total_count} configs OK)")
            return False


def run_bootstrap(force: bool = False) -> bool:
    """Convenience function to run bootstrap"""
    bootstrap = ConfigBootstrap()
    return bootstrap.run_bootstrap(force=force)


def validate_all_configs() -> bool:
    """Validate all configuration files"""
    bootstrap = ConfigBootstrap()
    
    all_valid = True
    for config_name in REQUIRED_CONFIGS:
        if not bootstrap.validate_config_file(config_name):
            all_valid = False
            
    return all_valid


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Bootstrap config files")
    parser.add_argument("--force", action="store_true", 
                       help="Force recreation of all config files")
    parser.add_argument("--validate", action="store_true",
                       help="Only validate existing configs")
    parser.add_argument("--verbose", action="store_true",
                       help="Enable verbose logging")
    
    args = parser.parse_args()
    
    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    if args.validate:
        if validate_all_configs():
            print("All configuration files are valid")
            exit(0)
        else:
            print("Some configuration files are invalid")
            exit(1)
    else:
        if run_bootstrap(force=args.force):
            exit(0)
        else:
            exit(1)