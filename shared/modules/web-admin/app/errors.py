"""One shape for a JSON error: a message chosen here, and an id for the log.

Every route used to answer ``{'error': str(e)}``. Whatever the exception
carried -- the config-manager URL, a database DSN, a filesystem path, a
library's internal message -- went to the browser, and to anything else
that could reach the port. The operator still needs that detail, so it goes
to the application log with a traceback, tagged with a short id that is
also returned. ``docker compose logs web-admin | grep <id>`` finds it.
"""

from __future__ import annotations

import secrets

from flask import current_app, jsonify


def error_response(status: int, message: str, exc: BaseException | None = None,
                   **extra):
    """``(jsonify({...}), status)`` with ``error``, ``error_id`` and ``extra``."""
    error_id = secrets.token_hex(4)
    if exc is not None:
        current_app.logger.error("%s [%s]", message, error_id, exc_info=exc)
    else:
        current_app.logger.error("%s [%s]", message, error_id)
    return jsonify({'error': message, 'error_id': error_id, **extra}), status
