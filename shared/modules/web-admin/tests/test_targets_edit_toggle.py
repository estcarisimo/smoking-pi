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
