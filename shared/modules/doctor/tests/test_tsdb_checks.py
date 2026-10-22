"""Tests for targets-in-tsdb and monitor-radio.

Docker is a fake that answers by what a command asks for, so no daemon,
InfluxDB or network is touched. Each check is shown to fire on the failure
it exists for: a measured target with no point in InfluxDB, a radio that
heard nothing for hours, a radio deaf right now.
"""

from __future__ import annotations

import datetime
import json
import pathlib

import pytest

from doctor import live_checks
from doctor.report import Status

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
NOW = datetime.datetime(2026, 10, 18, 12, 0, tzinfo=datetime.timezone.utc).timestamp()


def _iso(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _csv(columns: list[str], rows: list[list]) -> str:
    lines = ["#group," + ",".join(["false"] * (len(columns) + 1)),
             "#datatype,string,long," + ",".join(["string"] * (len(columns) - 1)),
             "#default,_result,,", ",result,table," + ",".join(columns)]
    lines += [",,0," + ",".join(str(v) for v in row) for row in rows]
    return "\r\n".join(lines) + "\r\n\r\n"


class FakeDocker:
    """Answers `exec <influx> ... <flux>` by which query it is."""

    def __init__(self, measurements=None, last_points=None, uplink=None, deaf=None,
                 influx="pro-influxdb-1", config_manager="pro-config-manager-1",
                 present=True, fail_queries=False, started=None):
        self.measurements = measurements
        self.last_points = last_points or []
        self.uplink = uplink or []
        self.deaf = deaf or []
        self.influx = influx
        self.config_manager = config_manager
        self.present = present
        self.fail_queries = fail_queries
        self.started = started or {}
        self.calls: list[list[str]] = []

    def available(self):
        return self.present

    def container_for_service(self, service):
        return {"influxdb": self.influx, "config-manager": self.config_manager,
                "smokeping": "pro-smokeping-1"}.get(service)

    def run(self, args):
        self.calls.append(args)
        if args[0] == "inspect":
            when = self.started.get(args[-1])
            return (0, _iso(when) + ".123456789Z") if when else (1, "")
        if args[:2] == ["exec", self.config_manager]:
            return 0, json.dumps(self.measurements)
        if args[:2] == ["exec", self.influx]:
            if self.fail_queries:
                return 1, ""
            flux = args[-1]
            if "last()" in flux and "exists r.target" in flux:
                return 0, _csv(["_time", "target"], self.last_points)
            if "elapsed(" in flux:
                return 0, _csv(["at", "interface", "kind", "value"], self.uplink)
            if "rx_packets" in flux:
                return 0, _csv(["_time", "associated", "interface", "rx_packets"], self.deaf)
        return 1, ""


class Repo:
    root = REPO_ROOT


def _rows(**states):
    return {"available": True, "targets": [
        {"section": "S", "name": n, "probe": "FPing", "step": 300, "age": 10, "state": s}
        for n, s in states.items()]}


# ---- targets-in-tsdb ------------------------------------------------------

def test_every_fresh_target_with_a_recent_point_passes():
    d = FakeDocker(_rows(Google="fresh", Old="stale", New="pending"),
                   [[_iso(NOW - 120), "Google"]])
    res = live_checks.check_targets_in_tsdb(d, Repo(), now=lambda: NOW)
    assert res.status is Status.OK, res.findings
    assert res.summary == "all 1 freshly measured targets have recent points in InfluxDB"


def test_a_measured_target_with_no_point_fails():
    d = FakeDocker(_rows(Google="fresh", Netflix_1="fresh"),
                   [[_iso(NOW - 120), "Google"]])
    res = live_checks.check_targets_in_tsdb(d, Repo(), now=lambda: NOW)
    assert res.status is Status.FAIL
    assert "1 target(s) SmokePing measures have no point in InfluxDB" in res.findings[0].message
    assert "Netflix_1" in res.findings[0].message


def test_nothing_in_influx_at_all_names_the_exporter():
    d = FakeDocker(_rows(A="fresh", B="fresh"), [])
    res = live_checks.check_targets_in_tsdb(d, Repo(), now=lambda: NOW)
    assert res.status is Status.FAIL
    assert "the exporter is not writing" in res.findings[0].message


def test_a_target_reaching_influx_late_fails():
    # 3 steps of 300 s + 600 s of slack = 25 min; this one is 40 min old.
    d = FakeDocker(_rows(Slow="fresh"), [[_iso(NOW - 2400), "Slow"]])
    res = live_checks.check_targets_in_tsdb(d, Repo(), now=lambda: NOW)
    assert res.status is Status.FAIL
    assert "reach InfluxDB late" in res.findings[0].message


def test_a_long_step_widens_the_window():
    rows = _rows(Hourly="fresh")
    rows["targets"][0]["step"] = 3600
    d = FakeDocker(rows, [[_iso(NOW - 2 * 3600), "Hourly"]])
    res = live_checks.check_targets_in_tsdb(d, Repo(), now=lambda: NOW)
    assert res.status is Status.OK, res.findings
    flux = next(c for c in d.calls if c[:2] == ["exec", "pro-influxdb-1"])[-1]
    assert "range(start: -11400s)" in flux  # 3 × 3600 + 600


@pytest.mark.parametrize("docker, why", [
    (FakeDocker(present=False), "docker is not available"),
    (FakeDocker(_rows(A="fresh"), influx=None), "ClickHouse backend"),
    (FakeDocker(_rows(A="missing")), "no target has fresh SmokePing data"),
])
def test_targets_in_tsdb_skips(docker, why):
    res = live_checks.check_targets_in_tsdb(docker, Repo(), now=lambda: NOW)
    assert res.status is Status.SKIP
    assert why in res.summary


def test_a_failing_influx_query_warns():
    d = FakeDocker(_rows(A="fresh"), fail_queries=True)
    assert live_checks.check_targets_in_tsdb(d, Repo(), now=lambda: NOW).status is Status.WARN


def test_the_token_never_reaches_the_command_line():
    d = FakeDocker(_rows(A="fresh"), [[_iso(NOW), "A"]])
    live_checks.check_targets_in_tsdb(d, Repo(), now=lambda: NOW)
    influx_call = next(c for c in d.calls if c[:2] == ["exec", "pro-influxdb-1"])
    assert '"$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN"' in " ".join(influx_call)


# ---- monitor-radio --------------------------------------------------------

def _uplink(newest=NOW - 10, interval=10, value=1, interface="wlan0", carried=None):
    """The uplink query's rows: last time on the route, newest sample, spacing."""
    rows = [[_iso(newest) + "", interface, "newest", float(value)],
            ["", interface, "interval", float(interval)]]
    if value == 1 or carried is not None:
        rows.append([_iso(carried if carried is not None else newest), interface,
                     "uplink", 1.0])
    return rows


def _deaf(start, seconds, interval=10, interface="wlan0"):
    return [[_iso(start + i * interval), 1, interface, 0]
            for i in range(int(seconds // interval))]


def _route(tmp_path, iface):
    path = tmp_path / "route"
    path.write_text("Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\n"
                    f"{iface}\t00000000\t0156A8C0\t0003\t0\t0\t600\n")
    return path


def _radio(d, tmp_path, timer=True, route=None):
    path = tmp_path / "smoking-pi-watchdog.timer"
    if timer:
        path.write_text("[Timer]\n")
    return live_checks.check_monitor_radio(
        d, Repo(), now=lambda: NOW, watchdog_timer=path,
        proc_route=route or tmp_path / "no-route")


def test_a_radio_that_heard_all_day_passes(tmp_path):
    res = _radio(FakeDocker(uplink=_uplink()), tmp_path)
    assert res.status is Status.OK, res.findings
    assert res.summary == "wlan0 heard throughout the last day"


def test_a_long_deaf_span_warns_with_its_length(tmp_path):
    d = FakeDocker(uplink=_uplink(), deaf=_deaf(NOW - 6 * 3600, 5 * 3600 + 30 * 60))
    res = _radio(d, tmp_path)
    assert res.status is Status.WARN
    message = res.findings[0].message
    assert "wlan0 heard nothing for 330 min in the last day (1 span(s), longest 330 min " \
        "from 2026-10-18 06:00 UTC)" in message
    assert "watchdog enable" not in message


def test_without_the_watchdog_the_finding_names_it(tmp_path):
    d = FakeDocker(uplink=_uplink(), deaf=_deaf(NOW - 6 * 3600, 3600))
    res = _radio(d, tmp_path, timer=False)
    assert "`sudo smoking-pi watchdog enable`" in res.findings[0].message


def test_a_radio_deaf_now_fails(tmp_path):
    d = FakeDocker(uplink=_uplink(newest=NOW - 10),
                   deaf=_deaf(NOW - 3600, 3600 - 10 + 10))
    res = _radio(d, tmp_path)
    assert res.status is Status.FAIL
    assert res.summary == "the uplink radio is deaf now"
    assert "wlan0 is deaf now (since 2026-10-18 11:00 UTC)" in res.findings[0].message


def test_lone_deaf_samples_are_not_a_hang(tmp_path):
    # The staging Pi: one quiet sample every few minutes, 30 s apart at most.
    samples = []
    for k in range(40):
        start = NOW - 20 * 3600 + k * 600
        samples += [[_iso(start), 1, "wlan0", 0], [_iso(start + 30), 1, "wlan0", 0],
                    [_iso(start + 60), 1, "wlan0", 0]]
    d = FakeDocker(uplink=_uplink(interval=30), deaf=samples)
    assert _radio(d, tmp_path).status is Status.OK


def test_a_short_blip_is_not_reported(tmp_path):
    d = FakeDocker(uplink=_uplink(), deaf=_deaf(NOW - 3600, 120))
    assert _radio(d, tmp_path).status is Status.OK


def test_a_stopped_collector_warns(tmp_path):
    res = _radio(FakeDocker(uplink=_uplink(newest=NOW - 3600)), tmp_path)
    assert res.status is Status.WARN
    assert "the newest Wi-Fi sample of wlan0 is 60 min old" in res.findings[0].message


@pytest.mark.parametrize("docker, why", [
    (FakeDocker(present=False), "docker is not available"),
    (FakeDocker(influx=None), "ClickHouse backend"),
    (FakeDocker(uplink=[]), "carried the default route"),
    (FakeDocker(uplink=_uplink(value=0)), "carried the default route"),
])
def test_monitor_radio_skips(docker, why, tmp_path):
    res = _radio(docker, tmp_path)
    assert res.status is Status.SKIP
    assert why in res.summary


def test_the_deaf_query_is_microcuts_own(tmp_path):
    d = FakeDocker(uplink=_uplink())
    _radio(d, tmp_path)
    flux = d.calls[-1][-1]
    assert 'from(bucket: "BUCKET")' in flux
    assert "r.rx_packets == 0 or r.associated == 0" in flux


def test_a_radio_that_lost_its_association_fails(tmp_path):
    # Not associated: the route left with the radio, so its newest sample
    # says uplink 0; it carried the route until the drop.
    d = FakeDocker(uplink=_uplink(newest=NOW - 10, value=0, carried=NOW - 1800),
                   deaf=[[_iso(NOW - 1800 + 10 * i), 0, "wlan0", 0] for i in range(180)])
    res = _radio(d, tmp_path)
    assert res.status is Status.FAIL
    assert "wlan0 is deaf now" in res.findings[0].message


def test_deaf_now_is_not_claimed_when_ethernet_took_the_route(tmp_path):
    d = FakeDocker(uplink=_uplink(newest=NOW - 10), deaf=_deaf(NOW - 3600, 3600))
    res = _radio(d, tmp_path, route=_route(tmp_path, "eth0"))
    assert res.status is Status.WARN


def test_the_gap_follows_the_measured_interval(tmp_path):
    # 30 s sampling: consecutive deaf samples 30 s apart are one run.
    d = FakeDocker(uplink=_uplink(interval=30), deaf=_deaf(NOW - 7200, 1800, interval=30))
    res = _radio(d, tmp_path)
    assert res.status is Status.WARN
    assert "30 min" in res.findings[0].message


def test_a_damaged_common_tree_warns_not_crashes(tmp_path):
    (tmp_path / "shared/modules/common").mkdir(parents=True)
    (tmp_path / "shared/modules/common/__init__.py").write_text("")
    (tmp_path / "shared/modules/common/microcuts.py").write_text("raise RuntimeError('x')\n")

    class Damaged:
        root = tmp_path

    res = live_checks.check_monitor_radio(FakeDocker(uplink=_uplink()), Damaged(),
                                          now=lambda: NOW, watchdog_timer=tmp_path / "x")
    assert res.status is Status.WARN
    import sys
    assert not [m for m in sys.modules if m.startswith("_doctor_common")]


def test_the_common_import_leaves_nothing_behind(tmp_path):
    import os
    import sys
    before = os.environ.get("INFLUX_BUCKET")
    _radio(FakeDocker(uplink=_uplink()), tmp_path)
    assert os.environ.get("INFLUX_BUCKET") == before
    assert not [m for m in sys.modules if m.startswith("_doctor_common")]
    assert not list((REPO_ROOT / "shared/modules/common").glob("__pycache__/*doctor*"))


def test_nanosecond_times_parse():
    assert live_checks._epoch("2026-10-18T18:04:17.000000000Z") == live_checks._epoch(
        "2026-10-18T18:04:17Z")


def test_a_late_target_right_after_a_restart_only_warns():
    d = FakeDocker(_rows(Slow="fresh"), [[_iso(NOW - 2400), "Slow"]],
                   started={"pro-smokeping-1": NOW - 120})
    res = live_checks.check_targets_in_tsdb(d, Repo(), now=lambda: NOW)
    assert res.status is Status.WARN
    assert "run `sudo smoking-pi doctor --live` again" in res.findings[-1].message


def test_a_null_target_list_does_not_crash():
    d = FakeDocker({"available": True, "targets": None})
    res = live_checks.check_targets_in_tsdb(d, Repo(), now=lambda: NOW)
    assert res.status is Status.SKIP


def test_a_tree_without_microcuts_warns(tmp_path):
    class Elsewhere:
        root = tmp_path

    res = live_checks.check_monitor_radio(FakeDocker(uplink=_uplink()), Elsewhere(),
                                          now=lambda: NOW, watchdog_timer=tmp_path / "x")
    assert res.status is Status.WARN
    assert "microcuts.py" in res.findings[0].message


def test_csv_with_several_tables_is_parsed():
    out = (_csv(["_time", "target"], [[_iso(NOW), "A"]])
           + _csv(["_time", "target"], [[_iso(NOW), "B"]]))

    class D:
        def run(self, args):
            return 0, out

    rows = live_checks._influx_rows(D(), "c", "q")
    assert [r["target"] for r in rows] == ["A", "B"]


def test_monitor_radio_sorts_by_time_before_taking_the_newest():
    """group() concatenates series (a disconnected sample writes without
    ssid/bssid, a series of its own) and last()/elapsed() follow row order:
    without a sort, the newest sample was the end of whichever series came
    last -- on the reference Pi after a reboot, a 17:19 sample two hours
    stale while the radio reported every 10 s."""
    code = "\n".join(line.split("//")[0] for line in live_checks._UPLINK_FLUX.splitlines())
    for name, until in (("samples = ", "\nup = "), ("spacing = ", "|> elapsed(")):
        part = code[code.index(name):]
        part = part[: part.index(until)]
        after_group = part.split('|> group(columns: ["interface"])', 1)[1]
        assert '|> sort(columns: ["_time"])' in after_group
    # newest and up are built from the sorted samples.
    assert "newest = samples |> last()" in code and "up = samples |> filter(" in code
