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


REPO_ROOT = Path(__file__).resolve().parents[4]
SEED_TARGETS = REPO_ROOT / 'shared/modules/config-manager/templates/targets.yaml'
PROVISIONING = REPO_ROOT / 'shared/modules/grafana/provisioning/dashboards'


def _dashboard_uid_map():
    """The category -> Grafana uid map the dashboard's per-category links use."""
    import re

    source = (TEMPLATES_DIR / 'dashboard.html').read_text(encoding='utf-8')
    match = re.search(r'GRAFANA_DASHBOARD_UIDS = \{(.*?)\};', source, re.S)
    assert match, 'dashboard.html no longer defines GRAFANA_DASHBOARD_UIDS'
    return dict(re.findall(r"'([\w-]+)':\s*'([\w-]+)'", match.group(1)))


@pytest.mark.skipif(not SEED_TARGETS.exists(), reason='seed not in this checkout')
def test_every_seed_category_links_to_its_own_dashboard():
    """HTTP and TCP targets fell through to the ICMP-only default and opened
    an empty page; every category the seed ships must be mapped."""
    import yaml

    seed = yaml.safe_load(SEED_TARGETS.read_text(encoding='utf-8'))
    uids = _dashboard_uid_map()
    missing = set(seed['active_targets']) - set(uids)
    assert not missing, f'categories without a Grafana dashboard: {sorted(missing)}'


@pytest.mark.skipif(not PROVISIONING.exists(), reason='dashboards not in this checkout')
def test_every_linked_dashboard_is_provisioned():
    import json

    provisioned = {
        json.loads(p.read_text(encoding='utf-8')).get('uid')
        for p in PROVISIONING.rglob('*.json')
    }
    unknown = set(_dashboard_uid_map().values()) - provisioned
    assert not unknown, f'links to dashboards nobody provisions: {sorted(unknown)}'


@pytest.mark.parametrize('category, shown', [
    ('http', 'HTTP'),
    ('tcp', 'TCP'),
    ('dns_wizard', 'DNS Wizard'),
    ('dns_resolvers', 'DNS Resolvers'),
    ('netflix_oca', 'Netflix OCA'),
    ('top_sites', 'Top Sites'),
    ('custom', 'Custom'),
    ('some_new_thing', 'Some New Thing'),
])
def test_category_names_keep_their_acronyms(app, category, shown):
    """The dashboard titled HTTP, TCP and DNS wizard targets "Http", "Tcp"
    and "Dns Wizard"."""
    assert app.jinja_env.filters['format_category_name'](category) == shown
