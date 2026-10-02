import json
import time
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


@pytest.fixture
def chicago(monkeypatch):
    monkeypatch.setenv("TZ", "America/Chicago")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_the_ledger_keeps_days_with_the_internet_part_and_months_by_service(chicago):
    svc = {"smokeping": {"rx": 100, "tx": 50}, "host": {"rx": 10, "tx": 5}}
    kinds = {"smokeping": "host_network", "host": "rest"}
    # 2026-10-01 23:55 and 2026-10-02 00:00 in Chicago: two days, one month.
    state, _ = meter.record({"intervals": []}, svc, kinds, 1790916900.0, 300.0,
                            {"rx": 80, "tx": 40})
    state, _ = meter.record(state, svc, kinds, 1790917200.0, 300.0, {"rx": 80, "tx": 40})
    assert state["days"] == {
        "2026-10-01": {"rx": 110, "tx": 55, "seconds": 300.0, "internet": {"rx": 80, "tx": 40}},
        "2026-10-02": {"rx": 110, "tx": 55, "seconds": 300.0, "internet": {"rx": 80, "tx": 40}},
    }
    assert state["months"] == {"2026-10": {"seconds": 600.0, "services": {
        "smokeping": {"rx": 200, "tx": 100}, "host": {"rx": 20, "tx": 10}}}}


def test_the_ledger_keeps_only_the_newest_days_and_months(monkeypatch):
    monkeypatch.setattr(meter, "KEEP_DAYS", 2)
    monkeypatch.setattr(meter, "KEEP_MONTHS", 1)
    state = {"intervals": []}
    for n in range(3):
        state, _ = meter.record(state, {"host": {"rx": 1, "tx": 1}}, {}, 1_790_000_000.0
                                + n * 40 * 86_400, 300.0)
    assert len(state["days"]) == 2 and len(state["months"]) == 1


def test_points():
    interval = {"t": 1301, "seconds": 300.0,
                "services": {"smokeping": {"rx": 100, "tx": 200, "kind": "host_network"}}}
    (pt,) = meter.points(interval)
    line = pt.to_line_protocol()
    assert line.startswith("service_traffic,kind=host_network,service=smokeping ")
    assert "rx_bytes=100i" in line and line.endswith(" 1301")
    interval["internet"] = {"rx": 70, "tx": 30}
    _, inet = meter.points(interval)
    line = inet.to_line_protocol()
    assert line.startswith("internet_traffic ") and "tx_bytes=30i" in line


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
                   fetch=lambda url, token, **_: found["list"], cgroups=ALL_LIVE)
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
    assert interval["internet"] == {"rx": 0, "tx": 0}
    assert (tmp_path / "state.json").exists()


def test_meter_keeps_the_last_services_when_config_manager_is_down(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "uplinks", lambda env: ["wlan0"])
    fake = FakeNft()
    answers = iter([services(), None])
    m = main.Meter({"NETMETER_STATE_DIR": str(tmp_path)}, nft_mod=fake,
                   fetch=lambda url, token, **_: next(answers), cgroups=ALL_LIVE)
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
                   fetch=lambda url, token, **_: services(),
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
                   fetch=lambda url, token, **_: services(), cgroups=ALL_LIVE)
    m.sync(mono=0.0)
    assert m.error.startswith("no ruleset") and m.loaded is None


def test_empty_counter_output_is_an_error():
    with pytest.raises(nft.NftError):
        nft.reset_counters(runner=runner('{"nftables": []}'))


def test_a_damaged_state_file_falls_back_to_the_previous_copy(tmp_path):
    path = str(tmp_path / "state.json")
    good = {"intervals": [], "months": {"2026-10": {"seconds": 1.0, "services": {}}}}
    meter.save_state(path, good)
    meter.save_state(path, dict(good, updated=1))
    (tmp_path / "state.json").write_text("[]")  # valid JSON, wrong shape
    assert meter.load_state(path)["months"] == good["months"]
    assert (tmp_path / "state.json.corrupt").exists()


def test_the_ledger_skips_a_pre_2020_clock_and_survives_wrong_shaped_rows():
    svc = {"host": {"rx": 1, "tx": 1}}
    state, _ = meter.record({"intervals": []}, svc, {}, 300.0, 300.0)
    assert "days" not in state or state["days"] == {}
    day = time.strftime("%Y-%m-%d", time.localtime(1_790_000_000))
    damaged = {"intervals": [], "days": {day: ["junk"]},
               "months": {day[:7]: {"services": "junk"}}}
    state, _ = meter.record(damaged, svc, {}, 1_790_000_000.0, 300.0)
    assert state["days"][day]["rx"] == 1
    assert state["months"][day[:7]]["services"] == {"host": {"rx": 1, "tx": 1}}


def _reset_by_peer(req, timeout):
    raise ConnectionResetError("secret-token-in-message")


def test_fetch_failure_is_info_while_starting_and_warning_after(caplog):
    caplog.set_level("INFO", logger="netmeter")
    assert main.fetch_containers("http://x", opener=_reset_by_peer, starting=True) is None
    (rec,) = caplog.records
    assert rec.levelname == "INFO" and "still starting" in rec.getMessage()
    assert "ConnectionResetError" in rec.getMessage()
    caplog.clear()
    assert main.fetch_containers("http://x", opener=_reset_by_peer) is None
    (rec,) = caplog.records
    assert rec.levelname == "WARNING" and "secret" not in rec.getMessage()


def test_meter_passes_the_grace_period_to_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "uplinks", lambda env: ["wlan0"])
    seen = []
    m = main.Meter({"NETMETER_STATE_DIR": str(tmp_path)}, nft_mod=FakeNft(),
                   fetch=lambda url, token, starting: seen.append(starting) or services(),
                   cgroups=ALL_LIVE, started=1000.0)
    m.sync(mono=1000.0 + main.STARTUP_GRACE - 1)
    m.sync(mono=1000.0 + main.STARTUP_GRACE)
    assert seen == [True, False]


class FlakyWrite:
    """InfluxDB that fails while ``down`` and records every batch it is sent."""

    def __init__(self):
        self.down = True
        self.batches = []

    def __call__(self, points):
        self.batches.append(list(points))
        if self.down:
            raise main.http.client.HTTPException("token=secret")


def test_a_failed_batch_is_sent_again_with_the_next(caplog):
    caplog.set_level("INFO", logger="netmeter")
    influx = FlakyWrite()
    w = main.TrafficWriter(influx)
    assert w(["a1", "a2"], starting=True) is False
    assert [r.levelname for r in caplog.records] == ["INFO"]
    assert "still starting" in caplog.records[0].getMessage()
    influx.down = False
    assert w(["b1"]) is True
    assert influx.batches[-1] == ["a1", "a2", "b1"] and w.held == []
    assert w(["c1"]) is True and influx.batches[-1] == ["c1"]
    assert all(r.levelname == "INFO" for r in caplog.records)
    assert not any("secret" in r.getMessage() for r in caplog.records)


def test_at_most_one_batch_waits(caplog):
    caplog.set_level("INFO", logger="netmeter")
    influx = FlakyWrite()
    w = main.TrafficWriter(influx)
    w(["a"], starting=True)
    w(["b"])  # after the grace period: an ERROR, and "a" has had its one retry
    assert influx.batches[-1] == ["a", "b"] and w.held == ["b"]
    assert [r.levelname for r in caplog.records] == ["INFO", "ERROR", "WARNING"]
    for i in range(10):
        w([f"x{i}"])
    assert w.held == ["x9"] and influx.batches[-1] == ["x8", "x9"]
    assert not any("secret" in r.getMessage() for r in caplog.records)
