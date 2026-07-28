"""Smoke tests for config files and SmokePing template rendering.

Deliberately avoids importing api.py / config_generator.py, which have
import-time side effects (bootstrap, DB connections). Those get covered
once the startup flow is refactored.
"""

from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, FileSystemLoader

MODULE_DIR = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("name", ["targets.yaml", "probes.yaml", "sources.yaml"])
def test_default_config_parses(name):
    path = MODULE_DIR / "config" / name
    if not path.exists():
        pytest.skip(f"{name} not present in module defaults")
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), f"{name} must parse to a mapping"


def test_targets_template_renders():
    env = Environment(loader=FileSystemLoader(MODULE_DIR / "templates"))
    template = env.get_template("smokeping_targets.j2")
    context = {
        "default_probe": "FPing",
        "targets": {
            "top_sites": [
                {"name": "Example", "host": "example.com", "title": "Example"}
            ],
            "netflix_oca": [],
            "dns_resolvers": [
                {"name": "GoogleDNS", "host": "8.8.8.8", "probe": "DNS", "title": "G"}
            ],
            "custom": [
                {"name": "MyHost", "host": "myhost.example", "title": "Mine"}
            ],
        },
    }
    output = template.render(**context)
    assert "host = example.com" in output
    assert "host = 8.8.8.8" in output
    assert "host = myhost.example" in output
    # The CPE include must survive template changes — cpe_discovery.py
    # depends on it being present in the generated Targets file.
    assert "@include /config/CPE_Targets" in output


def test_probes_template_parses():
    path = MODULE_DIR / "templates" / "probes.yaml"
    data = yaml.safe_load(path.read_text())
    assert "probes" in data or isinstance(data, dict)
