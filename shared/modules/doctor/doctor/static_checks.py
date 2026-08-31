"""Static instrumentation checks — repository only, no running stack.

Each check compares what one stage of the pipeline produces against what the
next stage expects, and each one exists because the corresponding failure
actually happened on this deployment:

    exporter source -> dashboard query -> datasource -> provisioning -> Grafana

These run in CI. The checks that need live data (is this target actually
producing points? does this query return rows?) live in the live checks and
run on the Pi.
"""

from __future__ import annotations

import pathlib

from . import sources
from .report import CheckResult, Finding, Status, result, skipped

# The two dashboard trees, and the datasource each is provisioned alongside.
INFLUX_TREE = "dashboards"
CLICKHOUSE_TREE = "dashboards-clickhouse"


class Repo:
    """Paths into a checked-out repository."""

    def __init__(self, root: pathlib.Path):
        self.root = root
        self.grafana = root / "shared/modules/grafana"
        self.provisioning = self.grafana / "provisioning"
        self.datasources_dir = self.provisioning / "datasources"
        self.exporters = root / "shared/modules/smokeping-exporters"
        self.alerter = root / "shared/modules/alerter"
        self.pro = root / "editions/pro"
        self.compose = self.pro / "docker-compose.yml"
        self.env_template = self.pro / ".env.template"
        self.alerting_doc = root / "docs/alerting.md"

    def exists(self) -> bool:
        return self.provisioning.is_dir()


def run_all(repo: Repo) -> list[CheckResult]:
    if not repo.exists():
        return [
            skipped(
                "repository",
                f"no provisioning tree under {repo.provisioning} — wrong --repo-root?",
            )
        ]

    influx, influx_broken = sources.load_dashboards(repo.provisioning, INFLUX_TREE)
    clickhouse, ch_broken = sources.load_dashboards(
        repo.provisioning, CLICKHOUSE_TREE
    )
    dashboards = influx + clickhouse
    broken = influx_broken + ch_broken
    datasources, ds_broken = sources.load_datasources(repo.datasources_dir)

    return [
        check_dashboards_parse(dashboards, broken),
        check_provisioning_yaml_parses(repo, ds_broken),
        check_dashboard_uids_unique(influx, clickhouse),
        check_one_default_datasource(repo, datasources),
        check_datasource_uids_resolve(influx, datasources),
        check_datasource_plugins_installed(repo, datasources),
        check_dashboards_are_scanned(repo, influx, clickhouse),
        check_panel_measurements_are_written(repo, influx),
        check_panel_tags_are_written(repo, influx),
        check_alerter_env_defaults_match(repo),
        check_alerter_env_declared(repo),
    ]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def check_dashboards_parse(dashboards, broken) -> CheckResult:
    findings = [
        Finding(f"invalid dashboard JSON: {error}", where=str(path))
        for path, error in broken
    ]
    return result(
        "dashboards-parse",
        findings,
        f"{len(dashboards)} dashboards parse",
    )


def check_provisioning_yaml_parses(repo: Repo, ds_broken) -> CheckResult:
    findings = [
        Finding(f"invalid provisioning YAML: {error}", where=str(path))
        for path, error in ds_broken
    ]
    count = 0
    for path in sorted(repo.provisioning.rglob("*.yaml")):
        count += 1
        if any(path == broken_path for broken_path, _ in ds_broken):
            continue
        providers, provider_broken = sources.load_providers(path)
        findings.extend(
            Finding(f"invalid provisioning YAML: {error}", where=str(bad))
            for bad, error in provider_broken
        )
    return result(
        "provisioning-yaml-parses", findings, f"{count} provisioning files parse"
    )


# ---------------------------------------------------------------------------
# Dashboard identity
# ---------------------------------------------------------------------------


def check_dashboard_uids_unique(influx, clickhouse) -> CheckResult:
    """Duplicate UIDs inside one tree make Grafana drop a dashboard on load.

    The two trees are never provisioned together — influxdb mode scans only
    `dashboards/`, clickhouse mode builds a tree from `dashboards-clickhouse/`
    — so a UID shared ACROSS trees is fine and deliberate.
    """
    findings = []
    total = 0
    for tree_name, tree in (("influxdb", influx), ("clickhouse", clickhouse)):
        seen: dict[str, str] = {}
        for dashboard in tree:
            total += 1
            uid = dashboard.uid
            if not uid:
                findings.append(
                    Finding(
                        "dashboard has no uid, so provisioning assigns a random one "
                        "and every deep link to it breaks on reprovision",
                        where=str(dashboard.path),
                    )
                )
                continue
            if uid in seen:
                findings.append(
                    Finding(
                        f"duplicate uid {uid!r} within the {tree_name} tree — "
                        f"also used by {seen[uid]}",
                        where=str(dashboard.path),
                    )
                )
            seen.setdefault(uid, str(dashboard.path))
    return result("dashboard-uids-unique", findings, f"{total} uids unique per tree")


# ---------------------------------------------------------------------------
# Datasources
# ---------------------------------------------------------------------------


