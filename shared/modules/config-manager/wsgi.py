"""
WSGI entrypoint for the config-manager API.

Importing this module performs the explicit one-time startup
initialization (bootstrap -> YAML->DB migration -> config generation),
serialized with a cross-process file lock so multiple gunicorn workers
do not race.

Run with:
    gunicorn --workers 2 --bind 0.0.0.0:5000 --timeout 120 wsgi:app
"""

from api import app, initialize

initialize()

__all__ = ["app"]
