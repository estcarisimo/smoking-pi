import json
from types import SimpleNamespace

import pytest

import main
import meter
import nft
import ruleset

COUNTERS = json.dumps({"nftables": [
    {"metainfo": {"version": "1.1.3"}},
    {"counter": {"family": "inet", "name": "total_tx", "table": "smoking_pi_meter",
                 "packets": 3, "bytes": 300}},
    {"counter": {"family": "inet", "name": "s_smokeping_tx", "table": "smoking_pi_meter",
                 "packets": 2, "bytes": 168}},
    {"counter": {"family": "inet", "name": "x", "table": "someone_else", "packets": 9,
                 "bytes": 9}},
]})


def runner(stdout="", code=0, stderr=""):
    calls = []

    def run(args, **kw):
        calls.append((args, kw.get("input")))
        return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)
    run.calls = calls
    return run


def test_reset_reads_only_our_table():
    r = runner(COUNTERS)
    assert nft.reset_counters(runner=r) == {"total_tx": (3, 300), "s_smokeping_tx": (2, 168)}
    assert r.calls[0][0] == ["nft", "-j", "reset", "counters", "table", "inet",
                             "smoking_pi_meter"]


def test_a_refused_script_raises_with_nft_last_line():
    r = runner(code=1, stderr="Error: x\nError: Could not process rule\n")
    with pytest.raises(nft.NftError, match="Could not process rule"):
        nft.load("table inet x {}", runner=r)


def test_teardown_is_create_then_delete():
    r = runner()
    nft.teardown(runner=r)
    assert r.calls[0][1] == ruleset.teardown()


def test_parse_counters_tolerates_junk():
    assert nft.parse_counters("not json") == {}
    assert nft.parse_counters('{"nftables": [{"counter": {"table": "smoking_pi_meter", '
                              '"name": "a", "bytes": "x"}}]}') == {}


def test_record_keeps_a_day_and_drops_the_future():
    old = {"t": 0, "seconds": 300, "services": {}}
    future = {"t": 10**9, "seconds": 300, "services": {}}
    state, interval = meter.record({"intervals": [old, future]},
                                   {"smokeping": {"rx": 1, "tx": 2}},
                                   {"smokeping": "host_network"}, 100_000.0, 300.0)
    assert state["intervals"] == [interval]
    assert interval["services"]["smokeping"] == {"rx": 1, "tx": 2, "kind": "host_network"}


def test_points():
    interval = {"t": 1301, "seconds": 300.0,
                "services": {"smokeping": {"rx": 100, "tx": 200, "kind": "host_network"}}}
    (pt,) = meter.points(interval)
    line = pt.to_line_protocol()
    assert line.startswith("service_traffic,kind=host_network,service=smokeping ")
    assert "rx_bytes=100i" in line and line.endswith(" 1301")


class FakeNft:
    NftError = nft.NftError

    def __init__(self):
        self.loaded = []
        self.counts = {}

    def load(self, script):
        self.loaded.append(script)

    def reset_counters(self):
        c, self.counts = self.counts, {}
        return c


def ALL_LIVE(svcs):
    return svcs, ()


def services():
    return [ruleset.Service("smokeping", True, "system.slice/docker-a.scope")]


def test_meter_loads_once_and_counts_across_a_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "uplinks", lambda env: ["wlan0"])
    fake = FakeNft()
    found = {"list": services()}
    m = main.Meter({"NETMETER_STATE_DIR": str(tmp_path)}, nft_mod=fake,
                   fetch=lambda url, token: found["list"], cgroups=ALL_LIVE)
    m.sync(mono=0.0)
    m.sync(mono=60.0)
    assert len(fake.loaded) == 1  # unchanged: not reloaded
    fake.counts = {"s_smokeping_tx": (1, 100), "total_tx": (1, 100)}
    found["list"] = services() + [ruleset.Service("grafana", False, ipv4=("172.18.0.5",))]
    m.sync(mono=120.0)  # changed: counters read before the reload
    assert len(fake.loaded) == 2
    fake.counts = {"s_smokeping_tx": (1, 50), "total_tx": (2, 80)}
    interval = m.collect(now=1_000_300.0, mono=300.0)
    assert interval["seconds"] == 300.0
    assert interval["services"]["smokeping"]["tx"] == 150
    assert interval["services"]["host"]["tx"] == 30
    assert (tmp_path / "state.json").exists()


def test_meter_keeps_the_last_services_when_config_manager_is_down(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "uplinks", lambda env: ["wlan0"])
    fake = FakeNft()
    answers = iter([services(), None])
    m = main.Meter({"NETMETER_STATE_DIR": str(tmp_path)}, nft_mod=fake,
                   fetch=lambda url, token: next(answers), cgroups=ALL_LIVE)
    m.sync(mono=0.0)
    m.sync(mono=60.0)
    assert [s.name for s in m.services] == ["smokeping"] and len(fake.loaded) == 1


