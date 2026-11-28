"""The service loops both entrypoints share.

:func:`run` (the lead in this release): log one line saying what state the
service is in, then rewrite the status file every TICK_SECONDS so the
healthcheck can tell it is alive, until SIGTERM. :func:`loop` (the agent):
the same, but each turn calls a step that does the work and says when it
wants to run again. Neither lets an unwritable status file stop it.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from typing import Callable

from . import protocol, status

log = logging.getLogger("fleet")


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def _install_signals(stop: threading.Event) -> None:
    def _stop(*_):
        stop.set()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)


def _write(report: dict, failing: bool) -> bool:
    """Write the status; return whether writing is failing now. A volume
    that refuses the write (full, wrong owner) must not kill the service:
    the healthcheck reports it, the log says it once."""
    try:
        status.write(dict(report, at=time.time()))
        if failing:
            log.info("status file writable again")
        return False
    except OSError as err:
        if not failing:
            log.warning("cannot write the status file (%s); the healthcheck will fail",
                        type(err).__name__)
        return True


def run(report: dict, line: str, problems: tuple[str, ...] = (),
        stop: threading.Event | None = None, tick: float = status.TICK_SECONDS) -> None:
    stop = stop or threading.Event()
    for problem in problems:
        log.warning("setting ignored: %s", problem)
    log.info("%s", line)
    report = dict(report, started=time.time(), protocol=protocol.PROTOCOL_MAX)
    _install_signals(stop)
    failing = False
    while True:
        failing = _write(report, failing)
        if stop.wait(tick):
            break
    log.info("stopped")


def loop(step: Callable[[], tuple[dict, float]], stop: threading.Event | None = None,
         tick: float = status.TICK_SECONDS) -> None:
    """Call ``step()`` -> (status report, seconds until it wants to run
    again), write the report, wait at most ``tick``; until SIGTERM."""
    stop = stop or threading.Event()
    _install_signals(stop)
    started = time.time()
    failing = False
    while True:
        report, delay = step()
        failing = _write(dict(report, started=started, protocol=protocol.PROTOCOL_MAX),
                         failing)
        if stop.wait(max(0.5, min(delay, tick))):
            break
    log.info("stopped")
