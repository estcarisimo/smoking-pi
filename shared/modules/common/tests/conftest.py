"""Pytest setup for the shared package. No test resolves a name or asks
Team Cymru: every lookup is a fake passed in."""

import sys
from pathlib import Path

MODULES = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MODULES))  # shared/modules: `common` as a package
