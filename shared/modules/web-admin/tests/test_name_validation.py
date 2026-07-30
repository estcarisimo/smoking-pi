"""
Target-name validation: the server-side rule and the client-side rule in
targets/add.html must be the same regex.
"""

from pathlib import Path

import pytest

from conftest import login

from app.routes.targets import NAME_PATTERN, validate_target_name

ADD_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / 'app' / 'templates' / 'targets' / 'add.html'
)


def test_client_side_pattern_matches_server_side():
    """The exact server regex must appear in the add-target template (both
    in the HTML pattern attribute and in the client-side JS)."""
    source = ADD_TEMPLATE.read_text(encoding='utf-8')
    occurrences = source.count(NAME_PATTERN)
    assert occurrences >= 2, (
        f"Expected the server name regex {NAME_PATTERN!r} in both the "
        f"pattern attribute and the JS validator of add.html "
        f"(found {occurrences} occurrence(s))"
    )


@pytest.mark.parametrize('name', [
    'a',
    'my_server',
    'Server_01',
    'a' * 30,          # exactly the 30-char cap
    'z1_2_3',
])
def test_valid_names_accepted(name):
    valid, error = validate_target_name(name)
    assert valid, f"{name!r} should be valid but got: {error}"


@pytest.mark.parametrize('name', [
    '',                # empty
    '1server',         # starts with digit
    '_server',         # starts with underscore
    'my-server',       # dash not allowed
    'my server',       # space not allowed
    'a' * 31,          # over the 30-char cap
    'sítio',           # non-ASCII
])
def test_invalid_names_rejected(name):
    valid, _error = validate_target_name(name)
    assert not valid, f"{name!r} should be rejected"


def test_add_target_ajax_returns_inline_field_errors(client, monkeypatch):
    """AJAX submits get per-field JSON errors instead of flash + reload."""
    from app.routes import targets as targets_module
    monkeypatch.setattr(
        targets_module.config_api, 'is_database_available', lambda: True
    )

    login(client)
    response = client.post(
        '/targets/add',
        data={'name': '1bad-name', 'hostname': '', 'target_type': 'icmp'},
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )

    assert response.status_code == 400
    data = response.get_json()
    assert data['success'] is False
    assert 'name' in data['errors']
    assert 'hostname' in data['errors']
