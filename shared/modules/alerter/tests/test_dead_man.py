"""The dead man's switch: check-ins an outside service expects."""

import logging

import httpx
import pytest

import dead_man
import main

URL = "https://hc-ping.example/0b4c-secret-uuid"
T = 1_791_818_100.0


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(dead_man, "_last", {"at": None, "ok": None})


@pytest.fixture
def pinged(monkeypatch):
    calls = []

    class _Response:
        status_code = 200

    def get(url, timeout):
        calls.append(url)
        return _Response()

    monkeypatch.setattr(dead_man.httpx, "get", get)
    return calls


def test_off_without_a_url(pinged):
    assert dead_man.check_in(ok=True, now=T) is None
    assert pinged == []


def test_not_an_http_url_is_off(monkeypatch, pinged):
    monkeypatch.setenv("ALERT_DEADMAN_URL", "hc-ping.example/uuid")
    assert dead_man.check_in(ok=True, now=T) is None
    assert pinged == []


def test_one_check_in_per_interval_and_at_once_on_a_change(monkeypatch, pinged):
    monkeypatch.setenv("ALERT_DEADMAN_URL", URL + "/")
    assert dead_man.check_in(ok=True, now=T) is True
    assert dead_man.check_in(ok=True, now=T + 60) is None
    assert dead_man.check_in(ok=True, now=T + dead_man.INTERVAL_S) is True
    # Blind: /fail at once, not after the interval.
    assert dead_man.check_in(ok=False, now=T + dead_man.INTERVAL_S + 60) is True
    assert dead_man.check_in(ok=True, now=T + dead_man.INTERVAL_S + 120) is True
    assert pinged == [URL, URL, URL + "/fail", URL]


def test_a_clock_that_went_back_checks_in(monkeypatch, pinged):
    monkeypatch.setenv("ALERT_DEADMAN_URL", URL)
    dead_man.check_in(ok=True, now=T)
    assert dead_man.check_in(ok=True, now=T - 3600) is True


def test_failures_never_raise_and_never_log_the_url(monkeypatch, caplog):
    monkeypatch.setenv("ALERT_DEADMAN_URL", URL)

    def boom(url, timeout):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(dead_man.httpx, "get", boom)
    with caplog.at_level(logging.INFO):
        assert dead_man.check_in(ok=True, now=T) is False
    assert "hc-ping.example" in caplog.text
    assert "secret-uuid" not in caplog.text

    class _Refused:
        status_code = 404

    monkeypatch.setattr(dead_man.httpx, "get", lambda url, timeout: _Refused())
    caplog.clear()
    with caplog.at_level(logging.INFO):
        assert dead_man.check_in(ok=False, now=T + 1) is False
    assert "HTTP 404" in caplog.text and "secret-uuid" not in caplog.text


def test_the_loop_checks_in_ok_after_an_evaluation_and_fail_when_blind(monkeypatch):
    seen = []
    monkeypatch.setattr(dead_man, "check_in",
                        lambda ok, now=None: seen.append(ok))
    monkeypatch.setattr(main.state, "load_state", lambda: {})
    monkeypatch.setattr(main.state, "save_state", lambda current: None)
    monkeypatch.setattr(main.notifier, "notify", lambda payload, image=None: True)
    for module in (main.incident_history, main.digest, main.reports_watcher):
        for name in ("merge", "maybe_record", "check"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, lambda *a, **k: None)
    readable = [False]

    def evaluate(**_):
        if not readable[0]:
            raise ConnectionError("influx down")
        return [], {"mean_rows": [], "micro_rows": [], "wifi_rows": {},
                    "uplink_changes": [], "windows": {"mean": 900, "step": 300},
                    "cadences": {}, "judged": set()}

    monkeypatch.setattr(main.evaluator, "evaluate_with_context", evaluate)
    main.run_iteration()
    readable[0] = True
    main.run_iteration()
    assert seen == [False, True]
