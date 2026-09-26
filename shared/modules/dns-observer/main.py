"""DNS observer supervisor: runs AdGuard Home and says whether it is observing.

One container, two processes: AdGuard Home serves DNS; this loop

1. merges our settings into AdGuard's config and starts it;
2. self-tests port 53 and restarts AdGuard when it stops answering;
3. sends the canary through the router and looks for it in the query log;
4. writes ``status.json`` (heartbeat, state, since when data exists).

If this process dies the container exits and Docker restarts it; if Docker
itself is down the heartbeat goes stale and every reader reports ``down``
(health.read_status). See docs/dns-observer.md, "When the observer is not
getting information".
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import secrets
import signal
import socket
import struct
import sys
import time
from datetime import datetime

import dns.asyncquery
import dns.message
import httpx

import adguard
import health
import wizard
from config import Config, ConfigError

log = logging.getLogger("dns-observer")

# A hung AdGuard is killed after SELFTEST_FAILURES misses in a row: ~30 s
# without DNS for a house whose router has no secondary. While it is not
# answering, the test repeats faster; the first STARTUP_GRACE seconds of a
# fresh process do not count, or a slow start would be killed in a loop.
SELFTEST_INTERVAL = 10
SELFTEST_RETRY = 3
SELFTEST_FAILURES = 3
STARTUP_GRACE = 15
MAX_BACKOFF = 60


def default_gateway(route_file: str = "/proc/net/route") -> str | None:
    """The IPv4 default gateway (the router), from the host's routing table.

    The container runs with the host's network, so this is the host's
    table. The lowest-metric default route wins.
    """
    best: tuple[int, str] | None = None
    try:
        with open(route_file) as fh:
            next(fh, None)
            for line in fh:
                fields = line.split()
                if len(fields) < 7 or fields[1] != "00000000":
                    continue
                if not int(fields[3], 16) & 0x2:  # RTF_GATEWAY
                    continue
                gw = socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
                metric = int(fields[6])
                if best is None or metric < best[0]:
                    best = (metric, gw)
    except OSError:
        return None
    return best[1] if best else None


def parse_time(value: str) -> float:
    """AdGuard's RFC 3339 times carry nanoseconds; Python takes microseconds."""
    value = re.sub(r"\.(\d{1,6})\d*", r".\1", value.replace("Z", "+00:00"))
    return datetime.fromisoformat(value).timestamp()


