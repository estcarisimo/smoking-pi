import asyncio

import pytest

import connection_test as ct
from config import Config

ROUTER = "192.168.1.1"
LAN = "192.168.1.10"


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(ct, "ARRIVAL_WAIT", 0.0)

    async def no_sleep(_):
        return None

    monkeypatch.setattr(ct.asyncio, "sleep", no_sleep)


class Net:
    """A fake house: which servers answer, and what the router does with
    names under the canary domain (forward all, some, none, or answer them
    itself)."""

    def __init__(self, *, forward="all", router_aa=False, down=(), upstream_ok=True):
        self.forward = forward
        self.router_aa = router_aa
        self.down = set(down)
        self.upstream_ok = upstream_ok
        self.log: list[dict] = []
        self.seen = 0

    async def query(self, name, rdtype, server, port):
        if server in self.down:
            raise TimeoutError
        if rdtype == "ANY":
            return ct.Answer("REFUSED", 0, False, 0.1)
        if not name.startswith("t"):  # the real name
            ok = self.upstream_ok
            return ct.Answer("NOERROR" if ok else "SERVFAIL", 1 if ok else 0, False, 12)
        # A test name asked of the router.
        self.seen += 1
        if self.forward == "all" or (self.forward == "some" and self.seen % 2):
            self.log.append({"question": {"name": name + "."}})
        return ct.Answer("NXDOMAIN", 0, self.router_aa, 9)

    async def querylog(self, *, search=None, limit=100):
        return [e for e in self.log if search in e["question"]["name"]][:limit]


def run(env, net, via=ROUTER, lan=LAN, count=6):
    return asyncio.run(ct.run(
        Config.from_env(env), via=via, count=count, name="example.com",
        query=net.query, querylog=net.querylog, lan=lan,
    ))


def results(checks):
    return [(c.name, c.result) for c in checks]


def test_everything_works(env):
    checks = run(env, Net())
    assert all(c.result == "ok" for c in checks)
    assert checks[-1].detail.startswith("6/6 ")


def test_router_not_pointed_at_the_pi(env):
    last = run(env, Net(forward="none"))[-1]
    assert last.result == "fail"
    assert "not using the Pi" in last.fix and LAN in last.fix


def test_router_answers_the_canary_suffix_itself(env):
    # RFC 6761 routers answer .invalid / .test authoritatively, never forwarding.
    last = run(env, Net(forward="none", router_aa=True))[-1]
    assert last.result == "fail"
    assert "itself" in last.detail
    assert "DNS_CANARY_DOMAIN canary.smoking-pi.home.arpa" in last.fix


def test_some_forwarded_is_a_warning_not_a_failure(env):
    last = run(env, Net(forward="some"))[-1]
    assert last.result == "warn"
    assert last.detail.startswith("3/6 ")
    assert "secondary" in last.fix


def test_server_down_stops_early(env):
    checks = run(env, Net(down={"127.0.0.1"}))
    assert results(checks) == [("Pi DNS server answers", "fail")]


def test_not_reachable_on_the_lan(env):
    checks = run(env, Net(down={LAN}))
    assert ("answers on the LAN", "fail") in results(checks)


def test_upstreams_failing(env):
    checks = dict(results(run(env, Net(upstream_ok=False))))
    assert checks["resolves through the upstreams"] == "fail"
    assert checks["router resolves"] == "fail"


def test_no_router_skips_the_router_checks(env):
    checks = run(env, Net(), via=None)
    assert checks[-1].result == "skip"
    assert not any(c.name == "router resolves" for c in checks)


def test_router_silent_on_test_names(env):
    net = Net(down={ROUTER})
    last = run(env, net)[-1]
    assert last.result == "fail"
    assert "did not answer" in last.detail


def test_default_canary_is_forwardable():
    # .invalid and .test are answered by RFC 6761 routers themselves.
    import config

    assert config.DEFAULT_CANARY_DOMAIN.endswith(".home.arpa")


def test_router_address(env):
    assert ct.router_address(Config.from_env({**env, "DNS_CANARY_VIA": "off"}), None) is None
    assert ct.router_address(Config.from_env({**env, "DNS_CANARY_VIA": "10.0.0.1"}), None) == "10.0.0.1"
    assert ct.router_address(Config.from_env(env), "10.9.9.9") == "10.9.9.9"
