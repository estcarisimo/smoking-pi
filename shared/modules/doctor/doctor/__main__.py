"""CLI for the instrumentation doctor.

    python -m doctor --repo-root /path/to/smoking-pi
    python -m doctor --json

Exit code is 1 if any check FAILs, so CI can gate on it. Warnings do not fail
the build.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from . import static_checks
from .report import Report


def _default_repo_root() -> pathlib.Path:
    """Walk up from this file to the repository root, else use the cwd.

    The package normally lives at shared/modules/doctor/doctor/, so the repo
    root is four levels up — but only trust that if it looks like the repo.
    """
    here = pathlib.Path(__file__).resolve()
    for candidate in list(here.parents)[:5]:
        if (candidate / "shared/modules/grafana/provisioning").is_dir():
            return candidate
    return pathlib.Path.cwd()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doctor",
        description=(
            "Verify the monitoring instrumentation, not the network: catch a "
            "measurement or panel that looks fine and silently charts nothing."
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=pathlib.Path,
        default=None,
        help="repository root (default: auto-detected from this file, else cwd)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="list findings for passing checks too",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.repo_root or _default_repo_root()
    repo = static_checks.Repo(root.resolve())

    report = Report(static_checks.run_all(repo))
    if args.json:
        print(report.render_json())
    else:
        print(f"instrumentation doctor — static checks against {repo.root}\n")
        print(report.render_text(verbose=args.verbose))
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
