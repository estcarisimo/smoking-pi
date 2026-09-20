#!/usr/bin/env python3
"""Refuse a docker-compose.packaged.yml that drops more than the code mounts.

The packaged override uses ``!override`` on a service's ``volumes`` (Compose
cannot remove one list entry), so every non-code volume is repeated there
by hand — and a volume added to the base file later would silently vanish
in packaged mode. This renders both files through Compose itself and
compares: the packaged stack must keep every mount of the base stack except
bind mounts whose source lies under ``shared/modules`` (the code the images
already carry), and must add none.

Usage: check-packaged-override.py <edition dir> [--env-file PATH]
Exit 0 when they agree; 1 with the differences listed otherwise.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

CODE_MARKER = "/shared/modules/"


def render(edition: Path, files: list[str], env_file: str | None) -> dict:
    cmd = ["docker", "compose"]
    if env_file:
        cmd += ["--env-file", env_file]
    for f in files:
        cmd += ["-f", f]
    cmd += ["config", "--format", "json"]
    out = subprocess.run(cmd, cwd=edition, check=True, capture_output=True, text=True)
    return json.loads(out.stdout)


def mounts(config: dict) -> dict[str, set[str]]:
    """service -> {"type source -> target"} for every volume."""
    result: dict[str, set[str]] = {}
    for name, svc in config.get("services", {}).items():
        entries = set()
        for v in svc.get("volumes", []) or []:
            entries.add(f"{v.get('type')} {v.get('source')} -> {v.get('target')}")
        result[name] = entries
    return result


def is_code_mount(entry: str) -> bool:
    return entry.startswith("bind ") and CODE_MARKER in entry


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("edition", type=Path)
    ap.add_argument("--env-file")
    args = ap.parse_args()

    base = mounts(render(args.edition, ["docker-compose.yml"], args.env_file))
    packaged = mounts(
        render(args.edition, ["docker-compose.yml", "docker-compose.packaged.yml"], args.env_file)
    )

    problems: list[str] = []
    dropped_code = 0
    for service, entries in base.items():
        after = packaged.get(service, set())
        for entry in entries - after:
            if is_code_mount(entry):
                dropped_code += 1
            else:
                problems.append(f"{service}: packaged mode DROPS {entry}")
        for entry in after - entries:
            problems.append(f"{service}: packaged mode ADDS {entry}")
    for service, entries in packaged.items():
        for entry in entries:
            if is_code_mount(entry):
                problems.append(f"{service}: packaged mode still mounts code: {entry}")

    if problems:
        print("\n".join(problems))
        return 1
    print(
        f"packaged override OK: {dropped_code} code mount(s) dropped, "
        f"every other volume of {len(base)} services kept"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
