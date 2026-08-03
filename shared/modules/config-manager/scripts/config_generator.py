#!/usr/bin/env python3
"""
SmokePing Configuration Generator
Generates SmokePing Targets and Probes files from the database (when the
YAML->DB migration has completed) or from YAML configuration files.

The generator only writes to OUTPUT_DIR (bind-mounted into the SmokePing
container); it never copies files to host paths and never spawns
subprocesses.
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from jinja2 import Environment, FileSystemLoader, TemplateNotFound

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from file_ops import atomic_write_text, get_config_lock
from scripts import ipv6_check

# Import database models if available
try:
    from models import (
        get_db_session, Probe, TargetCategory, database_mode_active,
        TargetRepository,
    )
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False

logger = logging.getLogger(__name__)

# Configuration paths
BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", BASE_DIR / "config"))
TEMPLATE_DIR = BASE_DIR / "templates"
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", BASE_DIR / "output"))

# Presentation defaults for the known categories. These match the sections
# SmokePing has always generated, so existing RRD paths are preserved.
# Unknown categories get sensible defaults derived from their name.
CATEGORY_PRESENTATION = {
    'top_sites': {
        'section': 'websites',
        'menu': 'Popular websites',
        'title': 'Outbound from the Pi to the Internet (using Ping)',
    },
    'netflix_oca': {
        'section': 'Netflix',
        'menu': 'Netflix',
        'title': 'Netflix Open Connect Appliances',
    },
    'dns_resolvers': {
        'section': 'DNS_Resolvers',
        'menu': 'DNS Resolvers',
        'title': 'Public DNS Resolvers',
    },
    'custom': {
        'section': 'Custom',
        'menu': 'Custom Targets',
        'title': 'User-Defined Targets',
    },
}

# Stable section ordering (known categories first, then any others in
# data order) so the generated file does not churn between runs.
CATEGORY_ORDER = ['top_sites', 'netflix_oca', 'dns_resolvers', 'custom']

# Probe configuration keys that may be emitted into the SmokePing Probes
# file. Anything else in probes.yaml (metadata, nested structures, ...)
# is skipped instead of being rendered as a Python literal.
PROBE_CONFIG_KEYS = frozenset({
    'binary', 'lookup', 'pings', 'step', 'forks', 'timeout', 'port',
    'offset', 'packetsize', 'hostinterval', 'mininterval', 'blazemode',
    'sourceaddress', 'protocol', 'retry', 'url', 'dns',
})


def _sanitize_section_name(name: str) -> str:
    """Make a category name safe for use as a SmokePing section name"""
    return ''.join(c if (c.isalnum() or c == '_') else '_' for c in name)


def filter_ipv6_targets(targets: List[Dict], allowed: bool) -> List[Dict]:
    """Drop IPv6-probe targets when the host has no global IPv6.

    Probing IPv6 without connectivity records a flat 100% loss that reads as
    an outage but only means there is no IPv6 here. The targets stay in the
    database and come back on their own once a recheck sees IPv6 working.
    """
    if allowed:
        return targets
    return [t for t in targets if not ipv6_check.is_ipv6_target(t)]


def build_category_context(active_targets: Dict,
                           category_meta: Optional[Dict] = None,
                           ipv6_allowed: Optional[bool] = None) -> List[Dict]:
    """Build the ordered, data-driven category list for the template.

    category_meta optionally maps category name -> {'display_name': ...}
    (e.g. from the target_categories table) and is used for categories
    without hardcoded presentation defaults.

    ipv6_allowed=False omits targets whose probe needs global IPv6; None
    means "not determined" and keeps every target.
    """
    category_meta = category_meta or {}
    allow_ipv6 = ipv6_allowed is not False
    ordered = [c for c in CATEGORY_ORDER if c in active_targets]
    ordered += [c for c in active_targets if c not in CATEGORY_ORDER]

    categories = []
    for name in ordered:
        targets = active_targets.get(name)
        if not isinstance(targets, list):
            continue

        targets = filter_ipv6_targets(targets, allow_ipv6)
        if not targets:
            # Every target here needed IPv6; drop the whole empty section.
            continue

        presentation = CATEGORY_PRESENTATION.get(name, {})
        meta = category_meta.get(name, {})
        display_name = meta.get('display_name')

        section = presentation.get('section') or _sanitize_section_name(name)
        menu = (presentation.get('menu') or display_name
                or name.replace('_', ' ').title())
        title = presentation.get('title') or display_name or menu

        categories.append({
            'name': name,
            'section': section,
            'menu': menu,
            'title': title,
            'targets': targets,
        })

    return categories


def render_probe_value(value) -> Optional[str]:
    """Render a probe config value as a SmokePing-safe string.

    Returns None for values that cannot be represented (dict/list/None).
    """
    if value is None or isinstance(value, (dict, list)):
        return None
    if isinstance(value, bool):
        return '1' if value else '0'
    return str(value)


class ConfigGenerator:
    """Generates SmokePing configuration files from database or YAML"""

    def __init__(self):
        self.env = Environment(
            loader=FileSystemLoader(TEMPLATE_DIR),
            trim_blocks=True,
            lstrip_blocks=True
        )
        self.targets_config = None
        self.probes_config = None
        self.category_meta = None
        self.use_database = False

    def _check_database_available(self) -> bool:
        """Check if database mode is active (migration completed)"""
        if not DATABASE_AVAILABLE:
            return False
        return database_mode_active()

    def load_configurations(self) -> bool:
        """Load configuration from database or YAML files.

        The database is re-probed on every call - a transient DB error
        falls back to YAML for this run only, it does not disable the
        database for the process lifetime.
        """
        self.use_database = self._check_database_available()
        if self.use_database:
            if self._load_from_database():
                return True
            logger.error("Database load failed - falling back to YAML for this run")
            self.use_database = False
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

                # Category display names from the database
                self.category_meta = {
                    cat.name: {'display_name': cat.display_name}
                    for cat in session.query(TargetCategory).order_by(TargetCategory.id).all()
                }

                # Load probes from database (id order keeps the Probes file stable)
                probes = session.query(Probe).order_by(Probe.id).all()
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
            logger.error(f"Failed to load from database: {e}")
            return False

    def _load_from_yaml(self) -> bool:
        """Load configuration from YAML files (fallback)"""
        try:
            # Load targets configuration
            with open(CONFIG_DIR / "targets.yaml", 'r') as f:
                self.targets_config = yaml.safe_load(f)

            # Load probes configuration
            with open(CONFIG_DIR / "probes.yaml", 'r') as f:
                self.probes_config = yaml.safe_load(f)

            self.category_meta = None
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

            ipv6_status = ipv6_check.get_status()
            ipv6_allowed = ipv6_status.get('available') is not False
            if not ipv6_allowed:
                logger.info("Omitting IPv6 targets: %s",
                            ipv6_status.get('reason', 'no global IPv6'))

            # Prepare context for template
            context = {
                'categories': build_category_context(
                    self.targets_config['active_targets'],
                    self.category_meta,
                    ipv6_allowed=ipv6_allowed,
                ),
                'default_probe': self.probes_config.get('default_probe', 'FPing'),
                'generated_at': datetime.now().isoformat(),
                'ipv6_skipped_reason': None if ipv6_allowed
                else ipv6_status.get('reason', 'no global IPv6 on this host'),
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

            if isinstance(probe_config, dict):
                for key, value in probe_config.items():
                    if key not in PROBE_CONFIG_KEYS:
                        logger.debug(f"Skipping non-probe key '{key}' for probe {probe_name}")
                        continue
                    rendered = render_probe_value(value)
                    if rendered is None:
                        logger.debug(f"Skipping unrenderable value for {probe_name}.{key}")
                        continue
                    lines.append(f"{key} = {rendered}")

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
        """Atomically write generated configuration to the output directory"""
        try:
            OUTPUT_DIR.mkdir(exist_ok=True, parents=True)

            targets_file = OUTPUT_DIR / "Targets"
            atomic_write_text(targets_file, targets_content)
            logger.info(f"Written Targets file to {targets_file}")

            probes_file = OUTPUT_DIR / "Probes"
            atomic_write_text(probes_file, probes_content)
            logger.info(f"Written Probes file to {probes_file}")

            return True

        except Exception as e:
            logger.error(f"Failed to write output files: {e}")
            return False

    def run(self) -> bool:
        """Main execution flow.

        Loads configuration (database first, YAML fallback), validates it,
        and atomically writes the Targets/Probes files to OUTPUT_DIR under
        the shared config lock. On validation failure this logs and returns
        False - it never attempts bootstrap recovery (the caller owns that).
        """
        with get_config_lock():
            # Load configurations (from database or YAML)
            if not self.load_configurations():
                logger.error("Failed to load configuration - generation aborted")
                return False

            # Validate configuration
            if not self.validate_configuration():
                logger.error("Configuration validation failed - generation aborted")
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

        return True


def generate_configs() -> bool:
    """Convenience function: generate SmokePing config files in-process"""
    return ConfigGenerator().run()


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Generate SmokePing configuration from database or YAML sources"
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    generator = ConfigGenerator()

    try:
        if generator.run():
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
