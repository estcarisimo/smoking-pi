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
import re

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
        # Other modules that write InfluxDB points of their own.
        self.writers = (self.exporters, root / "shared/modules/netmeter",
                        root / "shared/modules/inference")
        self.alerter = root / "shared/modules/alerter"
        # Shared code copied into the alerter image; it reads env too.
        self.common = root / "shared/modules/common"
        self.pro = root / "editions/pro"
        self.compose = self.pro / "docker-compose.yml"
        self.env_template = self.pro / ".env.template"
        self.alerting_doc = root / "docs/alerting.md"
        self.mcp_server = root / "shared/modules/mcp-server/server.py"
        self.mcp_doc = root / "docs/mcp-server.md"
        self.env_templates = sorted(root.glob("editions/*/.env.template"))

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
        check_panel_tags_are_written(repo, influx, clickhouse),
        check_text_stats_name_their_field(influx),
        check_overrides_match_a_series(influx),
        check_alerter_env_defaults_match(repo),
        check_alerter_env_declared(repo),
        check_mcp_tools_documented(repo),
        check_settings_schema(repo),
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
    vocab = sources.exporter_vocabulary(*repo.writers)
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


def check_panel_tags_are_written(repo: Repo, influx, clickhouse=()) -> CheckResult:
    """Same for tag names: `r.measurement_type` is a filter that can never match.

    ClickHouse has columns, not tags, so a predicate there is always a valid
    name; what goes wrong is the value. ``category = 'DNS_Resolvers'`` (the
    RRD directory) where the exporter writes ``dns`` matched nothing, in a
    template variable, from v2.5.0 until this check read SQL too. Every
    ``category``/``measurement_type`` literal a ClickHouse query compares
    against must be one the exporter's ``*_for()`` classifiers can return.
    """
    vocab = sources.exporter_vocabulary(*repo.writers)
    ch_vocab = sources.clickhouse_vocabulary(repo.exporters)
    if not vocab.tag_names and not ch_vocab:
        return skipped(
            "panel-tags-written", f"no .tag() literals found under {repo.exporters}"
        )
    findings = []
    checked = 0
    for dashboard in influx if vocab.tag_names else ():
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
    ch_exporters = sorted(repo.exporters.glob("*clickhouse*.py"))
    if ch_exporters and not ch_vocab:
        findings.append(
            Finding(
                "no `<column>_for()` classifier returns a literal, so ClickHouse "
                "predicates cannot be checked",
                where=", ".join(p.name for p in ch_exporters),
            )
        )
    ch_checked = 0
    for dashboard in clickhouse if ch_vocab else ():
        for where, query in sources.iter_queries(dashboard):
            for column, value in sorted(sources.sql_values_in(query, ch_vocab)):
                ch_checked += 1
                if value not in ch_vocab[column]:
                    findings.append(
                        Finding(
                            f"compares {column} to {value!r}, which the ClickHouse "
                            f"exporter never writes (written: "
                            f"{', '.join(sorted(ch_vocab[column]))})",
                            where=f"{dashboard.rel} / {where}",
                        )
                    )
    parts = []
    if vocab.tag_names:
        parts.append(f"{checked} tag references match {', '.join(vocab.sources)}")
    if ch_vocab:
        parts.append(f"{ch_checked} ClickHouse column values match the exporter")
    return result("panel-tags-written", findings, "; ".join(parts))


def check_text_stats_name_their_field(influx) -> CheckResult:
    """A stat reduces numeric fields only, unless ``reduceOptions.fields``
    names one: a query answering with text (an SSID, an address, a network)
    then shows its ``noValue`` ("unknown", "not collected yet") over data
    that is there. The Overview's whole first row did that from v2.14.0 to
    v2.15.6; the query answered correctly the entire time."""
    findings = []
    checked = 0
    for dashboard in influx:
        for panel in sources.iter_stat_panels(dashboard):
            queries = [
                t.get("query")
                for t in panel.get("targets") or []
                if isinstance(t, dict) and isinstance(t.get("query"), str)
            ]
            if not any(sources.shows_text(q) for q in queries):
                continue
            checked += 1
            reduce = (panel.get("options") or {}).get("reduceOptions") or {}
            if not reduce.get("fields"):
                findings.append(
                    Finding(
                        "stat answers with text but reduceOptions.fields is "
                        "empty, so it shows noValue; name the field "
                        '(e.g. "/^Value$/")',
                        where=f"{dashboard.rel} / {panel.get('title')}",
                    )
                )
    return result(
        "text-stats-name-their-field",
        findings,
        f"{checked} text stats name the field they show",
    )