def test_fetch_containers_parses_and_never_raises():
    class Resp:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self.body

    body = json.dumps({"containers": [
        {"service": "smokeping", "host_network": True,
         "cgroup": "system.slice/docker-a.scope", "cgroup_level": 2},
        {"service": "grafana", "host_network": False, "ipv4": ["172.18.0.5"]},
        {"nope": 1}]}).encode()
    seen = {}

    def opener(req, timeout):
        seen["token"] = req.get_header("X-api-token")
        return Resp(body)
    got = main.fetch_containers("http://127.0.0.1:5000/", "tok", opener=opener)
    assert [s.name for s in got] == ["smokeping", "grafana"] and seen["token"] == "tok"

    def broken(req, timeout):
        raise OSError("refused")
    assert main.fetch_containers("http://x", opener=broken) is None


def test_physical_interfaces(tmp_path):
    for name, device in (("wlan0", True), ("eth0", True), ("docker0", False), ("lo", False)):
        (tmp_path / name).mkdir()
        if device:
            (tmp_path / name / "device").mkdir()
    assert main.physical_interfaces(tmp_path) == ["eth0", "wlan0"]
    assert main.uplinks({"NETMETER_INTERFACES": "end0"}) == ["end0"]


def test_status_check(tmp_path, monkeypatch, capsys):
    import status
    monkeypatch.setenv("NETMETER_STATE_DIR", str(tmp_path))
    assert status.main(["--check"]) == 1
    main.write_status(str(tmp_path / "status.json"),
                      {"state": "counting", "uplinks": ["wlan0"], "services": ["smokeping"]})
    assert status.main(["--check"]) == 0
    assert status.main([]) == 0
    assert "smokeping" in capsys.readouterr().out
    main.write_status(str(tmp_path / "status.json"), {"state": "error", "reason": "refused"})
    assert status.main(["--check"]) == 1


def test_off_loads_nothing_and_removes_a_leftover(tmp_path, monkeypatch):
    import threading
    loaded = []
    monkeypatch.setattr(nft, "load", lambda *a, **k: loaded.append(a))
    waits = iter([False, True])
    monkeypatch.setattr(threading.Event, "is_set", lambda self: next(waits))
    monkeypatch.setattr(threading.Event, "wait", lambda self, t=None: True)
    assert main.run({"NETMETER": "off", "NETMETER_STATE_DIR": str(tmp_path)}) == 0
    # Only the teardown of a table a killed container left behind.
    assert [a[0] for a in loaded] == [ruleset.teardown()]
    assert '"state": "off"' in (tmp_path / "status.json").read_text()


def test_a_restart_with_the_same_id_reloads(tmp_path, monkeypatch):
    # docker restart: same path, new cgroup (new inode) -> the rule is dead.
    monkeypatch.setattr(main, "uplinks", lambda env: ["wlan0"])
    cg = tmp_path / "cg"
    (cg / "system.slice" / "docker-a.scope").mkdir(parents=True)
    fake = FakeNft()
    fake.counts = {"total_tx": (1, 1)}
    m = main.Meter({"NETMETER_STATE_DIR": str(tmp_path)}, nft_mod=fake,
                   fetch=lambda url, token: services(),
                   cgroups=lambda s: main.live_cgroups(s, cg))
    m.sync(mono=0.0)
    m.sync(mono=60.0)
    assert len(fake.loaded) == 1
    (cg / "system.slice" / "docker-a.scope").rmdir()
    (cg / "system.slice" / "keep").mkdir()  # take a different inode number
    (cg / "system.slice" / "docker-a.scope").mkdir()
    fake.counts = {"total_tx": (1, 1)}
    m.sync(mono=120.0)
    assert len(fake.loaded) == 2


def test_a_vanished_cgroup_is_left_out_not_fatal(tmp_path):
    kept, fp = main.live_cgroups(
        services() + [ruleset.Service("grafana", False, ipv4=("172.18.0.5",))], tmp_path)
    assert [s.name for s in kept] == ["grafana"] and fp == ()


def test_a_refused_reload_is_an_error_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "uplinks", lambda env: ["eth0+"])  # not quotable
    m = main.Meter({"NETMETER_STATE_DIR": str(tmp_path)}, nft_mod=FakeNft(),
                   fetch=lambda url, token: services(), cgroups=ALL_LIVE)
    m.sync(mono=0.0)
    assert m.error.startswith("no ruleset") and m.loaded is None


def test_empty_counter_output_is_an_error():
    with pytest.raises(nft.NftError):
        nft.reset_counters(runner=runner('{"nftables": []}'))
