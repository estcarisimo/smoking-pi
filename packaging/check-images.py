#!/usr/bin/env python3
"""Refuse a compose file, Dockerfile or release matrix that disagree on images.

Three things name the published images and must say the same:

* every ``build:`` service in the editions' compose files declares
  ``image: <registry>/<module>:<version>`` and ``pull_policy: missing``,
  where ``<module>`` is the directory of the Dockerfile it builds -- so the
  release workflow's ``ghcr.io/estcarisimo/smoking-pi/<module>`` is what a
  packaged install pulls, and a clone (``:dev``, never published) builds;
* every ``shared/modules/*/Dockerfile`` is built by some edition;
* the release workflow's build matrix lists exactly those modules, twice
  (the merge job repeats it).

Usage: check-images.py [--repo-root PATH] [--env-file PATH]
Exit 0 when they agree; 1 with the differences listed otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

EDITIONS = ("basic", "standard", "pro")
ALL_PROFILES = "influxdb,mcp,alerts,ai,clickhouse"
WORKFLOW = ".github/workflows/release.yml"
REGISTRY_DEFAULT = "ghcr.io/estcarisimo/smoking-pi"


def render(edition: Path, env_file: str | None) -> dict:
    files = ["docker-compose.yml"]
    if (edition / "docker-compose.clickhouse.yml").exists():
        files.append("docker-compose.clickhouse.yml")
    cmd = ["docker", "compose"]
    if env_file:
        cmd += ["--env-file", env_file]
    for f in files:
        cmd += ["-f", f]
    cmd += ["config", "--format", "json"]
    # The developer's shell must not decide the defaults under test. (The
    # edition's own .env still applies -- Compose reads it regardless -- so a
    # local .env carrying SMOKING_PI_VERSION would show up here as a
    # "default tag" problem; none of the templates declare it.)
    env = {k: v for k, v in os.environ.items() if not k.startswith("SMOKING_PI_")}
    env["COMPOSE_PROFILES"] = ALL_PROFILES
    out = subprocess.run(
        cmd, cwd=edition, check=True, capture_output=True, text=True, env=env
    )
    return json.loads(out.stdout)


def module_of(build: dict | str, edition: Path) -> str:
    """The shared/modules/<module> directory a build block's Dockerfile lives in."""
    if isinstance(build, str):
        context, dockerfile = build, "Dockerfile"
    else:
        context = build.get("context", ".")
        dockerfile = build.get("dockerfile", "Dockerfile")
    path = (edition / context / dockerfile).resolve()
    parts = path.parts
    if "modules" not in parts:
        raise ValueError(f"{path} is not under shared/modules")
    idx = len(parts) - 1 - parts[::-1].index("modules")
    return parts[idx + 1]


def matrix_services(workflow_text: str) -> list[list[str]]:
    """Every `service:` list in the workflow, in order."""
    lists: list[list[str]] = []
    for block in re.finditer(
        r"^\s+service:\n((?:\s+- [a-z-]+\n)+)", workflow_text, re.MULTILINE
    ):
        lists.append(re.findall(r"- ([a-z-]+)", block.group(1)))
    return lists


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    ap.add_argument("--env-file")
    args = ap.parse_args()
    root: Path = args.repo_root

    problems: list[str] = []
    built: set[str] = set()
    checked = 0
    for name in EDITIONS:
        edition = root / "editions" / name
        config = render(edition, args.env_file)
        for svc, spec in config.get("services", {}).items():
            if "build" not in spec:
                continue
            try:
                module = module_of(spec["build"], edition)
            except ValueError as exc:
                problems.append(f"{name}/{svc}: {exc}")
                continue
            built.add(module)
            checked += 1
            image = spec.get("image", "")
            want = re.compile(rf"^(?P<reg>.+)/{re.escape(module)}:(?P<ver>[^/:]+)$")
            m = want.match(image)
            if not m:
                problems.append(
                    f"{name}/{svc}: image is {image!r}, expected "
                    f"<registry>/{module}:<version>"
                )
            elif not args.env_file and m.group("reg") != REGISTRY_DEFAULT:
                problems.append(
                    f"{name}/{svc}: default registry is {m.group('reg')!r}, "
                    f"expected {REGISTRY_DEFAULT!r}"
                )
            elif not args.env_file and m.group("ver") != "dev":
                problems.append(
                    f"{name}/{svc}: default tag is {m.group('ver')!r}; a clone must "
                    "build, so the default must be `dev` (never published)"
                )
            if spec.get("pull_policy") != "missing":
                problems.append(
                    f"{name}/{svc}: pull_policy is {spec.get('pull_policy')!r}, "
                    "expected 'missing' (pull the published image, build only if absent)"
                )

    dockerfiles = {
        p.parent.name for p in (root / "shared" / "modules").glob("*/Dockerfile")
    }
    for module in sorted(dockerfiles - built):
        problems.append(f"shared/modules/{module}/Dockerfile is built by no edition")
    for module in sorted(built - dockerfiles):
        problems.append(
            f"{module} is built by an edition but has no shared/modules Dockerfile"
        )

    lists = matrix_services((root / WORKFLOW).read_text())
    if len(lists) < 2:
        problems.append(
            f"{WORKFLOW}: expected the service matrix in the build and merge jobs"
        )
    for i, services in enumerate(lists):
        if set(services) != dockerfiles:
            problems.append(
                f"{WORKFLOW}: service list #{i + 1} is {sorted(services)}, "
                f"Dockerfiles are {sorted(dockerfiles)}"
            )

    if problems:
        print("\n".join(problems))
        return 1
    print(
        f"images OK: {checked} built services across {len(EDITIONS)} editions name "
        f"{len(dockerfiles)} modules; the release matrix builds the same {len(dockerfiles)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
