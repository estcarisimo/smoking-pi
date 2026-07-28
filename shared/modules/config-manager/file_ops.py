"""
Shared file utilities for the config manager.

Provides atomic file writes (temp file + os.replace) and a single
cross-process file lock used to serialize startup initialization and
config generation across gunicorn workers.
"""

import logging
import os
import tempfile
from pathlib import Path

import yaml
from filelock import FileLock

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", BASE_DIR / "config"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", BASE_DIR / "output"))

# Single lock instance per process. filelock's FileLock is re-entrant on the
# same object, but two objects on the same path within one process would
# deadlock - so always hand out the same instance.
_config_lock = None


def get_config_lock(timeout: float = 120.0) -> FileLock:
    """Return the shared cross-process lock for config/init/generation."""
    global _config_lock
    if _config_lock is None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _config_lock = FileLock(
            str(CONFIG_DIR / ".config-manager.lock"), timeout=timeout
        )
    return _config_lock


def atomic_write_text(path, content: str) -> None:
    """Write text to path atomically (temp file in same dir + os.replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_yaml(path, data) -> None:
    """Serialize data as YAML and write it atomically."""
    atomic_write_text(
        path, yaml.dump(data, default_flow_style=False, sort_keys=False)
    )
