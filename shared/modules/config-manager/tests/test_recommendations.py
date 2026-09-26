"""The "Your connection" card: recommendations.py, and GET /recommendations
against a fake Docker client."""

import json
from types import SimpleNamespace

import pytest

import api as api_module
import recommendations

# The reference Pi, 2026-09-24: Wi-Fi uplink, router not measured, both
# system resolvers already seeded as DNS targets, CPE found by traceroute.
FACTS = {
    "uplink": "wlan0",
    "wireless": True,
    "gateway4": "192.168.86.1",
    "gateway6": None,
    "resolvers": ["1.1.1.1", "8.8.8.8"],
    "cpe": {"ipv4": "136.25.220.1", "ipv6": None, "updated": 1790210247.9},
}

TARGETS = """*** Targets ***
probe = FPing

+ dns_resolvers
menu = DNS

++ CloudflareDNS
probe = DNS
host = 1.1.1.1
lookup = google.com

++ GoogleDNS
probe = DNS
host = 8.8.8.8

+ top_sites

++ Google
host = google.com
"""

CPE = """+ CPE
menu = CPE

++ CPE_IPv4
host = 136.25.220.1
"""


def by_kind(body):
    return {(i["kind"], i["host"]): i for i in body["items"]}


def test_measured_hosts_reads_every_generated_file():
    assert recommendations.measured_hosts(TARGETS, CPE) == {
        "1.1.1.1": "CloudflareDNS", "8.8.8.8": "GoogleDNS",
        "google.com": "Google", "136.25.220.1": "CPE_IPv4"}


def test_the_reference_pi_is_told_its_router_is_not_measured():
    body = recommendations.recommend(FACTS, recommendations.measured_hosts(TARGETS, CPE))
    items = by_kind(body)
    assert body["uplink"] == {"interface": "wlan0", "wireless": True}
    router = items[("gateway", "192.168.86.1")]
    assert router["status"] == "suggested"
    assert router["suggest"] == {"target_type": "icmp", "name": "Router",
                                 "hostname": "192.168.86.1",
                                 "title": "Router (192.168.86.1)"}
    # The resolvers are already there, under the names the seed gave them.
    assert items[("resolver", "1.1.1.1")]["measured_as"] == "CloudflareDNS"
    assert items[("resolver", "8.8.8.8")]["status"] == "measured"
    assert items[("resolver", "8.8.8.8")]["suggest"] is None
    # The CPE is measured, but not by anything in the target list.
    cpe = items[("cpe", "136.25.220.1")]
    assert (cpe["status"], cpe["measured_as"]) == ("automatic", "CPE_IPv4")
    assert body["suggested"] == 1


def test_a_resolver_nobody_measures_is_a_dns_suggestion_the_form_accepts():
    facts = {**FACTS, "resolvers": ["192.168.86.1", "2001:db8::53"]}
    items = by_kind(recommendations.recommend(facts, {}))
    v4 = items[("resolver", "192.168.86.1")]["suggest"]
    assert v4["target_type"] == "dns" and v4["dns_query"] == "google.com"
    assert v4["name"] == "Resolver_192_168_86_1"
    v6 = items[("resolver", "2001:db8::53")]["suggest"]["name"]
    assert v6 == "Resolver_2001_db8_53"
    # The add form's rule: a letter first, letters digits underscores, <= 30.
    for name in (v4["name"], v6):
        assert name[0].isalpha() and len(name) <= 30
        assert all(c.isalnum() or c == "_" for c in name)


def test_a_long_v6_resolver_name_is_cut_to_the_form_limit():
    host = "2001:db8:1234:5678:9abc:def0:1234:5678"
    item = by_kind(recommendations.recommend(
        {"resolvers": [host]}, {}))[("resolver", host)]
    assert len(item["suggest"]["name"]) == 30


def test_on_host_resolvers_are_not_suggested():
    """127.0.0.53, ::1 and Tailscale's 100.100.100.100 answer on this host."""
    facts = {"resolvers": ["127.0.0.53", "::1", "100.100.100.100"]}
    body = recommendations.recommend(facts, {})
    assert {i["status"] for i in body["items"]} == {"local"}
    assert body["suggested"] == 0


