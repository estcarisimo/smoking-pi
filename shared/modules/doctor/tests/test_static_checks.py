"""Tests for the static instrumentation checks.

Every check here is proved by REINTRODUCING the bug it exists for and
asserting the doctor fails. A doctor that only ever passes on a healthy tree
is indistinguishable from a doctor that does nothing — which is the exact
failure mode (a green signal produced without doing the work) that this tool
was written to catch elsewhere in the stack.
"""

from __future__ import annotations

import json
import pathlib
import textwrap

import pytest
import yaml

from doctor import static_checks
from doctor.report import Report, Status

# ---------------------------------------------------------------------------
# A minimal but faithful synthetic repository
# ---------------------------------------------------------------------------

EXPORTER_SOURCE = '''
from influxdb_client import Point

DNS_DIRS = ("resolvers", "DNS_Resolvers")


def measurement_for(rrd_file, rrd_dir):
    return "dns_latency" if rrd_file in DNS_DIRS else "latency"


def build(rrd_file, rrd_dir, rows):
    measurement = measurement_for(rrd_file, rrd_dir)
    return (Point(measurement)
            .tag("target", "x")
            .tag("category", "y")
            .tag("probe_type", "z"))
'''

CPE_EXPORTER_SOURCE = '''
from influxdb_client import Point


def build(ip):
    return (Point("cpe_latency")
            .tag("target", ip)
            .tag("protocol", "ipv4")
            .tag("category", "cpe"))
'''

GRAFANA_DOCKERFILE = """
FROM grafana/grafana:12.4.3
RUN grafana cli --pluginsDir /var/lib/grafana/plugins \\
        plugins install grafana-clickhouse-datasource
"""

# A faithful miniature of the alerter's env handling: a DEFAULT_ constant read
# through a helper, the `get(...) or DEFAULT` idiom, and one knob that is only
# documented in the docs table.
ALERTER_SOURCE = '''
import os

DEFAULT_DOWN_WINDOW = 1200
DEFAULT_STALE_WINDOW = 1200
DEFAULT_OPENCLAW_CHANNEL = "telegram"
DEFAULT_STATE_FILE = "/var/lib/alerter/state.json"


def _env_int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def windows():
    return (
        _env_int("DOWN_WINDOW", DEFAULT_DOWN_WINDOW),
        _env_int("STALE_WINDOW", DEFAULT_STALE_WINDOW),
    )


def channel():
    return os.environ.get("OPENCLAW_CHANNEL") or DEFAULT_OPENCLAW_CHANNEL


def state_file():
    return os.environ.get("ALERT_STATE_FILE", DEFAULT_STATE_FILE)
'''

ALERTER_COMPOSE = {
    "services": {
        "alerter": {
            "environment": [
                "DOWN_WINDOW=${DOWN_WINDOW:-1200}",
                "STALE_WINDOW=${STALE_WINDOW:-1200}",
                "OPENCLAW_CHANNEL=${OPENCLAW_CHANNEL:-telegram}",
                "INFLUX_TOKEN=${INFLUX_TOKEN}",
            ]
        }
    }
}

ALERTER_ENV_TEMPLATE = "DOWN_WINDOW=\nSTALE_WINDOW=\n"

ALERTING_DOC = """
## Environment reference

| Variable | Default | Meaning |
|---|---|---|
| `DOWN_WINDOW` | `1200` | target_down window |
| `STALE_WINDOW` | `1200` | exporter_stale window |
| `OPENCLAW_CHANNEL` | `telegram` | delivery channel |
| `ALERT_STATE_FILE` | `/var/lib/alerter/state.json` | incident state |
"""


def _dashboard(uid, title, query, datasource_uid="influxdb"):
    return {
        "uid": uid,
        "title": title,
        "panels": [
            {
                "title": "Latency",
                "datasource": {"uid": datasource_uid},
                "targets": [{"refId": "A", "queryType": "flux", "query": query}],
            }
        ],
        "templating": {"list": []},
    }


GOOD_QUERY = (
    'from(bucket:"smokeping") |> range(start:v.timeRangeStart) '
    '|> filter(fn:(r)=> r._measurement == "latency" and r.category == "topsites")'
)


