#!/usr/bin/env python3
"""Build the OpenClaw skill from its header and the shared answering guide.

examples/openclaw/smokeping-monitoring/SKILL.md is GENERATED: the
OpenClaw-only header (openclaw-header.md, next to it) followed by
shared/modules/mcp-server/guide.py's GUIDE -- the text every assistant
receives as the MCP server's instructions. One source, so the skill and
every other assistant cannot drift apart; the MCP server's tests fail when
the committed SKILL.md is stale.

    python3 shared/scripts/build-openclaw-skill.py           # rewrite SKILL.md
    python3 shared/scripts/build-openclaw-skill.py --check   # exit 1 if stale

Then install it: ./shared/scripts/install-openclaw-skill.sh --reload
"""

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = ROOT / "examples" / "openclaw" / "smokeping-monitoring"
GUIDE = ROOT / "shared" / "modules" / "mcp-server" / "guide.py"
MARK = ("<!-- Generated from openclaw-header.md and shared/modules/mcp-server/guide.py "
        "by shared/scripts/build-openclaw-skill.py. Edit those, not this file. -->")


def build() -> str:
    spec = importlib.util.spec_from_file_location("guide", GUIDE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    header = (SKILL_DIR / "openclaw-header.md").read_text().rstrip() + "\n"
    # The front matter must stay first, so the mark goes after it.
    end = header.index("\n---\n", 4) + len("\n---\n")
    return (header[:end] + "\n" + MARK + "\n" + header[end:]
            + "\n---\n\n" + module.GUIDE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if SKILL.md is stale")
    args = parser.parse_args()
    target = SKILL_DIR / "SKILL.md"
    text = build()
    if args.check:
        if target.read_text() != text:
            print(f"stale: {target.relative_to(ROOT)} -- run {Path(__file__).name}",
                  file=sys.stderr)
            return 1
        print("SKILL.md is current")
        return 0
    target.write_text(text)
    print(f"wrote {target.relative_to(ROOT)} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