class Supervisor:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.started_at = time.time()
        self.proc: asyncio.subprocess.Process | None = None
        self.restarts = 0
        self.last_restart: float | None = None
        self.selftest_failures = 0
        self.server_ok: bool | None = None  # None: not tested yet
        self.proc_started = 0.0
        self.last_query_at: float | None = None
        self.canaries: dict[str, list] = {}  # nonce -> [sent_at, seen_at]
        self.upstreams: dict = {}
        self.top_domains: list[dict] = []
        self.api = adguard.AdGuardAPI(cfg)
        self.stopping = asyncio.Event()
        self._restore()

    # -- persistence across restarts -----------------------------------------

    def _restore(self) -> None:
        """Keep ``observed_until`` across a crash, so the gap has a start."""
        previous = health.read_status(self.cfg.status_path)
        self.last_query_at = previous.get("observed_until")

    # -- AdGuard Home process --------------------------------------------------

    async def run_adguard(self) -> None:
        backoff = 1
        while not self.stopping.is_set():
            self.proc = await asyncio.create_subprocess_exec(
                self.cfg.adguard_binary,
                "--no-check-update",
                "-c", self.cfg.adguard_conf,
                "-w", self.cfg.adguard_work,
            )
            log.info("AdGuard Home started (pid %s)", self.proc.pid)
            started = self.proc_started = time.monotonic()
            code = await self.proc.wait()
            if self.stopping.is_set():
                return
            self.server_ok = False
            self.restarts += 1
            self.last_restart = time.time()
            # A process that ran a while gets restarted at once; one that
            # dies on start is backed off, so a bad config does not spin.
            backoff = 1 if time.monotonic() - started > 60 else min(backoff * 2, MAX_BACKOFF)
            log.error("AdGuard Home exited with %s; restarting in %ss", code, backoff)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), backoff)

    async def selftest_once(self) -> bool:
        # ANY is refused by AdGuard itself (refuse_any, set in adguard.py),
        # in well under a millisecond and without touching an upstream. A
        # query that went upstream would fail during an internet outage and
        # get a healthy server killed.
        name = f"selftest-{secrets.token_hex(4)}.{self.cfg.canary_domain}"
        query = dns.message.make_query(name, "ANY")
        try:
            await dns.asyncquery.udp(
                query, self.cfg.selftest_host, port=self.cfg.port, timeout=3
            )
        except Exception as exc:  # timeout, refused, network unreachable
            log.warning("self-test on port %s failed: %s", self.cfg.port, exc)
            return False
        return True

    async def selftest_loop(self) -> None:
        while not self.stopping.is_set():
            ok = await self.selftest_once()
            in_grace = time.monotonic() - self.proc_started < STARTUP_GRACE
            if ok or not in_grace:
                self.server_ok = ok
            if ok:
                self.selftest_failures = 0
            elif not in_grace:
                self.selftest_failures += 1
            if self.selftest_failures >= SELFTEST_FAILURES and self.proc:
                log.error(
                    "AdGuard Home has not answered %s self-tests; killing it",
                    self.selftest_failures,
                )
                with contextlib.suppress(ProcessLookupError):
                    self.proc.kill()
                self.selftest_failures = 0
            wait = SELFTEST_INTERVAL if ok else SELFTEST_RETRY
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), wait)

    # -- canary ------------------------------------------------------------------

    def canary_via(self) -> str | None:
        if self.cfg.canary_via == "off":
            return None
        if self.cfg.canary_via == "auto":
            return default_gateway()
        return self.cfg.canary_via

    async def send_canary(self) -> None:
        via = self.canary_via()
        if via is None:
            return
        nonce = secrets.token_hex(6)
        self.canaries[nonce] = [time.time(), None]
        query = dns.message.make_query(f"{nonce}.{self.cfg.canary_domain}", "A")
        # The answer does not matter: arriving *here*, in the query log, is
        # the evidence. The router answering NXDOMAIN on its own is a miss.
        with contextlib.suppress(Exception):
            await dns.asyncquery.udp(query, via, port=self.cfg.canary_port, timeout=5)

    async def canary_loop(self) -> None:
        await asyncio.sleep(10)
        while not self.stopping.is_set():
            await self.send_canary()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), self.cfg.canary_interval)

    # -- reading the query log ------------------------------------------------------

    def _is_ours(self, name: str) -> bool:
        return name.rstrip(".").lower().endswith(self.cfg.canary_domain)

    async def refresh(self) -> None:
        now = time.time()
        entries = await self.api.querylog(limit=200)
        organic = []
        for e in entries:
            name = str((e.get("question") or {}).get("name", ""))
            try:
                e["_ts"] = parse_time(e["time"])
            except (KeyError, ValueError):
                continue
            if not self._is_ours(name):
                organic.append(e)
        if organic:
            newest = max(e["_ts"] for e in organic)
            self.last_query_at = max(newest, self.last_query_at or 0)
        self.upstreams = health.summarize_upstreams(organic, self.cfg.fallback, now)

        # Canaries still unseen and younger than a day: one search each.
        for nonce, rec in list(self.canaries.items()):
            if rec[0] < now - 86400:
                del self.canaries[nonce]
                continue
            if rec[1] is not None:
                continue
            for e in await self.api.querylog(search=nonce, limit=5):
                name = str((e.get("question") or {}).get("name", ""))
                if name.lower().startswith(nonce):
                    rec[1] = parse_time(e["time"])
                    break

        with contextlib.suppress(httpx.HTTPError, ValueError):
            stats = await self.api.stats()
            self.top_domains = [
                {"name": name, "queries": count}
                for item in stats.get("top_queried_domains", [])
                for name, count in item.items()
                if not self._is_ours(name)
            ][:25]

    # -- status ------------------------------------------------------------------------

    def status(self) -> dict:
        now = time.time()
        canaries = sorted((rec[0], rec[1]) for rec in self.canaries.values())
        via = self.canary_via()
        state = health.evaluate(
            now=now,
            started_at=self.started_at,
            server_ok=self.server_ok,
            port=self.cfg.port,
            last_query_at=self.last_query_at,
            canaries=canaries,
            canary_enabled=via is not None,
            canary_misses=self.cfg.canary_misses,
            canary_interval=self.cfg.canary_interval,
            quiet_after=self.cfg.quiet_after,
            upstreams=self.upstreams,
        )
        settled = now - health.CANARY_TIMEOUT
        day = [
            c for c in canaries if c[0] >= now - 86400 and (c[1] or c[0] < settled)
        ]
        return {
            **state,
            "live": state["state"] in health.LIVE_STATES,
            "heartbeat": now,
            "stale_after": now + health.STALE_AFTER,
            "started_at": self.started_at,
            "observed_until": self.last_query_at,
            "coverage": health.COVERAGE,
            "server": {
                "answering": self.server_ok,
                "restarts": self.restarts,
                "last_restart": self.last_restart,
                "port": self.cfg.port,
                "bind": self.cfg.bind_hosts,
            },
            "canary": {
                "enabled": via is not None,
                "via": via,
                "domain": self.cfg.canary_domain,
                "interval": self.cfg.canary_interval,
                "last_sent": canaries[-1][0] if canaries else None,
                "last_seen": max((c[1] for c in canaries if c[1]), default=None),
                "sent_24h": len(day),
                "seen_24h": sum(1 for c in day if c[1] is not None),
            },
            "upstreams": {
                **self.upstreams,
                "encrypted": self.cfg.upstreams,
                "fallback": self.cfg.fallback,
            },
            "top_domains": self.top_domains,
        }

    async def status_loop(self) -> None:
        last_state = None
        while not self.stopping.is_set():
            try:
                await self.refresh()
            except (httpx.HTTPError, ValueError) as exc:
                # AdGuard starting or restarting; the self-test decides.
                log.debug("query log unavailable: %s", exc)
            status = self.status()
            if status["state"] != last_state:
                log.info("state %s: %s", status["state"], status["reason"])
                last_state = status["state"]
            health.write_status(self.cfg.status_path, status)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), health.STATUS_INTERVAL)

    # -- the DNS wizard ------------------------------------------------------------

    async def wizard_loop(self) -> None:
        """wizard.py's pass, in a thread: the first one reads up to a week
        of log, and the self-test and canary must not wait for it."""
        wiz = wizard.Wizard(
            os.path.join(self.cfg.adguard_work, "data"),
            self.cfg.state_dir,
            canary_domain=self.cfg.canary_domain,
            extra_own=self.cfg.wizard_exclude,
            top=self.cfg.wizard_top,
            score=self.cfg.wizard_score,
            coverage=self.cfg.wizard_coverage,
            floor=self.cfg.wizard_floor,
            max_k=self.cfg.wizard_max,
        )
        await asyncio.sleep(30)
        while not self.stopping.is_set():
            try:
                await asyncio.to_thread(wiz.run_once)
            except Exception:  # one bad pass must not end the loop
                log.exception("DNS wizard pass failed")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), self.cfg.wizard_interval)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stopping.set)
        tasks = [
            asyncio.create_task(self.run_adguard()),
            asyncio.create_task(self.selftest_loop()),
            asyncio.create_task(self.canary_loop()),
            asyncio.create_task(self.status_loop()),
        ]
        if self.cfg.wizard_interval:
            tasks.append(asyncio.create_task(self.wizard_loop()))
        await self.stopping.wait()
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.proc.wait(), 10)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # A stop on purpose is said at once; only a crash waits for the
        # heartbeat to go stale.
        final = self.status()
        final.update(
            state="stopped",
            reason="The DNS observer was stopped (docker stop, compose down or "
            f"an upgrade) at {time.strftime('%Y-%m-%d %H:%M:%S')}.",
            fix="Start it again: docker compose up -d dns-observer. The router's "
            "secondary DNS keeps the house online meanwhile, if one is set.",
            live=False,
            stale_after=final["heartbeat"],
        )
        final["server"]["answering"] = False
        health.write_status(self.cfg.status_path, final)
        await self.api.close()


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        cfg = Config.from_env()
    except ConfigError as exc:
        log.error("configuration refused: %s", exc)
        return 2
    new = adguard.write_config(cfg)
    log.info(
        "%s AdGuard Home config; DNS on %s port %s, upstreams %s, fallback %s",
        "wrote new" if new else "updated",
        ",".join(cfg.bind_hosts), cfg.port,
        ",".join(cfg.upstreams), ",".join(cfg.fallback) or "none",
    )
    asyncio.run(Supervisor(cfg).run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
