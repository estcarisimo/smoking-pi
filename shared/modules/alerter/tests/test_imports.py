"""Importing every module must work without any environment variables.

The autouse fixture in conftest.py scrubs all alerter/Influx env vars, so a
plain import here proves the "feature off cleanly when env unset" pattern:
no client construction, no network, no file writes at import time.
"""

import importlib


def test_import_all_modules_without_env():
    for name in ("flux", "evaluator", "state", "notifier", "reports_watcher", "main"):
        module = importlib.import_module(name)
        assert module is not None


def test_lazy_influx_client_not_constructed_at_import():
    import flux

    assert flux._influx_client is None
