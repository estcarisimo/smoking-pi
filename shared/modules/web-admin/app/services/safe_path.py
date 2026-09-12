"""Confine a caller-supplied filename to one directory.

Every path the web-admin builds from request data goes through here: the
CrUX and Cloudflare cache files are named after a ``country`` query
parameter, and the AI reports page takes a ``file`` parameter. Each caller
already allow-lists the value with a regex, but a regex is a promise about
the *string*; this is a check on the *path* -- normalise, then require that
the result still sits inside the base directory. It is also the shape CodeQL
recognises as a sanitizer, which the regex alone is not.
"""

from __future__ import annotations

import os
from pathlib import Path


class UnsafePath(ValueError):
    """A name that would resolve outside its directory."""


def confine(base: Path | str, name: str) -> Path:
    """Return ``base/name`` as a Path, or raise :class:`UnsafePath`.

    ``name`` must be a single path component: no separators, no ``..``. The
    normalised join must start with the normalised base plus a separator, so
    ``base`` itself and any sibling that merely shares a prefix are rejected.
    """
    base_norm = os.path.normpath(str(base))
    candidate = os.path.normpath(os.path.join(base_norm, str(name)))
    if not candidate.startswith(base_norm + os.sep):
        raise UnsafePath(f"refusing path outside {base_norm}")
    # Backslash is not a separator on Linux, so normpath leaves it alone --
    # but the same name copied to a Windows checkout would be one. Refuse it
    # rather than depend on the platform.
    if any(sep in str(name) for sep in (os.sep, '\\', '/')):
        raise UnsafePath("refusing a name with a directory separator")
    return Path(candidate)
