"""
Template hygiene: every template must compile, and none may pull assets
from a CDN (the admin UI has to work during an internet outage).
"""

from pathlib import Path

import pytest

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / 'app' / 'templates'
TEMPLATE_FILES = sorted(TEMPLATES_DIR.rglob('*.html'))


def _relative_names():
    return [str(p.relative_to(TEMPLATES_DIR)) for p in TEMPLATE_FILES]


def test_templates_exist():
    assert TEMPLATE_FILES, f"No templates found under {TEMPLATES_DIR}"


@pytest.mark.parametrize('template_path', TEMPLATE_FILES,
                         ids=_relative_names())
def test_template_compiles(app, template_path):
    """Every template file must compile with the app's Jinja environment."""
    source = template_path.read_text(encoding='utf-8')
    name = str(template_path.relative_to(TEMPLATES_DIR))
    # Raises jinja2.TemplateSyntaxError on failure
    app.jinja_env.compile(source, name=name, filename=str(template_path))


@pytest.mark.parametrize('template_path', TEMPLATE_FILES,
                         ids=_relative_names())
def test_template_has_no_cdn_references(template_path):
    """All CSS/JS must be vendored locally, never loaded from a CDN."""
    source = template_path.read_text(encoding='utf-8')
    for forbidden in ('cdn.jsdelivr.net', 'unpkg'):
        assert forbidden not in source, (
            f"{template_path.name} references {forbidden}; assets must be "
            "vendored under app/static/vendor/"
        )
