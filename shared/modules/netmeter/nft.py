"""Running nft: load a script, reset and read the counters, tear down."""

from __future__ import annotations

import json
import logging
import subprocess

import ruleset

log = logging.getLogger("netmeter")

NFT = "nft"


class NftError(RuntimeError):
    """nft refused a script or could not be run."""


def _run(args: list[str], stdin: str | None = None, runner=subprocess.run) -> str:
    try:
        proc = runner([NFT, *args], input=stdin, capture_output=True, text=True,
                      timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        raise NftError(f"nft could not run: {type(e).__name__}") from None
    if proc.returncode != 0:
        # nft's message names the rule it refused; it carries no secret.
        lines = (proc.stderr or "").strip().splitlines()
        raise NftError(lines[-1] if lines else "nft failed")
    return proc.stdout


def load(script: str, runner=subprocess.run) -> None:
    _run(["-f", "-"], stdin=script, runner=runner)


def parse_counters(text: str) -> dict[str, tuple[int, int]]:
    """``nft -j`` counter output -> {name: (packets, bytes)}."""
    try:
        body = json.loads(text or "{}")
    except ValueError:
        return {}
    out = {}
    for item in body.get("nftables", []) if isinstance(body, dict) else []:
        c = item.get("counter") if isinstance(item, dict) else None
        if isinstance(c, dict) and c.get("table") == ruleset.TABLE and "name" in c:
            try:
                out[c["name"]] = (int(c.get("packets", 0)), int(c.get("bytes", 0)))
            except (TypeError, ValueError):
                continue
    return out


def reset_counters(runner=subprocess.run) -> dict[str, tuple[int, int]]:
    """Read every counter of the table and zero it, atomically per counter:
    what comes back is exactly what was counted since the last reset."""
    return parse_counters(_run(["-j", "reset", "counters", "table", ruleset.FAMILY,
                                ruleset.TABLE], runner=runner))


def teardown(runner=subprocess.run) -> None:
    load(ruleset.teardown(), runner=runner)