@pytest.fixture()
def repo(tmp_path):
    """A synthetic repo that passes every check, ready to be broken."""
    provisioning = tmp_path / "shared/modules/grafana/provisioning"
    (provisioning / "dashboards/overview").mkdir(parents=True)
    (provisioning / "dashboards-clickhouse").mkdir(parents=True)
    (provisioning / "datasources").mkdir(parents=True)
    exporters = tmp_path / "shared/modules/smokeping-exporters"
    exporters.mkdir(parents=True)

    (exporters / "rrd2influx.py").write_text(EXPORTER_SOURCE)
    (exporters / "microcut_detector.py").write_text(CPE_EXPORTER_SOURCE)
    (tmp_path / "shared/modules/grafana/Dockerfile").write_text(GRAFANA_DOCKERFILE)

    alerter = tmp_path / "shared/modules/alerter"
    alerter.mkdir(parents=True)
    (alerter / "evaluator.py").write_text(ALERTER_SOURCE)
    pro = tmp_path / "editions/pro"
    pro.mkdir(parents=True)
    (pro / "docker-compose.yml").write_text(yaml.safe_dump(ALERTER_COMPOSE))
    (pro / ".env.template").write_text(ALERTER_ENV_TEMPLATE)
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "docs/alerting.md").write_text(ALERTING_DOC)

    (provisioning / "dashboards/overview/latency.json").write_text(
        json.dumps(_dashboard("lat-v1", "Latency", GOOD_QUERY))
    )
    (provisioning / "dashboards/dashboard.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": 1,
                "providers": [
                    {
                        "name": "SmokePing",
                        "type": "file",
                        "options": {
                            "path": "/etc/grafana/provisioning/dashboards",
                            "foldersFromFilesStructure": True,
                        },
                    }
                ],
            }
        )
    )
    (provisioning / "dashboards-clickhouse/dashboard.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": 1,
                "providers": [
                    {
                        "name": "SmokePing ClickHouse",
                        "type": "file",
                        "options": {
                            "path": "/etc/grafana/provisioning/dashboards-clickhouse"
                        },
                    }
                ],
            }
        )
    )
    (provisioning / "datasources/influxdb.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": 1,
                "datasources": [
                    {
                        "name": "InfluxDB",
                        "uid": "influxdb",
                        "type": "influxdb",
                        "isDefault": True,
                    }
                ],
            }
        )
    )
    (provisioning / "datasources/clickhouse.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": 1,
                "datasources": [
                    {
                        "name": "ClickHouse",
                        "uid": "clickhouse",
                        "type": "grafana-clickhouse-datasource",
                        "isDefault": False,
                    }
                ],
            }
        )
    )
    return static_checks.Repo(tmp_path)


def run(repo) -> dict:
    return {r.name: r for r in static_checks.run_all(repo)}


def _write_dashboard(repo, name, data):
    path = repo.provisioning / "dashboards/overview" / name
    path.write_text(json.dumps(data))
    return path


# ---------------------------------------------------------------------------
# The healthy baseline
# ---------------------------------------------------------------------------


def test_healthy_repo_passes_everything(repo):
    results = run(repo)
    failing = {n: r.summary for n, r in results.items() if r.status is not Status.OK}
    assert not failing, failing


def test_exporter_vocabulary_resolves_indirect_measurements(repo):
    """Point(measurement) via a helper must still yield latency/dns_latency."""
    from doctor.sources import exporter_vocabulary

    vocab = exporter_vocabulary(repo.exporters)
    assert vocab.measurements == {"latency", "dns_latency", "cpe_latency"}
    assert vocab.tag_names == {"target", "category", "probe_type", "protocol"}


# ---------------------------------------------------------------------------
# Each check, proved by reintroducing its bug
# ---------------------------------------------------------------------------


def test_catches_two_datasources_claiming_default(repo):
    """The v2.5.0 regression: Grafana refuses to start with two defaults."""
    path = repo.datasources_dir / "clickhouse.yaml"
    data = yaml.safe_load(path.read_text())
    data["datasources"][0]["isDefault"] = True
    path.write_text(yaml.safe_dump(data))

    check = run(repo)["one-default-datasource"]
    assert check.status is Status.FAIL
    rendered = " ".join(f.render() for f in check.findings)
    assert "2 datasources claim isDefault" in rendered
    assert "refuses to start" in rendered


def test_catches_no_default_datasource(repo):
    path = repo.datasources_dir / "influxdb.yaml"
    data = yaml.safe_load(path.read_text())
    data["datasources"][0]["isDefault"] = False
    path.write_text(yaml.safe_dump(data))

    assert run(repo)["one-default-datasource"].status is Status.FAIL