def check_one_default_datasource(repo: Repo, datasources) -> CheckResult:
    """More than one isDefault in a provisioned directory and Grafana will not start.

    This is the v2.5.0 regression: `editions/pro` bind-mounts the whole
    datasources directory read-only, so every file in it is provisioned
    together, and the ClickHouse datasource was set isDefault alongside the
    InfluxDB one. Grafana refused to boot and a fresh install came up with no
    Grafana at all — with nothing in CI to catch it.
    """
    defaults = [d for d in datasources if d.is_default]
    findings = []
    if len(defaults) > 1:
        names = ", ".join(
            f"{d.name} ({pathlib.Path(d.path).name})" for d in defaults
        )
        findings.append(
            Finding(
                f"{len(defaults)} datasources claim isDefault: {names}. The whole "
                f"directory is provisioned at once, and Grafana refuses to start "
                f"when two datasources are default.",
                where=str(repo.datasources_dir),
            )
        )
    elif not defaults and datasources:
        findings.append(
            Finding(
                "no datasource is marked isDefault; panels that omit an explicit "
                "datasource will not resolve one",
                where=str(repo.datasources_dir),
            )
        )
    return result(
        "one-default-datasource",
        findings,
        f"exactly one of {len(datasources)} datasources is default",
    )


def check_datasource_uids_resolve(influx, datasources) -> CheckResult:
    """Every uid a dashboard references must exist in the provisioned set."""
    known = {d.uid for d in datasources if d.uid} | sources.BUILTIN_DATASOURCE_UIDS
    dashboard_uids = {d.uid for d in influx if d.uid}
    findings = []
    checked = 0
    for dashboard in influx:
        for where, uid in sources.iter_datasource_refs(dashboard):
            checked += 1
            if uid.startswith("${") or uid.startswith("$"):
                continue  # a dashboard variable, resolved at render time
            if uid in dashboard_uids:
                continue  # self-reference in a panel link, not a datasource
            if uid not in known:
                findings.append(
                    Finding(
                        f"references datasource uid {uid!r}, which no provisioned "
                        f"datasource declares (have: "
                        f"{', '.join(sorted(u for u in known if not u.startswith('-')))})",
                        where=f"{dashboard.rel} / {where}",
                    )
                )
    return result(
        "datasource-uids-resolve", findings, f"{checked} references resolve"
    )


def check_datasource_plugins_installed(repo: Repo, datasources) -> CheckResult:
    """A datasource whose plugin is not installed provisions but never queries."""
    installed = sources.installed_plugins(repo.grafana / "Dockerfile")
    findings = []
    for datasource in datasources:
        kind = datasource.type
        if not kind:
            findings.append(
                Finding(
                    f"datasource {datasource.name!r} declares no type",
                    where=str(datasource.path),
                )
            )
            continue
        if kind in sources.CORE_DATASOURCE_TYPES or kind in installed:
            continue
        findings.append(
            Finding(
                f"datasource {datasource.name!r} needs plugin {kind!r}, which the "
                f"Grafana image does not install "
                f"(installs: {', '.join(sorted(installed)) or 'none'})",
                where=str(datasource.path),
            )
        )
    return result(
        "datasource-plugins-installed",
        findings,
        f"{len(datasources)} datasources have a usable plugin",
    )


# ---------------------------------------------------------------------------
# Provisioning coverage
# ---------------------------------------------------------------------------


def check_dashboards_are_scanned(repo: Repo, influx, clickhouse) -> CheckResult:
    """A dashboard file in a directory no provider scans is simply never loaded.

    This is how the entire ClickHouse dashboard set stayed invisible: the JSON
    was on disk and looked fine.
    """
    findings = []
    for tree, dashboards in ((INFLUX_TREE, influx), (CLICKHOUSE_TREE, clickhouse)):
        directory = repo.provisioning / tree
        provider_file = directory / "dashboard.yaml"
        providers, _ = sources.load_providers(provider_file)
        if not dashboards:
            continue
        if not providers:
            findings.append(
                Finding(
                    f"{len(dashboards)} dashboards here but {provider_file.name} "
                    f"declares no provider, so none of them are ever loaded",
                    where=str(directory),
                )
            )
            continue
        # The provider path is a container path; what matters is that it ends
        # at this tree, so the recursive walk reaches these files.
        if not any(p.scan_path.rstrip("/").endswith(tree) for p in providers):
            scanned = ", ".join(p.scan_path for p in providers) or "nothing"
            findings.append(
                Finding(
                    f"{len(dashboards)} dashboards here, but the provider scans "
                    f"{scanned} — these files are never loaded",
                    where=str(directory),
                )
            )
    return result(
        "dashboards-are-scanned",
        findings,
        f"{len(influx) + len(clickhouse)} dashboards sit under a scanned path",
    )


# ---------------------------------------------------------------------------
# Vocabulary: what panels ask for vs what exporters write
# ---------------------------------------------------------------------------