def test_a_link_local_v6_router_is_explained_not_suggested():
    body = recommendations.recommend({"gateway6": "fe80::1"}, {})
    (item,) = body["items"]
    assert item["status"] == "not_measurable" and item["suggest"] is None


def test_a_global_v6_router_is_suggested():
    body = recommendations.recommend({"gateway6": "2001:db8::1"}, {})
    (item,) = body["items"]
    assert item["suggest"]["name"] == "Router6"


def test_matching_ignores_case():
    facts = {"resolvers": ["DNS.Example"]}
    item = recommendations.recommend(facts, {"dns.example": "Mine"})["items"][0]
    assert item["measured_as"] == "Mine"


def test_nothing_known_is_an_empty_card_not_an_error():
    body = recommendations.recommend({}, {})
    assert body == {"available": True,
                    "uplink": {"interface": None, "wireless": False},
                    "items": [], "suggested": 0, "public_resolver": {}}


# --- the Docker side --------------------------------------------------------

class FakeContainer:
    def __init__(self, network_mode, replies):
        self.attrs = {"HostConfig": {"NetworkMode": network_mode}}
        self.replies = replies  # {argv[0]: (exit_code, bytes)}
        self.calls = []

    def exec_run(self, cmd, demux=False):
        self.calls.append(cmd)
        code, out = self.replies.get(cmd[0], (0, b""))
        # docker-py: with demux=True, output is (stdout, stderr).
        if demux and not isinstance(out, tuple):
            out = (out, None)
        return SimpleNamespace(exit_code=code, output=out)


@pytest.fixture()
def fake_smokeping(monkeypatch):
    def install(network_mode="host", replies=None):
        container = FakeContainer(network_mode, replies or {})
        client = SimpleNamespace(containers=SimpleNamespace(get=lambda name: container))
        monkeypatch.setattr(api_module.docker, "from_env", lambda: client)
        monkeypatch.setattr(api_module, "resolve_container_name", lambda svc: "pro-smokeping-1")
        return container
    return install


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("CONFIG_API_TOKEN", raising=False)
    api_module.app.config["TESTING"] = True
    with api_module.app.test_client() as c:
        yield c


def test_the_endpoint_reads_the_host_from_smokepings_namespace(
        client, fake_smokeping, monkeypatch, tmp_path):
    (tmp_path / "Targets").write_text(TARGETS)
    monkeypatch.setattr(api_module, "OUTPUT_DIR", tmp_path)
    container = fake_smokeping(replies={
        # A log line on stderr must not break the parse of stdout.
        "python3": (0, (json.dumps(FACTS).encode() + b"\n", b"INFO something\n")),
        "cat": (0, CPE.encode()),
    })
    body = client.get("/recommendations").get_json()
    assert body["available"] is True
    assert body["suggested"] == 1
    assert ["python3", "/exporters/host_facts.py"] in container.calls
    assert ["cat", "/config/CPE_Targets"] in container.calls


def test_a_smokeping_on_a_docker_network_says_so(client, fake_smokeping):
    """Standard's SmokePing is on the bridge: its gateway is Docker's, and
    calling that "your router" would be wrong in the most confusing way."""
    container = fake_smokeping(network_mode="standard_default")
    body = client.get("/recommendations").get_json()
    assert body["available"] is False
    assert "Docker network" in body["reason"]
    assert container.calls == []


def test_a_failing_host_facts_is_reported(client, fake_smokeping):
    fake_smokeping(replies={"python3": (2, b"No such file")})
    body = client.get("/recommendations").get_json()
    assert body == {"available": False,
                    "reason": "could not read the host's network (host_facts exit 2)"}


def test_unreadable_facts_are_reported(client, fake_smokeping):
    fake_smokeping(replies={"python3": (0, b"not json")})
    assert client.get("/recommendations").get_json()["available"] is False


def test_the_endpoint_requires_the_token(client, monkeypatch):
    monkeypatch.setenv("CONFIG_API_TOKEN", "sekrit")
    assert client.get("/recommendations").status_code == 401


def test_the_public_resolver_is_passed_through_untouched():
    snap = {"router": {"ok": True, "owner": "AS15169 GOOGLE - Google LLC, US"}}
    body = recommendations.recommend({**FACTS, "public_resolver": snap}, {})
    assert body["public_resolver"] == snap
    assert recommendations.recommend(FACTS, {})["public_resolver"] == {}
