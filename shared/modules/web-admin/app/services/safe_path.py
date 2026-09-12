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
    name = str(name)
    # "" joins to the base itself and ".." to its parent; on a root base the
    # prefix check below cannot tell either from a child, so refuse by name.
    if name in ('', '.', '..'):
        raise UnsafePath("refusing an empty or relative name")
    # Backslash is not a separator on Linux, so normpath leaves it alone --
    # but the same name copied to a Windows checkout would be one. Refuse it
    # rather than depend on the platform.
    if any(sep in name for sep in (os.sep, '\\', '/')):
        raise UnsafePath("refusing a name with a directory separator")
    base_norm = os.path.normpath(str(base))
    # A prefix that already ends in the separator (the root) must not get a
    # second one, or nothing under it ever passes.
    prefix = base_norm if base_norm.endswith(os.sep) else base_norm + os.sep
    candidate = os.path.normpath(os.path.join(base_norm, name))
    if not candidate.startswith(prefix):
        raise UnsafePath(f"refusing path outside {base_norm}")
    # The lexical check above is what CodeQL scores, and it is enough for a
    # name. It is not enough for a symlink already sitting in the directory:
    # base/latest.md -> /etc/passwd passes lexically and open() follows it.
    # The cache directories are container-private and the reports directory
    # is mounted read-only, so a planted link is not today's threat -- but
    # the check is one realpath, so make it.
    real_base = os.path.realpath(base_norm)
    real_prefix = real_base if real_base.endswith(os.sep) else real_base + os.sep
    if not os.path.realpath(candidate).startswith(real_prefix):
        raise UnsafePath(f"refusing a link out of {base_norm}")
    return Path(candidate)
