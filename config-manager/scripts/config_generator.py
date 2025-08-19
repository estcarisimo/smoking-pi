#!/usr/bin/env python3
"""
SmokePing Configuration Generator
Generates SmokePing Targets and Probes files from YAML configuration
"""

import argparse
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import yaml
from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from .bootstrap import validate_all_configs, run_bootstrap

# Import database models if available
try:
    # Add parent directory to path for imports
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    
    from models import (
        get_db_session, Target, TargetCategory, Probe, SystemMetadata,
        TargetRepository, CategoryRepository, ProbeRepository
    )
    DATABASE_AVAILABLE = True
except ImportError as e:
    DATABASE_AVAILABLE = False

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration paths
BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = BASE_DIR / "config"
TEMPLATE_DIR = BASE_DIR / "templates"
OUTPUT_DIR = BASE_DIR / "output"

# SmokePing config paths for different variants
SMOKEPING_CONFIGS = {
    'minimal': Path("/home/smokingpi/smoking-pi/minimal/config"),
    'grafana-influx': 'docker_container'  # Special marker for Docker deployment
}


class ConfigGenerator:
    """Generates SmokePing configuration files from YAML or database sources"""
    
    def __init__(self):
        self.env = Environment(
            loader=FileSystemLoader(TEMPLATE_DIR),
            trim_blocks=True,
            lstrip_blocks=True
        )
        self.targets_config = None
        self.probes_config = None
        self.use_database = self._check_database_available()
        logger.info(f"Config generator initialized with database support: {self.use_database}")
    
    def _check_database_available(self) -> bool:
        """Check if database is available and has migrated data"""
        if not DATABASE_AVAILABLE:
            return False
        
        try:
            session = get_db_session()
            try:
                # Check if migration marker exists
                marker = session.query(SystemMetadata).filter(
                    SystemMetadata.key == 'yaml_migration_completed'
                ).first()
                return marker is not None
            finally:
                session.close()
        except Exception as e:
            logger.warning(f"Database not available: {e}")
            return False
    
    def load_configurations(self) -> bool:
        """Load configuration from database or YAML files"""
        if self.use_database:
            return self._load_from_database()
        else:
            return self._load_from_yaml()
    
    def _load_from_database(self) -> bool:
        """Load configuration from PostgreSQL database"""
        try:
            session = get_db_session()
            try:
                target_repo = TargetRepository(session)
                targets = target_repo.get_all(active_only=True)
                
                # Convert database targets to YAML-like structure
                active_targets = {}
                for target in targets:
                    category_name = target.category.name
                    if category_name not in active_targets:
                        active_targets[category_name] = []
                    
                    target_dict = {
                        'name': target.name,
                        'host': target.host,
                        'title': target.title,
                        'probe': target.probe.name
                    }
                    
                    if target.lookup:
                        target_dict['lookup'] = target.lookup
                    
                    # Add Netflix OCA metadata if present
                    if target.asn or target.cache_id or target.city:
                        target_dict['metadata'] = {
                            'asn': target.asn,
                            'cache_id': target.cache_id,
                            'city': target.city,
                            'domain': target.domain,
                            'iata_code': target.iata_code,
                            'latitude': float(target.latitude) if target.latitude else None,
                            'longitude': float(target.longitude) if target.longitude else None,
                            'location_code': target.location_code,
                            'raw_city': target.raw_city,
                            'type': target.metadata_type
                        }
                    
                    active_targets[category_name].append(target_dict)
                
                self.targets_config = {
                    'active_targets': active_targets,
                    'metadata': {
                        'source': 'database',
                        'total_targets': len(targets),
                        'last_updated': datetime.now().isoformat()
                    }
                }
                
                # Load probes from database
                probes = session.query(Probe).all()
                probes_config = {}
                default_probe = None
                
                for probe in probes:
                    probe_dict = {
                        'binary': probe.binary_path,
                        'step': probe.step_seconds,
                        'pings': probe.pings
                    }
                    if probe.forks:
                        probe_dict['forks'] = probe.forks
                    
                    probes_config[probe.name] = probe_dict
                    
                    if probe.is_default:
                        default_probe = probe.name
                
                self.probes_config = {
                    'probes': probes_config,
                    'default_probe': default_probe or 'FPing'
                }
                
                logger.info(f"Successfully loaded configuration from database: {len(targets)} targets, {len(probes)} probes")
                return True
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Failed to load from database, falling back to YAML: {e}")
            self.use_database = False
            return self._load_from_yaml()
    
    def _load_from_yaml(self) -> bool:
        """Load configuration from YAML files (fallback)"""
        try:
            # Load targets configuration
            with open(CONFIG_DIR / "targets.yaml", 'r') as f:
                self.targets_config = yaml.safe_load(f)
            
            # Load probes configuration
            with open(CONFIG_DIR / "probes.yaml", 'r') as f:
                self.probes_config = yaml.safe_load(f)
            
            logger.info("Successfully loaded configuration from YAML files")
            return True
            
        except FileNotFoundError as e:
            logger.error(f"Configuration file not found: {e}")
            return False
        except yaml.YAMLError as e:
            logger.error(f"Invalid YAML in configuration: {e}")
            return False
    
    def generate_targets_file(self) -> Optional[str]:
        """Generate SmokePing Targets configuration"""
        try:
            template = self.env.get_template("smokeping_targets.j2")
            
            # Prepare context for template
            context = {
                'targets': self.targets_config['active_targets'],
                'default_probe': self.probes_config.get('default_probe', 'FPing'),
                'generated_at': datetime.now().isoformat()
            }
            
            # Render template
            content = template.render(**context)
            
            logger.info("Successfully generated Targets configuration")
            return content
            
        except TemplateNotFound:
            logger.error("Template file not found: smokeping_targets.j2")
            return None
        except Exception as e:
            logger.error(f"Failed to generate Targets file: {e}")
            return None
    
    def generate_probes_file(self) -> str:
        """Generate SmokePing Probes configuration"""
        lines = ["*** Probes ***", ""]
        
        for probe_name, probe_config in self.probes_config['probes'].items():
            lines.append(f"+ {probe_name}")
            
            for key, value in probe_config.items():
                lines.append(f"{key} = {value}")
            
            lines.append("")
        
        content = "\n".join(lines)
        logger.info("Successfully generated Probes configuration")
        return content
    
    def validate_configuration(self) -> bool:
        """Validate the generated configuration"""
        # Check for required sections
        if not self.targets_config or 'active_targets' not in self.targets_config:
            logger.error("Missing active_targets in configuration")
            return False
        
        # Check for at least one target
        total_targets = sum(
            len(targets) for targets in self.targets_config['active_targets'].values()
            if isinstance(targets, list)
        )
        
        if total_targets == 0:
            logger.warning("No active targets configured")
        
        # Validate probe references
        available_probes = set(self.probes_config['probes'].keys())
        
        for category, targets in self.targets_config['active_targets'].items():
            if not isinstance(targets, list):
                continue
                
            for target in targets:
                probe = target.get('probe', self.probes_config.get('default_probe'))
                if probe and probe not in available_probes:
                    logger.error(f"Target {target.get('name')} references unknown probe: {probe}")
                    return False
        
        logger.info(f"Configuration validated: {total_targets} targets configured")
        return True
    
    def write_output_files(self, targets_content: str, probes_content: str) -> bool:
        """Write generated configuration to output directory"""
        try:
            # Create output directory
            OUTPUT_DIR.mkdir(exist_ok=True)
            
            # Write Targets file
            targets_file = OUTPUT_DIR / "Targets"
            with open(targets_file, 'w') as f:
                f.write(targets_content)
            logger.info(f"Written Targets file to {targets_file}")
            
            # Write Probes file
            probes_file = OUTPUT_DIR / "Probes"
            with open(probes_file, 'w') as f:
                f.write(probes_content)
            logger.info(f"Written Probes file to {probes_file}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to write output files: {e}")
            return False
    
    def deploy_to_variant(self, variant: str, dry_run: bool = False) -> bool:
        """Deploy configuration files to a specific SmokePing variant"""
        if variant not in SMOKEPING_CONFIGS:
            logger.error(f"Unknown variant: {variant}")
            return False
        
        config_path = SMOKEPING_CONFIGS[variant]
        
        if dry_run:
            logger.info(f"[DRY RUN] Would deploy to {variant}")
            return True
        
        try:
            if variant == 'grafana-influx':
                # Deploy to Docker container
                import subprocess
                
                # Copy Targets file to SmokePing container
                targets_result = subprocess.run([
                    'docker', 'cp', 
                    str(OUTPUT_DIR / "Targets"),
                    'grafana-influx-smokeping-1:/etc/smokeping/config.d/Targets'
                ], capture_output=True, text=True, timeout=30)
                
                if targets_result.returncode != 0:
                    logger.error(f"Failed to copy Targets file: {targets_result.stderr}")
                    return False
                
                # Copy Probes file to SmokePing container
                probes_result = subprocess.run([
                    'docker', 'cp', 
                    str(OUTPUT_DIR / "Probes"),
                    'grafana-influx-smokeping-1:/etc/smokeping/config.d/Probes'
                ], capture_output=True, text=True, timeout=30)
                
                if probes_result.returncode != 0:
                    logger.error(f"Failed to copy Probes file: {probes_result.stderr}")
                    return False
                
                logger.info(f"Successfully deployed configuration to {variant} container")
                
                # Restart SmokePing service to reload configuration
                restart_result = subprocess.run([
                    'docker', 'exec', 'grafana-influx-smokeping-1',
                    'service', 'smokeping', 'restart'
                ], capture_output=True, text=True, timeout=30)
                
                if restart_result.returncode == 0:
                    logger.info("SmokePing service restarted successfully")
                else:
                    logger.warning(f"SmokePing service restart failed: {restart_result.stderr}")
                
                return True
                
            else:
                # Traditional file copy for non-Docker variants
                if not config_path.exists():
                    logger.error(f"Configuration directory does not exist: {config_path}")
                    return False
                
                # Copy generated files
                shutil.copy2(OUTPUT_DIR / "Targets", config_path / "Targets")
                shutil.copy2(OUTPUT_DIR / "Probes", config_path / "Probes")
                
                logger.info(f"Successfully deployed configuration to {variant}")
                return True
            
        except Exception as e:
            logger.error(f"Failed to deploy to {variant}: {e}")
            return False
    
    def run(self, deploy_to: Optional[str] = None, dry_run: bool = False) -> bool:
        """Main execution flow"""
        # For YAML mode, ensure config files exist and are valid
        if not self.use_database:
            if not validate_all_configs():
                logger.warning("Some config files are invalid, attempting bootstrap recovery...")
                if not run_bootstrap():
                    logger.error("Bootstrap recovery failed")
                    return False
        
        # Load configurations (from database or YAML)
        if not self.load_configurations():
            return False
        
        # Validate configuration
        if not self.validate_configuration():
            return False
        
        # Generate Targets file
        targets_content = self.generate_targets_file()
        if not targets_content:
            return False
        
        # Generate Probes file
        probes_content = self.generate_probes_file()
        
        # Write output files
        if not self.write_output_files(targets_content, probes_content):
            return False
        
        # Deploy if requested
        if deploy_to:
            variants = [deploy_to] if deploy_to != 'all' else list(SMOKEPING_CONFIGS.keys())
            
            for variant in variants:
                if not self.deploy_to_variant(variant, dry_run):
                    return False
        
        return True


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Generate SmokePing configuration from YAML sources"
    )
    parser.add_argument(
        '--deploy-to',
        choices=['minimal', 'grafana-influx', 'all'],
        help='Deploy generated configuration to SmokePing variant'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without making changes'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    generator = ConfigGenerator()
    
    try:
        success = generator.run(
            deploy_to=args.deploy_to,
            dry_run=args.dry_run
        )
        
        if success:
            logger.info("Configuration generation completed successfully")
            sys.exit(0)
        else:
            logger.error("Configuration generation failed")
            sys.exit(1)
            
    except KeyboardInterrupt:
        logger.info("Process interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()