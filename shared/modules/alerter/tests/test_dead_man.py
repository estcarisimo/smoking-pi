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
    monkeypatch.setattr(dead_man, "_last",
                        {"sent_at": None, "sent_ok": None, "tried_at": None})


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
        assert dead_man.check_in(ok=False, now=T + dead_man.RETRY_S) is False
    assert "HTTP 404" in caplog.text and "secret-uuid" not in caplog.text


def test_a_failed_check_in_is_retried_a_minute_later_not_five(monkeypatch):
    monkeypatch.setenv("ALERT_DEADMAN_URL", URL)
    answers = iter([httpx.ConnectError("no route"), 200])
    calls = []

    class _Response:
        status_code = 200

    def get(url, timeout):
        calls.append(url)
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return _Response()

    monkeypatch.setattr(dead_man.httpx, "get", get)
    assert dead_man.check_in(ok=True, now=T) is False
    assert dead_man.check_in(ok=True, now=T + 30) is None
    assert dead_man.check_in(ok=True, now=T + dead_man.RETRY_S) is True
    assert len(calls) == 2


def test_a_flapping_state_never_pings_more_than_once_a_minute(monkeypatch, pinged):
    monkeypatch.setenv("ALERT_DEADMAN_URL", URL)
    for i in range(10):
        dead_man.check_in(ok=i % 2 == 0, now=T + 20 * i)
    assert len(pinged) == 4  # T, T+60, T+120, T+180 (every third try)


def test_fail_keeps_a_query_string_a_query_string(monkeypatch, pinged):
    monkeypatch.setenv("ALERT_DEADMAN_URL", URL + "?create=1")
    dead_man.check_in(ok=False, now=T)
    assert pinged == [URL + "/fail?create=1"]


def _loop(monkeypatch, current):
    seen = []
    monkeypatch.setattr(dead_man, "check_in",
                        lambda ok, now=None: seen.append(ok))
    monkeypatch.setattr(main.state, "load_state", lambda: current)
    monkeypatch.setattr(main.state, "save_state", lambda state: None)
    monkeypatch.setattr(main.notifier, "send", lambda payload, image=None: "sent")
    for module in (main.incident_history, main.digest, main.reports_watcher):
        for name in ("merge", "maybe_record", "check"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, lambda *a, **k: None)
    return seen


def _evaluation(readable):
    def evaluate(**_):
        if not readable[0]:
            raise ConnectionError("influx down")
        return [], {"mean_rows": [], "micro_rows": [], "wifi_rows": {},
                    "uplink_changes": [], "windows": {"mean": 900, "step": 300},
                    "cadences": {}, "judged": set()}
    return evaluate


def test_the_loop_checks_in_ok_and_fail_only_once_blind_long_enough(monkeypatch):
    """An InfluxDB restart (an upgrade, a backup) is not a page: /fail only
    once the blindness lasted as long as influx_unreadable waits."""
    current = {}
    seen = _loop(monkeypatch, current)
    clock = {"now": T}
    monkeypatch.setattr(main.time, "time", lambda: clock["now"])
    readable = [False]
    monkeypatch.setattr(main.evaluator, "evaluate_with_context", _evaluation(readable))
    main.run_iteration()
    assert seen == []
    clock["now"] = T + main.UNREADABLE_AFTER_S
    main.run_iteration()
    assert seen == [False]
    readable[0] = True
    clock["now"] += 60
    main.run_iteration()
    assert seen == [False, True]


def test_an_iteration_that_crashes_does_not_check_in(monkeypatch):
    """The headline: an alerter broken after its evaluation is silence."""
    seen = _loop(monkeypatch, {})
    monkeypatch.setattr(main.evaluator, "evaluate_with_context", _evaluation([True]))

    def broken(state):
        raise OSError("disk full")

    monkeypatch.setattr(main.state, "save_state", broken)
    with pytest.raises(OSError):
        main.run_iteration()
    assert seen == []
