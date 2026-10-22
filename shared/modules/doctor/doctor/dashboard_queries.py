"""Live check: every provisioned dashboard's queries run in the installed Grafana.

The static checks read the dashboards as files: a measurement nobody writes,
a tag that does not exist, a link to a uid no file defines. They cannot see
what only running a query shows. On 2026-10-15 a hand-run audit found a
production panel failing on every load with a Flux runtime error (DNS
Wizard "Top services by presence": ``pivot: specified row key column does
not exist in table: cdn``) and a dashboard that opened blank because its
variable picked a dead value (CPE), while every doctor check was green.

``dashboard-queries-run`` does what that audit did, read-only: it asks
Grafana for every dashboard, keeps the provisioned ones, resolves each
template variable by running its query (the saved value when the variable
still offers it, as Grafana does on open, else the first one), substitutes
the variables into every panel and annotation query, and POSTs each to
``/api/ds/query`` with the dashboard's default time range. Sequential, ten
seconds per query and two minutes in all: the Pi is small and this runs
beside the measurements.

FAIL: a query that errors, a variable whose query errors or offers no value,
a ``/d/<uid>`` link to a dashboard the installed Grafana does not have, a
dashboard of the repository's tree that Grafana does not provision.
WARN: a query that timed out, the budget running out, and panels that
returned no rows. Many panels are empty by design (no IPv6 here, no change
in the range, a target without DNS), so empty is listed, not failed; panels
inside a collapsed row and panels that set their own ``noValue`` text are
left out of that list.
"""

from __future__ import annotations

import base64
import copy
import datetime
import http.client
import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import sources
from .report import CheckResult, Finding, Status, skipped

NAME = "dashboard-queries-run"
GRAFANA_URL = "http://127.0.0.1:3000"
QUERY_TIMEOUT = 10.0
TOTAL_BUDGET = 120.0
MAX_DATA_POINTS = 1000
ERROR_CHARS = 240
EMPTY_SHOWN = 12

# Datasource uids that are Grafana itself, not a backend to query.
_BUILTIN_DATASOURCES = {"-- Grafana --", "grafana", "-- Dashboard --", "-- Mixed --"}
_MIXED = "-- Mixed --"
# A Grafana-relative link: "/d/<uid>" or "/d-solo/<uid>" right after a quote,
# a parenthesis or a space, so prose and other sites' URLs
# (docs.google.com/d/...) do not count.
_LINK = re.compile(r"""(?:^|(?<=["'(\s]))/d(?:-solo)?/([A-Za-z0-9_-]+)""")
_RELATIVE = re.compile(r"^now(?:-(\d+)([smhdwMy]))?(?:/([smhdwMy]))?$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800,
                 "M": 30 * 86400, "y": 365 * 86400}


class Timeout(Exception):
    """A request took longer than its share of the budget."""


class Unreachable(Exception):
    """Nothing answered at the Grafana URL."""


class Grafana:
    """The HTTP layer, injectable so the tests never reach the network.

    ``request`` returns (status, parsed JSON or None); it raises only
    ``Timeout`` and ``Unreachable``. ``timeout`` bounds each socket
    operation, as urllib does, not the whole transfer. The password stays in this process: it is
    put in a Basic header here, never on a command line.
    """

    def __init__(self, user: str, password: str, url: str = GRAFANA_URL):
        self.url = url.rstrip("/")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self._auth = "Basic " + token

    def request(self, method: str, path: str, body=None, timeout: float = QUERY_TIMEOUT):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Authorization", self._auth)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, _parse(resp.read())
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read()
            except (OSError, http.client.HTTPException):
                body = b""
            return exc.code, _parse(body)
        except TimeoutError as exc:
            raise Timeout() from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise Timeout() from exc
            raise Unreachable() from exc
        except (OSError, http.client.HTTPException) as exc:
            # A reset, a refused connection, a truncated body: Grafana went
            # away mid-answer.
            raise Unreachable() from exc


def _parse(raw: bytes):
    try:
        return json.loads(raw.decode() or "null")
    except ValueError:
        return None


