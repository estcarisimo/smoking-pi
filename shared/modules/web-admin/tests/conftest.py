"""
Pytest fixtures for the web-admin test suite.

Environment is prepared BEFORE the app package is imported so that the
fail-fast SECRET_KEY check and the credential warnings behave predictably.
"""

import os
import sys
from pathlib import Path

import pytest

MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-pytest')
os.environ['WEB_ADMIN_USERNAME'] = 'admin'
os.environ['WEB_ADMIN_PASSWORD'] = 'test-password'
os.environ.pop('WEB_ADMIN_PASSWORD_HASH', None)
os.environ.pop('ENABLE_SCHEDULER', None)

from app import create_app  # noqa: E402


@pytest.fixture()
def app():
    """App with CSRF disabled for functional tests."""
    application = create_app('testing')
    application.config['WTF_CSRF_ENABLED'] = False
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def csrf_app():
    """App with CSRF enforcement left on (as in production)."""
    application = create_app('testing')
    application.config['WTF_CSRF_ENABLED'] = True
    yield application


@pytest.fixture()
def csrf_client(csrf_app):
    return csrf_app.test_client()


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """The login rate limiter is process-global; isolate tests from each other."""
    from app.routes import auth
    auth._login_failures.clear()
    yield
    auth._login_failures.clear()


def login(client, username='admin', password='test-password'):
    return client.post(
        '/login',
        data={'username': username, 'password': password},
        follow_redirects=False,
    )
