"""Load the artifacts the static checks compare against each other.

Everything here reads the repository, not a running stack: dashboard JSON,
provisioning YAML, the Grafana Dockerfile's plugin installs, and the vocabulary
the exporters actually write to InfluxDB.

The exporter vocabulary is extracted from the exporter source with `ast`, not
copied into a list here. A hand-maintained copy is exactly the kind of thing
that drifts silently, which is the class of bug this tool exists to catch.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
from dataclasses import dataclass, field

import yaml

# Grafana's built-in pseudo-datasources, which never appear in provisioning.
BUILTIN_DATASOURCE_UIDS = {
    "-- Grafana --",
    "-- Mixed --",
    "-- Dashboard --",
    "grafana",
    "datasource",
}

# Datasource types Grafana ships in core; anything else needs a plugin install.
CORE_DATASOURCE_TYPES = {
    "influxdb",
    "postgres",
    "mysql",
    "prometheus",
    "loki",
    "graphite",
    "elasticsearch",
    "cloudwatch",
    "testdata",
    "grafana-testdata-datasource",
    "grafana",
    "datasource",
    "tempo",
    "jaeger",
    "zipkin",
    "mssql",
    "opentsdb",
}

# Columns Flux itself provides; they are not exporter tags.
FLUX_BUILTIN_COLUMNS = {
    "_time",
    "_value",
    "_field",
    "_measurement",
    "_start",
    "_stop",
    "result",
    "table",
    "host",
}


@dataclass
class Dashboard:
    path: pathlib.Path
    tree: str
    data: dict

    @property
    def uid(self) -> str | None:
        return self.data.get("uid")

    @property
    def title(self) -> str:
        return self.data.get("title") or self.path.name

    @property
    def rel(self) -> str:
        return f"{self.tree}/{self.path.name}"


@dataclass
class Datasource:
    path: pathlib.Path
    name: str
    uid: str | None
    type: str | None
    is_default: bool


@dataclass
class Provider:
    path: pathlib.Path
    name: str
    scan_path: str


@dataclass
class ExporterVocabulary:
    """What the exporters actually write, read out of their source."""

    measurements: set[str] = field(default_factory=set)
    tag_names: set[str] = field(default_factory=set)
    sources: list[str] = field(default_factory=list)


def _walk_json(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in root.rglob("*.json") if p.is_file())


def load_dashboards(provisioning_root: pathlib.Path, tree: str) -> tuple[
    list[Dashboard], list[tuple[pathlib.Path, str]]
]:
    """Return (dashboards, unparseable) for one provisioning tree."""
    directory = provisioning_root / tree
    dashboards: list[Dashboard] = []
    broken: list[tuple[pathlib.Path, str]] = []
    if not directory.is_dir():
        return dashboards, broken
    for path in _walk_json(directory):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            broken.append((path, str(exc)))
            continue
        if not isinstance(data, dict):
            broken.append((path, "top-level JSON is not an object"))
            continue
        dashboards.append(Dashboard(path=path, tree=tree, data=data))
    return dashboards, broken


def load_datasources(directory: pathlib.Path) -> tuple[
    list[Datasource], list[tuple[pathlib.Path, str]]
]:
    found: list[Datasource] = []
    broken: list[tuple[pathlib.Path, str]] = []
    if not directory.is_dir():
        return found, broken
    for path in sorted(directory.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except (OSError, yaml.YAMLError) as exc:
            broken.append((path, str(exc)))
            continue
        for entry in data.get("datasources") or []:
            if not isinstance(entry, dict):
                continue
            found.append(
                Datasource(
                    path=path,
                    name=entry.get("name") or "<unnamed>",
                    uid=entry.get("uid"),
                    type=entry.get("type"),
                    is_default=bool(entry.get("isDefault")),
                )
            )
    return found, broken


def load_providers(path: pathlib.Path) -> tuple[
    list[Provider], list[tuple[pathlib.Path, str]]
]:
    providers: list[Provider] = []
    broken: list[tuple[pathlib.Path, str]] = []
    if not path.is_file():
        return providers, broken
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        return providers, [(path, str(exc))]
    for entry in data.get("providers") or []:
        if not isinstance(entry, dict):
            continue
        scan = ((entry.get("options") or {}).get("path")) or ""
        providers.append(
            Provider(path=path, name=entry.get("name") or "<unnamed>", scan_path=scan)
        )
    return providers, broken


def installed_plugins(dockerfile: pathlib.Path) -> set[str]:
    """Plugin ids the Grafana image bakes in via `grafana cli plugins install`."""
    if not dockerfile.is_file():
        return set()
    text = dockerfile.read_text()
    plugins: set[str] = set()
    for match in re.finditer(r"plugins\s+install\s+([^\n\\&|;]+)", text):
        for token in match.group(1).split():
            token = token.strip()
            if token and not token.startswith("-"):
                plugins.add(token)
    return plugins


# ---------------------------------------------------------------------------
# What the exporters write
# ---------------------------------------------------------------------------


def _literal(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _string_returns(func: ast.FunctionDef) -> set[str]:
    """Every string literal a function can return, including ternary branches."""
    values: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        for candidate in _unwrap(node.value):
            text = _literal(candidate)
            if text:
                values.add(text)
    return values


def _unwrap(node: ast.expr) -> list[ast.expr]:
    """Flatten conditional expressions so both branches are visible."""
    if isinstance(node, ast.IfExp):
        return _unwrap(node.body) + _unwrap(node.orelse)
    return [node]


class _PointVisitor(ast.NodeVisitor):
    """Collect measurement names and tag names the module writes.

    ``Point("cpe_latency")`` is direct. ``Point(measurement)`` is not, and
    rrd2influx writes both of its measurements that way — via
    ``measurement = measurement_for(...)``, a helper that returns one of two
    string literals. Resolving that one level of indirection is the difference
    between knowing the real vocabulary and reporting every ping panel as
    broken, so it is done rather than approximated.
    """

    def __init__(self, functions: dict[str, ast.FunctionDef]) -> None:
        self.functions = functions
        self.measurements: set[str] = set()
        self.tag_names: set[str] = set()
        self._assignments: dict[str, str] = {}  # local name -> helper called

    def visit_Assign(self, node: ast.Assign) -> None:
        if (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            self._assignments[node.targets[0].id] = node.value.func.id
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id == "Point" and node.args:
            self.measurements |= self._resolve(node.args[0])
        elif isinstance(func, ast.Attribute) and func.attr == "tag" and node.args:
            value = _literal(node.args[0])
            if value:
                self.tag_names.add(value)
        self.generic_visit(node)

    def _resolve(self, node: ast.expr) -> set[str]:
        direct = _literal(node)
        if direct:
            return {direct}
        if isinstance(node, ast.Name):
            helper = self._assignments.get(node.id)
            if helper and helper in self.functions:
                return _string_returns(self.functions[helper])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in self.functions:
                return _string_returns(self.functions[node.func.id])
        return set()


def exporter_vocabulary(exporter_dir: pathlib.Path) -> ExporterVocabulary:
    """Measurements and tag names the exporters write, read from their source."""
    vocab = ExporterVocabulary()
    if not exporter_dir.is_dir():
        return vocab
    for path in sorted(exporter_dir.glob("*.py")):
        # ClickHouse support is parked and writes a different schema.
        if "clickhouse" in path.name:
            continue
        try:
            tree = ast.parse(path.read_text())
        except (OSError, SyntaxError):
            continue
        functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        visitor = _PointVisitor(functions)
        visitor.visit(tree)
        if visitor.measurements or visitor.tag_names:
            vocab.measurements |= visitor.measurements
            vocab.tag_names |= visitor.tag_names
            vocab.sources.append(path.name)
    return vocab


# ---------------------------------------------------------------------------
# What the dashboards ask for
# ---------------------------------------------------------------------------

_MEASUREMENT_RE = re.compile(r'r\._measurement\s*[=!]=\s*"([^"]+)"')
_TAG_REF_RE = re.compile(r"\br\.([A-Za-z_][A-Za-z0-9_]*)")


def iter_queries(dashboard: Dashboard):
    """Yield (panel_title, query_text) for every query in a dashboard.

    Covers panel targets and template-variable queries, which is where the
    `category = 'DNS_Resolvers'` class of mistake actually lives.
    """
    for panel in _iter_panels(dashboard.data):
        title = panel.get("title") or "<untitled panel>"
        for target in panel.get("targets") or []:
            if not isinstance(target, dict):
                continue
            query = target.get("query") or target.get("rawSql")
            if isinstance(query, str) and query.strip():
                yield title, query
    for variable in (dashboard.data.get("templating") or {}).get("list") or []:
        if not isinstance(variable, dict):
            continue
        query = variable.get("query")
        if isinstance(query, dict):
            query = query.get("query")
        if isinstance(query, str) and query.strip():
            yield f"variable ${variable.get('name')}", query


def _iter_panels(data: dict):
    """Panels, including those nested inside collapsed rows."""
    for panel in data.get("panels") or []:
        if not isinstance(panel, dict):
            continue
        yield panel
        for nested in panel.get("panels") or []:
            if isinstance(nested, dict):
                yield nested


def iter_datasource_refs(dashboard: Dashboard):
    """Yield (where, uid) for every datasource reference in a dashboard."""
    for panel in _iter_panels(dashboard.data):
        title = panel.get("title") or "<untitled panel>"
        yield from _refs_from(panel.get("datasource"), title)
        for target in panel.get("targets") or []:
            if isinstance(target, dict):
                yield from _refs_from(target.get("datasource"), title)
    for variable in (dashboard.data.get("templating") or {}).get("list") or []:
        if isinstance(variable, dict):
            yield from _refs_from(
                variable.get("datasource"), f"variable ${variable.get('name')}"
            )
    for annotation in (dashboard.data.get("annotations") or {}).get("list") or []:
        if isinstance(annotation, dict):
            yield from _refs_from(annotation.get("datasource"), "annotation")


def _refs_from(datasource, where: str):
    if isinstance(datasource, dict):
        uid = datasource.get("uid")
        if isinstance(uid, str) and uid:
            yield where, uid


def measurements_in(query: str) -> set[str]:
    return set(_MEASUREMENT_RE.findall(query))


def tag_refs_in(query: str) -> set[str]:
    return {
        name
        for name in _TAG_REF_RE.findall(query)
        if name not in FLUX_BUILTIN_COLUMNS
    }