def read_env_file(path: pathlib.Path) -> dict[str, str]:
    """KEY=value lines, quotes stripped as ``env_get`` does.

    Raises OSError, or ValueError when the file is not text.
    """
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def host_timezone(localtime: pathlib.Path = pathlib.Path("/etc/localtime")) -> str:
    """The host's IANA zone name (what a browser would send), else UTC."""
    tz = os.environ.get("TZ", "").lstrip(":")
    if tz and "/" in tz and not tz.startswith("/"):
        return tz
    try:
        target = str(localtime.resolve())
    except OSError:
        return "UTC"
    marker = "zoneinfo/"
    return target.split(marker, 1)[1] if marker in target else "UTC"


def _epoch_ms(spec, now_ms: int) -> int:
    """A dashboard time bound (``now``, ``now-6h``, epoch ms) as epoch ms."""
    if isinstance(spec, (int, float)):
        return int(spec)
    text = str(spec or "").strip()
    if text.isdigit():
        return int(text)
    match = _RELATIVE.match(text)
    if match:
        amount, unit, round_to = match.groups()
        moment = now_ms - (int(amount) * _UNIT_SECONDS[unit] * 1000 if amount else 0)
        if round_to:
            # Start of the unit, in UTC; weeks, months and years to the day.
            step = min(_UNIT_SECONDS[round_to], 86400) * 1000
            moment -= moment % step
        return moment
    try:
        moment = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return now_ms - 6 * 3600 * 1000
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return int(moment.timestamp() * 1000)


def _format(name: str, value: str, fmt: str | None, is_multi: bool = False) -> str:
    """One value in a Grafana format. A multi-value variable keeps its list
    shape in ``json``, ``regex`` and ``pipe``/``csv`` though only one value
    is ever sent: this check runs one value per variable."""
    if fmt == "sqlstring":
        return "'" + value.replace("'", "''") + "'"
    if fmt == "queryparam":
        return f"var-{name}={urllib.parse.quote(value, safe='')}"
    if fmt == "regex":
        return f"({re.escape(value)})" if is_multi else re.escape(value)
    if fmt == "json":
        return json.dumps([value] if is_multi else value)
    if fmt == "singlequote":
        return "'" + value.replace("'", "\\'") + "'"
    if fmt == "doublequote":
        return '"' + value.replace('"', '\\"') + '"'
    if fmt in ("percentencode", "urlencode"):
        return urllib.parse.quote(value, safe="")
    return value


def substitute(text: str, values: dict[str, str], multi=frozenset()) -> str:
    """Grafana's ``${x}``, ``${x:fmt}``, ``[[x]]`` and ``$x``; unknowns kept."""

    def braced(match):
        name, fmt = match.group(1), match.group(2)
        if name not in values:
            return match.group(0)
        return _format(name, values[name], fmt, name in multi)

    def bare(match):
        name = match.group(1)
        return values[name] if name in values else match.group(0)

    text = re.sub(r"\$\{([A-Za-z0-9_]+)(?::([A-Za-z0-9_]+))?(?::[^}]*)?\}", braced, text)
    text = re.sub(r"\[\[([A-Za-z0-9_]+)(?::([A-Za-z0-9_]+))?\]\]", braced, text)
    # Longest name first is implicit: \w+ is greedy, so $target never
    # matches a variable named $t.
    return re.sub(r"\$([A-Za-z0-9_]+)", bare, text)


def _substitute_all(node, values, multi=frozenset()):
    if isinstance(node, str):
        return substitute(node, values, multi)
    if isinstance(node, list):
        return [_substitute_all(n, values, multi) for n in node]
    if isinstance(node, dict):
        return {k: _substitute_all(v, values, multi) for k, v in node.items()}
    return node


