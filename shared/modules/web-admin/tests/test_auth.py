"""
Authentication tests: login success/failure, lockout, open-redirect rejection,
password-hash support and unconfigured-credentials behavior.
"""

from werkzeug.security import generate_password_hash

from conftest import login


def test_login_page_renders(client):
    response = client.get('/login')
    assert response.status_code == 200
    assert b'Sign In' in response.data


def test_login_success_redirects_to_dashboard(client):
    response = login(client)
    assert response.status_code == 302
    assert response.headers['Location'] in ('/', 'http://localhost/')


def test_login_wrong_password_fails(client):
    response = login(client, password='wrong-password')
    assert response.status_code == 200
    assert b'Invalid username or password' in response.data


def test_login_wrong_username_fails(client):
    response = login(client, username='root')
    assert response.status_code == 200
    assert b'Invalid username or password' in response.data


def test_lockout_after_five_failures(client):
    for _ in range(5):
        response = login(client, password='wrong-password')
        assert response.status_code == 200

    # Even the correct password is rejected while locked out
    response = login(client)
    assert response.status_code == 429
    assert b'Too many failed login attempts' in response.data


def test_successful_login_resets_failure_count(client):
    for _ in range(4):
        login(client, password='wrong-password')
    response = login(client)
    assert response.status_code == 302
    # Log out so the next attempt actually hits password validation
    client.get('/logout')
    # Counter was reset; more failures allowed without immediate lockout
    response = login(client, password='wrong-password')
    assert response.status_code == 200


def test_open_redirect_absolute_url_rejected(client):
    response = client.post(
        '/login',
        data={
            'username': 'admin',
            'password': 'test-password',
            'next': 'http://evil.example.com/phish',
        },
    )
    assert response.status_code == 302
    assert 'evil.example.com' not in response.headers['Location']


def test_open_redirect_protocol_relative_rejected(client):
    response = client.post(
        '/login',
        data={
            'username': 'admin',
            'password': 'test-password',
            'next': '//evil.example.com/phish',
        },
    )
    assert response.status_code == 302
    assert 'evil.example.com' not in response.headers['Location']


def test_relative_next_url_allowed(client):
    response = client.post(
        '/login',
        data={
            'username': 'admin',
            'password': 'test-password',
            'next': '/targets/',
        },
    )
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/targets/')


def test_password_hash_takes_precedence(client, monkeypatch):
    monkeypatch.setenv(
        'WEB_ADMIN_PASSWORD_HASH', generate_password_hash('hash-only-password')
    )
    # Plaintext WEB_ADMIN_PASSWORD is ignored once the hash is configured
    response = login(client)
    assert response.status_code == 200
    assert b'Invalid username or password' in response.data

    response = login(client, password='hash-only-password')
    assert response.status_code == 302


def test_login_impossible_when_unconfigured(client, monkeypatch):
    monkeypatch.delenv('WEB_ADMIN_PASSWORD', raising=False)
    monkeypatch.delenv('WEB_ADMIN_PASSWORD_HASH', raising=False)

    response = client.get('/login')
    assert b'Credentials not configured' in response.data

    response = login(client)
    assert response.status_code == 200
    assert b'Login is not configured' in response.data


def test_pages_require_login(client):
    response = client.get('/')
    assert response.status_code == 302
    assert '/login' in response.headers['Location']


def test_api_returns_401_when_unauthenticated(client):
    response = client.get('/api/status')
    assert response.status_code == 401
    assert response.get_json()['error'] == 'Authentication required'