def check_panel_measurements_are_written(repo: Repo, influx) -> CheckResult:
    """A panel filtering on a measurement nothing writes charts nothing, silently."""
    vocab = sources.exporter_vocabulary(repo.exporters)
    if not vocab.measurements:
        return skipped(
            "panel-measurements-written",
            f"no Point() literals found under {repo.exporters}",
        )
    findings = []
    checked = 0
    for dashboard in influx:
        for where, query in sources.iter_queries(dashboard):
            for measurement in sources.measurements_in(query):
                checked += 1
                if measurement not in vocab.measurements:
                    findings.append(
                        Finding(
                            f"queries measurement {measurement!r}, which no exporter "
                            f"writes (written: "
                            f"{', '.join(sorted(vocab.measurements))})",
                            where=f"{dashboard.rel} / {where}",
                        )
                    )
    return result(
        "panel-measurements-written",
        findings,
        f"{checked} measurement predicates match {', '.join(vocab.sources)}",
    )


def check_panel_tags_are_written(repo: Repo, influx) -> CheckResult:
    """Same for tag names: `r.measurement_type` is a filter that can never match."""
    vocab = sources.exporter_vocabulary(repo.exporters)
    if not vocab.tag_names:
        return skipped(
            "panel-tags-written", f"no .tag() literals found under {repo.exporters}"
        )
    findings = []
    checked = 0
    for dashboard in influx:
        for where, query in sources.iter_queries(dashboard):
            for tag in sources.tag_refs_in(query):
                checked += 1
                if tag not in vocab.tag_names:
                    findings.append(
                        Finding(
                            f"filters on tag {tag!r}, which no exporter writes "
                            f"(written: {', '.join(sorted(vocab.tag_names))})",
                            where=f"{dashboard.rel} / {where}",
                        )
                    )
    return result(
        "panel-tags-written",
        findings,
        f"{checked} tag references match {', '.join(vocab.sources)}",
    )


__all__ = ["Repo", "run_all", "Status"]


# ---------------------------------------------------------------------------
# Module defaults vs deployed defaults
# ---------------------------------------------------------------------------


def _same_value(module_value: object, compose_value: str) -> bool:
    """Compare a Python constant with a Compose default string.

    Numerically where both sides parse as numbers, so ``20.0`` and ``"20"``
    agree, and textually otherwise.
    """
    try:
        return float(module_value) == float(compose_value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(module_value) == compose_value


def check_alerter_env_defaults_match(repo: Repo) -> CheckResult:
    """A Compose ``${VAR:-x}`` default must equal the module's DEFAULT_ constant.

    This exists because of a specific, expensive bug. A flapping incident was
    fixed by raising ``DEFAULT_DOWN_WINDOW`` from 900 to 1200 — but
    docker-compose.yml pinned ``DOWN_WINDOW=${DOWN_WINDOW:-900}``, so every
    deployed container kept the old value and the fix did nothing. Both files
    read as correct on their own; only the pair is wrong, and nothing compared
    them.
    """
    if not repo.compose.is_file():
        return skipped(
            "alerter-env-defaults-match", f"no compose file at {repo.compose}"
        )
    env = sources.module_env(repo.alerter)
    if not env.usages:
        return skipped(
            "alerter-env-defaults-match", f"no alerter source under {repo.alerter}"
        )

    declared = sources.compose_service_env(repo.compose, "alerter")
    findings: list[Finding] = []
    compared = 0
    for name, compose_default in sorted(declared.items()):
        if compose_default is None or name not in env.names():
            continue
        const, module_default = env.default_for(name)
        if const is None or module_default is None:
            continue
        compared += 1
        if not _same_value(module_default, compose_default):
            findings.append(
                Finding(
                    f"{name}: compose defaults to {compose_default!r} but "
                    f"{const} is {module_default!r} — the compose value wins, "
                    f"so the module default is dead code",
                    where="editions/pro/docker-compose.yml",
                )
            )
    return result(
        "alerter-env-defaults-match",
        findings,
        f"{compared} compose defaults match their module constants",
    )


def check_alerter_env_declared(repo: Repo) -> CheckResult:
    """Every env var the alerter reads should be discoverable by an operator.

    A knob that exists only in Python is one nobody can find: not in compose,
    not in .env.template, not in the docs table. Warn rather than fail — an
    undiscoverable setting is a documentation gap, not a broken deployment.
    """
    env = sources.module_env(repo.alerter)
    if not env.usages:
        return skipped(
            "alerter-env-declared", f"no alerter source under {repo.alerter}"
        )

    known = (
        set(sources.compose_service_env(repo.compose, "alerter"))
        | sources.env_template_keys(repo.env_template)
        | sources.doc_env_keys(repo.alerting_doc)
    )
    findings = [
        Finding(
            f"{name} is read by the alerter but appears in neither "
            f"docker-compose.yml, .env.template, nor docs/alerting.md",
            where=next(u.where for u in env.usages if u.name == name),
        )
        for name in sorted(env.names() - known)
    ]
    return result(
        "alerter-env-declared",
        findings,
        f"{len(env.names())} alerter env vars are documented",
        status=Status.WARN,
    )
