"""
CSRF enforcement tests: state-changing requests without a token are rejected.
"""


def test_login_post_without_csrf_token_rejected(csrf_client):
    response = csrf_client.post(
        '/login', data={'username': 'admin', 'password': 'test-password'}
    )
    assert response.status_code == 400


def test_api_post_without_csrf_token_rejected(csrf_client):
    response = csrf_client.post('/api/apply')
    assert response.status_code == 400
    assert 'error' in response.get_json()


def test_login_page_contains_csrf_token(csrf_client):
    response = csrf_client.get('/login')
    assert response.status_code == 200
    assert b'name="csrf_token"' in response.data