def test_catches_a_dangling_datasource_uid(repo):
    """The eight dashboards that pointed at a `clickhouse-alt` that never existed."""
    _write_dashboard(
        repo,
        "dangling.json",
        _dashboard("dang-v1", "Dangling", GOOD_QUERY, datasource_uid="clickhouse-alt"),
    )
    check = run(repo)["datasource-uids-resolve"]
    assert check.status is Status.FAIL
    assert "clickhouse-alt" in check.findings[0].render()


def test_builtin_and_variable_datasources_are_not_dangling(repo):
    _write_dashboard(
        repo,
        "builtin.json",
        _dashboard("builtin-v1", "Builtin", GOOD_QUERY, datasource_uid="-- Mixed --"),
    )
    _write_dashboard(
        repo,
        "variable.json",
        _dashboard("var-v1", "Var", GOOD_QUERY, datasource_uid="${DS_INFLUX}"),
    )
    assert run(repo)["datasource-uids-resolve"].status is Status.OK


def test_catches_a_missing_plugin(repo):
    """A datasource provisions fine and then cannot answer a single query."""
    (repo.grafana / "Dockerfile").write_text("FROM grafana/grafana:12.4.3\n")
    check = run(repo)["datasource-plugins-installed"]
    assert check.status is Status.FAIL
    assert "grafana-clickhouse-datasource" in check.findings[0].render()


def test_catches_duplicate_uids_within_a_tree(repo):
    _write_dashboard(repo, "clone.json", _dashboard("lat-v1", "Clone", GOOD_QUERY))
    check = run(repo)["dashboard-uids-unique"]
    assert check.status is Status.FAIL
    assert "duplicate uid 'lat-v1'" in check.findings[0].render()


def test_the_same_uid_across_the_two_trees_is_fine(repo):
    """influxdb and clickhouse trees are never provisioned together."""
    (repo.provisioning / "dashboards-clickhouse/latency.json").write_text(
        json.dumps(_dashboard("lat-v1", "Latency (ClickHouse)", GOOD_QUERY))
    )
    assert run(repo)["dashboard-uids-unique"].status is Status.OK


def test_catches_a_dashboard_with_no_uid(repo):
    data = _dashboard("x", "No UID", GOOD_QUERY)
    del data["uid"]
    _write_dashboard(repo, "nouid.json", data)
    check = run(repo)["dashboard-uids-unique"]
    assert check.status is Status.FAIL
    assert "no uid" in check.findings[0].render()


def test_catches_dashboards_nobody_scans(repo):
    """The entire ClickHouse dashboard set, on disk and never loaded."""
    (repo.provisioning / "dashboards-clickhouse/ch.json").write_text(
        json.dumps(_dashboard("ch-v1", "ClickHouse", GOOD_QUERY))
    )
    provider = repo.provisioning / "dashboards-clickhouse/dashboard.yaml"
    data = yaml.safe_load(provider.read_text())
    data["providers"][0]["options"]["path"] = "/etc/grafana/provisioning/somewhere-else"
    provider.write_text(yaml.safe_dump(data))

    check = run(repo)["dashboards-are-scanned"]
    assert check.status is Status.FAIL
    assert "never loaded" in check.findings[0].render()


def test_catches_a_measurement_nothing_writes(repo):
    """A panel charting `cpe_microcuts` when the exporter writes `cpe_latency`."""
    query = 'filter(fn:(r)=> r._measurement == "cpe_microcuts")'
    _write_dashboard(repo, "typo.json", _dashboard("typo-v1", "Typo", query))
    check = run(repo)["panel-measurements-written"]
    assert check.status is Status.FAIL
    assert "cpe_microcuts" in check.findings[0].render()


def test_catches_a_tag_nothing_writes(repo):
    """`r.measurement_type` on DNS panels — a filter that can never match."""
    query = (
        'filter(fn:(r)=> r._measurement == "dns_latency" '
        'and r.measurement_type == "latency")'
    )
    _write_dashboard(repo, "vocab.json", _dashboard("vocab-v1", "Vocab", query))
    check = run(repo)["panel-tags-written"]
    assert check.status is Status.FAIL
    assert "measurement_type" in check.findings[0].render()


def test_template_variable_queries_are_checked_too(repo):
    """The DNS_Resolvers mistake lived in a variable query, not a panel."""
    data = _dashboard("tv-v1", "Template", GOOD_QUERY)
    data["templating"]["list"] = [
        {
            "name": "target",
            "type": "query",
            "query": 'filter(fn:(r)=> r._measurement == "not_a_measurement")',
        }
    ]
    _write_dashboard(repo, "tmpl.json", data)
    check = run(repo)["panel-measurements-written"]
    assert check.status is Status.FAIL
    assert "variable $target" in check.findings[0].where


