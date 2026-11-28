"""The real node agent against the real lead, in process.

The agent's join, heartbeat, renewal and leave (fleet/join.py, agent.py,
leave.py) talk through an injected transport that calls the lead's WSGI
application (fleet/service.py) directly, presenting the client certificate
the agent would present over TLS. No socket opens. What the TLS handshake
itself accepts and refuses is tests/test_lead_tls.py's.
"""

import json
import re
from pathlib import Path

import pytest

from fleet import admin, join, leave, pki, protocol, settings
from fleet.agent import Agent
from fleet.layout import node_paths
from fleet.transport import TransportError
from leadkit import LEAD_URL, NODE, Clock, call, make_lead

_PEM = re.compile(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----\n?", re.S)


class WsgiTransport:
    """fleet.transport.Transport onto a LeadService, with the TLS checks the
    real handshake makes: the node trusts the pinned root alone, and the
    lead accepts a client chain only if it leads to its root."""

    def __init__(self, service, base: str = LEAD_URL):
        self.service = service
        self.base = base
        self.calls: list[str] = []

    def request(self, method, url, body, tls):
        if not url.startswith(self.base + "/"):
            raise TransportError("refused")
        path = url[len(self.base):]
        self.calls.append(path)
        root = self.service.ca.root
        if tls.root is not None and pki.load_cert(Path(tls.root).read_bytes()) != root:
            raise TransportError("certificate")  # the lead's chain ends elsewhere
        cert_pem = None
        if tls.chain is not None and Path(tls.chain).exists():
            pems = _PEM.findall(Path(tls.chain).read_text())
            certs = [pki.load_cert(p) for p in pems]
            ok = len(certs) >= 2 and all(pki.verify_issued_by(a, b)
                                         for a, b in zip(certs, certs[1:]))
            if not ok or not pki.verify_issued_by(certs[-1], root):
                raise TransportError("tls")
            cert_pem = pems[0]
        status, out = call(self.service, method, path,
                           raw=body if body is not None else b"", cert_pem=cert_pem)
        return status, json.dumps(out).encode()


def node_cfg():
    return settings.load({"FLEET_NODE_ID": NODE, "FLEET_NODE_NAME": "Kitchen Pi",
                          "FLEET_SITE_NAME": "Home"})


def join_string(service, ttl=900) -> protocol.JoinString:
    """A join string minted on the lead's own clock."""
    secret, record = service.registry.add_token(ttl)
    return protocol.JoinString(
        fleet_id=service.fleet.fleet_id, lead_urls=(LEAD_URL,),
        token=protocol.Secret(secret), root_sha256=service.ca.root_sha256)


def run_agent(agent, clock, until, steps=40):
    """Step the agent, advancing the shared clock, until ``until()``."""
    for _ in range(steps):
        delay = agent.step()
        if until():
            return
        clock.advance(max(delay, 1.0))
    raise AssertionError("the agent never got there")


@pytest.fixture
def fleet(tmp_path):
    clock = Clock()
    lead_paths, service = make_lead(tmp_path / "lead", clock=clock)
    return clock, lead_paths, service, WsgiTransport(service), node_paths(tmp_path / "node")


def test_join_heartbeat_renew_revoke_leave_and_join_again(fleet):
    clock, lead_paths, service, transport, npaths = fleet
    cfg = node_cfg()

    # Join with a token the lead printed.
    text, _about = admin.token(lead_paths, settings.load({"FLEET_LEAD_URLS": LEAD_URL}),
                               900, None, None)
    result = join.enroll(protocol.JoinString.parse(text), cfg, npaths, transport)
    assert result["node_id"] == NODE and result["fleet_id"] == service.fleet.fleet_id
    node = service.registry.node(NODE)
    assert node["state"] == "active" and node["site_name"] == "Home"
    assert transport.calls == ["/fleet/root", "/fleet/enroll"]

    # Heartbeats over the node's certificate, recorded by the lead.
    agent = Agent(cfg, npaths, transport, clock=clock, env={})
    run_agent(agent, clock, lambda: service.registry.node(NODE)["last_heartbeat"])
    assert agent.report()["lead_link"] == "ok"
    beat = json.loads(service.registry.node(NODE)["last_heartbeat"])
    assert beat["inventory"]["node_name"] == "Kitchen Pi"

    # 75 days on, the certificate is due: the node renews with a new key.
    key_before, leaf_before = npaths.key.read_text(), npaths.chain.read_text()
    clock.advance(75 * 86400)
    run_agent(agent, clock, lambda: "/fleet/renew" in transport.calls)
    assert npaths.key.read_text() != key_before
    assert npaths.chain.read_text() != leaf_before
    assert sum(1 for n in service.registry.nodes() if n["node_id"] == NODE) == 1
    new_leaf = pki.load_cert(npaths.chain.read_bytes())
    assert service.registry.cert(pki.fingerprint(new_leaf)).node_id == NODE
    transport.calls.clear()
    run_agent(agent, clock, lambda: "/fleet/heartbeat" in transport.calls)
    assert agent.report()["lead_link"] == "ok"

    # Revoked on the lead: the agent's next heartbeat stops it for good.
    admin.revoke(lead_paths, "Kitchen Pi")
    run_agent(agent, clock, lambda: agent.report()["state"] == "revoked")
    transport.calls.clear()
    for _ in range(5):
        clock.advance(3600)
        agent.step()
    assert "/fleet/heartbeat" not in transport.calls

    # `fleet leave` on a revoked node removes the enrollment without asking
    # the lead; a new join string brings the same node id back.
    said = []
    code, out = leave.leave(cfg, npaths, transport, False, said.append)
    assert code == 0 and out["left"] and not npaths.key.exists()
    join.enroll(join_string(service), cfg, npaths, transport)
    assert service.registry.node(NODE)["state"] == "active"

    # A plain leave tells the lead; the node may join again afterwards.
    agent = Agent(cfg, npaths, transport, clock=clock, env={})
    run_agent(agent, clock, lambda: agent.report()["lead_link"] == "ok")
    code, out = leave.leave(cfg, npaths, transport, False, said.append)
    assert code == 0 and out["told_lead"]
    assert service.registry.node(NODE)["state"] == "left"
    join.enroll(join_string(service), cfg, npaths, transport)
    assert service.registry.node(NODE)["state"] == "active"


def test_a_node_whose_certificate_expired_joins_again_after_leave_force(fleet):
    """docs/fleet.md: the way back from cert_expired is `fleet leave --force`
    and a new join; the lead accepts a node id whose certificates all
    expired, and refuses (node_exists) one that still holds a live one."""
    clock, lead_paths, service, transport, npaths = fleet
    cfg = node_cfg()
    join.enroll(join_string(service), cfg, npaths, transport)
    # A second installation (or a lost answer) while the certificate lives:
    other = node_paths(npaths.root.parent / "other")
    with pytest.raises(join.JoinFailed) as err:
        join.enroll(join_string(service), cfg, other, transport)
    assert err.value.code == "node_exists" and "fleet revoke" in err.value.message
    # The lead away for 91 days: the certificate expired.
    clock.advance(91 * 86400)
    agent = Agent(cfg, npaths, transport, clock=clock, env={})
    agent.step()
    assert agent.report()["state"] == "cert_expired"
    code, _ = leave.leave(cfg, npaths, transport, False, lambda _t: None)
    assert code == 1  # it cannot tell the lead: --force
    code, _ = leave.leave(cfg, npaths, transport, True, lambda _t: None)
    assert code == 0
    join.enroll(join_string(service), cfg, npaths, transport)
    assert service.registry.node(NODE)["state"] == "active"


def test_rate_limited_join_is_the_throttle_for_invalid_tokens_only(fleet):
    _clock, _lead_paths, service, transport, npaths = fleet
    cfg = node_cfg()
    bad = protocol.JoinString(fleet_id=service.fleet.fleet_id, lead_urls=(LEAD_URL,),
                              token=protocol.Secret("w" * 43),
                              root_sha256=service.ca.root_sha256)
    codes = []
    for _ in range(6):
        with pytest.raises(join.JoinFailed) as err:
            join.enroll(bad, cfg, npaths, transport)
        codes.append(err.value.code)
    assert codes[:4] == ["token_invalid"] * 4 and codes[4:] == ["rate_limited"] * 2
    assert "new join string" in err.value.message
    # From the same (throttled) address, a valid token joins at once.
    join.enroll(join_string(service), cfg, npaths, transport)
    assert service.registry.node(NODE)["state"] == "active"


def test_leave_on_a_host_that_is_also_the_lead_keeps_the_lead_serving(fleet):
    """The agent's leave touches the agent's volume only; the lead's state,
    and its URLs (cli keeps FLEET_LEAD_URLS when fleet-lead is on), stay."""
    _clock, lead_paths, service, transport, npaths = fleet
    cfg = node_cfg()
    join.enroll(join_string(service), cfg, npaths, transport)
    before = sorted(p.relative_to(lead_paths.root) for p in lead_paths.root.rglob("*")
                    if p.is_file() and p.name != "status.json")
    code, out = leave.leave(cfg, npaths, transport, False, lambda _t: None)
    assert code == 0 and out["told_lead"]
    after = sorted(p.relative_to(lead_paths.root) for p in lead_paths.root.rglob("*")
                   if p.is_file() and p.name != "status.json")
    assert [str(p) for p in after if not str(p).startswith("lead/registry.db")] == \
        [str(p) for p in before if not str(p).startswith("lead/registry.db")]
    status, body = call(service, "GET", "/fleet/root")
    assert status == 200 and body["root_pem"] == pki.cert_pem(service.ca.root)


def test_the_transport_refuses_a_chain_from_another_fleet(fleet, tmp_path):
    _clock, _lead_paths, service, transport, npaths = fleet
    join.enroll(join_string(service), node_cfg(), npaths, transport)
    other_paths, other = make_lead(tmp_path / "other-lead")
    stranger = WsgiTransport(other)
    with pytest.raises(TransportError):
        stranger.request("POST", LEAD_URL + "/fleet/heartbeat", b"{}",
                         type("T", (), {"root": None, "chain": npaths.chain, "key": None})())
