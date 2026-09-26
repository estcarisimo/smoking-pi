#!/usr/bin/env python3
"""
Config Manager REST API
Provides REST interface for SmokePing configuration management
"""

import hmac
import json
import logging
import secrets
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Dict, Any
import yaml
import subprocess
import threading
import time
import os
import docker

from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.exceptions import BadRequest

# Import our existing config generator and bootstrap
from scripts.config_generator import ConfigGenerator, probe_dict_from_row
from scripts.bootstrap import run_bootstrap
from scripts import ipv6_check

# Shared file lock / atomic write helpers
from file_ops import get_config_lock, atomic_write_yaml
import freshness
import assistant
import recommendations
import wizard_adopt

# Import database models and repositories
from models import (
    get_db_session, Target, TargetCategory, Probe, Source, SystemMetadata,
    TargetRepository, CategoryRepository, ProbeRepository,
    database_mode_active,
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# What a mutation says when the configuration was written but SmokePing did
# not confirm the reload: the change is saved, not yet measured.
RELOAD_UNCONFIRMED = ('Configuration saved, but SmokePing did not confirm the '
                      'reload -- restart SmokePing to apply it')


def error_response(status: int, message: str, exc: BaseException | None = None,
                   **extra):
    """A JSON error whose body is chosen here, never derived from an exception.

    The old shape was ``{'error': str(e)}``, which put whatever the exception
    carried -- a database URL, a file path, a Docker socket error, a
    traceback fragment -- on the wire to any client that could reach the
    port. The detail still matters to the operator, so it goes to the log
    with a traceback, tagged with a short id that is also returned; grep the
    id in ``docker compose logs config-manager`` to find the full story.
    """
    error_id = secrets.token_hex(4)
    if exc is not None:
        logger.error("%s [%s]", message, error_id, exc_info=exc)
    else:
        logger.error("%s [%s]", message, error_id)
    return jsonify({'error': message, 'error_id': error_id, **extra}), status

app = Flask(__name__)
CORS(app)

# Configuration paths
BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", BASE_DIR / "config"))
# The only YAML files a request may name. update_config() resolves the
# requested type through this table rather than formatting a filename.
CONFIG_FILES = {
    'targets': CONFIG_DIR / 'targets.yaml',
    'probes': CONFIG_DIR / 'probes.yaml',
    'sources': CONFIG_DIR / 'sources.yaml',
}
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", BASE_DIR / "output"))


def require_api_token(f):
    """Optional shared-token auth.

    If CONFIG_API_TOKEN is set in the environment, the request must carry
    it via 'Authorization: Bearer <token>' or 'X-API-Token: <token>'.
    If the env var is unset, requests pass through (backward compatible).
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        expected = os.environ.get('CONFIG_API_TOKEN')
        if expected:
            provided = None
            auth_header = request.headers.get('Authorization', '')
            if auth_header.startswith('Bearer '):
                provided = auth_header[len('Bearer '):].strip()
            if not provided:
                provided = request.headers.get('X-API-Token')
            if not provided or not hmac.compare_digest(provided, expected):
                return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return wrapper


class ConfigManagerAPI:
    """REST API for SmokePing configuration management"""
    
    def __init__(self):
        self.generator = ConfigGenerator()

        # Database mode is probed lazily and re-checked periodically, so a
        # transient DB outage never disables database mode for the whole
        # process lifetime.
        self._db_mode_cache = None  # tuple(bool, checked_at)
        self._db_mode_ttl = 30  # seconds

        # What the RRD guard did before the latest reload (see _guard_rrds).
        self.last_rrd_guard: Dict[str, Any] = {}

        # Initialize status cache
        self._status_cache = {}
        self._cache_ttl = 30  # 30 seconds cache TTL

    @property
    def use_database(self) -> bool:
        """Whether PostgreSQL is the active config backend (cached, re-probed)"""
        now = time.time()
        if (self._db_mode_cache is None
                or now - self._db_mode_cache[1] > self._db_mode_ttl):
            self._db_mode_cache = (self._check_database_available(), now)
        return self._db_mode_cache[0]

    def refresh_database_mode(self) -> None:
        """Force a re-probe of database availability on next access"""
        self._db_mode_cache = None

    def _check_database_available(self) -> bool:
        """Check if database is available and migration has completed"""
        return database_mode_active()

    def get_config(self, config_type: str = 'all') -> Dict[str, Any]:
        """Get current configuration from database or YAML fallback"""
        if self.use_database:
            return self._get_config_from_database(config_type)
        else:
            return self._get_config_from_yaml(config_type)
    
    def _get_config_from_database(self, config_type: str = 'all') -> Dict[str, Any]:
        """Get configuration from PostgreSQL database"""
        session = get_db_session()
        try:
            result = {}
            
            if config_type in ['all', 'targets']:
                target_repo = TargetRepository(session)
                targets = target_repo.get_all()
                
                # Group targets by category
                active_targets = {}
                for target in targets:
                    if target.is_active:
                        category_name = target.category.name
                        if category_name not in active_targets:
                            active_targets[category_name] = []
                        active_targets[category_name].append(target.to_dict())
                
                result['targets'] = {
                    'active_targets': active_targets,
                    'metadata': {
                        'total_targets': len([t for t in targets if t.is_active]),
                        'last_updated': datetime.now().isoformat()
                    }
                }
            
            if config_type in ['all', 'probes']:
                probes = session.query(Probe).order_by(Probe.id).all()
                probes_config = {
                    probe.name: probe_dict_from_row(probe) for probe in probes
                }
                
                result['probes'] = {'probes': probes_config}
            
            if config_type in ['all', 'sources']:
                sources = session.query(Source).all()
                sources_config = {}
                for source in sources:
                    sources_config[source.name] = {
                        'display_name': source.display_name,
                        'enabled': source.enabled
                    }
                
                result['sources'] = {'sources': sources_config}
            
            # Add metadata
            result['metadata'] = {
                'source': 'database',
                'generated_at': datetime.now().isoformat(),
                'config_type': config_type
            }
            
            return result
            
        except Exception as e:
            # Fall back to YAML for this request only - database mode is
            # re-probed periodically instead of being disabled permanently.
            logger.error(f"Database error, falling back to YAML for this request: {e}")
            return self._get_config_from_yaml(config_type)
        finally:
            session.close()
    
    def _get_config_from_yaml(self, config_type: str = 'all') -> Dict[str, Any]:
        """Get configuration from YAML files (fallback)"""
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
                'source': 'yaml',
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
            # The file is looked up, never built from the request string: the
            # type is a key into a literal table, so an unknown type fails
            # here and no caller-supplied text ever reaches a path.
            config_file = CONFIG_FILES.get(config_type)
            if config_file is None:
                raise ValueError(f"Unknown configuration type: {config_type}")

            # Validate the configuration data (the route already did, and
            # returned the reasons; this is the guard for other callers).
            problems = self.validate_config(config_type, config_data)
            if problems:
                raise ValueError("Invalid configuration: " + "; ".join(problems))
            
            with get_config_lock():
                # Backup existing config (keep the 5 most recent)
                backup_file = config_file.with_suffix(f'.yaml.backup.{int(time.time())}')
                if config_file.exists():
                    backup_file.write_text(config_file.read_text())
                    logger.info(f"Backed up {config_file} to {backup_file}")
                    backups = sorted(
                        config_file.parent.glob(f'{config_file.stem}.yaml.backup.*'),
                        key=lambda p: p.name,
                    )
                    for old_backup in backups[:-5]:
                        old_backup.unlink(missing_ok=True)

                # Write new configuration atomically
                atomic_write_yaml(config_file, config_data)

            logger.info(f"Updated {config_type} configuration")
            
            # Generate and deploy new SmokePing configuration
            reloaded = self._regenerate_smokeping_config()

            return {
                'success': True,
                'reloaded': reloaded,
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
            success = self.generator.run()

            if success:
                reloaded = self._signal_smokeping_reload()
                return {
                    'success': True,
                    'reloaded': reloaded,
                    'message': ('SmokePing configuration generated and deployed'
                                if reloaded else RELOAD_UNCONFIRMED),
                    'generated_at': datetime.now().isoformat()
                }
            else:
                raise RuntimeError("Configuration generation failed")

        except Exception as e:
            logger.error(f"Failed to generate SmokePing configuration: {e}")
            raise

    def _smokeping_runner(self, argv):
        """Run a command inside the SmokePing container's network namespace.

        SmokePing uses network_mode: host, so this sees what its probes see.
        config-manager's own namespace is a Docker bridge with no IPv6 at all,
        which would make every check report "no IPv6" (see scripts/ipv6_check).
        """
        container_name = resolve_container_name('smokeping')
        client = docker.from_env()
        container = client.containers.get(container_name)
        result = container.exec_run(argv, demux=False)
        output = result.output
        if isinstance(output, bytes):
            output = output.decode('utf-8', 'replace')
        return output or '', result.exit_code

    def refresh_ipv6_status(self) -> Dict[str, Any]:
        """Re-run the IPv6 check and publish the verdict. Returns the status.

        A check that could not run at all leaves the previous verdict in
        place: a transient docker-exec failure must not drop IPv6 targets.
        """
        status = ipv6_check.check(self._smokeping_runner)
        if status.get('error'):
            logger.warning("Keeping previous IPv6 status: %s", status['reason'])
            return ipv6_check.get_status()
        status['checked_at'] = datetime.now().isoformat()
        ipv6_check.set_status(status)
        return status

    def _guard_rrds(self, container) -> Dict[str, Any]:
        """Archive every RRD the new configuration would make SmokePing die on.

        SmokePing refuses to load when an RRD's step or ping count differs
        from its probe's, and the refusal kills the daemon -- every target
        stops, not only that one. rrd_guard.py (in the SmokePing image)
        moves such files to /data/.archive/ before the reload. The expected
        cadence comes from the generated Targets and Probes, plus the CPE
        targets and the Database defaults inside the container. Never
        raises: a guard that cannot run leaves the reload as it was before.
        """
        try:
            targets_file = OUTPUT_DIR / "Targets"
            probes_file = OUTPUT_DIR / "Probes"
            if not targets_file.exists():
                return {'ran': False, 'reason': 'no generated Targets file yet'}
            cpe = container.exec_run(['cat', '/config/CPE_Targets'])
            database = container.exec_run(['cat', '/config/Database'])
            expected = freshness.expected_cadence(
                targets_file.read_text(),
                cpe.output.decode(errors='replace') if cpe.exit_code == 0 else '',
                probes_file.read_text() if probes_file.exists() else '',
                database.output.decode(errors='replace') if database.exit_code == 0 else '',
            )
            ran = container.exec_run(
                ['python3', '/exporters/rrd_guard.py', json.dumps({'expected': expected})],
                demux=True)
            stdout = (ran.output[0] or b'').decode(errors='replace')
            if ran.exit_code != 0:
                logger.warning(f"RRD guard failed (exit {ran.exit_code}): {stdout.strip()}")
                return {'ran': False, 'reason': f'rrd_guard exit {ran.exit_code}'}
            report = json.loads(stdout)
            for moved in report.get('archived', []):
                logger.warning(
                    "Archived %s (step %s, %s pings) -> %s: the probe now wants "
                    "step %s, %s pings", moved['rrd'], moved['had']['step'],
                    moved['had']['pings'], moved['to'], moved['wants']['step'],
                    moved['wants']['pings'])
            for error in report.get('errors', []):
                logger.warning("RRD guard: %s: %s", error.get('rrd'), error.get('error'))
            return {'ran': True, **report}
        except Exception as e:
            logger.warning(f"RRD guard could not run: {e}")
            return {'ran': False, 'reason': 'rrd_guard could not run'}

    def _signal_smokeping_reload(self) -> bool:
        """Ask SmokePing to reload its config; True only if the signal landed.

        The generated files are bind-mounted into the SmokePing container,
        so a SIGHUP is enough - no file copying is needed. Best effort in
        that a failure does not fail the request (the configuration is
        saved either way), but it is reported: this used to ignore
        exec_run's exit code and log "Sent reload signal" when killall had
        found no smokeping process to signal.
        """
        self.last_rrd_guard = {}
        try:
            container_name = resolve_container_name('smokeping')
            client = docker.from_env()
            container = client.containers.get(container_name)
            self.last_rrd_guard = self._guard_rrds(container)
            result = container.exec_run(['killall', '-HUP', 'smokeping'])
            if result.exit_code != 0:
                output = (result.output or b'').decode(errors='replace').strip()
                logger.warning(
                    f"SmokePing reload failed in '{container_name}' "
                    f"(killall exit {result.exit_code}): {output}")
                return False
            logger.info(f"Sent reload signal to SmokePing container '{container_name}'")
            return True
        except Exception as e:
            logger.warning(f"Could not signal SmokePing reload: {e}")
            return False

    def restart_smokeping(self) -> Dict[str, Any]:
        """Restart SmokePing service"""
        try:
            container_name = resolve_container_name('smokeping')
            client = docker.from_env()
            container = client.containers.get(container_name)
            container.restart(timeout=30)

            logger.info(f"SmokePing container '{container_name}' restarted successfully")
            return {
                'success': True,
                'message': 'SmokePing service restarted',
                'restarted_at': datetime.now().isoformat()
            }

        except Exception as e:
            logger.error(f"Failed to restart SmokePing: {e}")
            raise
    
    def measurement_freshness(self) -> Dict[str, Any]:
        """Per-target freshness: is SmokePing actually writing data?

        The expected targets come from the generated Targets file here,
        plus CPE_Targets, which cpe_discovery.py writes inside the SmokePing
        container and Targets @includes. The mtimes come from the same
        container, through the Docker socket this service already uses
        for the reload -- the RRDs live in a volume only SmokePing mounts.
        """
        targets_file = OUTPUT_DIR / "Targets"
        probes_file = OUTPUT_DIR / "Probes"
        if not targets_file.exists():
            return {'available': False,
                    'reason': 'no generated Targets file yet'}
        container_name = resolve_container_name('smokeping')
        container = docker.from_env().containers.get(container_name)
        found = container.exec_run(
            ['find', '/data', '-name', '*.rrd', '-printf', '%T@ %P\\n'])
        if found.exit_code != 0:
            return {'available': False,
                    'reason': f'could not list RRD files (find exit {found.exit_code})'}
        cpe = container.exec_run(['cat', '/config/CPE_Targets'])
        # time.time() and the RRD mtimes share a clock: containers read the
        # host kernel's. The target ages come from PostgreSQL as durations,
        # so its session time zone never enters the arithmetic.
        now = time.time()
        body = freshness.report(
            targets_text=targets_file.read_text(),
            cpe_text=cpe.output.decode(errors='replace') if cpe.exit_code == 0 else '',
            probes_text=probes_file.read_text() if probes_file.exists() else '',
            find_output=found.output.decode(errors='replace'),
            now=now,
            changed_at={name: now - age for name, age in self._target_change_ages().items()},
            started_at=_container_started_at(container),
        )
        return {'available': True, 'checked_at': datetime.now().isoformat(), **body}

    def assistant_status(self) -> Dict[str, Any]:
        """Whether an assistant is calling the MCP server; see assistant.py.

        Reads the mcp-server container's own tool= log lines through the
        Docker socket, as `smoking-pi openclaw --check` does from the host.
        A project without that container (not Pro, or the mcp profile off)
        answers "absent", which is a state, not an error.
        """
        try:
            container_name = resolve_container_name('mcp-server')
        except Exception:
            return {'available': True, **assistant.summarize(None, None, '')}
        container = docker.from_env().containers.get(container_name)
        state = container.attrs.get('State', {})
        logs = ''
        if container.status == 'running':
            # tail bounds the read on a server that has run for months.
            logs = container.logs(timestamps=True, tail=20000).decode(errors='replace')
        return {'available': True, **assistant.summarize(
            container.status, state.get('StartedAt'), logs)}

    def connection_recommendations(self) -> Dict[str, Any]:
        """The host's uplink, router, resolvers and CPE, and which of them
        SmokePing already measures. See recommendations.py.

        The facts come from host_facts.py run inside the SmokePing
        container: this service sits on a Docker bridge, so its own routes
        and resolvers are Docker's. That only works where SmokePing shares
        the host's network; elsewhere the card says so instead of showing
        the bridge's gateway as "your router".
        """
        targets_file = OUTPUT_DIR / "Targets"
        container_name = resolve_container_name('smokeping')
        container = docker.from_env().containers.get(container_name)
        mode = container.attrs.get('HostConfig', {}).get('NetworkMode')
        if mode != 'host':
            return recommendations.unavailable(
                "SmokePing runs on a Docker network in this edition, so it "
                "cannot see this host's router or resolvers")
        # demux: stdout is the JSON, stderr (a log line, a traceback) is not.
        ran = container.exec_run(['python3', '/exporters/host_facts.py'], demux=True)
        stdout, stderr = ran.output if isinstance(ran.output, tuple) else (ran.output, b'')
        if ran.exit_code != 0:
            logger.warning("host_facts.py exited %s: %s", ran.exit_code,
                           (stderr or b'').decode(errors='replace')[-500:])
            return recommendations.unavailable(
                f"could not read the host's network (host_facts exit {ran.exit_code})")
        try:
            facts = json.loads((stdout or b'').decode(errors='replace'))
        except ValueError:
            return recommendations.unavailable("the host's network facts were unreadable")
        cpe = container.exec_run(['cat', '/config/CPE_Targets'])
        measured = recommendations.measured_hosts(
            targets_file.read_text() if targets_file.exists() else '',
            cpe.output.decode(errors='replace') if cpe.exit_code == 0 else '')
        return recommendations.recommend(facts, measured)

    def _target_change_ages(self) -> Dict[str, float]:
        """Seconds since each target last changed (added, edited, toggled).

        ``updated_at`` is a naive timestamp in the session's time zone, so
        the subtraction happens in PostgreSQL against ``localtimestamp``,
        the same clock and zone that wrote it. Empty in YAML mode, which
        has no per-target history: there, only SmokePing's start marks a
        target as pending.
        """
        if not self.use_database:
            return {}
        from sqlalchemy import func
        session = get_db_session()
        try:
            rows = session.query(
                Target.name,
                func.extract('epoch', func.localtimestamp() - Target.updated_at),
            ).all()
            return {name: float(age) for name, age in rows if age is not None}
        except Exception as e:
            logger.warning(f"Could not read target change times: {e}")
            return {}
        finally:
            session.close()

    def get_status(self) -> Dict[str, Any]:
        """Get service status"""
        try:
            # Check database status
            database_status = self._check_database_status()
            
            # Check if config files exist (fallback)
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
            
            # Determine overall status
            if database_status['available']:
                status = 'healthy' if database_status['has_data'] else 'partial'
            else:
                status = 'healthy' if all(configs_exist.values()) else 'partial'
            
            return {
                'status': status,
                'database': database_status,
                'yaml_configs': configs_exist,
                'generated_files': generated_exist,
                'smokeping': smokeping_status,
                'using_database': self.use_database,
                'last_check': datetime.now().isoformat()
            }
            
        except Exception:
            logger.error("Failed to get status", exc_info=True)
            return {
                'status': 'error',
                'error': 'status check failed; see config-manager log',
                'last_check': datetime.now().isoformat()
            }
    
    def _check_database_status(self) -> Dict[str, Any]:
        """Check PostgreSQL database status"""
        try:
            session = get_db_session()
            try:
                # Check if tables exist and have data
                target_count = session.query(Target).count()
                category_count = session.query(TargetCategory).count()
                probe_count = session.query(Probe).count()
                
                # Check migration marker
                migration_marker = session.query(SystemMetadata).filter(
                    SystemMetadata.key == 'yaml_migration_completed'
                ).first()
                
                return {
                    'available': True,
                    'has_data': target_count > 0,
                    'target_count': target_count,
                    'category_count': category_count,
                    'probe_count': probe_count,
                    'migration_completed': migration_marker is not None,
                    'migration_date': migration_marker.value if migration_marker else None
                }
                
            finally:
                session.close()
                
        except Exception:
            # The exception text is where the database URL (and password)
            # would appear. It stays in the log.
            logger.warning("Database check failed", exc_info=True)
            return {
                'available': False,
                'error': 'database unavailable; see config-manager log'
            }
    
    def validate_config(self, config_type: str, config: Dict[str, Any]) -> list:
        """Problems with a config as a list of plain strings; [] when valid.

        Returns rather than raises so the route can send the reasons back
        to the caller: every string here is a literal we wrote, not
        exception text, which is what keeps the response free of anything
        an exception might carry. The targets validator also normalizes
        metadata in place, as before.
        """
        if config_type == 'targets':
            return self._validate_targets_config(config)
        if config_type == 'probes':
            return self._validate_probes_config(config)
        if config_type == 'sources':
            return self._validate_sources_config(config)
        return [f"Unknown configuration type: {config_type}"]

    def _validate_targets_config(self, config: Dict[str, Any]) -> list:
        """Validate targets configuration"""
        if not isinstance(config, dict) or 'active_targets' not in config:
            return ["Missing 'active_targets' in configuration"]
        if not isinstance(config['active_targets'], dict):
            return ["'active_targets' must be a mapping of category to list"]
        
        if 'metadata' not in config:
            config['metadata'] = {}
        
        # Update metadata
        config['metadata']['last_updated'] = datetime.now().isoformat()
        
        problems = []
        # Count total targets
        total_targets = 0
        for category, targets in config['active_targets'].items():
            if isinstance(targets, list):
                total_targets += len(targets)
                
                # Validate each target
                for target in targets:
                    if not isinstance(target, dict):
                        problems.append(f"Invalid target format in {category}")
                    elif 'name' not in target or 'host' not in target:
                        problems.append(
                            f"Target missing required fields (name, host) in {category}"
                        )
        
        config['metadata']['total_targets'] = total_targets
        return problems
    
    def _validate_probes_config(self, config: Dict[str, Any]) -> list:
        """Validate probes configuration"""
        if not isinstance(config, dict) or 'probes' not in config:
            return ["Missing 'probes' in configuration"]
        
        if not config['probes']:
            return ["At least one probe must be configured"]
        return []
    
    def _validate_sources_config(self, config: Dict[str, Any]) -> list:
        """Validate sources configuration"""
        # Sources config is more flexible, just ensure it's valid YAML
        if not isinstance(config, dict):
            return ["Sources configuration must be a dictionary"]
        return []
    
    def _regenerate_smokeping_config(self) -> bool:
        """Regenerate SmokePing configuration in-process.

        Raises if the files could not be written. Returns whether SmokePing
        confirmed the reload, which callers pass on as ``reloaded``: saved
        and picked up are different claims.
        """
        try:
            if not self.generator.run():
                raise RuntimeError("Configuration generation failed")
            reloaded = self._signal_smokeping_reload()
            logger.info("SmokePing configuration regenerated")
            return reloaded
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
        """Check SmokePing container status using Docker Python API with caching"""
        cache_key = 'smokeping_status'
        current_time = time.time()
        
        # Check if we have a cached result that's still valid
        if cache_key in self._status_cache:
            cached_data, cached_time = self._status_cache[cache_key]
            if current_time - cached_time < self._cache_ttl:
                logger.debug(f"Using cached SmokePing status (age: {current_time - cached_time:.1f}s)")
                return cached_data
        
        # No valid cache, perform actual check
        logger.debug("Performing fresh SmokePing status check")
        try:
            # The labels are the only way in. There is deliberately no
            # fallback to a guessed name: a container carrying no compose
            # labels is not this project's SmokePing whatever it is called,
            # and reporting on somebody else's container is worse than
            # reporting that ours is missing.
            try:
                container_name = resolve_container_name('smokeping')
                logger.debug(f"Resolved SmokePing container name: {container_name}")
            except Exception as e:
                logger.warning(f"Could not resolve the SmokePing container: {e}")
                return {
                    'running': False,
                    'status': 'not_found',
                    'container_name': None,
                    'error': 'No SmokePing container in this Compose project'
                }
            
            # Use Docker Python API for more reliable status checking
            client = docker.from_env()
            
            try:
                container = client.containers.get(container_name)
                
                # Get detailed container state
                state = container.attrs.get('State', {})
                health = state.get('Health', {})
                
                # Determine container status
                is_running = container.status == 'running'
                created_time = container.attrs.get('Created', 'unknown')
                
                if is_running:
                    logger.info(f"SmokePing container '{container_name}' detected as running")
                else:
                    logger.warning(f"SmokePing container '{container_name}' is {container.status}")
                
                result = {
                    'running': is_running,
                    'status': container.status,
                    'created': created_time,
                    'health': health.get('Status', 'none') if health else 'none',
                    'container_name': container_name,
                    'state_details': {
                        'pid': state.get('Pid', 0),
                        'exit_code': state.get('ExitCode', 0),
                        'started_at': state.get('StartedAt', ''),
                        'finished_at': state.get('FinishedAt', '')
                    }
                }
                
                # Cache the successful result
                self._status_cache[cache_key] = (result, current_time)
                return result
                
            except docker.errors.NotFound:
                logger.warning(f"SmokePing container '{container_name}' not found")
                return {
                    'running': False,
                    'status': 'not_found',
                    'container_name': container_name,
                    'error': f"Container '{container_name}' not found"
                }
                
        except docker.errors.DockerException:
            logger.error("Docker API error checking SmokePing status",
                         exc_info=True)
            return {
                'running': False,
                'status': 'error',
                'error': 'Docker API error; see config-manager log'
            }
        except Exception:
            logger.error("Unexpected error checking SmokePing status",
                         exc_info=True)
            return {
                'running': False,
                'status': 'unknown',
                'error': 'status check failed; see config-manager log'
            }


# Initialize API instance
api = ConfigManagerAPI()

# Startup initialization -----------------------------------------------------
_initialized = False


def initialize() -> None:
    """Explicit one-time startup initialization.

    Runs bootstrap (create missing YAML configs), the idempotent YAML->DB
    migration (when DATABASE_URL is configured), and one config generation.
    Serialized with a cross-process file lock so concurrent gunicorn
    workers do not race; every step is idempotent, so a second worker
    running through it is a no-op.

    Called from the WSGI entrypoint (wsgi.py) and from __main__ - never at
    module import time.
    """
    global _initialized
    if _initialized:
        return

    with get_config_lock():
        # 1. Ensure YAML config files exist (first run / recovery)
        logger.info("Running configuration bootstrap...")
        if not run_bootstrap():
            logger.error("Bootstrap failed - some config files may be missing")
        else:
            logger.info("Bootstrap completed successfully")

        # 2. Migrate YAML -> PostgreSQL (idempotent; upserts missing probes
        #    on already-migrated deployments)
        if os.environ.get('DATABASE_URL'):
            try:
                from scripts.migrate_yaml_to_db import run_migration
                if run_migration(config_dir=CONFIG_DIR):
                    logger.info("Database migration check completed")
                else:
                    logger.error("Database migration failed - continuing in YAML mode")
            except Exception as e:
                logger.error(f"Database migration error - continuing in YAML mode: {e}")
        else:
            logger.info("DATABASE_URL not set - running in YAML mode")

        # 3. Determine IPv6 reachability before generating, so the first
        #    Targets file already reflects it. If SmokePing is not up yet the
        #    check reports an error and the status stays unknown (IPv6 targets
        #    kept); the recheck thread settles it shortly after.
        try:
            status = api.refresh_ipv6_status()
            logger.info("IPv6 measurements %s: %s",
                        "enabled" if status.get('available') is not False
                        else "disabled", status.get('reason'))
        except Exception as e:
            logger.warning(f"Initial IPv6 check failed: {e}")

        # 4. Generate SmokePing config once at startup
        try:
            if api.generator.run():
                logger.info("Initial SmokePing configuration generated")
            else:
                logger.error("Initial SmokePing configuration generation failed")
        except Exception as e:
            logger.error(f"Initial config generation error: {e}")

    api.refresh_database_mode()
    _start_ipv6_recheck_thread()
    _initialized = True


def _ipv6_recheck_loop() -> None:
    """Re-check IPv6 periodically; regenerate only when the verdict flips.

    This is what makes the gate self-healing: when IPv6 starts working the
    targets come back without anyone touching the config, and when it stops
    they drop out instead of charting a flat 100% loss.
    """
    interval = ipv6_check.recheck_interval()
    while True:
        time.sleep(interval)
        try:
            previous = ipv6_check.get_status().get('available')
            current = api.refresh_ipv6_status().get('available')
            if current == previous:
                continue
            logger.info("IPv6 reachability changed (%s -> %s); regenerating",
                        previous, current)
            with get_config_lock():
                api._regenerate_smokeping_config()
        except Exception as e:
            logger.warning(f"IPv6 recheck failed: {e}")


def _start_ipv6_recheck_thread() -> None:
    if ipv6_check.mode() != 'auto':
        logger.info("IPv6 recheck thread not started (IPV6_MODE=%s)",
                    ipv6_check.mode())
        return
    thread = threading.Thread(target=_ipv6_recheck_loop, name='ipv6-recheck',
                              daemon=True)
    thread.start()
    logger.info("IPv6 recheck thread started (every %ds)",
                ipv6_check.recheck_interval())


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'config-manager',
        'timestamp': datetime.now().isoformat()
    })


@app.route('/ipv6-status', methods=['GET'])
@require_api_token
def ipv6_status():
    """Current IPv6 reachability verdict and whether targets are gated."""
    status = ipv6_check.get_status()
    return jsonify({
        **status,
        'measurements_allowed': ipv6_check.measurements_allowed(),
        'recheck_interval': ipv6_check.recheck_interval(),
    })


@app.route('/ipv6-status/refresh', methods=['POST'])
@require_api_token
def ipv6_status_refresh():
    """Force an immediate re-check, regenerating config if the verdict flips."""
    try:
        previous = ipv6_check.get_status().get('available')
        status = api.refresh_ipv6_status()
        regenerated = False
        if status.get('available') != previous:
            with get_config_lock():
                api._regenerate_smokeping_config()
            regenerated = True
        return jsonify({**status, 'regenerated': regenerated})
    except Exception as e:
        return error_response(500, "IPv6 refresh failed", e)


@app.route('/status', methods=['GET'])
@require_api_token
def get_status():
    """Get service status"""
    try:
        status = api.get_status()
        return jsonify(status)
    except Exception as e:
        return error_response(500, "Status check failed", e,
                              timestamp=datetime.now().isoformat())


@app.route('/config', methods=['GET'])
@app.route('/config/<config_type>', methods=['GET'])
@require_api_token
def get_config(config_type='all'):
    """Get configuration"""
    try:
        config = api.get_config(config_type)
        return jsonify(config)
    except ValueError as e:
        # Raised for an unknown type or an unreadable YAML file; the detail
        # (a path, a parser message) belongs in the log.
        return error_response(400, "Configuration could not be read", e)
    except Exception as e:
        return error_response(500, "Failed to get config", e)


@app.route('/config/<config_type>', methods=['PUT'])
@require_api_token
def update_config(config_type):
    """Update configuration"""
    try:
        if not request.is_json:
            raise BadRequest("Content-Type must be application/json")
        
        config_data = request.get_json()
        if not config_data:
            raise BadRequest("Empty request body")

        if config_type not in CONFIG_FILES:
            return error_response(400, "Unknown configuration type",
                                  known=sorted(CONFIG_FILES))
        # Validation problems are plain strings built by our validators, not
        # exception text, so they can be returned verbatim.
        problems = api.validate_config(config_type, config_data)
        if problems:
            return jsonify({'error': 'Invalid configuration',
                            'problems': problems}), 400

        result = api.update_config(config_type, config_data)
        return jsonify(result)
        
    except BadRequest:
        return error_response(400, "Request body must be a non-empty JSON object")
    except ValueError as e:
        return error_response(400, "Invalid configuration", e)
    except Exception as e:
        return error_response(500, "Failed to update config", e)


def _container_started_at(container) -> Any:
    """Epoch seconds of the container's last start, or None.

    Docker reports RFC 3339 with nanoseconds ("2026-09-22T02:10:05.123456789Z");
    fromisoformat takes at most microseconds.
    """
    raw = ((getattr(container, 'attrs', None) or {}).get('State') or {}).get('StartedAt')
    if not raw or raw.startswith('0001-'):
        return None
    try:
        main, _, frac = raw.rstrip('Z').partition('.')
        stamp = main + ('.' + frac[:6] if frac else '') + '+00:00'
        return datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return None


@app.route('/measurements', methods=['GET'])
@require_api_token
def measurements():
    """Is SmokePing measuring each configured target? See freshness.py."""
    try:
        return jsonify(api.measurement_freshness())
    except Exception as e:
        return error_response(500, "Failed to check measurement freshness", e)


@app.route('/recommendations', methods=['GET'])
@require_api_token
def connection_recommendations():
    """What this host's connection suggests measuring. See recommendations.py."""
    try:
        return jsonify(api.connection_recommendations())
    except Exception as e:
        return error_response(500, "Failed to read the host's connection", e)


# The web admin's welcome tour (first-run stage C) shows itself until it is
# finished or skipped once. A file beside the generated config, like the
# IPv6 status: it is state of this install, and it must survive a restart
# of either service. Deleting it brings the tour back.
FIRST_RUN_OUTCOMES = ('done', 'skipped')


def _first_run_file() -> Path:
    return OUTPUT_DIR / '.first-run.json'


@app.route('/first-run', methods=['GET'])
@require_api_token
def get_first_run():
    """Has the welcome tour been finished or skipped on this install?"""
    try:
        state = json.loads(_first_run_file().read_text())
    except FileNotFoundError:
        return jsonify({'completed': False, 'outcome': None, 'at': None})
    except (OSError, ValueError) as e:
        # Unreadable is not "never seen": a corrupt file must not trap
        # every login in the tour.
        logger.warning(f"Unreadable first-run marker: {e}")
        return jsonify({'completed': True, 'outcome': 'unknown', 'at': None})
    outcome = state.get('outcome') if isinstance(state, dict) else None
    return jsonify({'completed': True,
                    'outcome': outcome if outcome in FIRST_RUN_OUTCOMES else 'unknown',
                    'at': state.get('at') if isinstance(state, dict) else None})


@app.route('/first-run', methods=['POST'])
@require_api_token
def set_first_run():
    """Record the tour's outcome ('done' or 'skipped'), or 'reset' to show it again."""
    body = request.get_json(silent=True) or {}
    outcome = body.get('outcome')
    try:
        if outcome == 'reset':
            _first_run_file().unlink(missing_ok=True)
            return jsonify({'completed': False, 'outcome': None, 'at': None})
        if outcome not in FIRST_RUN_OUTCOMES:
            return jsonify({'error': "outcome must be 'done', 'skipped' or 'reset'"}), 400
        state = {'outcome': outcome, 'at': datetime.now().isoformat(timespec='seconds')}
        # Finished before the first config generation made the directory.
        _first_run_file().parent.mkdir(parents=True, exist_ok=True)
        _first_run_file().write_text(json.dumps(state))
        return jsonify({'completed': True, **state})
    except OSError as e:
        return error_response(500, "Failed to record the welcome tour", e)


@app.route('/generate', methods=['POST'])
@require_api_token
def generate_config():
    """Generate SmokePing configuration"""
    try:
        result = api.generate_smokeping_config()
        return jsonify(result)
    except Exception as e:
        return error_response(500, "Failed to generate config", e)


@app.route('/restart', methods=['POST'])
@require_api_token
def restart_smokeping():
    """Restart SmokePing service"""
    try:
        result = api.restart_smokeping()
        return jsonify(result)
    except Exception as e:
        return error_response(500, "Failed to restart SmokePing", e)


@app.route('/wizard/adopt', methods=['POST'])
@require_api_token
def wizard_adopt_route():
    """Adopt the DNS wizard's selection as targets (add only).

    ``?dry_run=1`` says what would be added and changes nothing. One
    transaction and one regeneration, however many targets.
    """
    if not api.use_database:
        return jsonify({'error': 'Database not available'}), 400
    dry_run = request.args.get('dry_run') in ('1', 'true', 'yes')
    try:
        snapshot = wizard_adopt.read_snapshot()
    except wizard_adopt.Unavailable as e:
        return jsonify({'error': wizard_adopt.UNAVAILABLE.get(e.code, 'unavailable'),
                        'code': e.code}), 409
    try:
        max_services = int(os.environ.get('DNS_WIZARD_MAX') or 60)
    except ValueError:
        max_services = 60
    session = get_db_session()
    try:
        result = wizard_adopt.adopt(
            session, (Target, TargetCategory, Probe), snapshot,
            max_services=max_services, dry_run=dry_run,
        )
    except wizard_adopt.Unavailable as e:
        session.rollback()
        return jsonify({'error': wizard_adopt.UNAVAILABLE.get(e.code, 'unavailable'),
                        'code': e.code}), 409
    except Exception as e:
        session.rollback()
        return error_response(500, "Failed to adopt the DNS wizard's selection", e)
    finally:
        session.close()
    result['reloaded'] = None
    if not dry_run and result['targets_added']:
        try:
            result['reloaded'] = api._regenerate_smokeping_config()
        except Exception as e:
            return error_response(500, "Targets added, but regenerating the configuration failed", e)
    return jsonify(result)


@app.route('/oca/refresh', methods=['POST'])
@require_api_token
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
        return error_response(500, "OCA refresh failed", e)


# Database-specific endpoints for target management
@app.route('/targets', methods=['GET'])
@require_api_token
def get_targets():
    """Get all targets from database"""
    if not api.use_database:
        return jsonify({'error': 'Database not available, use /config/targets for YAML'}), 400
    
    try:
        session = get_db_session()
        try:
            target_repo = TargetRepository(session)
            
            # Get filter parameters
            active_only = request.args.get('active_only', 'false').lower() == 'true'
            category = request.args.get('category')
            
            targets = target_repo.get_all(active_only=active_only, category_name=category)
            
            return jsonify({
                'targets': [target.to_dict() for target in targets],
                'total': len(targets),
                'filters': {
                    'active_only': active_only,
                    'category': category
                }
            })
            
        finally:
            session.close()
            
    except Exception as e:
        return error_response(500, "Failed to get targets", e)


# Set on the probe, never on one target: SmokePing measures every target of
# a probe on the probe's step and ping count. These used to be accepted here
# and dropped without a word (models.TARGET_WRITABLE_FIELDS has neither).
PROBE_OWNED_FIELDS = ('step_seconds', 'pings')


def _probe_owned_fields_response(target_data):
    """A 400 naming the probe-owned fields in ``target_data``, or None."""
    if not isinstance(target_data, dict):
        return None
    fields = [f for f in PROBE_OWNED_FIELDS if f in target_data]
    if not fields:
        return None
    return error_response(
        400,
        f"{' and '.join(fields)} cannot be set on a target: every target "
        "of a probe is measured on that probe's step and ping count. "
        "Change the probe, or give the target another probe.",
        fields=fields,
    )


@app.route('/targets', methods=['POST'])
@require_api_token
def create_target():
    """Create new target in database"""
    if not api.use_database:
        return jsonify({'error': 'Database not available'}), 400
    
    try:
        if not request.is_json:
            raise BadRequest("Content-Type must be application/json")
        
        target_data = request.get_json()
        if not target_data:
            raise BadRequest("Empty request body")
        refused = _probe_owned_fields_response(target_data)
        if refused:
            return refused
        
        # Validate required fields
        required_fields = ['name', 'host', 'title', 'category_id', 'probe_id']
        for field in required_fields:
            if field not in target_data:
                raise BadRequest(f"Missing required field: {field}")
        
        session = get_db_session()
        try:
            target_repo = TargetRepository(session)
            target = target_repo.create(target_data)
            
            # Regenerate configuration after adding target
            reloaded = api._regenerate_smokeping_config()

            return jsonify({
                'success': True,
                'reloaded': reloaded,
                'target': target.to_dict(),
                'message': 'Target created successfully'
            }), 201
            
        finally:
            session.close()
            
    except BadRequest:
        return error_response(400, "Request body must be a non-empty JSON object")
    except Exception as e:
        return error_response(500, "Failed to create target", e)


@app.route('/targets/<int:target_id>', methods=['PUT'])
@require_api_token
def update_target(target_id):
    """Update target in database"""
    if not api.use_database:
        return jsonify({'error': 'Database not available'}), 400
    
    try:
        if not request.is_json:
            raise BadRequest("Content-Type must be application/json")
        
        target_data = request.get_json()
        if not target_data:
            raise BadRequest("Empty request body")
        refused = _probe_owned_fields_response(target_data)
        if refused:
            return refused
        
        session = get_db_session()
        try:
            target_repo = TargetRepository(session)
            target = target_repo.update(target_id, target_data)
            
            if not target:
                return jsonify({'error': 'Target not found'}), 404
            
            # Regenerate configuration after updating target
            reloaded = api._regenerate_smokeping_config()

            return jsonify({
                'success': True,
                'reloaded': reloaded,
                'target': target.to_dict(),
                'message': 'Target updated successfully'
            })
            
        finally:
            session.close()
            
    except BadRequest:
        return error_response(400, "Request body must be a non-empty JSON object")
    except Exception as e:
        return error_response(500, "Failed to update target", e)


@app.route('/targets/<int:target_id>', methods=['DELETE'])
@require_api_token
def delete_target(target_id):
    """Delete target from database"""
    if not api.use_database:
        return jsonify({'error': 'Database not available'}), 400
    
    try:
        session = get_db_session()
        try:
            target_repo = TargetRepository(session)
            success = target_repo.delete(target_id)
            
            if not success:
                return jsonify({'error': 'Target not found'}), 404
            
            # Regenerate configuration after deleting target
            reloaded = api._regenerate_smokeping_config()

            return jsonify({
                'success': True,
                'reloaded': reloaded,
                'message': 'Target deleted successfully'
            })
            
        finally:
            session.close()
            
    except Exception as e:
        return error_response(500, "Failed to delete target", e)


@app.route('/targets/<int:target_id>/toggle', methods=['POST'])
@require_api_token
def toggle_target(target_id):
    """Toggle target active status"""
    if not api.use_database:
        return jsonify({'error': 'Database not available'}), 400
    
    try:
        session = get_db_session()
        try:
            target_repo = TargetRepository(session)
            target = target_repo.toggle_active(target_id)
            
            if not target:
                return jsonify({'error': 'Target not found'}), 404
            
            # Regenerate configuration after toggling target
            reloaded = api._regenerate_smokeping_config()

            return jsonify({
                'success': True,
                'reloaded': reloaded,
                'target': target.to_dict(),
                'message': f"Target {'activated' if target.is_active else 'deactivated'} successfully"
            })
            
        finally:
            session.close()
            
    except Exception as e:
        return error_response(500, "Failed to toggle target", e)


@app.route('/categories', methods=['GET'])
@require_api_token
def get_categories():
    """Get all target categories"""
    if not api.use_database:
        return jsonify({'error': 'Database not available'}), 400
    
    try:
        session = get_db_session()
        try:
            category_repo = CategoryRepository(session)
            categories = category_repo.get_all()
            
            return jsonify({
                'categories': [{
                    'id': cat.id,
                    'name': cat.name,
                    'display_name': cat.display_name,
                    'description': cat.description
                } for cat in categories]
            })
            
        finally:
            session.close()
            
    except Exception as e:
        return error_response(500, "Failed to get categories", e)


@app.route('/assistant', methods=['GET'])
@require_api_token
def get_assistant():
    """Is a chat assistant calling the MCP server? (assistant.py)"""
    try:
        return jsonify(api.assistant_status())
    except Exception as e:
        return error_response(503, "Could not read the MCP server's state", e,
                              available=False)


# What a probe's cycle may be set to (PUT /probes/<name>). Steps from one
# minute to one hour; SmokePing's shipped step is 300. At least 3 pings, so
# "more than 1.5 pings' worth lost" (common.cadence.EVENT_LOST_PINGS) is
# not every single loss; at most 20, what the Database section defaults to.
PROBE_STEPS = (60, 120, 300, 600, 900, 1800, 3600)
PROBE_MIN_PINGS = 3
PROBE_MAX_PINGS = 20
PROBE_EDITABLE = ('step_seconds', 'pings')


# SmokePing's own defaults (Smokeping/probes/*.pm): fping waits 1 s between
# packets to one target and runs every target at once; the basefork probes
# (DNS, TCPPing, Curl) give each ping up to `timeout` seconds -- 5, and 10
# for Curl -- one after another, `forks` targets at a time (default 5).
FPING_PACKET_GAP_S = 1.0
BASEFORK_TIMEOUT_S = 5.0
CURL_TIMEOUT_S = 10.0
BASEFORK_FORKS = 5


def probe_worst_seconds(probe, pings: int, active_targets: int) -> float:
    """How long one cycle of ``probe`` can take, every ping timing out."""
    options = probe.options or {}
    kind = probe.module or probe.name
    try:
        if kind.startswith('FPing'):
            return pings * float(options.get('hostinterval') or FPING_PACKET_GAP_S)
        default = CURL_TIMEOUT_S if kind == 'Curl' else BASEFORK_TIMEOUT_S
        per_ping = float(options.get('timeout') or default)
    except (TypeError, ValueError):
        return 0.0
    forks = probe.forks or BASEFORK_FORKS
    batches = max(1, -(-int(active_targets) // forks))
    return batches * pings * per_ping


def probe_cadence_problem(step: Any, pings: Any, worst=None):
    """Why ``step``/``pings`` cannot be a probe's cycle, or None.

    ``worst`` is a callable giving how long a cycle of ``pings`` can take
    (probe_worst_seconds): a cycle longer than the step would overlap the
    next, and SmokePing would fall behind on every target of the probe.
    """
    if not isinstance(step, int) or isinstance(step, bool) or step not in PROBE_STEPS:
        allowed = ', '.join(str(s) for s in PROBE_STEPS)
        return f"step_seconds must be one of {allowed}"
    if (not isinstance(pings, int) or isinstance(pings, bool)
            or not PROBE_MIN_PINGS <= pings <= PROBE_MAX_PINGS):
        return f"pings must be between {PROBE_MIN_PINGS} and {PROBE_MAX_PINGS}"
    seconds = worst(pings) if worst is not None else 0.0
    if seconds > step:
        return (f"{pings} pings can take up to {seconds:g} s when they time out, "
                f"longer than a {step} s step")
    return None


@app.route('/probes/<name>', methods=['PUT'])
@require_api_token
def update_probe(name):
    """Change a probe's step and/or pings, then regenerate and reload.

    Every target of the probe starts a new RRD: the reload's RRD guard
    moves the old files to /data/.archive/ (see _guard_rrds), because
    SmokePing refuses, fatally, to load an RRD made for another cycle. The
    response says how many targets that concerns and what the guard did.
    """
    if not api.use_database:
        return jsonify({'error': 'Database not available'}), 400
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not any(k in body for k in PROBE_EDITABLE):
        return error_response(400, "Send step_seconds and/or pings")
    unknown = sorted(k for k in body if k not in PROBE_EDITABLE)
    if unknown:
        return error_response(
            400, "Only step_seconds and pings can be changed here", fields=unknown)
    try:
        session = get_db_session()
        try:
            probe = ProbeRepository(session).get_by_name(name)
            if probe is None:
                return jsonify({'error': 'Probe not found'}), 404
            previous = {'step_seconds': probe.step_seconds, 'pings': probe.pings}
            wanted = {**previous, **{k: body[k] for k in PROBE_EDITABLE if k in body}}
            targets = session.query(Target).filter(Target.probe_id == probe.id)
            counts = {'targets': targets.count(),
                      'active_targets': targets.filter(Target.is_active.is_(True)).count()}
            problem = probe_cadence_problem(
                wanted['step_seconds'], wanted['pings'],
                lambda n: probe_worst_seconds(probe, n, counts['active_targets']))
            if problem:
                return error_response(400, problem)
            if wanted == previous:
                return jsonify({'success': True, 'changed': False, 'probe': name,
                                'previous': previous, **counts})
            probe.step_seconds = wanted['step_seconds']
            probe.pings = wanted['pings']
            session.commit()
        finally:
            session.close()
        try:
            reloaded = api._regenerate_smokeping_config()
        except Exception as e:
            # Put the row back: otherwise the database says the new cycle
            # while SmokePing still runs the old one, a retry answers
            # "nothing changed", and the next unrelated regeneration applies
            # it by surprise.
            session = get_db_session()
            try:
                probe = ProbeRepository(session).get_by_name(name)
                probe.step_seconds = previous['step_seconds']
                probe.pings = previous['pings']
                session.commit()
            finally:
                session.close()
            return error_response(
                500, "The configuration could not be regenerated; "
                     "the probe keeps its previous cycle", e)
        logger.info("Probe %s: step %s -> %s, pings %s -> %s", name,
                    previous['step_seconds'], wanted['step_seconds'],
                    previous['pings'], wanted['pings'])
        return jsonify({
            'success': True, 'changed': True, 'probe': name,
            'previous': previous, 'current': wanted, **counts,
            'reloaded': reloaded, 'rrd_guard': api.last_rrd_guard,
        })
    except Exception as e:
        return error_response(500, "Failed to update probe", e)


@app.route('/probes', methods=['GET'])
@require_api_token
def get_probes():
    """Get all probes"""
    if not api.use_database:
        return jsonify({'error': 'Database not available'}), 400
    
    try:
        session = get_db_session()
        try:
            probe_repo = ProbeRepository(session)
            probes = probe_repo.get_all()
            
            return jsonify({
                'probes': [{
                    'id': probe.id,
                    'name': probe.name,
                    'binary_path': probe.binary_path,
                    'step_seconds': probe.step_seconds,
                    'pings': probe.pings,
                    'forks': probe.forks,
                    'is_default': probe.is_default,
                    'module': probe.module,
                    'options': probe.options or {},
                    'targets': len(probe.targets),
                    'active_targets': sum(1 for t in probe.targets if t.is_active),
                } for probe in probes]
            })
            
        finally:
            session.close()
            
    except Exception as e:
        return error_response(500, "Failed to get probes", e)


# Container resolution endpoints
COMPOSE_PROJECT_LABEL = 'com.docker.compose.project'
COMPOSE_SERVICE_LABEL = 'com.docker.compose.service'


def compose_project_name() -> str:
    """The Compose project this config-manager belongs to.

    Every edition's compose file passes ``COMPOSE_PROJECT_NAME`` in, defaulted
    to the edition directory name, which is also what Compose itself would
    derive. The literal below is only a last resort for a config-manager
    started by hand without the variable.
    """
    return os.environ.get('COMPOSE_PROJECT_NAME', 'pro')


def belongs_to_project(container, project_name: str) -> bool:
    """Whether a container was started by this stack's Compose project.

    The label is the whole test. Container *names* are not evidence: the
    project is called ``pro`` by default, so a name test also claims an
    unrelated ``prometheus`` or ``proxy`` running on the same host. Compose
    labels every container it starts, including the ones that set an explicit
    ``container_name``, so nothing that belongs to us is missed by asking.
    """
    return container.labels.get(COMPOSE_PROJECT_LABEL) == project_name


def resolve_container_name(service_name: str) -> str:
    """Resolve the container name for a service of *this* Compose project.

    Both labels have to match. The service label on its own is not an
    identity: a host running two editions side by side has two containers
    labeled ``smokeping``, and whichever one the daemon listed first would
    win — so ``POST /restart``, which the web admin's restart button calls,
    could restart the other edition's SmokePing.

    Stopped containers are included, because resolving one is how a caller
    asks for its status or restarts it.
    """
    project_name = compose_project_name()
    try:
        client = docker.from_env()
        for container in client.containers.list(all=True):
            if (belongs_to_project(container, project_name)
                    and container.labels.get(COMPOSE_SERVICE_LABEL) == service_name):
                return container.name
    except Exception as e:
        logger.error(
            f"Error resolving container for service '{service_name}' in "
            f"project '{project_name}': {e}"
        )
        raise Exception(f"Container for service '{service_name}' not found")

    raise Exception(f"Container for service '{service_name}' not found")


@app.route('/api/containers/<service_name>', methods=['GET'])
@require_api_token
def get_container_name(service_name):
    """Get actual container name for a compose service"""
    try:
        container_name = resolve_container_name(service_name)
        return jsonify({
            'success': True,
            'service': service_name,
            'container_name': container_name
        })
    except Exception as e:
        return error_response(404, "Container not found or Docker unavailable", e,
                              success=False, service=service_name)


@app.route('/api/containers', methods=['GET'])
@require_api_token
def list_containers():
    """List all containers in the compose stack"""
    try:
        client = docker.from_env()
        project_name = compose_project_name()
        
        containers = []
        # Running only, unlike resolve_container_name: this answers "what is
        # up right now", and a stopped container has no status worth listing.
        # Resolution includes stopped ones because restarting one is the
        # point of asking.
        for container in client.containers.list():
            if belongs_to_project(container, project_name):
                containers.append({
                    'name': container.name,
                    'service': container.labels.get(COMPOSE_SERVICE_LABEL, 'unknown'),
                    'status': container.status,
                    'id': container.short_id
                })
        
        return jsonify({
            'success': True,
            'project': project_name,
            'containers': containers
        })
    except Exception as e:
        return error_response(500, "Could not list containers", e, success=False)


@app.route('/api/containers/<service_name>/status', methods=['GET'])
@require_api_token
def get_container_status(service_name):
    """Get status of a specific container"""
    try:
        container_name = resolve_container_name(service_name)
        client = docker.from_env()
        container = client.containers.get(container_name)
        
        return jsonify({
            'success': True,
            'service': service_name,
            'container_name': container_name,
            'status': container.status,
            'health': container.attrs.get('State', {}).get('Health', {}).get('Status', 'none')
        })
    except Exception as e:
        return error_response(404, "Container not found or Docker unavailable", e,
                              success=False, service=service_name)


@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404


@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({'error': 'Method not allowed'}), 405


@app.route('/docs')
@require_api_token
def api_documentation():
    """API documentation endpoint"""
    return """
    <h1>SmokePing Config Manager API</h1>
    <p>REST API for managing SmokePing configuration</p>
    <h2>Configuration Endpoints:</h2>
    <ul>
        <li><strong>GET /health</strong> - Health check</li>
        <li><strong>GET /status</strong> - Service status (includes database status)</li>
        <li><strong>GET /config</strong> - Get all configurations (YAML or database)</li>
        <li><strong>GET /config/{type}</strong> - Get specific configuration (targets, probes, sources)</li>
        <li><strong>PUT /config/{type}</strong> - Update specific configuration (YAML mode only)</li>
        <li><strong>POST /generate</strong> - Generate SmokePing configuration</li>
        <li><strong>POST /restart</strong> - Restart SmokePing service</li>
        <li><strong>POST /oca/refresh</strong> - Refresh OCA data</li>
    </ul>
    <h2>Database Endpoints (when PostgreSQL is available):</h2>
    <ul>
        <li><strong>GET /targets</strong> - Get all targets (supports ?active_only=true&category=name)</li>
        <li><strong>POST /targets</strong> - Create new target</li>
        <li><strong>PUT /targets/{id}</strong> - Update target by ID</li>
        <li><strong>DELETE /targets/{id}</strong> - Delete target by ID</li>
        <li><strong>POST /targets/{id}/toggle</strong> - Toggle target active status</li>
        <li><strong>GET /categories</strong> - Get all target categories</li>
        <li><strong>GET /probes</strong> - Get all probe configurations</li>
    </ul>
    <h2>Container Management Endpoints:</h2>
    <ul>
        <li><strong>GET /api/containers</strong> - List all containers in the stack</li>
        <li><strong>GET /api/containers/{service}</strong> - Get actual container name for a service</li>
        <li><strong>GET /api/containers/{service}/status</strong> - Get container status and health</li>
    </ul>
    <p>The API automatically detects whether to use PostgreSQL database or YAML files based on migration status.</p>
    """


@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500


if __name__ == '__main__':
    logger.info("Starting Config Manager REST API")
    initialize()
    app.run(host='0.0.0.0', port=5000, debug=False)