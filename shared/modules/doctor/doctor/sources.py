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


@dataclass
class EnvUsage:
    """One environment variable a module reads, and the fallback it uses."""

    name: str
    where: str
    default_const: str | None = None  # a DEFAULT_* name, when the fallback is one
    default_literal: str | None = None  # an inline literal fallback


@dataclass
class ModuleEnv:
    """Every env var a module reads, plus its module-level constants."""

    usages: list[EnvUsage] = field(default_factory=list)
    constants: dict[str, object] = field(default_factory=dict)

    def names(self) -> set[str]:
        return {u.name for u in self.usages}

    def default_for(self, name: str) -> tuple[str | None, object | None]:
        """The constant name and value backing ``name``, if any.

        A variable read in more than one place resolves to the first usage
        that actually carries a fallback.
        """
        for usage in self.usages:
            if usage.name != name:
                continue
            if usage.default_const and usage.default_const in self.constants:
                return usage.default_const, self.constants[usage.default_const]
            if usage.default_literal is not None:
                return None, usage.default_literal
        return None, None


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


# ---------------------------------------------------------------------------
# What a module reads from the environment, and what Compose supplies
# ---------------------------------------------------------------------------

# Helpers whose first argument is an env var name and second is its fallback.
_ENV_HELPERS = {"_env_int", "_env_float", "_env_bool", "_env_str"}


def _is_env_get(node: ast.expr) -> bool:
    """True for ``os.environ.get(...)`` / ``environ.get(...)`` calls."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "get":
        return False
    value = node.func.value
    if isinstance(value, ast.Name) and value.id == "environ":
        return True
    return (
        isinstance(value, ast.Attribute)
        and value.attr == "environ"
        and isinstance(value.value, ast.Name)
        and value.value.id == "os"
    )


def _env_call_name(node: ast.Call) -> str | None:
    """The env var name a call reads, when it is a plain string literal.

    Non-literal first arguments are ignored on purpose: that is the body of
    the ``_env_int(name, default)`` helper itself, not a usage.
    """
    if not node.args:
        return None
    return _literal(node.args[0])


def _default_of(node: ast.expr) -> tuple[str | None, str | None]:
    """Split a fallback expression into (constant name, literal)."""
    if isinstance(node, ast.Name):
        return node.id, None
    if isinstance(node, ast.Constant) and node.value is not None:
        return None, str(node.value)
    return None, None


class _EnvVisitor(ast.NodeVisitor):
    """Collect env reads, including the ``get(...) or DEFAULT`` idiom."""

    def __init__(self, where: str):
        self.where = where
        self.usages: list[EnvUsage] = []
        self._defaulted: set[int] = set()  # id() of calls handled by a BoolOp

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        # `os.environ.get("X") or DEFAULT_X` — the fallback lives in the
        # BoolOp, not in the call, so the call alone looks defaultless.
        if isinstance(node.op, ast.Or) and len(node.values) == 2:
            left, right = node.values
            call = left
            if isinstance(call, ast.Call) and (
                _is_env_get(call)
                or (isinstance(call.func, ast.Name) and call.func.id in _ENV_HELPERS)
            ):
                name = _env_call_name(call)
                if name and len(call.args) < 2:
                    const, literal = _default_of(right)
                    self.usages.append(
                        EnvUsage(name, self.where, const, literal)
                    )
                    self._defaulted.add(id(call))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if id(node) in self._defaulted:
            self.generic_visit(node)
            return
        is_get = _is_env_get(node)
        is_helper = isinstance(node.func, ast.Name) and node.func.id in _ENV_HELPERS
        if is_get or is_helper:
            name = _env_call_name(node)
            if name:
                const = literal = None
                if len(node.args) > 1:
                    const, literal = _default_of(node.args[1])
                self.usages.append(EnvUsage(name, self.where, const, literal))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # `os.environ["X"]` — required, no fallback.
        value = node.value
        looks_like_environ = (
            isinstance(value, ast.Name) and value.id == "environ"
        ) or (
            isinstance(value, ast.Attribute)
            and value.attr == "environ"
            and isinstance(value.value, ast.Name)
            and value.value.id == "os"
        )
        if looks_like_environ:
            name = _literal(node.slice)
            if name:
                self.usages.append(EnvUsage(name, self.where))
        self.generic_visit(node)


def _module_constants(tree: ast.Module) -> dict[str, object]:
    """Top-level UPPER_SNAKE assignments bound to a literal."""
    constants: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.isupper():
                constants[target.id] = node.value.value
    return constants


def module_env(module_dir: pathlib.Path) -> ModuleEnv:
    """Every env var the module's own source reads, with its fallbacks.

    Read out of the source rather than listed here, for the same reason the
    exporter vocabulary is: a hand-maintained list drifts silently, and
    silent drift is the bug class this tool exists to catch.
    """
    env = ModuleEnv()
    if not module_dir.is_dir():
        return env
    for path in sorted(module_dir.glob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except (OSError, SyntaxError):
            continue
        env.constants.update(_module_constants(tree))
        visitor = _EnvVisitor(path.name)
        visitor.visit(tree)
        env.usages.extend(visitor.usages)
    return env


_COMPOSE_DEFAULT_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*))?\}$")


def compose_service_env(
    compose_path: pathlib.Path, service: str
) -> dict[str, str | None]:
    """Env names a Compose service declares -> the ``${VAR:-default}`` default.

    The value is None when the entry supplies no default (``${VAR}`` or a
    literal), which callers treat as "declared, nothing to compare".
    """
    declared: dict[str, str | None] = {}
    try:
        data = yaml.safe_load(compose_path.read_text())
    except (OSError, yaml.YAMLError):
        return declared
    if not isinstance(data, dict):
        return declared
    block = (data.get("services") or {}).get(service) or {}
    entries = block.get("environment") or []
    if isinstance(entries, dict):
        entries = [f"{k}={v}" for k, v in entries.items()]
    for entry in entries:
        if not isinstance(entry, str) or "=" not in entry:
            continue
        key, _, raw = entry.partition("=")
        match = _COMPOSE_DEFAULT_RE.match(raw.strip())
        declared[key.strip()] = match.group(2) if match else None
    return declared


def env_template_keys(path: pathlib.Path) -> set[str]:
    """Keys present in a .env.template, ignoring comments."""
    keys: set[str] = set()
    try:
        text = path.read_text()
    except OSError:
        return keys
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


_DOC_ENV_ROW_RE = re.compile(r"^\|\s*`([A-Z][A-Z0-9_]*)`\s*\|")


def doc_env_keys(path: pathlib.Path) -> set[str]:
    """Env names documented as rows of a Markdown reference table."""
    try:
        text = path.read_text()
    except OSError:
        return set()
    return {
        match.group(1)
        for match in (_DOC_ENV_ROW_RE.match(line) for line in text.splitlines())
        if match
    }
