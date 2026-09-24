#!/usr/bin/env python3
"""Move aside every RRD that SmokePing would refuse to load.

SmokePing checks each target's RRD against its probe when it loads its
configuration, and a different ``step`` or a different number of pings (one
``pingN`` data source per ping) is fatal: the daemon dies with "RRD
parameter mismatch ... You must delete <file>.rrd" (Smokeping.pm,
init_target_tree), and every target stops being measured -- not only the
one whose file disagrees. That happens whenever a probe's step or pings
change, and also when a paused target comes back, or a deleted one is added
again under the same name, after its probe changed.

config-manager runs this with ``docker exec`` before every reload, passing
what the NEW configuration expects as one JSON argument:

    {"expected": {"websites/Google.rrd": {"step": 300, "pings": 10}, ...}}

Each existing RRD in that map whose step or ping count differs is moved,
not deleted, to ``<datadir>/.archive/<UTC stamp>/<same path>``: SmokePing
then creates a fresh one, and the old history can still be read with
rrdtool. A dot directory, so the exporters' globs never export it as a
target. Prints one JSON object:

    {"checked": 30, "archived": [{"rrd": "websites/Google.rrd",
      "had": {"step": 300, "pings": 10}, "wants": {"step": 60, "pings": 10},
      "to": ".archive/20260924T150000Z/websites/Google.rrd"}], "errors": []}

A file it cannot read is reported in ``errors`` and left in place: moving
history away on a guess is worse than a reload that fails loudly. With
``"dry_run": true`` nothing moves, and ``archived`` lists what would.

It runs while SmokePing is still up (before the reload signal). A probe
round that writes to a file at the moment it is renamed writes into the
archived copy -- a rename keeps open files valid -- so at worst one point
lands in the archive instead of the fresh file. Nothing is lost or torn.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

DATADIR = Path("/data")
ARCHIVE = ".archive"

_STEP = re.compile(r"^step = (\d+)$", re.M)
_PING_DS = re.compile(r"^ds\[ping(\d+)\]\.index = ", re.M)


def parse_info(text: str) -> dict[str, int] | None:
    """``rrdtool info`` output -> ``{"step": s, "pings": n}``, or None."""
    step = _STEP.search(text)
    if not step:
        return None
    pings = max((int(n) for n in _PING_DS.findall(text)), default=0)
    return {"step": int(step.group(1)), "pings": pings}


def rrd_info(path: Path) -> str:
    return subprocess.run(
        ["rrdtool", "info", str(path)],
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout


def _confined(datadir: Path, rel: str) -> Path | None:
    """``datadir/rel`` when it stays inside datadir and outside the archive."""
    if not rel.endswith(".rrd") or rel.startswith(ARCHIVE + "/"):
        return None
    path = (datadir / rel).resolve()
    root = datadir.resolve()
    if root not in path.parents:
        return None
    return path


def guard(
    expected: dict[str, dict[str, int]],
    datadir: Path = DATADIR,
    info: Callable[[Path], str] = rrd_info,
    stamp: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Archive the RRDs in ``expected`` that disagree with it; report."""
    stamp = stamp or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    report: dict[str, Any] = {"checked": 0, "archived": [], "errors": []}
    for rel, wants in sorted(expected.items()):
        try:
            want = {"step": int(wants["step"]), "pings": int(wants["pings"])}
        except (KeyError, TypeError, ValueError):
            report["errors"].append({"rrd": rel, "error": "expected needs integer step and pings"})
            continue
        path = _confined(datadir, rel)
        if path is None:
            report["errors"].append({"rrd": rel, "error": "not a path under the datadir"})
            continue
        if not path.is_file():
            continue  # SmokePing creates it
        report["checked"] += 1
        try:
            had = parse_info(info(path))
        except (OSError, subprocess.SubprocessError) as exc:
            report["errors"].append({"rrd": rel, "error": f"rrdtool info failed: {exc}"})
            continue
        if had is None:
            report["errors"].append({"rrd": rel, "error": "no step in rrdtool info"})
            continue
        if had == want:
            continue
        dest = datadir / ARCHIVE / stamp / rel
        if dry_run:
            report["archived"].append({
                "rrd": rel, "had": had, "wants": want,
                "to": str(dest.relative_to(datadir)), "dry_run": True,
            })
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, dest)
        except OSError as exc:
            report["errors"].append({"rrd": rel, "error": f"could not archive: {exc}"})
            continue
        report["archived"].append({
            "rrd": rel, "had": had, "wants": want,
            "to": str(dest.relative_to(datadir)),
        })
    return report


def main(argv: list[str]) -> int:
    try:
        payload = json.loads(argv[1])
        expected = payload["expected"]
        if not isinstance(expected, dict):
            raise TypeError("expected must be an object")
    except (IndexError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": f"usage: rrd_guard.py '<json>': {exc}"}))
        return 2
    datadir = Path(payload.get("datadir") or DATADIR)
    print(json.dumps(guard(expected, datadir, dry_run=bool(payload.get("dry_run")))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
