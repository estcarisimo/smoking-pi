"""Secrets stay where layout.py says: never in status.json, a log line or repr.

Every secret file is filled with a sentinel, then everything this release
writes or prints is searched for it."""

import json
import logging
import threading

from fleet import agent, layout, lead, runner, status

NODE = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
SENTINEL = "SENTINEL-SECRET-7f3e"


def fill_secrets(root):
    for paths, names in ((layout.node_paths(root), layout.NODE_SECRETS),
                         (layout.lead_paths(root), layout.LEAD_SECRETS)):
        for name in names:
            f = getattr(paths, name)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(SENTINEL)
    # An enrollment file that (wrongly) carried a token must not leak either.
    enrollment = layout.node_paths(root).enrollment
    enrollment.write_text(json.dumps({"fleet_id": "x", "write_token": SENTINEL}))


_real_run = runner.run


def _run_once(report, line, problems=()):
    stop = threading.Event()
    stop.set()
    _real_run(report, line, problems, stop)


def test_status_and_log_never_carry_a_secret(tmp_path, monkeypatch, caplog):
    root = tmp_path / "state"
    fill_secrets(root)
    monkeypatch.setenv("FLEET_NODE_ID", NODE)
    monkeypatch.setattr(runner, "run", _run_once)
    for module in (agent, lead):
        with caplog.at_level(logging.DEBUG):
            module.main()
        assert status.read()["role"] == module.__name__.split(".")[-1]
        assert SENTINEL not in status.path(root).read_text()
        assert SENTINEL not in caplog.text


def test_status_fields_name_no_secret():
    for name in status.FIELDS:
        assert not any(w in name for w in ("token", "key", "secret", "password"))


def test_secret_files_are_declared_on_both_sides():
    assert set(layout.NODE_SECRETS) == {"key", "store_token"}
    assert {"root_key", "intermediate_key"} <= set(layout.LEAD_SECRETS)
