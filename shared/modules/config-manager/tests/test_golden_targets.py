"""Golden-file test for the data-driven Targets template.

Renders the template with the IMMUTABLE fixture targets.yaml and asserts
the output matches the committed fixture golden, so a template refactor
cannot silently change SmokePing section names (which would orphan RRD
history).

Deliberately does NOT read editions/pro/config-manager/* — those are
live runtime state on a deployed system: in database mode the OCA
refresh updates PostgreSQL (and the generated output) without touching
the YAML export, so the live pair legitimately drifts and made this
test flaky against reality. Fixtures pin one known-good pair forever.
"""

from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, FileSystemLoader

from scripts.config_generator import build_category_context

MODULE_DIR = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _render_from_fixture() -> str:
    targets_config = yaml.safe_load((FIXTURES / "targets.yaml").read_text())
    probes_config = yaml.safe_load((FIXTURES / "probes.yaml").read_text())
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
    return (FIXTURES / "Targets.golden").read_text()


@pytest.fixture(scope="module")
def rendered():
    return _render_from_fixture()


def test_target_sections_match_golden(rendered, golden):
    assert _extract(rendered, ("++ ",)) == _extract(golden, ("++ ",))


def test_hosts_match_golden(rendered, golden):
    assert _extract(rendered, ("host = ",)) == _extract(golden, ("host = ",))


def test_section_headers_match_golden(rendered, golden):
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


def test_cpe_include_preserved(rendered, golden):
    assert "@include /config/CPE_Targets" in rendered
    assert "@include /config/CPE_Targets" in golden