def _interval_ms(span_ms: int) -> int:
    """Grafana's query interval, in whole seconds as its frontend rounds it.

    Flux receives it as ``v.windowPeriod``: a fractional interval reaches
    it as ``1m26.4s``, which is not a Flux duration, and every panel that
    aggregates by it fails to compile.
    """
    return max((span_ms // MAX_DATA_POINTS) // 1000 * 1000, 1000)


def _builtins(start_ms: int, end_ms: int, timezone: str, uid: str) -> dict[str, str]:
    span = max(end_ms - start_ms, 1000)
    interval_ms = _interval_ms(span)
    return {
        "__from": str(start_ms),
        "__to": str(end_ms),
        "__timezone": timezone,
        "__url_time_range": f"from={start_ms}&to={end_ms}",
        "__interval": f"{interval_ms // 1000}s",
        "__interval_ms": str(interval_ms),
        "__rate_interval": f"{4 * interval_ms // 1000}s",
        "__range": f"{span // 1000}s",
        "__range_s": str(span // 1000),
        "__range_ms": str(span),
        "__dashboard": uid,
    }


def _rows(result: dict) -> int:
    count = 0
    for frame in result.get("frames") or []:
        values = (frame.get("data") or {}).get("values") or []
        if values and isinstance(values[0], list):
            count += len(values[0])
    return count


def _first_column(result: dict) -> list[str]:
    """A variable query's values: ``__value``, ``_value``, else the first field."""
    out: list[str] = []
    for frame in result.get("frames") or []:
        fields = [f.get("name") for f in (frame.get("schema") or {}).get("fields") or []]
        values = (frame.get("data") or {}).get("values") or []
        if not values:
            continue
        index = 0
        for wanted in ("__value", "_value"):
            if wanted in fields:
                index = fields.index(wanted)
                break
        if index < len(values) and isinstance(values[index], list):
            out.extend(str(v) for v in values[index] if v is not None)
    return out


def _apply_regex(options: list[str], pattern: str) -> list[str]:
    """A variable's ``regex`` filter, written as /pattern/flags or bare."""
    if not pattern:
        return options
    flags = 0
    match = re.match(r"^/(.*)/([a-z]*)$", pattern, re.DOTALL)
    if match:
        pattern, letters = match.groups()
        flags = re.IGNORECASE if "i" in letters else 0
    try:
        compiled = re.compile(pattern, flags)
    except re.error:
        return options
    kept = []
    for option in options:
        found = compiled.search(option)
        if found:
            value = found.group(1) if compiled.groups else option
            if value is not None:
                kept.append(value)
    return kept


def _sort(options: list[str], mode) -> list[str]:
    if mode in (1, 2):
        return sorted(options, reverse=mode == 2)
    if mode in (3, 4):
        def number(v):
            found = re.search(r"-?\d+(\.\d+)?", v)
            return float(found.group(0)) if found else float("inf")
        return sorted(options, key=number, reverse=mode == 4)
    if mode in (5, 6):
        return sorted(options, key=str.lower, reverse=mode == 6)
    return options


def _iter_query_panels(panels, collapsed: bool = False):
    """(panel, inside a collapsed row) for every panel, rows' children too."""
    for panel in panels or []:
        if not isinstance(panel, dict):
            continue
        if panel.get("type") == "row":
            yield from _iter_query_panels(panel.get("panels"), bool(panel.get("collapsed")))
            continue
        yield panel, collapsed


def _has_no_value_text(panel: dict) -> bool:
    defaults = (panel.get("fieldConfig") or {}).get("defaults") or {}
    return bool(defaults.get("noValue")) or bool((panel.get("options") or {}).get("noValue"))


class _Run:
    """One pass over the installed Grafana, sharing a deadline."""

    def __init__(self, grafana, clock, budget: float, timeout: float):
        self.grafana = grafana
        self.clock = clock
        self.deadline = clock() + budget
        self.timeout = timeout
        self.queries = 0
        self.checked = 0
        self.out_of_budget = False
        self.stopped: str | None = None  # "budget" or "unreachable"
        self.datasources_read = False
        self.default_ds: str | None = None
        self.ds_by_name: dict[str, str] = {}
        self.ds_type: dict[str, str] = {}

    def remaining(self) -> float:
        return self.deadline - self.clock()

    def get(self, path: str):
        left = self.remaining()
        if left <= 0:
            self.out_of_budget = True
            raise Timeout()
        return self.grafana.request("GET", path, timeout=min(self.timeout, left))

    def load_datasources(self) -> None:
        try:
            status, data = self.get("/api/datasources")
        except (Timeout, Unreachable):
            return
        if status != 200 or not isinstance(data, list):
            return
        self.datasources_read = True
        for ds in data:
            if not isinstance(ds, dict) or not ds.get("uid"):
                continue
            self.ds_by_name[str(ds.get("name"))] = ds["uid"]
            self.ds_type[ds["uid"]] = str(ds.get("type") or "")
            if ds.get("isDefault"):
                self.default_ds = ds["uid"]

    def datasource_uid(self, *refs) -> str | None:
        """The first datasource that names one; the default when none does."""
        for ref in refs:
            uid = ref.get("uid") if isinstance(ref, dict) else ref
            if isinstance(uid, str) and uid:
                return self.ds_by_name.get(uid, uid)
        return self.default_ds

    def query(self, query: dict, start_ms: int, end_ms: int):
        """(error text or None, the query's result); raises Timeout."""
        left = self.remaining()
        if left <= 0:
            self.out_of_budget = True
            raise Timeout()
        span = max(end_ms - start_ms, 1000)
        query = dict(query)
        query.setdefault("refId", "A")
        query.setdefault("intervalMs", _interval_ms(span))
        query.setdefault("maxDataPoints", MAX_DATA_POINTS)
        body = {"from": str(start_ms), "to": str(end_ms), "queries": [query]}
        self.queries += 1
        status, data = self.grafana.request(
            "POST", "/api/ds/query", body, timeout=min(self.timeout, left))
        results = (data or {}).get("results") if isinstance(data, dict) else None
        result = (results or {}).get(query["refId"]) if isinstance(results, dict) else None
        if isinstance(result, dict) and result.get("error"):
            return _clip(str(result["error"])), {}
        if status != 200:
            message = data.get("message") if isinstance(data, dict) else None
            return _clip(f"HTTP {status}" + (f": {message}" if message else "")), {}
        return None, result if isinstance(result, dict) else {}


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= ERROR_CHARS else text[: ERROR_CHARS - 1] + "…"


def _variable_query(variable: dict, uid: str, ds_type: str,
                    values: dict[str, str], multi=frozenset()) -> dict | None:
    """The /api/ds/query entry for a variable, or None when its query is not text."""
    raw = variable.get("query")
    if isinstance(raw, dict):
        query = copy.deepcopy(raw)
        text = raw.get("query") or raw.get("rawSql") or ""
    else:
        query, text = {}, raw or ""
    if not isinstance(text, str):
        return None
    text = substitute(text, values, multi)
    query.update({"refId": "variable", "datasource": {"uid": uid},
                  "query": text, "rawSql": text})
    if "clickhouse" in ds_type:
        # The ClickHouse plugin's format is a number (1 = table), as its
        # panels store it; the SQL datasources take the word.
        query.setdefault("format", 1)
        query.setdefault("queryType", "sql")
    else:
        query.setdefault("format", "table")
    return query


def _uses(node, names: set[str]) -> bool:
    """Whether a query refers to any of these variables, in any syntax."""
    if not names:
        return False
    text = json.dumps(node)
    return any(re.search(r"(\$\{|\[\[)" + re.escape(n) + r"\b|\$" + re.escape(n) + r"\b", text)
               for n in names)


def _attempt(run: _Run, query: dict, start_ms: int, end_ms: int):
    """("ok", result) | ("error", text) | ("timeout", seconds) | ("stop", None).

    "stop" ends the whole run: the budget is spent or Grafana went away.
    Nothing here raises.
    """
    if run.stopped:
        return "stop", None
    allowed = min(run.timeout, max(run.remaining(), 0.0))
    try:
        error, answer = run.query(query, start_ms, end_ms)
    except Timeout:
        if run.out_of_budget:
            run.stopped = "budget"
            return "stop", None
        return "timeout", allowed
    except Unreachable:
        run.stopped = "unreachable"
        return "stop", None
    if error:
        return "error", error
    return "ok", answer


def _check_dashboard(run: _Run, dash: dict, now_ms: int, timezone: str,
                     fails: list, warns: list, empty: list) -> None:
    title = dash.get("title") or dash.get("uid") or "<untitled>"
    uid = dash.get("uid") or ""
    window = dash.get("time") or {}
    start_ms = _epoch_ms(window.get("from", "now-6h"), now_ms)
    end_ms = _epoch_ms(window.get("to", "now"), now_ms)
    label = f"{window.get('from', 'now-6h')} to {window.get('to', 'now')}"
    values = _builtins(start_ms, end_ms, timezone, uid)
    multi: set[str] = set()
    # Variables with no value: the panels that use them are not run, so one
    # broken variable is one finding, not one per panel.
    unresolved: set[str] = set()

    for variable in (dash.get("templating") or {}).get("list") or []:
        if not isinstance(variable, dict) or not variable.get("name"):
            continue
        name = str(variable["name"])
        if variable.get("multi") or variable.get("includeAll"):
            multi.add(name)
        kind = variable.get("type")
        current = (variable.get("current") or {}).get("value")
        if isinstance(current, list):
            current = current[0] if current else None
        if kind != "query":
            raw = variable.get("query")
            if kind == "custom" and isinstance(raw, str):
                options = [o.strip() for o in raw.split(",") if o.strip()]
                values[name] = str(current) if current in options else (options[0] if options else "")
            elif kind in ("textbox", "constant"):
                values[name] = str(current) if current not in (None, "") else str(raw or "")
            elif kind == "interval" and isinstance(raw, str):
                values[name] = str(current or raw.split(",")[0].strip())
            else:
                values[name] = "" if current is None else str(current)
            continue
        ds_uid = run.datasource_uid(variable.get("datasource"))
        if not ds_uid:
            unresolved.add(name)
            fails.append(Finding(f"variable ${name}: no datasource to query", where=title))
            continue
        query = _variable_query(variable, ds_uid, run.ds_type.get(ds_uid, ""), values, multi)
        if query is None:
            unresolved.add(name)
            warns.append(Finding(f"variable ${name}: its query is not text this check can run",
                                 where=title))
            continue
        outcome, detail = _attempt(run, query, start_ms, end_ms)
        if outcome == "stop":
            return
        if outcome != "ok":
            unresolved.add(name)
            if outcome == "timeout":
                warns.append(Finding(f"variable ${name}: no answer in {detail:.0f} s",
                                     where=title))
            else:
                fails.append(Finding(f"variable ${name}: query fails: {detail}", where=title))
            continue
        options = _sort(_apply_regex(_first_column(detail), variable.get("regex") or ""),
                        variable.get("sort"))
        # Saved on "All" with a custom all value: Grafana sends that value,
        # whatever the query offers (none, on a fresh install, is fine).
        if current == "$__all" and variable.get("includeAll") and variable.get("allValue"):
            values[name] = str(variable["allValue"])
            continue
        if not options:
            unresolved.add(name)
            fails.append(Finding(
                f"variable ${name} offers no value in {label}: every panel that uses it "
                "opens empty", where=title))
            continue
        values[name] = str(current) if current in options else options[0]

    empty_here = []
    for panel, collapsed in _iter_query_panels(dash.get("panels")):
        targets = [t for t in panel.get("targets") or []
                   if isinstance(t, dict) and not t.get("hide")]
        if not targets or panel.get("libraryPanel") or _uses(panel, unresolved):
            continue
        panel_title = substitute(str(panel.get("title") or f"panel {panel.get('id')}"),
                                 values, multi)
        panel_ds = panel.get("datasource")
        rows = 0
        ran = errored = False
        for target in targets:
            refs = [target.get("datasource")]
            if not (isinstance(panel_ds, dict) and panel_ds.get("uid") == _MIXED):
                refs.append(panel_ds)
            ds_uid = run.datasource_uid(*refs)
            if not ds_uid:
                warns.append(Finding(
                    f"{panel_title!r} ({target.get('refId', '?')}): names no datasource "
                    "and Grafana's default could not be read; not run", where=title))
                continue
            if ds_uid in _BUILTIN_DATASOURCES:
                continue
            query = _substitute_all(copy.deepcopy(target), values, multi)
            query["datasource"] = {"uid": ds_uid}
            outcome, detail = _attempt(run, query, start_ms, end_ms)
            if outcome == "stop":
                return
            where = f"{panel_title!r} ({target.get('refId', '?')})"
            if outcome == "timeout":
                errored = True
                warns.append(Finding(f"{where}: no answer in {detail:.0f} s", where=title))
                continue
            ran = True
            if outcome == "error":
                errored = True
                fails.append(Finding(f"{where}: query fails: {detail}", where=title))
                continue
            rows += _rows(detail)
        if ran and not errored and rows == 0 and not collapsed \
                and not _has_no_value_text(panel):
            empty_here.append(panel_title)
        # Recorded as it grows, so a run cut short keeps what it saw.
        if empty_here and (not empty or empty[-1][0] != title):
            empty.append((title, label, empty_here))

    for annotation in (dash.get("annotations") or {}).get("list") or []:
        if not isinstance(annotation, dict) or annotation.get("builtIn"):
            continue
        if annotation.get("enable") is False:
            continue
        target = annotation.get("target")
        if not isinstance(target, dict) or _uses(target, unresolved):
            continue
        ds_uid = run.datasource_uid(annotation.get("datasource"))
        if not ds_uid or ds_uid in _BUILTIN_DATASOURCES:
            continue
        query = _substitute_all(copy.deepcopy(target), values, multi)
        query["datasource"] = {"uid": ds_uid}
        query.setdefault("refId", "Anno")
        outcome, detail = _attempt(run, query, start_ms, end_ms)
        if outcome == "stop":
            return
        name = annotation.get("name")
        if outcome == "timeout":
            warns.append(Finding(f"annotation {name!r}: no answer in {detail:.0f} s",
                                 where=title))
        elif outcome == "error":
            fails.append(Finding(f"annotation {name!r}: query fails: {detail}", where=title))
    run.checked += 1


def check_dashboard_queries_run(
    repo=None,
    docker=None,
    grafana=None,
    env_file: pathlib.Path | None = None,
    clock=time.monotonic,
    now=time.time,
    timezone: str | None = None,
    budget: float = TOTAL_BUDGET,
    timeout: float = QUERY_TIMEOUT,
) -> CheckResult:
    """Run every provisioned dashboard's queries the way opening it would."""
    started = clock()
    env: dict[str, str] = {}
    if grafana is None:
        if docker is None or not docker.available():
            return skipped(NAME, "docker is not available here")
        if not docker.container_for_service("grafana"):
            return skipped(NAME, "no Grafana here (Basic and Standard have none, or it is stopped)")
        path = env_file or _env_path(repo)
        try:
            env = read_env_file(path)
        except (OSError, ValueError):
            return skipped(NAME, f"cannot read {path} for Grafana's admin password "
                                 "(run with sudo)")
        password = env.get("GF_SECURITY_ADMIN_PASSWORD", "")
        if not password:
            return skipped(NAME, f"no GF_SECURITY_ADMIN_PASSWORD in {path}")
        grafana = Grafana(env.get("GF_SECURITY_ADMIN_USER") or "admin", password)
    elif env_file is not None:
        try:
            env = read_env_file(env_file)
        except (OSError, ValueError):
            env = {}

    run = _Run(grafana, clock, budget, timeout)
    try:
        return _check_all(run, repo, env, started, now, timezone, budget)
    except Exception as exc:  # noqa: BLE001 -- one check must not abort --live
        return CheckResult(NAME, Status.WARN, "stopped on an unexpected error", [
            Finding(f"{type(exc).__name__} after {run.queries} queries; the other "
                    "checks are unaffected. Please report it with this line")])


def _check_all(run: _Run, repo, env, started, now, timezone, budget) -> CheckResult:
    clock = run.clock
    try:
        status, listing = run.get("/api/search?type=dash-db&limit=5000")
    except (Timeout, Unreachable):
        return skipped(NAME, f"Grafana is not answering at {GRAFANA_URL}")
    if status in (401, 403):
        return CheckResult(NAME, Status.WARN, "Grafana refused the admin credentials", [
            Finding("GF_SECURITY_ADMIN_PASSWORD in the env file is not Grafana's admin "
                    "password; `sudo smoking-pi restart grafana` applies it")])
    if status != 200 or not isinstance(listing, list):
        return skipped(NAME, f"Grafana's dashboard list answered HTTP {status}")
    run.load_datasources()

    fails: list[Finding] = []
    warns: list[Finding] = []
    empty: list[tuple[str, str, list[str]]] = []
    installed = {item.get("uid") for item in listing if isinstance(item, dict)}
    provisioned: dict[str, dict] = {}
    user_dashboards = 0
    now_ms = int(now() * 1000)
    timezone = timezone or host_timezone()
    for item in listing:
        if not isinstance(item, dict) or not item.get("uid"):
            continue
        uid = str(item["uid"])
        try:
            status, data = run.get(f"/api/dashboards/uid/{urllib.parse.quote(uid)}")
        except Timeout:
            if run.out_of_budget:
                run.stopped = "budget"
                break
            warns.append(Finding(f"could not fetch dashboard {uid} (no answer)"))
            continue
        except Unreachable:
            run.stopped = "unreachable"
            break
        if status != 200 or not isinstance(data, dict):
            warns.append(Finding(f"could not fetch dashboard {uid} (HTTP {status})"))
            continue
        meta = data.get("meta") or {}
        if not meta.get("provisioned"):
            user_dashboards += 1
            continue
        dash = data.get("dashboard")
        provisioned[uid] = dash if isinstance(dash, dict) else {}

    for uid, dash in sorted(provisioned.items()):
        for link in sorted(set(_LINK.findall(json.dumps(dash)))):
            if link not in installed:
                fails.append(Finding(f"links to /d/{link}, which this Grafana does not have",
                                     where=dash.get("title") or uid))

    tree = _repo_tree(repo, env)
    if tree is not None and not run.stopped:
        for path, repo_uid in tree:
            if repo_uid not in provisioned:
                fails.append(Finding(
                    f"{path} (uid {repo_uid}) is not provisioned in Grafana: the "
                    "dashboard is missing or stale (`smoking-pi logs grafana`)"))

    for uid, dash in sorted(provisioned.items()):
        if run.stopped:
            break
        _check_dashboard(run, dash, now_ms, timezone, fails, warns, empty)

    elapsed = clock() - started
    unchecked = len(provisioned) - run.checked
    if run.stopped == "budget":
        warns.append(Finding(
            f"stopped at the {budget:.0f} s budget after {run.queries} queries: "
            f"{unchecked} dashboard(s) not fully checked"))
    elif run.stopped == "unreachable":
        warns.append(Finding(
            f"Grafana stopped answering after {run.queries} queries: {unchecked} "
            "dashboard(s) not fully checked (`smoking-pi logs grafana`)"))
    if not run.datasources_read:
        warns.append(Finding("could not read Grafana's datasource list: panels that "
                             "name no datasource were not run"))
    if not provisioned and not run.stopped:
        warns.append(Finding("Grafana provisions no dashboard (still starting, or its "
                             "provisioning failed: `smoking-pi logs grafana`)"))
    elif provisioned and run.queries == 0 and not run.stopped:
        warns.append(Finding("no query was run: nothing was checked"))
    empty_count = sum(len(panels) for _, _, panels in empty)
    for title, label, panels in empty:
        shown = ", ".join(repr(p) for p in panels[:EMPTY_SHOWN])
        more = f" and {len(panels) - EMPTY_SHOWN} more" if len(panels) > EMPTY_SHOWN else ""
        warns.append(Finding(
            f"{len(panels)} panel(s) returned no rows in {label} (often by design: no "
            f"IPv6, no change in range, a target without that layer): {shown}{more}",
            where=title))

    summary = (f"{len(provisioned)} provisioned dashboards, {run.queries} queries in "
               f"{elapsed:.0f} s")
    if user_dashboards:
        summary += f"; {user_dashboards} user dashboard(s) skipped"
    if fails:
        noun = "failure" if len(fails) == 1 else "failures"
        status = Status.FAIL
        summary = f"{len(fails)} {noun}; " + summary
    elif warns:
        status = Status.WARN
    else:
        status = Status.OK
    if empty_count:
        summary += f"; {empty_count} empty panel(s)"
    return CheckResult(NAME, status, summary, fails + warns)


def _env_path(repo) -> pathlib.Path:
    configured = os.environ.get("SMOKING_PI_ENV_FILE")
    if configured:
        return pathlib.Path(configured)
    root = pathlib.Path(getattr(repo, "root", "."))
    return root / "editions/pro/.env"


def _repo_tree(repo, env: dict[str, str]):
    """(relative path, uid) of the repo tree this Grafana provisions, or None."""
    provisioning = getattr(repo, "provisioning", None)
    if provisioning is None or not pathlib.Path(provisioning).is_dir():
        return None
    tree = ("dashboards-clickhouse" if env.get("TSDB_TYPE", "influxdb") == "clickhouse"
            else "dashboards")
    dashboards, _ = sources.load_dashboards(pathlib.Path(provisioning), tree)
    out = []
    for dashboard in dashboards:
        uid = dashboard.data.get("uid")
        if isinstance(uid, str) and uid:
            out.append((f"{tree}/{dashboard.path.relative_to(pathlib.Path(provisioning) / tree)}",
                        uid))
    return out
