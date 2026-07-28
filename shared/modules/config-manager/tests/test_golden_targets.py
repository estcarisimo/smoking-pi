"""Golden-file test for the data-driven Targets template.

Renders the template with the current Pro edition targets.yaml and asserts
the generated sections/hosts match the committed Targets file, so the
template refactor cannot silently change SmokePing section names (which
would orphan RRD history).
"""

from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, FileSystemLoader

from scripts.config_generator import build_category_context

MODULE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = MODULE_DIR.parent.parent.parent
EDITION_DIR = REPO_ROOT / "editions" / "pro" / "config-manager"


def _render_from_edition_config() -> str:
    targets_config = yaml.safe_load(
        (EDITION_DIR / "config" / "targets.yaml").read_text()
    )
    probes_config = yaml.safe_load(
        (EDITION_DIR / "config" / "probes.yaml").read_text()
    )
    env = Environment(
        loader=FileSystemLoader(MODULE_DIR / "templates"),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("smokeping_targets.j2")
    return template.render(
        default_probe=probes_config.get("default_probe", "FPing"),
        categories=build_category_context(targets_config["active_targets"]),
    )


def _extract(lines_text: str, prefixes) -> list:
    return [
        line.strip()
        for line in lines_text.splitlines()
        if line.strip().startswith(prefixes)
    ]


@pytest.fixture(scope="module")
def golden():
    path = EDITION_DIR / "output" / "Targets"
    if not path.exists():
        pytest.skip("committed edition output/Targets not present")
    return path.read_text()


def test_target_sections_match_golden(golden):
    rendered = _render_from_edition_config()
    assert _extract(rendered, ("++ ",)) == _extract(golden, ("++ ",))


def test_hosts_match_golden(golden):
    rendered = _render_from_edition_config()
    assert _extract(rendered, ("host = ",)) == _extract(golden, ("host = ",))


def test_section_headers_match_golden(golden):
    rendered = _render_from_edition_config()
    # top-level sections ("+ websites", "+ Netflix", ...) must be identical
    plus_lines = [
        line
        for line in _extract(rendered, ("+ ",))
        if not line.startswith("++ ")
    ]
    golden_plus = [
        line
        for line in _extract(golden, ("+ ",))
        if not line.startswith("++ ")
    ]
    assert plus_lines == golden_plus


def test_cpe_include_preserved(golden):
    rendered = _render_from_edition_config()
    assert "@include /config/CPE_Targets" in rendered
    assert "@include /config/CPE_Targets" in golden
