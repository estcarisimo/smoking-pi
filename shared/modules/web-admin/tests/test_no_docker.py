"""
The web-admin app must never touch Docker directly: container operations go
through the config-manager REST API only.
"""

import sys


def test_docker_never_imported(app):
    # The app fixture has fully imported the application (all blueprints,
    # services and tasks). Docker must not be among the loaded modules --
    # the docker SDK is intentionally absent from the test environment too.
    assert 'docker' not in sys.modules


def test_flask_cors_not_used(app):
    assert 'flask_cors' not in sys.modules


def test_flask_basicauth_not_used(app):
    assert 'flask_basicauth' not in sys.modules
