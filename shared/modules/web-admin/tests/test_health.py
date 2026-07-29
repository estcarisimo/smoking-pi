"""
/health endpoint tests: unauthenticated, JSON, reports config-manager reachability.
"""


def test_health_requires_no_auth(client):
    response = client.get('/health')
    assert response.status_code == 200


def test_health_reports_reachability(client, monkeypatch):
    from app.routes import api as api_module

    monkeypatch.setattr(api_module.config_api, 'is_available', lambda: True)
    data = client.get('/health').get_json()
    assert data['status'] == 'ok'
    assert data['app'] == 'web-admin'
    assert data['config_manager_reachable'] is True

    monkeypatch.setattr(api_module.config_api, 'is_available', lambda: False)
    data = client.get('/health').get_json()
    assert data['status'] == 'ok'
    assert data['config_manager_reachable'] is False


def test_unknown_page_returns_html_error(client):
    from conftest import login

    login(client)
    response = client.get('/does-not-exist')
    assert response.status_code == 404
    assert b'Page Not Found' in response.data
    assert response.mimetype == 'text/html'


def test_unknown_api_route_returns_json_error(client):
    from conftest import login

    login(client)
    response = client.get('/api/does-not-exist')
    assert response.status_code == 404
    assert response.get_json() == {'error': 'Not found'}
