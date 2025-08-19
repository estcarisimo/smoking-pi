#!/usr/bin/env python3
"""
YAML to PostgreSQL Migration Script
Migrates existing YAML configuration files to PostgreSQL database
"""

import argparse
import logging
import sys
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import (
    DatabaseManager, get_db_session,
    Target, TargetCategory, Probe, Source, SystemMetadata,
    TargetRepository, CategoryRepository, ProbeRepository
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class YAMLToDBMigrator:
    """Migrates YAML configuration to PostgreSQL database"""
    
    def __init__(self, config_dir: Path, database_url: Optional[str] = None):
        self.config_dir = Path(config_dir)
        self.db_manager = DatabaseManager(database_url)
        
        # Verify config files exist
        self.targets_file = self.config_dir / "targets.yaml"
        self.probes_file = self.config_dir / "probes.yaml"
        self.sources_file = self.config_dir / "sources.yaml"
        
        if not self.targets_file.exists():
            raise FileNotFoundError(f"Targets file not found: {self.targets_file}")
        if not self.probes_file.exists():
            raise FileNotFoundError(f"Probes file not found: {self.probes_file}")
        if not self.sources_file.exists():
            raise FileNotFoundError(f"Sources file not found: {self.sources_file}")
    
    def load_yaml_configs(self) -> Dict:
        """Load all YAML configuration files"""
        logger.info("Loading YAML configuration files...")
        
        with open(self.targets_file, 'r') as f:
            targets_config = yaml.safe_load(f)
        
        with open(self.probes_file, 'r') as f:
            probes_config = yaml.safe_load(f)
        
        with open(self.sources_file, 'r') as f:
            sources_config = yaml.safe_load(f)
        
        logger.info(f"Loaded {len(targets_config.get('active_targets', {}))} target categories")
        logger.info(f"Loaded {len(probes_config.get('probes', {}))} probes")
        logger.info(f"Loaded {len(sources_config.get('sources', {}))} sources")
        
        return {
            'targets': targets_config,
            'probes': probes_config,
            'sources': sources_config
        }
    
    def migrate_categories(self, session, targets_config: Dict):
        """Migrate target categories"""
        logger.info("Migrating target categories...")
        category_repo = CategoryRepository(session)
        
        categories_added = 0
        for category_name in targets_config.get('active_targets', {}).keys():
            existing = category_repo.get_by_name(category_name)
            if not existing:
                # Create display name
                display_name = {
                    'custom': 'Custom Targets',
                    'dns_resolvers': 'DNS Resolvers',
                    'netflix_oca': 'Netflix OCA',
                    'top_sites': 'Top Sites'
                }.get(category_name, category_name.replace('_', ' ').title())
                
                category = TargetCategory(
                    name=category_name,
                    display_name=display_name,
                    description=f"Migrated from YAML: {category_name}"
                )
                session.add(category)
                categories_added += 1
                logger.info(f"Added category: {category_name}")
        
        session.commit()
        logger.info(f"Migrated {categories_added} target categories")
    
    def migrate_probes(self, session, probes_config: Dict):
        """Migrate probe configurations"""
        logger.info("Migrating probe configurations...")
        probe_repo = ProbeRepository(session)
        
        probes_added = 0
        for probe_name, probe_data in probes_config.get('probes', {}).items():
            existing = probe_repo.get_by_name(probe_name)
            if not existing:
                probe = Probe(
                    name=probe_name,
                    binary_path=probe_data.get('binary', f'/usr/bin/{probe_name.lower()}'),
                    step_seconds=probe_data.get('step', 300),
                    pings=probe_data.get('pings', 10),
                    forks=probe_data.get('forks'),
                    is_default=(probe_name == 'FPing')
                )
                session.add(probe)
                probes_added += 1
                logger.info(f"Added probe: {probe_name}")
        
        session.commit()
        logger.info(f"Migrated {probes_added} probes")
    
    def migrate_sources(self, session, sources_config: Dict):
        """Migrate source configurations"""
        logger.info("Migrating source configurations...")
        
        sources_added = 0
        for source_name, source_data in sources_config.get('sources', {}).items():
            existing = session.query(Source).filter(Source.name == source_name).first()
            if not existing:
                # Only migrate current categories
                if source_name in ['topsites', 'netflix', 'custom', 'dns']:
                    source = Source(
                        name=source_name,
                        display_name=source_data.get('display_name', source_name.title()),
                        enabled=source_data.get('enabled', True)
                    )
                    session.add(source)
                    sources_added += 1
                    logger.info(f"Added source: {source_name}")
        
        session.commit()
        logger.info(f"Migrated {sources_added} sources")
    
    def migrate_targets(self, session, targets_config: Dict):
        """Migrate targets"""
        logger.info("Migrating targets...")
        target_repo = TargetRepository(session)
        category_repo = CategoryRepository(session)
        probe_repo = ProbeRepository(session)
        
        targets_added = 0
        for category_name, targets_list in targets_config.get('active_targets', {}).items():
            if not isinstance(targets_list, list):
                continue
            
            category = category_repo.get_by_name(category_name)
            if not category:
                logger.error(f"Category not found: {category_name}")
                continue
            
            for target_data in targets_list:
                existing = target_repo.get_by_name(target_data.get('name'))
                if not existing:
                    # Get probe
                    probe_name = target_data.get('probe', 'FPing')
                    probe = probe_repo.get_by_name(probe_name)
                    if not probe:
                        logger.error(f"Probe not found: {probe_name}")
                        continue
                    
                    # Build target data
                    target_dict = {
                        'name': target_data.get('name'),
                        'host': target_data.get('host'),
                        'title': target_data.get('title', target_data.get('name')),
                        'category_id': category.id,
                        'probe_id': probe.id,
                        'lookup': target_data.get('lookup'),
                        'is_active': True
                    }
                    
                    # Add Netflix OCA metadata if present
                    metadata = target_data.get('metadata', {})
                    if metadata:
                        target_dict.update({
                            'asn': metadata.get('asn'),
                            'cache_id': metadata.get('cache_id'),
                            'city': metadata.get('city'),
                            'domain': metadata.get('domain'),
                            'iata_code': metadata.get('iata_code'),
                            'latitude': metadata.get('latitude'),
                            'longitude': metadata.get('longitude'),
                            'location_code': metadata.get('location_code'),
                            'raw_city': metadata.get('raw_city'),
                            'metadata_type': metadata.get('type')
                        })
                    
                    target = Target(**target_dict)
                    session.add(target)
                    targets_added += 1
                    logger.info(f"Added target: {target_data.get('name')} ({category_name})")
        
        session.commit()
        logger.info(f"Migrated {targets_added} targets")
    
    def migrate_system_metadata(self, session, configs: Dict):
        """Migrate system metadata"""
        logger.info("Migrating system metadata...")
        
        metadata_added = 0
        targets_metadata = configs['targets'].get('metadata', {})
        
        for key, value in targets_metadata.items():
            existing = session.query(SystemMetadata).filter(SystemMetadata.key == key).first()
            if not existing:
                metadata = SystemMetadata(
                    key=key,
                    value=str(value)
                )
                session.add(metadata)
                metadata_added += 1
        
        # Add migration completion marker
        migration_marker = SystemMetadata(
            key='yaml_migration_completed',
            value=datetime.now().isoformat()
        )
        session.add(migration_marker)
        metadata_added += 1
        
        session.commit()
        logger.info(f"Migrated {metadata_added} metadata entries")
    
    def run_migration(self, backup_yaml: bool = True) -> bool:
        """Run the complete migration"""
        try:
            logger.info("Starting YAML to PostgreSQL migration...")
            
            # Backup YAML files if requested
            if backup_yaml:
                self.backup_yaml_files()
            
            # Load YAML configurations
            configs = self.load_yaml_configs()
            
            # Create database tables
            self.db_manager.create_tables()
            
            # Run migration in session
            session = self.db_manager.get_session()
            try:
                self.migrate_categories(session, configs['targets'])
                self.migrate_probes(session, configs['probes'])
                self.migrate_sources(session, configs['sources'])
                self.migrate_targets(session, configs['targets'])
                self.migrate_system_metadata(session, configs)
                
                logger.info("Migration completed successfully!")
                return True
                
            except Exception as e:
                logger.error(f"Migration failed: {e}")
                session.rollback()
                raise
            finally:
                session.close()
        
        except Exception as e:
            logger.error(f"Migration error: {e}")
            return False
    
    def backup_yaml_files(self):
        """Create backup of YAML files"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self.config_dir / f"backup_pre_migration_{timestamp}"
        backup_dir.mkdir(exist_ok=True)
        
        for yaml_file in [self.targets_file, self.probes_file, self.sources_file]:
            if yaml_file.exists():
                backup_file = backup_dir / yaml_file.name
                backup_file.write_text(yaml_file.read_text())
                logger.info(f"Backed up {yaml_file.name} to {backup_file}")
    
    def verify_migration(self) -> bool:
        """Verify migration was successful"""
        logger.info("Verifying migration...")
        
        session = self.db_manager.get_session()
        try:
            target_repo = TargetRepository(session)
            targets = target_repo.get_all()
            
            categories = session.query(TargetCategory).all()
            probes = session.query(Probe).all()
            
            logger.info(f"Verification complete:")
            logger.info(f"  - {len(categories)} categories")
            logger.info(f"  - {len(probes)} probes")
            logger.info(f"  - {len(targets)} targets")
            
            return len(targets) > 0
            
        finally:
            session.close()

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Migrate YAML configuration to PostgreSQL')
    parser.add_argument('--config-dir', 
                       default='/app/config',
                       help='Directory containing YAML config files')
    parser.add_argument('--database-url',
                       help='PostgreSQL connection URL')
    parser.add_argument('--no-backup',
                       action='store_true',
                       help='Skip YAML file backup')
    parser.add_argument('--verify-only',
                       action='store_true',
                       help='Only verify existing migration')
    
    args = parser.parse_args()
    
    try:
        migrator = YAMLToDBMigrator(args.config_dir, args.database_url)
        
        if args.verify_only:
            success = migrator.verify_migration()
        else:
            success = migrator.run_migration(backup_yaml=not args.no_backup)
            if success:
                migrator.verify_migration()
        
        sys.exit(0 if success else 1)
        
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()