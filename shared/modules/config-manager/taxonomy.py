"""Service taxonomy: what kind of service a target is, and who runs it.

A target's ``category`` is a technical probe bucket (which SmokePing section,
which InfluxDB tag). It never said what the service is, so the Quality
dashboard, the DNS wizard and the assistant could only call a target
"topsites". The labels resolved here live beside it: a service name and one
or more categories from a vocabulary of our own (templates/taxonomy.yaml),
each with the rule that produced it, the evidence behind the rule and a
confidence. A per-install override (the ``target_labels`` table) replaces
the resolved label entirely. See docs/taxonomy.md.

Everything above ``main()`` is a pure function of the loaded file. The CLI
(``python taxonomy.py list|unknown|set|reset``) runs inside the container
for ``smoking-pi taxonomy`` and asks the running API, like budget.py.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import functools
import ipaddress
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

TAXONOMY_FILE = Path(__file__).resolve().parent / "templates" / "taxonomy.yaml"

UNKNOWN = "unknown"
MATCH_KEYS = ("name", "host_suffix", "category")
SOURCES = ("manual", "adguard", "ndpi", "radar")
CONFIDENCES = ("high", "medium", "low")

# The probe suffix a target's variants carry (Google_h2, Google_tcp443,
# W_netflix_com_icmp). The same pattern as common/links.py PROBE_SUFFIX_RE:
# config-manager's image does not carry shared/modules/common.
PROBE_SUFFIX_RE = re.compile(r"(_h[123]|_tcp(443)?|_icmp)$")

# Longest a service name or a note may be (target_labels columns).
SERVICE_MAX = 100
NOTE_MAX = 500


class TaxonomyError(ValueError):
    """The taxonomy file is malformed; ``problems`` lists every reason."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def base_name(name: str) -> str:
    """Google_h2 -> Google, Google_tcp443 -> Google, uba_ar -> uba_ar."""
    return PROBE_SUFFIX_RE.sub("", name or "")


def _strings(value: Any) -> list[str] | None:
    """A match value as a list of non-empty strings, or None if it is not one."""
    items = value if isinstance(value, list) else [value]
    if not items or not all(isinstance(v, str) and v.strip() for v in items):
        return None
    return [v.strip() for v in items]


