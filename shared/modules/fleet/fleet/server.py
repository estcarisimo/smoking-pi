"""Serving the lead over HTTPS with gunicorn (worker class ``gthread``).

Why gunicorn: it is the server the Fleet spike verified to hand the
application the client certificate the TLS handshake checked
(``environ["gunicorn.socket"].getpeercert()``); uvicorn and hypercorn do
not expose it. ``gthread`` keeps connections alive (``sync`` closes each).

TLS: ``ca_certs`` is the fleet **root** (nodes present leaf + intermediate,
so a certificate from any lead's intermediate verifies), ``cert_reqs`` is
``CERT_OPTIONAL`` (enrollment comes without a client certificate; every
other route requires one, in :mod:`fleet.service`). gunicorn builds a TLS
context for every new connection from the files, so a re-issued server
certificate is served without a restart.

One worker process with threads: the registry is SQLite, shared safely, and
one Pi has no use for more. The worker writes the status file and re-issues
the server certificate when due (:func:`fleet.lead.housekeeping`).
"""

from __future__ import annotations

import os
import ssl
from typing import Callable

from gunicorn.app.base import BaseApplication

from .settings import LEAD_LISTEN_PORT as LISTEN_PORT


def options(paths, worker_started: Callable) -> dict:
    tmp = "/dev/shm" if os.access("/dev/shm", os.W_OK) else str(paths.root)
    return {
        "bind": [f"0.0.0.0:{LISTEN_PORT}"],
        "worker_class": "gthread",
        "workers": 1,
        "threads": 8,
        # One file holds the key and its chain (pki.ensure_server_cert), so
        # a re-issue never pairs a new key with an old certificate.
        "certfile": str(paths.server_key),
        "keyfile": str(paths.server_key),
        "ca_certs": str(paths.root_cert),
        "cert_reqs": ssl.CERT_OPTIONAL,
        "timeout": 30,
        "graceful_timeout": 10,
        "keepalive": 75,
        "limit_request_line": 1024,
        "limit_request_fields": 50,
        "limit_request_field_size": 8190,
        # The root filesystem is read-only: the workers' heartbeat files go
        # to a tmpfs, and there is no control socket to place anywhere.
        "worker_tmp_dir": tmp,
        "control_socket_disable": True,
        "accesslog": None,
        "errorlog": "-",
        "loglevel": "info",
        "proc_name": "fleet-lead",
        "post_worker_init": worker_started,
    }


class _Application(BaseApplication):
    def __init__(self, app, opts: dict):
        self._app = app
        self._opts = opts
        super().__init__()

    def load_config(self):
        for key, value in self._opts.items():
            self.cfg.set(key, value)

    def load(self):
        return self._app


def serve(app, paths, worker_started: Callable) -> None:
    """Run until SIGTERM (gunicorn's own signal handling)."""
    _Application(app, options(paths, worker_started)).run()
