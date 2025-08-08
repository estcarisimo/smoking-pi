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
    """Generates SmokePing configuration files from YAML sources"""
    
    def __init__(self):
        self.env = Environment(
            loader=FileSystemLoader(TEMPLATE_DIR),
            trim_blocks=True,
            lstrip_blocks=True
        )
        self.targets_config = None
        self.probes_config = None
    
    def load_configurations(self) -> bool:
        """Load YAML configuration files"""
        try:
            # Load targets configuration
            with open(CONFIG_DIR / "targets.yaml", 'r') as f:
                self.targets_config = yaml.safe_load(f)
            
            # Load probes configuration
            with open(CONFIG_DIR / "probes.yaml", 'r') as f:
                self.probes_config = yaml.safe_load(f)
            
            logger.info("Successfully loaded configuration files")
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
        # Ensure config files exist and are valid
        if not validate_all_configs():
            logger.warning("Some config files are invalid, attempting bootstrap recovery...")
            if not run_bootstrap():
                logger.error("Bootstrap recovery failed")
                return False
        
        # Load configurations
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