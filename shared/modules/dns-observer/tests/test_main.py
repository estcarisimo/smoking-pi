import asyncio

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


def test_refresh_separates_canaries_from_the_house(env):
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


def test_status_carries_what_readers_need(env):
    sup = main.Supervisor(Config.from_env(env))
    sup.server_ok = True
    s = sup.status()
    for key in ("state", "reason", "live", "heartbeat", "stale_after",
                "observed_until", "coverage", "canary", "upstreams", "server"):
        assert key in s
    assert s["stale_after"] - s["heartbeat"] == main.health.STALE_AFTER