def test_panels_nested_in_collapsed_rows_are_checked(repo):
    data = _dashboard("row-v1", "Rows", GOOD_QUERY)
    data["panels"].append(
        {
            "title": "Collapsed row",
            "type": "row",
            "collapsed": True,
            "panels": [
                {
                    "title": "Hidden",
                    "datasource": {"uid": "influxdb"},
                    "targets": [
                        {"query": 'filter(fn:(r)=> r._measurement == "ghost")'}
                    ],
                }
            ],
        }
    )
    _write_dashboard(repo, "rows.json", data)
    check = run(repo)["panel-measurements-written"]
    assert check.status is Status.FAIL
    assert "ghost" in check.findings[0].render()


def test_catches_invalid_dashboard_json(repo):
    (repo.provisioning / "dashboards/overview/broken.json").write_text("{not json")
    check = run(repo)["dashboards-parse"]
    assert check.status is Status.FAIL


def test_flux_builtin_columns_are_not_treated_as_tags(repo):
    query = (
        'filter(fn:(r)=> r._measurement == "latency" and r._field == "median") '
        "|> sort(columns:[\"_time\"]) |> map(fn:(r)=> ({r with _value: r._value}))"
    )
    _write_dashboard(repo, "builtins.json", _dashboard("bi-v1", "Builtins", query))
    assert run(repo)["panel-tags-written"].status is Status.OK


# ---------------------------------------------------------------------------
# Reporting and CLI
# ---------------------------------------------------------------------------


def test_report_exit_code_and_rendering(repo):
    path = repo.datasources_dir / "clickhouse.yaml"
    data = yaml.safe_load(path.read_text())
    data["datasources"][0]["isDefault"] = True
    path.write_text(yaml.safe_dump(data))

    report = Report(static_checks.run_all(repo))
    assert report.failed is True
    assert report.exit_code == 1
    text = report.render_text()
    assert "[FAIL] one-default-datasource" in text
    payload = json.loads(report.render_json())
    assert payload["ok"] is False
    failed = [c for c in payload["checks"] if c["status"] == "fail"]
    assert failed and failed[0]["findings"]


def test_healthy_report_exits_zero(repo):
    report = Report(static_checks.run_all(repo))
    assert report.exit_code == 0
    assert "0 fail" in report.render_text()


