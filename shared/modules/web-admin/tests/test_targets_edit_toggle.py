"""
Edit / toggle / bulk-delete target routes go through the config-manager
gateway (single CRUD path) and are database-mode only.
"""

from conftest import login


def _db_available(monkeypatch, available=True):
    from app.routes import targets as targets_module
    monkeypatch.setattr(
        targets_module.config_api, 'is_database_available', lambda: available
    )
    return targets_module


def test_toggle_calls_gateway(client, monkeypatch):
    targets_module = _db_available(monkeypatch)
    calls = {}

    def fake_toggle(target_id):
        calls['toggled_id'] = target_id
        return {'success': True, 'target': {'id': target_id, 'is_active': False}}

    monkeypatch.setattr(
        targets_module.config_api, 'toggle_target_in_db', fake_toggle
    )

    login(client)
    response = client.post('/targets/42/toggle')

    assert response.status_code == 200
    data = response.get_json()
    assert data['success'] is True
    assert data['is_active'] is False
    assert calls['toggled_id'] == 42


def test_toggle_requires_database_mode(client, monkeypatch):
    _db_available(monkeypatch, available=False)

    login(client)
    response = client.post('/targets/42/toggle')

    assert response.status_code == 400
    assert response.get_json()['success'] is False


def test_toggle_missing_target_returns_404(client, monkeypatch):
    targets_module = _db_available(monkeypatch)

    def fake_toggle(target_id):
        raise ValueError('Target not found')

    monkeypatch.setattr(
        targets_module.config_api, 'toggle_target_in_db', fake_toggle
    )

    login(client)
    response = client.post('/targets/999/toggle')

    assert response.status_code == 404


def test_edit_calls_gateway_with_mapped_probe(client, monkeypatch):
    targets_module = _db_available(monkeypatch)
    calls = {}

    monkeypatch.setattr(
        targets_module.config_api,
        'get_probes_from_db',
        lambda: {'probes': [{'id': 1, 'name': 'FPing'}, {'id': 2, 'name': 'FPing6'}]},
    )
    monkeypatch.setattr(
        targets_module, 'validate_hostname', lambda hostname: (True, None)
    )

    def fake_update(target_id, data):
        calls['target_id'] = target_id
        calls['data'] = data
        return {'success': True}

    monkeypatch.setattr(
        targets_module.config_api, 'update_target_in_db', fake_update
    )

    login(client)
    response = client.put('/targets/7', json={
        'title': 'New Title',
        'host': 'example.com',
        'probe': 'FPing6',
    })

    assert response.status_code == 200
    assert response.get_json()['success'] is True
    assert calls['target_id'] == 7
    assert calls['data'] == {
        'title': 'New Title', 'host': 'example.com', 'probe_id': 2,
    }


def test_edit_rejects_unresolvable_host(client, monkeypatch):
    targets_module = _db_available(monkeypatch)
    monkeypatch.setattr(
        targets_module, 'validate_hostname',
        lambda hostname: (False, f"Cannot resolve hostname: {hostname}"),
    )

    login(client)
    response = client.put('/targets/7', json={'host': 'no.such.host.invalid'})

    assert response.status_code == 400
    data = response.get_json()
    assert data['success'] is False
    assert 'host' in data['errors']


def test_edit_rejects_unknown_probe(client, monkeypatch):
    targets_module = _db_available(monkeypatch)
    monkeypatch.setattr(
        targets_module.config_api,
        'get_probes_from_db',
        lambda: {'probes': [{'id': 1, 'name': 'FPing'}]},
    )

    login(client)
    response = client.put('/targets/7', json={'probe': 'WarpDrive'})

    assert response.status_code == 400
    assert 'probe' in response.get_json()['errors']


def test_edit_with_empty_payload_is_rejected(client, monkeypatch):
    _db_available(monkeypatch)

    login(client)
    response = client.put('/targets/7', json={})

    assert response.status_code == 400


def test_bulk_delete_calls_gateway_per_id(client, monkeypatch):
    targets_module = _db_available(monkeypatch)
    deleted = []

    monkeypatch.setattr(
        targets_module.config_api,
        'delete_target_from_db',
        lambda target_id: deleted.append(target_id) or {'success': True},
    )

    login(client)
    response = client.post('/targets/bulk-delete', json={'ids': [1, 2, 3]})

    assert response.status_code == 200
    data = response.get_json()
    assert data['success'] is True
    assert data['deleted'] == [1, 2, 3]
    assert deleted == [1, 2, 3]


def test_bulk_delete_validates_payload(client, monkeypatch):
    _db_available(monkeypatch)

    login(client)
    for bad_payload in ({}, {'ids': []}, {'ids': ['x']}, {'ids': 'nope'}):
        response = client.post('/targets/bulk-delete', json=bad_payload)
        assert response.status_code == 400, bad_payload


def _capture_db_create(monkeypatch, targets_module):
    """Stub the DB lookups the add route makes; capture what it creates."""
    created = {}
    monkeypatch.setattr(targets_module.config_api, 'get_categories_from_db',
                        lambda: {'categories': [
                            {'name': 'custom', 'id': 1},
                            {'name': 'dns_resolvers', 'id': 2},
                            {'name': 'http', 'id': 3},
                            {'name': 'tcp', 'id': 4}]})
    monkeypatch.setattr(targets_module.config_api, 'get_probes_from_db',
                        lambda: {'probes': [
                            {'name': 'FPing', 'id': 1}, {'name': 'DNS', 'id': 2},
                            {'name': 'CurlHTTP1', 'id': 3},
                            {'name': 'CurlHTTP2', 'id': 4},
                            {'name': 'CurlHTTP3', 'id': 5},
                            {'name': 'TCPPing', 'id': 6}]})
    monkeypatch.setattr(targets_module.config_api, 'get_all_targets_from_db',
                        lambda: {'targets': []})
    monkeypatch.setattr(targets_module.config_api, 'create_target_in_db',
                        lambda data: created.update(data))
    return created


def test_add_http_target_picks_probe_by_version_and_suffixes_name(client, monkeypatch):
    targets_module = _db_available(monkeypatch)
    created = _capture_db_create(monkeypatch, targets_module)

    login(client)
    response = client.post(
        '/targets/add',
        data={'name': 'Google', 'hostname': 'www.google.com',
              'target_type': 'http', 'http_version': '3'},
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert response.status_code == 200, response.get_json()
    assert created['name'] == 'Google_h3'      # exporters read the version here
    assert created['probe_id'] == 5             # CurlHTTP3
    assert created['category_id'] == 3          # http section


def test_add_http_target_without_version_is_a_field_error(client, monkeypatch):
    targets_module = _db_available(monkeypatch)
    _capture_db_create(monkeypatch, targets_module)

    login(client)
    response = client.post(
        '/targets/add',
        data={'name': 'Google', 'hostname': 'www.google.com',
              'target_type': 'http'},
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert response.status_code == 400
    assert 'http_version' in response.get_json()['errors']


def test_add_tcp_target_uses_tcpping(client, monkeypatch):
    targets_module = _db_available(monkeypatch)
    created = _capture_db_create(monkeypatch, targets_module)

    login(client)
    response = client.post(
        '/targets/add',
        data={'name': 'Google_tcp443', 'hostname': 'www.google.com',
              'target_type': 'tcp'},
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert response.status_code == 200, response.get_json()
    assert created['probe_id'] == 6 and created['category_id'] == 4
