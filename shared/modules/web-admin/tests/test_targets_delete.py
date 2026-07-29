"""
Target deletion goes through the config-manager gateway (single CRUD path).
"""

from conftest import login


def test_delete_target_calls_gateway(client, monkeypatch):
    from app.routes import targets as targets_module

    calls = {}

    monkeypatch.setattr(
        targets_module.config_api, 'is_database_available', lambda: True
    )
    monkeypatch.setattr(
        targets_module.config_api,
        'get_all_targets_from_db',
        lambda: {'targets': [{'id': 42, 'name': 'foo', 'host': 'foo.example.com'}]},
    )

    def fake_delete(target_id):
        calls['deleted_id'] = target_id
        return {'success': True}

    monkeypatch.setattr(
        targets_module.config_api, 'delete_target_from_db', fake_delete
    )

    login(client)
    response = client.post('/targets/delete/foo')

    assert response.status_code == 200
    assert response.get_json()['success'] is True
    assert calls['deleted_id'] == 42


def test_delete_missing_target_returns_404(client, monkeypatch):
    from app.routes import targets as targets_module

    monkeypatch.setattr(
        targets_module.config_api, 'is_database_available', lambda: True
    )
    monkeypatch.setattr(
        targets_module.config_api, 'get_all_targets_from_db', lambda: {'targets': []}
    )

    login(client)
    response = client.post('/targets/delete/nope')

    assert response.status_code == 404
    assert response.get_json()['success'] is False