def test_cli_runs_against_a_repo_root(repo, capsys):
    from doctor.__main__ import main

    code = main(["--repo-root", str(repo.root), "--json"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_missing_repo_root_is_skipped_not_crashed(tmp_path, capsys):
    from doctor.__main__ import main

    assert main(["--repo-root", str(tmp_path)]) == 0
    assert "wrong --repo-root" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Alerter env: module defaults vs deployed defaults
# ---------------------------------------------------------------------------


def _set_compose_env(repo, entries):
    path = repo.pro / "docker-compose.yml"
    data = yaml.safe_load(path.read_text())
    data["services"]["alerter"]["environment"] = entries
    path.write_text(yaml.safe_dump(data))


def test_alerter_env_defaults_match_on_a_healthy_repo(repo):
    assert run(repo)["alerter-env-defaults-match"].status is Status.OK


def test_compose_default_overriding_a_module_default_fails(repo):
    """THE bug: a flap fix raised DEFAULT_DOWN_WINDOW to 1200, but compose
    still pinned 900, so the deployed container never saw the fix."""
    _set_compose_env(
        repo,
        [
            "DOWN_WINDOW=${DOWN_WINDOW:-900}",
            "STALE_WINDOW=${STALE_WINDOW:-1200}",
            "OPENCLAW_CHANNEL=${OPENCLAW_CHANNEL:-telegram}",
        ],
    )
    check = run(repo)["alerter-env-defaults-match"]
    assert check.status is Status.FAIL
    rendered = " ".join(f.render() for f in check.findings)
    assert "DOWN_WINDOW" in rendered
    assert "900" in rendered and "1200" in rendered


def test_a_mismatched_string_default_also_fails(repo):
    _set_compose_env(repo, ["OPENCLAW_CHANNEL=${OPENCLAW_CHANNEL:-slack}"])
    check = run(repo)["alerter-env-defaults-match"]
    assert check.status is Status.FAIL
    assert "OPENCLAW_CHANNEL" in " ".join(f.render() for f in check.findings)


def test_numeric_defaults_compare_by_value_not_text(repo):
    """20.0 in Python and "20" in compose are the same setting."""
    (repo.alerter / "evaluator.py").write_text(
        ALERTER_SOURCE + "\nDEFAULT_HIGH_LOSS_PCT = 20.0\n"
        "def loss():\n    return _env_int('HIGH_LOSS_PCT', DEFAULT_HIGH_LOSS_PCT)\n"
    )
    _set_compose_env(repo, ["HIGH_LOSS_PCT=${HIGH_LOSS_PCT:-20}"])
    assert run(repo)["alerter-env-defaults-match"].status is Status.OK


def test_env_without_a_compose_default_is_not_compared(repo):
    """`${VAR}` supplies no default, so there is nothing to disagree with."""
    _set_compose_env(repo, ["DOWN_WINDOW=${DOWN_WINDOW}"])
    assert run(repo)["alerter-env-defaults-match"].status is Status.OK


def test_a_literal_default_does_not_shadow_the_real_constant(repo):
    """An inlined literal must not knock a variable out of the comparison.

    The alerter logs its InfluxDB URL at startup with its own fallback. That
    literal usage is scanned alongside the constant-backed one, and when the
    literal won, INFLUX_URL quietly stopped being compared against Compose --
    the check silently doing less work while still reporting ok.
    """
    (repo.alerter / "logging_extra.py").write_text(
        "import os\n\n"
        "def banner():\n"
        "    return os.environ.get('DOWN_WINDOW', '900')\n"
    )
    check = run(repo)["alerter-env-defaults-match"]
    assert check.status is Status.OK, [f.render() for f in check.findings]
    assert "3 compose defaults" in check.summary


def test_missing_alerter_source_skips_rather_than_passing(repo):
    """An empty module must not read as 16 matching defaults."""
    (repo.alerter / "evaluator.py").unlink()
    assert run(repo)["alerter-env-defaults-match"].status is Status.SKIP


# ---------------------------------------------------------------------------
# Alerter env: discoverability
# ---------------------------------------------------------------------------


def test_alerter_env_declared_on_a_healthy_repo(repo):
    """ALERT_STATE_FILE is absent from compose and .env.template but present
    in the docs table, which is enough to find it."""
    assert run(repo)["alerter-env-declared"].status is Status.OK


def test_an_undocumented_env_var_warns(repo):
    (repo.alerter / "evaluator.py").write_text(
        ALERTER_SOURCE
        + "\ndef secret():\n    return os.environ.get('ALERT_SECRET_KNOB', 'x')\n"
    )
    check = run(repo)["alerter-env-declared"]
    assert check.status is Status.WARN
    assert "ALERT_SECRET_KNOB" in " ".join(f.render() for f in check.findings)


def test_documenting_it_anywhere_clears_the_warning(repo):
    (repo.alerter / "evaluator.py").write_text(
        ALERTER_SOURCE
        + "\ndef secret():\n    return os.environ.get('ALERT_SECRET_KNOB', 'x')\n"
    )
    (repo.pro / ".env.template").write_text(
        ALERTER_ENV_TEMPLATE + "ALERT_SECRET_KNOB=\n"
    )
    assert run(repo)["alerter-env-declared"].status is Status.OK


def test_a_warning_does_not_fail_the_run(repo):
    """Discoverability is a documentation gap, not a broken deployment."""
    (repo.alerter / "evaluator.py").write_text(
        ALERTER_SOURCE
        + "\ndef secret():\n    return os.environ.get('ALERT_SECRET_KNOB', 'x')\n"
    )
    assert Report(static_checks.run_all(repo)).exit_code == 0


# ---------------------------------------------------------------------------
# The real repository
# ---------------------------------------------------------------------------


def _real_repo_root() -> pathlib.Path | None:
    here = pathlib.Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "shared/modules/grafana/provisioning").is_dir():
            return candidate
    return None


def test_this_repository_is_clean():
    """The checks must hold against the actual deployment, not just fixtures."""
    root = _real_repo_root()
    if root is None:  # pragma: no cover - running outside a checkout
        pytest.skip("not inside a smoking-pi checkout")
    report = Report(static_checks.run_all(static_checks.Repo(root)))
    assert report.exit_code == 0, textwrap.indent(report.render_text(), "  ")
