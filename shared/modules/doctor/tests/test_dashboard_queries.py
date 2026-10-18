"""Tests for dashboard-queries-run.

Grafana is a fake answering from a dict, so nothing reaches the network.
Each failure the check exists for is shown to fire: a panel whose query
errors, a variable that errors or offers nothing, a dead link, a repository
dashboard Grafana does not provision.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from doctor import dashboard_queries as dq
from doctor.report import Status


def _frame(values):
    return {"schema": {"fields": [{"name": "_value"}]}, "data": {"values": [values]}}


class FakeGrafana:
    """Dashboards by uid; ds/query answered by the first matching substring."""

    def __init__(self, dashboards, answers=None, user_uids=()):
        self.dashboards = dashboards
        self.answers = answers or {}
        self.user_uids = set(user_uids)
        self.calls: list[tuple[str, str, dict | None]] = []
        self.queries: list[str] = []

    def request(self, method, path, body=None, timeout=10.0):
        self.calls.append((method, path, body))
        if path.startswith("/api/search"):
            return 200, [{"uid": uid, "title": d.get("title")}
                         for uid, d in self.dashboards.items()]
        if path == "/api/datasources":
            return 200, [{"uid": "influxdb", "name": "InfluxDB", "type": "influxdb",
                          "isDefault": True}]
        if path.startswith("/api/dashboards/uid/"):
            uid = path.rsplit("/", 1)[1]
            return 200, {"dashboard": self.dashboards[uid],
                         "meta": {"provisioned": uid not in self.user_uids}}
        if path == "/api/ds/query":
            query = body["queries"][0]
            text = query.get("query") or query.get("rawSql") or ""
            self.queries.append(text)
            ref = query["refId"]
            for needle, answer in self.answers.items():
                if needle in text:
                    if answer == "timeout":
                        raise dq.Timeout()
                    if isinstance(answer, str):
                        return 400, {"results": {ref: {"error": answer, "status": 400}}}
                    return 200, {"results": {ref: {"frames": [_frame(answer)]}}}
            return 200, {"results": {ref: {"frames": [_frame([1.0])]}}}
        return 404, None


def _panel(title, query, **extra):
    return {"type": "timeseries", "title": title, "datasource": {"uid": "influxdb"},
            "targets": [{"refId": "A", "query": query}], **extra}


def _dash(uid, panels=(), variables=(), annotations=(), **extra):
    return {"uid": uid, "title": uid.title(), "time": {"from": "now-6h", "to": "now"},
            "panels": list(panels), "templating": {"list": list(variables)},
            "annotations": {"list": list(annotations)}, **extra}


def _run(grafana, **kwargs):
    kwargs.setdefault("timezone", "America/Chicago")
    return dq.check_dashboard_queries_run(grafana=grafana, **kwargs)


def test_a_healthy_grafana_passes_and_states_its_time():
    g = FakeGrafana({"a": _dash("a", [_panel("Latency", "from(bucket)")])})
    res = _run(g)
    assert res.status is Status.OK, res.findings
    assert "1 provisioned dashboards, 1 queries in" in res.summary
    assert res.summary.split(" in ")[1].split(" ")[1] == "s"


def test_an_erroring_panel_fails_with_grafanas_message():
    g = FakeGrafana({"w": _dash("w", [_panel("Top services", "pivot cdn")])},
                    {"pivot cdn": "pivot: specified row key column does not exist: cdn"})
    res = _run(g)
    assert res.status is Status.FAIL
    assert "'Top services' (A): query fails: pivot: specified row key column" \
        in res.findings[0].message
    assert res.findings[0].where == "W"


def test_an_empty_variable_fails():
    var = {"type": "query", "name": "cpe", "datasource": {"uid": "influxdb"},
           "query": "tagValues cpe"}
    g = FakeGrafana({"c": _dash("c", [_panel("RTT", "r.cpe == \"${cpe}\"")], [var])},
                    {"tagValues cpe": []})
    res = _run(g)
    assert res.status is Status.FAIL
    assert "variable $cpe offers no value in now-6h to now" in res.findings[0].message


def test_a_variable_whose_query_errors_fails():
    var = {"type": "query", "name": "t", "datasource": {"uid": "influxdb"},
           "query": "broken var"}
    g = FakeGrafana({"c": _dash("c", [], [var])}, {"broken var": "compile error"})
    res = _run(g)
    assert res.status is Status.FAIL
    assert "variable $t: query fails: compile error" in res.findings[0].message


def test_an_empty_panel_warns_and_is_listed():
    g = FakeGrafana({"a": _dash("a", [_panel("IPv6", "v6 only"), _panel("v4", "v4")])},
                    {"v6 only": []})
    res = _run(g)
    assert res.status is Status.WARN
    assert "1 empty panel(s)" in res.summary
    assert "'IPv6'" in res.findings[0].message
    assert "'v4'" not in res.findings[0].message


def test_collapsed_row_and_no_value_panels_are_not_warned():
    row = {"type": "row", "title": "Driver extras", "collapsed": True,
           "panels": [_panel("Noise", "noise")]}
    quiet = _panel("Changes", "changes",
                   fieldConfig={"defaults": {"noValue": "No change in range"}})
    g = FakeGrafana({"a": _dash("a", [row, quiet])}, {"noise": [], "changes": []})
    res = _run(g)
    assert res.status is Status.OK, res.findings
    # Still run: an error inside a collapsed row is still an error.
    assert "noise" in g.queries


def test_an_error_inside_a_collapsed_row_still_fails():
    row = {"type": "row", "collapsed": True, "panels": [_panel("Noise", "noise")]}
    g = FakeGrafana({"a": _dash("a", [row])}, {"noise": "runtime error"})
    assert _run(g).status is Status.FAIL


def test_user_dashboards_are_skipped_and_counted():
    g = FakeGrafana({"mine": _dash("mine", [_panel("X", "user query")]),
                     "a": _dash("a", [_panel("Y", "ours")])},
                    {"user query": "would fail"}, user_uids={"mine"})
    res = _run(g)
    assert res.status is Status.OK, res.findings
    assert "1 user dashboard(s) skipped" in res.summary
    assert "user query" not in g.queries


def test_a_timeout_is_reported_not_raised():
    g = FakeGrafana({"a": _dash("a", [_panel("Slow", "slow"), _panel("Fast", "fast")])},
                    {"slow": "timeout"})
    res = _run(g)
    assert res.status is Status.WARN
    assert "'Slow' (A): no answer in 10 s" in res.findings[0].message
    assert "fast" in g.queries  # the next query still ran


def test_the_budget_stops_the_run_with_a_warning():
    ticks = iter(range(0, 1000, 50))
    g = FakeGrafana({"a": _dash("a", [_panel(f"P{i}", f"q{i}") for i in range(10)])})
    res = _run(g, clock=lambda: next(ticks), budget=120)
    assert res.status is Status.WARN
    assert any("stopped at the 120 s budget" in f.message for f in res.findings)
    assert len(g.queries) < 10


def test_a_dead_dashboard_link_fails():
    panel = _panel("Go", "q", links=[{"url": "/d/gone-v1/x?${__url_time_range}"}])
    g = FakeGrafana({"a": _dash("a", [panel]),
                     "b": _dash("b", links=[{"url": "/d/a/y"}])})
    res = _run(g)
    assert res.status is Status.FAIL
    assert [f.message for f in res.findings] == [
        "links to /d/gone-v1, which this Grafana does not have"]


class Repo:
    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.provisioning = self.root / "shared/modules/grafana/provisioning"


def _repo_with(tmp_path, tree, uids):
    repo = Repo(tmp_path)
    for uid in uids:
        path = repo.provisioning / tree / "Folder" / f"{uid}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"uid": uid}))
    return repo


def test_a_repo_dashboard_grafana_does_not_provision_fails(tmp_path):
    repo = _repo_with(tmp_path, "dashboards", ["a", "new-v1"])
    _repo_with(tmp_path, "dashboards-clickhouse", ["ch-only"])
    g = FakeGrafana({"a": _dash("a")})
    res = _run(g, repo=repo)
    assert res.status is Status.FAIL
    assert [f.message.split(" is not")[0] for f in res.findings] == [
        "dashboards/Folder/new-v1.json (uid new-v1)"]


def test_clickhouse_mode_compares_the_clickhouse_tree(tmp_path):
    repo = _repo_with(tmp_path, "dashboards", ["influx-only"])
    _repo_with(tmp_path, "dashboards-clickhouse", ["a"])
    env = tmp_path / "env"
    env.write_text("TSDB_TYPE=clickhouse\n")
    res = _run(FakeGrafana({"a": _dash("a")}), repo=repo, env_file=env)
    assert res.status is Status.OK, res.findings


def test_variables_substitute_in_every_syntax():
    var = {"type": "query", "name": "target", "datasource": {"uid": "influxdb"},
           "query": "list targets", "current": {"value": "Netflix"}}
    query = ('a="${target}" b="[[target]]" c="$target" d=${target:sqlstring} '
             'e=${target:queryparam} tz=${__timezone} f=$__from u=${unknown}')
    g = FakeGrafana({"a": _dash("a", [_panel("P", query)], [var])},
                    {"list targets": ["Google", "O'Neil"]})
    _run(g, now=lambda: 1_000_000)
    sent = g.queries[-1]
    # The saved value is gone, so the first offered one is used, as on open.
    assert ('a="Google" b="Google" c="Google" d=\'Google\' e=var-target=Google '
            'tz=America/Chicago f=978400000 u=${unknown}') == sent


def test_the_saved_value_is_kept_when_still_offered():
    var = {"type": "query", "name": "cpe", "datasource": {"uid": "influxdb"},
           "query": "list cpe", "current": {"value": "10.0.0.1"}}
    g = FakeGrafana({"a": _dash("a", [_panel("P", "x=${cpe:sqlstring}")], [var])},
                    {"list cpe": ["10.0.0.9", "O'Neil", "10.0.0.1"]})
    _run(g)
    assert g.queries[-1] == "x='10.0.0.1'"


def test_chained_variables_see_the_earlier_value():
    first = {"type": "query", "name": "category", "datasource": {"uid": "influxdb"},
             "query": "list categories"}
    second = {"type": "query", "name": "target", "datasource": {"uid": "influxdb"},
              "query": "targets in ${category}"}
    g = FakeGrafana({"a": _dash("a", [], [first, second])},
                    {"list categories": ["topsites"], "targets in": ["Google"]})
    _run(g)
    assert "targets in topsites" in g.queries


def test_annotations_run_and_their_errors_fail():
    anno = {"name": "Uplink changed", "enable": True, "datasource": {"uid": "influxdb"},
            "target": {"refId": "Anno", "query": "host_uplink"}}
    builtin = {"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
               "name": "Annotations & Alerts"}
    g = FakeGrafana({"a": _dash("a", annotations=[builtin, anno])},
                    {"host_uplink": "error in pivot"})
    res = _run(g)
    assert res.status is Status.FAIL
    assert "annotation 'Uplink changed': query fails" in res.findings[0].message


def test_nothing_but_reads_and_ds_queries():
    g = FakeGrafana({"a": _dash("a", [_panel("P", "q")])})
    _run(g)
    assert {m for m, p, _ in g.calls if p != "/api/ds/query"} == {"GET"}
    assert {m for m, p, _ in g.calls if p == "/api/ds/query"} == {"POST"}


class FakeDocker:
    def __init__(self, present=True, grafana="pro-grafana-1"):
        self.present, self.grafana = present, grafana

    def available(self):
        return self.present

    def container_for_service(self, service):
        return self.grafana if service == "grafana" else None


@pytest.mark.parametrize("docker, env, why", [
    (FakeDocker(present=False), "GF_SECURITY_ADMIN_PASSWORD=x\n", "docker is not"),
    (FakeDocker(grafana=None), "GF_SECURITY_ADMIN_PASSWORD=x\n", "no Grafana here"),
    (FakeDocker(), "GF_SECURITY_ADMIN_PASSWORD=\n", "no GF_SECURITY_ADMIN_PASSWORD"),
    (FakeDocker(), None, "cannot read"),
])
def test_skips_cleanly(tmp_path, docker, env, why):
    path = tmp_path / "env"
    if env is not None:
        path.write_text(env)
    res = dq.check_dashboard_queries_run(docker=docker, env_file=path)
    assert res.status is Status.SKIP
    assert why in res.summary


def test_grafana_not_answering_skips():
    class Down:
        def request(self, *a, **k):
            raise dq.Unreachable()

    res = _run(Down())
    assert res.status is Status.SKIP
    assert "not answering" in res.summary


def test_the_env_file_is_read_like_env_get(tmp_path):
    path = tmp_path / "env"
    path.write_text("# c\nGF_SECURITY_ADMIN_PASSWORD='a=b'\nGF_SECURITY_ADMIN_USER=\"ops\"\n")
    env = dq.read_env_file(path)
    assert env["GF_SECURITY_ADMIN_PASSWORD"] == "a=b"
    assert env["GF_SECURITY_ADMIN_USER"] == "ops"


def test_relative_ranges():
    now = 10_000_000_000
    assert dq._epoch_ms("now", now) == now
    assert dq._epoch_ms("now-24h", now) == now - 86_400_000
    assert dq._epoch_ms("now-7d", now) == now - 7 * 86_400_000


def test_the_interval_is_whole_seconds():
    # v.windowPeriod: 86.4 s reached Flux as `1m26.4s` and failed to compile.
    g = FakeGrafana({"a": dict(_dash("a", [_panel("P", "q")]),
                               time={"from": "now-24h", "to": "now"})})
    _run(g)
    sent = [b for m, p, b in g.calls if p == "/api/ds/query"][0]["queries"][0]
    assert sent["intervalMs"] % 1000 == 0
    assert sent["intervalMs"] == 86_000
