import asyncio

import httpx
import pytest

import main
from config import Config

ROUTE = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
wlan0\t00000000\t0156A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0
eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0
wlan0\t0056A8C0\t00000000\t0001\t0\t0\t600\t00FFFFFF\t0\t0\t0
"""


def test_default_gateway_lowest_metric(tmp_path):
    route = tmp_path / "route"
    route.write_text(ROUTE)
    assert main.default_gateway(str(route)) == "192.168.1.1"


def test_default_gateway_none(tmp_path):
    route = tmp_path / "route"
    route.write_text(ROUTE.splitlines()[0] + "\n")
    assert main.default_gateway(str(route)) is None
    assert main.default_gateway(str(tmp_path / "missing")) is None


def test_parse_time_nanoseconds():
    assert main.parse_time("2026-09-25T22:26:15.213633677Z") == pytest.approx(
        1790375175.213633, abs=1e-5
    )
    assert main.parse_time("2026-09-25T22:26:15Z") == 1790375175.0


class FakeAPI:
    def __init__(self, log):
        self.log = log

    async def querylog(self, *, search=None, limit=100):
        if search:
            return [e for e in self.log if search in e["question"]["name"]][:limit]
        return self.log[:limit]

    async def stats(self):
        return {"top_queried_domains": [
            {"netflix.com": 40}, {"abc123.canary.smoking-pi.home.arpa": 1}, {"bbc.co.uk": 3},
        ]}

    async def close(self):
        pass


def entry(name, time, **kw):
    return {"question": {"name": name, "type": "A"}, "time": time, "status": "NOERROR",
            "upstream": "https://1.1.1.1:443/dns-query", **kw}


def test_refresh_separates_canaries_from_the_house(env, monkeypatch):
    # refresh() drops canaries older than a day; pin the clock to the log's hour.
    monkeypatch.setattr(main.time, "time", lambda: 1790375200.0)
    sup = main.Supervisor(Config.from_env(env))
    sup.canaries = {"abc123": [1790375100.0, None], "ffff00": [1790375100.0, None]}
    sup.api = FakeAPI([
        entry("abc123.canary.smoking-pi.home.arpa", "2026-09-25T22:25:00.5Z"),
        entry("selftest-1.canary.smoking-pi.home.arpa", "2026-09-25T22:26:30Z"),
        entry("netflix.com", "2026-09-25T22:26:00Z"),
    ])
    asyncio.run(sup.refresh())
    assert sup.last_query_at == main.parse_time("2026-09-25T22:26:00Z")
    assert sup.canaries["abc123"][1] == main.parse_time("2026-09-25T22:25:00.5Z")
    assert sup.canaries["ffff00"][1] is None
    assert [d["name"] for d in sup.top_domains] == ["netflix.com", "bbc.co.uk"]


class CountingAPI(FakeAPI):
    def __init__(self, log):
        super().__init__(log)
        self.searches = []

    async def querylog(self, *, search=None, limit=100):
        if search:
            self.searches.append(search)
        return await super().querylog(search=search, limit=limit)


def test_unseen_canaries_cost_one_search_however_many_there_are(env, monkeypatch):
    """A day of canaries that never arrived (a deaf radio, a router that does
    not forward here) was 288 searches every 30 s, and AdGuard spent a core
    on them. Now: one search for the canary domain, whatever the count."""
    now = 1790375200.0
    monkeypatch.setattr(main.time, "time", lambda: now)
    sup = main.Supervisor(Config.from_env(env))
    sup.canaries = {f"n{i:04d}": [now - 300 * i, None] for i in range(288)}
    sup.canaries["abc123"] = [now - 100, None]
    sup.api = CountingAPI([
        entry("abc123.canary.smoking-pi.home.arpa", "2026-09-25T22:25:00.5Z"),
        entry("netflix.com", "2026-09-25T22:26:00Z"),
    ])
    asyncio.run(sup.refresh())
    assert sup.api.searches == [sup.cfg.canary_domain]
    assert sup.canaries["abc123"][1] == main.parse_time("2026-09-25T22:25:00.5Z")
    # Older than the lookup window: still counted for the status, not searched.
    assert sup.canaries["n0100"][1] is None


def test_self_tests_do_not_push_an_arrived_canary_off_the_page(env, monkeypatch):
    """The self-test queries the canary domain every 10 s; with refreshes
    stalled for a while, 100 of them can be newer than a canary that did
    arrive. A fake that honors `limit`, newest first, must still find it."""
    now = 1790375200.0
    monkeypatch.setattr(main.time, "time", lambda: now)
    sup = main.Supervisor(Config.from_env(env))
    sup.canaries = {"abc123": [now - 1200, None]}
    newer = [entry(f"selftest-{i:08x}.canary.smoking-pi.home.arpa",
                   "2026-09-25T22:26:00Z") for i in range(100)]
    sup.api = CountingAPI(newer + [
        entry("abc123.canary.smoking-pi.home.arpa", "2026-09-25T22:06:00Z")])
    asyncio.run(sup.refresh())
    assert sup.canaries["abc123"][1] == main.parse_time("2026-09-25T22:06:00Z")


def test_no_unseen_canary_no_search(env, monkeypatch):
    now = 1790375200.0
    monkeypatch.setattr(main.time, "time", lambda: now)
    sup = main.Supervisor(Config.from_env(env))
    sup.canaries = {"abc123": [now - 100, now - 99],
                    "old000": [now - 2 * main.CANARY_LOOKUP_S, None]}
    sup.api = CountingAPI([entry("netflix.com", "2026-09-25T22:26:00Z")])
    asyncio.run(sup.refresh())
    assert sup.api.searches == []


def test_status_carries_what_readers_need(env):
    sup = main.Supervisor(Config.from_env(env))
    sup.server_ok = True
    s = sup.status()
    for key in ("state", "reason", "live", "heartbeat", "stale_after",
                "observed_until", "coverage", "canary", "upstreams", "server"):
        assert key in s
    assert s["stale_after"] - s["heartbeat"] == main.health.STALE_AFTER


class DownAPI(FakeAPI):
    def __init__(self):
        super().__init__([])
        self.down = True

    async def querylog(self, *, search=None, limit=100):
        if self.down:
            raise httpx.ConnectError("refused")
        return [entry("netflix.com", "2026-09-25T22:26:00Z")][:limit]


def test_the_wizard_says_once_when_adguard_does_not_answer(env, caplog):
    sup = main.Supervisor(Config.from_env(env))
    sup.api = DownAPI()
    with caplog.at_level("INFO"):
        assert asyncio.run(sup.unflushed_log()) is None
        assert asyncio.run(sup.unflushed_log()) is None
        sup.api.down = False
        assert len(asyncio.run(sup.unflushed_log())) == 1
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and "ConnectError" in warnings[0].getMessage()
    assert "answers again" in caplog.text
