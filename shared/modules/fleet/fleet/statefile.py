"""Reading the small JSON state files the entrypoints decide their state from."""

from __future__ import annotations

import json
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
