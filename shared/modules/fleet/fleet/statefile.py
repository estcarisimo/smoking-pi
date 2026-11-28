"""The small state files: reading what the entrypoints decide their state
from, and writing them atomically (fleet/nodestate.py)."""

from __future__ import annotations

import json
import os
from pathlib import Path

ABSENT = "absent"
CORRUPT = "corrupt"


def read_object(path: Path) -> dict | str:
    """The file's JSON object; ABSENT when there is no file; CORRUPT when it
    cannot be read, is not JSON, or is not an object. A corrupt file is
    never treated as absent: absent means "start over", corrupt means
    "stop and tell a person"."""
    if not path.exists():
        return ABSENT
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return CORRUPT
    return data if isinstance(data, dict) else CORRUPT


def write_bytes(path: Path, data: bytes, mode: int = 0o644) -> None:
    """Write a file atomically with ``mode`` from its creation on: a reader
    sees the old file or the new one, never half of one, and a secret file
    is never readable by others, not even for an instant."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)  # whatever the umask said
        os.replace(tmp, path)
        fsync_dir(path.parent)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def fsync_dir(directory: Path) -> None:
    """Make a rename in ``directory`` survive a power cut (the file's own
    fsync does not cover its directory entry). Best effort where a
    directory cannot be opened."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def replace(src: Path, dst: Path) -> None:
    """os.replace, durable (fsync of the directory after it)."""
    os.replace(src, dst)
    fsync_dir(dst.parent)


def write_object(path: Path, data: dict) -> None:
    """A JSON object, atomically (see write_bytes)."""
    write_bytes(path, (json.dumps(data, indent=1, sort_keys=True) + "\n").encode(), 0o600)


def remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
