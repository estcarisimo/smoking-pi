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
                 present=True, fail_queries=False):
        self.measurements = measurements
        self.last_points = last_points or []
        self.uplink = uplink or []
        self.deaf = deaf or []
        self.influx = influx
        self.config_manager = config_manager
        self.present = present
        self.fail_queries = fail_queries
        self.calls: list[list[str]] = []

    def available(self):
        return self.present

    def container_for_service(self, service):
        return {"influxdb": self.influx, "config-manager": self.config_manager}.get(service)

    def run(self, args):
        self.calls.append(args)
        if args[:2] == ["exec", self.config_manager]:
            return 0, json.dumps(self.measurements)
        if args[:2] == ["exec", self.influx]:
            if self.fail_queries:
                return 1, ""
            flux = args[-1]
            if "last()" in flux and "exists r.target" in flux:
                return 0, _csv(["_time", "target"], self.last_points)
            if "count()" in flux:
                return 0, _csv(["_time", "_value", "interface", "n"], self.uplink)
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
    flux = d.calls[-1][-1]
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

def _uplink(newest=NOW - 10, interval=10, value=1, interface="wlan0"):
    return [[_iso(newest), value, interface, int(86400 / interval)]]


def _deaf(start, seconds, interval=10, interface="wlan0"):
    return [[_iso(start + i * interval), 1, interface, 0]
            for i in range(int(seconds // interval))]


def _radio(d, tmp_path, timer=True):
    path = tmp_path / "smoking-pi-watchdog.timer"
    if timer:
        path.write_text("[Timer]\n")
    return live_checks.check_monitor_radio(d, Repo(), now=lambda: NOW, watchdog_timer=path)


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
    (FakeDocker(uplink=[]), "does not carry the default route"),
    (FakeDocker(uplink=_uplink(value=0)), "does not carry the default route"),
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