def check_overrides_match_a_series(influx) -> CheckResult:
    """An override matched ``byName`` on a ``yield(name:)`` value never
    applies: Grafana names a Flux series by its field and tags. The CPE
    microcuts' failures axis and six Wi-Fi Link overrides (units, axes, a
    0-100 scale) were dead this way; the panels drew, just wrongly."""
    findings = []
    checked = 0
    for dashboard in influx:
        for panel in sources.iter_panels(dashboard):
            config = panel.get("fieldConfig") or {}
            renamed = panel.get("transformations") or any(
                prop.get("id") == "displayName"
                for override in config.get("overrides") or []
                for prop in (override or {}).get("properties") or []
            )
            if (config.get("defaults") or {}).get("displayName") or renamed:
                continue  # names come from displayName or a transformation
            queries = [
                t.get("query")
                for t in panel.get("targets") or []
                if isinstance(t, dict) and isinstance(t.get("query"), str)
            ]
            yielded: set[str] = set()
            produced: set[str] = set()
            for query in queries:
                yielded |= sources.yield_names_in(query)
                produced |= sources.series_names_in(query)
            for override in config.get("overrides") or []:
                matcher = (override or {}).get("matcher") or {}
                if matcher.get("id") != "byName":
                    continue
                name = matcher.get("options")
                checked += 1
                if name in yielded and name not in produced:
                    findings.append(
                        Finding(
                            f"override byName {name!r} matches a yield(name:), "
                            "which Grafana never uses as a series name; match "
                            "the query instead (byFrameRefID)",
                            where=f"{dashboard.rel} / {panel.get('title')}",
                        )
                    )
    return result(
        "overrides-match-a-series",
        findings,
        f"{checked} byName overrides name a series a query can produce",
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
    env = sources.module_env(repo.alerter, repo.common)
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
    env = sources.module_env(repo.alerter, repo.common)
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


def check_mcp_tools_documented(repo: Repo) -> CheckResult:
    """The MCP tool table must list every tool the server registers, and no others.

    An assistant only calls what its client advertises, so an undocumented
    tool is one nobody knows to ask for, and a documented tool that no longer
    exists is worse: it reads as a promise. This check exists because the four
    alert-control tools (``mute_alerts``, ``unmute_alerts``, ``ack_incident``,
    ``list_alert_state``) shipped with the alerting work and were described in
    docs/alerting.md, while the MCP server's own table — the page someone
    reads to find out what the server can do — kept listing eleven.
    """
    tools = sources.mcp_tool_names(repo.mcp_server)
    if not tools:
        return skipped(
            "mcp-tools-documented", f"no @mcp.tool functions under {repo.mcp_server}"
        )
    documented = sources.doc_tool_names(repo.mcp_doc)
    if not documented and not repo.mcp_doc.is_file():
        return skipped("mcp-tools-documented", f"no {repo.mcp_doc}")
    # A doc that exists but yields no rows is the worst case, not a reason to
    # skip: a table deleted or reformatted past recognition leaves every tool
    # undocumented, and skipping says "nothing to check here" while the exit
    # code stays zero. Fall through and report all of them.

    findings = [
        Finding(
            f"{name}() is registered with @mcp.tool but is not a row of the "
            f"tool table — clients will offer it, the docs will not explain it",
            where="docs/mcp-server.md",
        )
        for name in sorted(tools - documented)
    ]
    findings += [
        Finding(
            f"{name}() is a row of the tool table but no longer exists in the "
            f"server — the docs promise a tool nothing registers",
            where="docs/mcp-server.md",
        )
        for name in sorted(documented - tools)
    ]
    return result(
        "mcp-tools-documented",
        findings,
        f"{len(tools)} MCP tools, all in the tool table",
    )


# The types `smoking-pi config set` knows how to check (validate_setting in
# cli/lib/config.sh), and the flags it reads.
SETTING_TYPES = {"int", "float", "bool", "time", "date", "tz", "url", "origin"}
SETTING_FLAGS = {"secret", "install"}
# The name patterns the command also applies, as a net (is_secret_key,
# is_install_key): a key they catch must say so in the template too.
_SECRET_NAME = re.compile(r"TOKEN|PASSWORD|SECRET|_KEY$|^ALERT_WEBHOOK_URL$")
_INSTALL_NAME = re.compile(
    r"^(POSTGRES_|INFLUX_|DOCKER_INFLUXDB_|CLICKHOUSE_|GF_SECURITY_)"
    r"|^(WEB_ADMIN_PASSWORD|SECRET_KEY|CONFIG_API_TOKEN|MCP_API_TOKEN|TSDB_TYPE)$"
)
_NUMBER = re.compile(r"^[-+]?([0-9]+(\.[0-9]*)?|\.[0-9]+)$")
_BOUNDS = ("min=", "gt=", "max=", "allow=")


def _meta_problems(meta: tuple[str, ...]) -> list[str]:
    problems: list[str] = []
    types = [w for w in meta if w in SETTING_TYPES or w.startswith("enum:")]
    for word in meta:
        if word in SETTING_TYPES or word in SETTING_FLAGS:
            continue
        if word.startswith("enum:") and all(word[5:].split("|")):
            continue
        if word.startswith(_BOUNDS) and _NUMBER.match(word.split("=", 1)[1]):
            if not set(types) & {"int", "float"}:
                problems.append(f"{word} needs int or float")
            continue
        problems.append(f"unknown word {word!r}")
    if len(types) > 1:
        problems.append(f"more than one type ({' '.join(types)})")
    bounds = _bounds(meta)
    low = bounds.get("min", bounds.get("gt"))
    if low is not None and "max" in bounds and low > bounds["max"]:
        problems.append(f"its lower bound {low:g} is above its max {bounds['max']:g}")
    return problems


def _bounds(meta: tuple[str, ...]) -> dict[str, float]:
    out: dict[str, float] = {}
    for word in meta:
        name, _, number = word.partition("=")
        if word.startswith(_BOUNDS) and _NUMBER.match(number):
            out[name] = float(number)
    return out


def _value_fits(meta: tuple[str, ...], value: str) -> bool:
    """Whether a template's own value passes its declared type."""
    if not value:
        return True
    for word in meta:
        if word in ("int", "float"):
            if not (re.match(r"^[-+]?[0-9]+$", value) if word == "int" else _NUMBER.match(value)):
                return False
            b, v = _bounds(meta), float(value)
            if "allow" in b and v == b["allow"]:
                return True
            return not (("min" in b and v < b["min"]) or ("gt" in b and v <= b["gt"])
                        or ("max" in b and v > b["max"]))
        if word == "bool":
            return value in ("true", "false", "1", "0")
        if word.startswith("enum:"):
            return value in word[5:].split("|")
        if word == "origin":
            return re.match(r"^https://[^/\s?#]+/?$", value) is not None
        if word == "url":
            return re.match(r"^https?://\S+$", value) is not None
        if word == "time":
            return re.match(r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$", value) is not None
    return True


def check_settings_schema(repo: Repo) -> CheckResult:
    """Every .env.template key sits in a section, and its `#:` line is valid.

    The template is the settings' schema for `smoking-pi config` (list by
    section, describe, and the type check `config set` runs before writing).
    A key outside every section lists under "Other"; a misspelled type
    silently turns validation off; a credential without `secret` relies on
    the command's name patterns alone. Each of those is a finding here.
    """
    if not repo.env_templates:
        return skipped("settings-schema", "no editions/*/.env.template found")
    findings: list[Finding] = []
    total = 0
    for template in repo.env_templates:
        rel = template.relative_to(repo.root)
        settings, orphans = sources.env_template_settings(template)
        total += len(settings)
        for number, text in orphans:
            findings.append(Finding(f"'#: {text}' is not directly above a key", where=f"{rel}:{number}"))
        for s in settings:
            where = f"{rel}:{s.line}"
            if not s.section:
                findings.append(Finding(f"{s.key} is in no section (add a '## Name' line above it)", where=where))
            for problem in _meta_problems(s.meta):
                findings.append(Finding(f"{s.key}: {problem}", where=where))
            if _SECRET_NAME.search(s.key) and "secret" not in s.meta:
                findings.append(Finding(f"{s.key} looks like a credential but its '#:' line lacks 'secret'", where=where))
            if _INSTALL_NAME.search(s.key) and not s.key.endswith("_PORT") and "install" not in s.meta:
                findings.append(Finding(f"{s.key} is fixed at install but its '#:' line lacks 'install'", where=where))
            if not _value_fits(s.meta, s.value):
                findings.append(Finding(f"{s.key}={s.value} does not pass its own type", where=where))
    return result(
        "settings-schema",
        findings,
        f"{total} settings in {len(repo.env_templates)} templates, all in a section and well-typed",
    )
