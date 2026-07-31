"""Pytest fixtures for the ai-insights test suite.

All network access (InfluxDB, Anthropic) is mocked; importing the modules
must never require environment variables.
"""

import sys
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))