def validate(data: Any) -> list[str]:
    """Every problem with a parsed taxonomy file; empty means valid."""
    if not isinstance(data, dict):
        return ["the file is not a mapping"]
    problems: list[str] = []
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        problems.append("version must be a positive integer")

    categories = data.get("categories")
    if not isinstance(categories, dict) or not categories:
        problems.append("categories must be a non-empty mapping")
        categories = {}
    for cid, body in categories.items():
        if not isinstance(cid, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", cid):
            problems.append(f"category id {cid!r} must be lower_snake_case")
        if not isinstance(body, dict) or not all(
                isinstance(body.get(k), str) and body.get(k).strip()
                for k in ("label", "description")):
            problems.append(f"category {cid}: needs a label and a description")
    if categories and UNKNOWN not in categories:
        problems.append("categories must include unknown")

    rules = data.get("rules")
    if not isinstance(rules, list):
        problems.append("rules must be a list")
        rules = []
    seen: dict[tuple[str, str], int] = {}
    for i, rule in enumerate(rules, 1):
        where = f"rule {i}"
        if not isinstance(rule, dict):
            problems.append(f"{where}: not a mapping")
            continue
        match = rule.get("match")
        if not isinstance(match, dict) or len(match) != 1:
            problems.append(f"{where}: match must have exactly one key")
        else:
            (key, value), = match.items()
            values = _strings(value)
            if key not in MATCH_KEYS:
                problems.append(f"{where}: match key {key!r} is not one of "
                                f"{', '.join(MATCH_KEYS)}")
            elif values is None:
                problems.append(f"{where}: match {key} must be a non-empty "
                                "string or list of strings")
            else:
                where = f"rule {i} ({key}: {values[0]})"
                for v in values:
                    k = (key, v.lower() if key == "host_suffix" else v)
                    if k in seen:
                        problems.append(f"{where}: {key} {v} is already "
                                        f"matched by rule {seen[k]}")
                    seen[k] = i
        cats = rule.get("categories")
        if (not isinstance(cats, list) or not cats
                or not all(isinstance(c, str) for c in cats)):
            problems.append(f"{where}: categories must be a non-empty list")
        else:
            for c in cats:
                if c not in categories:
                    problems.append(f"{where}: category {c} is not in the vocabulary")
                elif c == UNKNOWN:
                    problems.append(f"{where}: a rule cannot assign unknown")
            if len(set(cats)) != len(cats):
                problems.append(f"{where}: categories repeat")
        service = rule.get("service")
        if service is not None and (not isinstance(service, str)
                                    or not service.strip()):
            problems.append(f"{where}: service must be a non-empty string")
        if rule.get("source") not in SOURCES:
            problems.append(f"{where}: source must be one of {', '.join(SOURCES)}")
        if rule.get("confidence") not in CONFIDENCES:
            problems.append(f"{where}: confidence must be one of "
                            f"{', '.join(CONFIDENCES)}")
        since = rule.get("since")
        if not isinstance(since, _dt.date):
            try:
                _dt.date.fromisoformat(str(since))
            except ValueError:
                problems.append(f"{where}: since must be a date (YYYY-MM-DD)")
        note = rule.get("note")
        if note is not None and not isinstance(note, str):
            problems.append(f"{where}: note must be a string")
    return problems


def parse(text: str) -> dict:
    """Parse and validate taxonomy YAML text; raises TaxonomyError."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        raise TaxonomyError(["the file is not valid YAML"]) from None
    problems = validate(data)
    if problems:
        raise TaxonomyError(problems)
    return data


def load(path: Path | str = TAXONOMY_FILE) -> "Taxonomy":
    """Read, validate and index a taxonomy file (see ``Taxonomy``)."""
    return Taxonomy(parse(Path(path).read_text()))


@functools.lru_cache(maxsize=1)
def shipped() -> "Taxonomy":
    """The image's taxonomy, read once per process."""
    return load(TAXONOMY_FILE)


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


class Taxonomy:
    """A validated taxonomy with its rules indexed by match key."""

    def __init__(self, data: dict):
        self.version: int = data["version"]
        self.categories: dict[str, dict] = data["categories"]
        self.rules: list[dict] = data["rules"]
        self.by_name: dict[str, dict] = {}
        self.by_suffix: dict[str, dict] = {}
        self.by_category: dict[str, dict] = {}
        index = {"name": self.by_name, "host_suffix": self.by_suffix,
                 "category": self.by_category}
        for rule in self.rules:
            (key, value), = rule["match"].items()
            for v in _strings(value):
                index[key][v.lower() if key == "host_suffix" else v] = rule

    def category_ids(self) -> list[str]:
        """Every assignable category (the vocabulary without unknown)."""
        return [c for c in self.categories if c != UNKNOWN]

    def describe(self) -> dict:
        """The vocabulary as the API serves it."""
        return {
            "version": self.version,
            "categories": [
                {"id": cid, "label": body["label"],
                 "description": body["description"]}
                for cid, body in self.categories.items()
            ],
            "rules": len(self.rules),
        }

    def _host_rule(self, host: str | None) -> tuple[str, dict] | None:
        host = (host or "").strip().lower().rstrip(".")
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        if not host:
            return None
        if _is_ip(host):
            # An address is only ever itself: 1.1.1.1 must not claim 11.1.1.1.
            rule = self.by_suffix.get(host)
            return (host, rule) if rule else None
        labels = host.split(".")
        for i in range(len(labels)):  # longest suffix first
            suffix = ".".join(labels[i:])
            rule = self.by_suffix.get(suffix)
            if rule:
                return suffix, rule
        return None

    def resolve(self, name: str | None, host: str | None,
                category: str | None, title: str | None = None) -> dict:
        """The label for one target: name > host suffix > category bucket.

        ``service`` falls back to the target's title (then its name) when
        the rule names none, as the bucket rules for resolvers do.
        """
        hit: tuple[str, dict] | None = None
        for candidate in (name, base_name(name or "")):
            if candidate and candidate in self.by_name:
                hit = (f"name:{candidate}", self.by_name[candidate])
                break
        if hit is None:
            found = self._host_rule(host)
            if found:
                hit = (f"host_suffix:{found[0]}", found[1])
        if hit is None and category and category in self.by_category:
            hit = (f"category:{category}", self.by_category[category])
        if hit is None:
            return {"service": title or name, "categories": [UNKNOWN],
                    "source": None, "confidence": None, "rule": None,
                    "note": None}
        rule_id, rule = hit
        return {
            "service": rule.get("service") or title or name,
            "categories": list(rule["categories"]),
            "source": rule["source"],
            "confidence": rule["confidence"],
            "rule": rule_id,
            "note": rule.get("note"),
        }

    def validate_override(self, body: Any) -> tuple[dict | None, list[str]]:
        """A cleaned override from a request body, or the literal reasons not."""
        if not isinstance(body, dict):
            return None, ["the body must be a JSON object"]
        reasons: list[str] = []
        service = body.get("service")
        if not isinstance(service, str) or not service.strip():
            reasons.append("service is required")
        elif len(service.strip()) > SERVICE_MAX:
            reasons.append("service must be at most 100 characters")
        cats = body.get("categories")
        if (not isinstance(cats, list) or not cats
                or not all(isinstance(c, str) for c in cats)):
            reasons.append("categories must be a non-empty list of category ids")
            cats = []
        else:
            valid = set(self.category_ids())
            if any(c not in valid for c in cats):
                reasons.append("categories must come from GET /taxonomy "
                               "(and cannot be unknown)")
        note = body.get("note")
        if note is not None and not isinstance(note, str):
            reasons.append("note must be a string")
        elif note and len(note) > NOTE_MAX:
            reasons.append("note must be at most 500 characters")
        unexpected = set(body) - {"service", "categories", "note"}
        if unexpected:
            reasons.append("only service, categories and note can be set")
        if reasons:
            return None, reasons
        return {
            "service": service.strip(),
            "categories": list(dict.fromkeys(cats)),
            "note": (note or "").strip() or None,
        }, []


def label_fields(resolved: dict, override: dict | None = None) -> dict:
    """The label fields a target carries in API responses.

    An override replaces the resolved label entirely and is reported as
    ``source: manual``, ``rule: override``.
    """
    if override is not None:
        return {
            "service": override["service"],
            "categories": list(override["categories"]),
            "label_source": "manual",
            "label_confidence": "high",
            "label_rule": "override",
            "label_note": override.get("note"),
        }
    return {
        "service": resolved["service"],
        "categories": resolved["categories"],
        "label_source": resolved["source"],
        "label_confidence": resolved["confidence"],
        "label_rule": resolved["rule"],
        "label_note": resolved["note"],
    }


def is_unknown(fields: dict) -> bool:
    return fields.get("categories") == [UNKNOWN]


# --- CLI: smoking-pi taxonomy -----------------------------------------------

API = "http://127.0.0.1:5000"


def _call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """One request to the running API; (status, body). Status 0: no answer."""
    import urllib.error
    import urllib.request

    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    token = os.environ.get("CONFIG_API_TOKEN", "")
    if token:
        req.add_header("X-API-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"the config-manager API did not answer: {type(exc).__name__}",
              file=sys.stderr)
        return 0, {}


def _refused(status: int, body: dict) -> int:
    if status:
        print(f"refused ({status}): {body.get('error', 'no reason given')}",
              file=sys.stderr)
        for reason in body.get("reasons") or []:
            print(f"  - {reason}", file=sys.stderr)
    return 1


def _table(targets: list[dict]) -> str:
    rows = [("TARGET", "SERVICE", "CATEGORIES", "SOURCE", "RULE")]
    for t in targets:
        rows.append((t["name"], t.get("service") or "-",
                     ",".join(t.get("categories") or []),
                     t.get("label_source") or "-", t.get("label_rule") or "-"))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]) - 1)]
    return "\n".join(
        "  ".join(c.ljust(w) for c, w in zip(r, widths)) + "  " + r[-1]
        for r in rows)


