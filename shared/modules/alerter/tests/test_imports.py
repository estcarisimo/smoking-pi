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
    """The client must not exist until something actually queries.

    Asserted against common.tsdb, not the `flux` shim. The shim re-exports
    public names only: binding `_influx_client` through it would snapshot
    None at import and never change, so the assertion would hold forever
    whether or not a client had been constructed -- a green test that tests
    nothing.
    """
    from common import tsdb

    assert tsdb._influx_client is None
