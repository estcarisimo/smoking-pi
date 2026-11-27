"""The idle loop both entrypoints share in this release.

Logs one line saying what state the service is in, then rewrites the status
file every TICK_SECONDS so the healthcheck can tell it is alive, until
SIGTERM. Nothing here talks to the network.
"""

from __future__ import annotations

import logging
import signal
import threading
import time

from . import protocol, status

log = logging.getLogger("fleet")


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def run(report: dict, line: str, problems: tuple[str, ...] = (),
        stop: threading.Event | None = None, tick: float = status.TICK_SECONDS) -> None:
    stop = stop or threading.Event()
    for problem in problems:
        log.warning("setting ignored: %s", problem)
    log.info("%s", line)
    report = dict(report, started=time.time(), protocol=protocol.PROTOCOL_MAX)

    def _stop(*_):
        stop.set()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
    while True:
        status.write(dict(report, at=time.time()))
        if stop.wait(tick):
            break
    log.info("stopped")