def _find(targets: list[dict], name: str) -> dict | None:
    return next((t for t in targets if t["name"] == name), None)


def main(argv: list[str] | None = None) -> int:
    """``python taxonomy.py list [--json] | unknown [--json] | set | reset``."""
    parser = argparse.ArgumentParser(
        prog="taxonomy", description="What kind of service each target is.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_list = sub.add_parser("list", help="every active target's label")
    p_list.add_argument("--json", action="store_true")
    p_unknown = sub.add_parser("unknown", help="active targets no rule labels")
    p_unknown.add_argument("--json", action="store_true")
    p_set = sub.add_parser("set", help="override one target's label")
    p_set.add_argument("target")
    p_set.add_argument("service")
    p_set.add_argument("categories", help="comma-separated category ids")
    p_set.add_argument("--note")
    p_reset = sub.add_parser("reset", help="drop a target's override")
    p_reset.add_argument("target")
    args = parser.parse_args(argv)

    path = "/taxonomy/unknown" if args.command == "unknown" else "/taxonomy/targets"
    status, body = _call("GET", path)
    if status != 200:
        return _refused(status, body)
    targets = body.get("targets") or []

    if args.command in ("list", "unknown"):
        if args.json:
            print(json.dumps(body, indent=2))
        elif not targets:
            print("Every active target has a label." if args.command == "unknown"
                  else "No active targets.")
        else:
            print(f"taxonomy v{body.get('version')}")
            print(_table(targets))
        return 0

    target = _find(targets, args.target)
    if target is None or target.get("id") is None:
        print(f"no target named {args.target} in the database "
              "(overrides need database mode)", file=sys.stderr)
        return 1
    if args.command == "set":
        payload = {"service": args.service,
                   "categories": [c.strip() for c in args.categories.split(",")
                                  if c.strip()]}
        if args.note is not None:
            payload["note"] = args.note
        status, body = _call("PUT", f"/targets/{target['id']}/labels", payload)
    else:
        status, body = _call("DELETE", f"/targets/{target['id']}/labels")
    if status != 200:
        return _refused(status, body)
    t = body.get("target") or {}
    print(f"{t.get('name')}: {t.get('service')} "
          f"[{','.join(t.get('categories') or [])}] ({t.get('label_source')}, "
          f"{t.get('label_rule')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
