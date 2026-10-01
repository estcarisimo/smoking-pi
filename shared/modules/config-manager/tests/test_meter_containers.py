"""GET /meter/containers: what the traffic meter keys its counters on."""

from types import SimpleNamespace

import pytest

import api as api_module
import meter_containers as mc

CID = "5f4cee0a9519f75e2b1d9bf50449756321a4007359d189ca78583d5da12e2cc0"


def attrs(service, host=False, running=True, project="pro", cid=CID, networks=None):
    return {
        "Id": cid,
        "Config": {"Labels": {"com.docker.compose.project": project,
                              "com.docker.compose.service": service}},
        "State": {"Running": running},
        "HostConfig": {"NetworkMode": "host" if host else "pro_default"},
        "NetworkSettings": {"Networks": networks or {}},
    }


def test_cgroup_paths_per_driver():
    assert mc.cgroup_of(CID, "systemd") == (f"system.slice/docker-{CID}.scope", 2)
    assert mc.cgroup_of(CID, "cgroupfs") == (f"docker/{CID}", 1)
    assert mc.cgroup_of(CID, "something") is None
    assert mc.cgroup_of('x" accept', "systemd") is None


def test_describe_keeps_this_project_running_only():
    got = mc.describe([
        attrs("smokeping", host=True),
        attrs("grafana", networks={"pro_default": {"IPAddress": "172.18.0.4",
                                                   "GlobalIPv6Address": ""}}),
        attrs("postgres", running=False),
        attrs("smokeping", host=True, project="other"),
        attrs("web-admin", networks={"a": {"IPAddress": "not-an-ip"}}),
    ], "pro", "systemd")
    assert got == [
        {"service": "grafana", "host_network": False, "ipv4": ["172.18.0.4"], "ipv6": []},
        {"service": "smokeping", "host_network": True,
         "cgroup": f"system.slice/docker-{CID}.scope", "cgroup_level": 2},
        {"service": "web-admin", "host_network": False, "ipv4": [], "ipv6": []},
    ]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("CONFIG_API_TOKEN", raising=False)
    api_module.app.config["TESTING"] = True
    with api_module.app.test_client() as c:
        yield c


def test_endpoint(client, monkeypatch):
    fake = SimpleNamespace(
        info=lambda: {"CgroupDriver": "systemd"},
        containers=SimpleNamespace(list=lambda: [SimpleNamespace(attrs=attrs("smokeping",
                                                                             host=True))]))
    monkeypatch.setattr(api_module.docker, "from_env", lambda: fake)
    monkeypatch.setattr(api_module, "compose_project_name", lambda: "pro")
    body = client.get("/meter/containers").get_json()
    assert body["cgroup_driver"] == "systemd"
    assert body["containers"][0]["cgroup_level"] == 2


def test_endpoint_without_docker_answers_a_static_error(client, monkeypatch):
    def boom():
        raise RuntimeError("socket /var/run/docker.sock secret detail")
    monkeypatch.setattr(api_module.docker, "from_env", boom)
    resp = client.get("/meter/containers")
    assert resp.status_code == 500
    assert "secret detail" not in resp.get_data(as_text=True)
