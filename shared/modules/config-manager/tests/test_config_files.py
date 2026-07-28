"""Smoke tests for config files and SmokePing template rendering."""

from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, FileSystemLoader

from scripts.config_generator import build_category_context

MODULE_DIR = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("name", ["targets.yaml", "probes.yaml", "sources.yaml"])
def test_default_config_parses(name):
    path = MODULE_DIR / "config" / name
    if not path.exists():
        pytest.skip(f"{name} not present in module defaults")
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), f"{name} must parse to a mapping"


def test_targets_template_renders():
    env = Environment(
        loader=FileSystemLoader(MODULE_DIR / "templates"),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("smokeping_targets.j2")
    active_targets = {
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
    }
    output = template.render(
        default_probe="FPing",
        categories=build_category_context(active_targets),
    )
    assert "host = example.com" in output
    assert "host = 8.8.8.8" in output
    assert "host = myhost.example" in output
    # Empty categories must not emit a section header
    assert "+ Netflix" not in output
    # The CPE include must survive template changes — cpe_discovery.py
    # depends on it being present in the generated Targets file.
    assert "@include /config/CPE_Targets" in output


def test_unknown_category_gets_sensible_defaults():
    env = Environment(
        loader=FileSystemLoader(MODULE_DIR / "templates"),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("smokeping_targets.j2")
    active_targets = {
        "my_new_category": [
            {"name": "Thing", "host": "thing.example", "title": "Thing"}
        ],
    }
    output = template.render(
        default_probe="FPing",
        categories=build_category_context(
            active_targets,
            {"my_new_category": {"display_name": "My New Category"}},
        ),
    )
    assert "+ my_new_category" in output
    assert "menu = My New Category" in output
    assert "host = thing.example" in output


def test_probes_template_parses():
    path = MODULE_DIR / "templates" / "probes.yaml"
    data = yaml.safe_load(path.read_text())
    assert "probes" in data or isinstance(data, dict)
