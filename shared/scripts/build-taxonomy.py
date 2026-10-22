#!/usr/bin/env python3
"""Check the service taxonomy's rules against external sources and propose new ones.

A maintainer tool, not a `smoking-pi` command: it fetches third-party lists
over the network, and what it produces is a report a person reads and turns
into a pull request. Nothing on a Pi runs it, and it never edits
templates/taxonomy.yaml: a relabel renames a target's service in Grafana, so
every change to a rule is a human's, in review (docs/taxonomy.md, *Refreshing
the rules*).

The sources are evidence, consulted at run time and never written to the
repository (each is off unless its flag is given):

  --adguard [FILE]  AdGuard HostlistsRegistry assets/services.json (GPL-3.0)
  --ndpi [FILE]     nDPI src/lib/ndpi_content_match.c.inc host_match (LGPL)
  --radar           Cloudflare Radar domain categories (CC BY-NC); needs
                    CLOUDFLARE_API_TOKEN in the environment

The report has three sections: (a) unlabeled targets (from --targets, the
output of `smoking-pi taxonomy list --json`) with each source's suggestion
as a rule to paste, at confidence low; (b) disagreements: shipped rules a
source no longer lists, or puts in another category (the rule's own
source; what the other sources say is listed for information); (c) stale: rules whose
`since` is older than --max-age days. --check exits 1 when (b) or (c) has
an entry, to run by hand before a release.

    python3 shared/scripts/build-taxonomy.py --adguard --ndpi --targets targets.json
    python3 shared/scripts/build-taxonomy.py --adguard --check
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TAXONOMY = ROOT / "shared" / "modules" / "config-manager" / "templates" / "taxonomy.yaml"

ADGUARD_URL = ("https://raw.githubusercontent.com/AdguardTeam/HostlistsRegistry/"
               "main/assets/services.json")
NDPI_URL = "https://raw.githubusercontent.com/ntop/nDPI/dev/src/lib/ndpi_content_match.c.inc"
RADAR_URL = "https://api.cloudflare.com/client/v4/radar/ranking/domain/{domain}"
USER_AGENT = "smoking-pi build-taxonomy (maintainer tool)"

# Each source's categories onto our vocabulary (templates/taxonomy.yaml).
# Only what maps cleanly; the rest is no suggestion. A tuple is a source
# category that holds several of ours: the first is what a proposal says,
# and a rule with any of them agrees. A test checks every value is a
# category the vocabulary has.
ADGUARD_GROUPS = {
    "streaming": ("video", "audio"),  # Netflix and Spotify alike
    "social_network": "social",
    "messenger": "messaging",
    "gaming": "gaming",
    "ai": "ai_assistant",
    "cdn": "cdn",
    "hosting": "cloud_saas",
    "software": "software_update",
}
NDPI_CATEGORIES = {
    "VIDEO": "video",
    "MEDIA": "video",
    "STREAMING": "video",
    "MUSIC": "audio",
    "SOCIAL_NETWORK": "social",
    "CHAT": "messaging",
    "VOIP": "realtime_comms",
    "GAME": "gaming",
    "CLOUD": "cloud_saas",
    "COLLABORATIVE": "cloud_saas",
    "PRODUCTIVITY": "cloud_saas",
    "MAIL": "cloud_saas",
    "SW_UPDATE": "software_update",
    "CDN_PROXY": "cdn",
    "ADVERTISEMENT": "ads_analytics",
    "ARTIFICIAL_INTELLIGENCE": "ai_assistant",
    "VIRTUAL_ASSISTANT": "ai_assistant",
    "SEARCH_ENGINE": "web_search",
    "CONNECTIVITY_CHECK": "connectivity",
}
# Cloudflare Radar content category ids.
RADAR_CATEGORIES = {
    164: "video",         # Video Streaming
    184: "ai_assistant",  # Artificial Intelligence
    100: "gaming",        # Gaming
    81: "cdn",            # Content Servers
}

HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9-]{1,63}\.)+[a-z][a-z0-9-]{1,62}$")  # not an IP
NDPI_ROW_RE = re.compile(
    r'\{\s*"(?P<host>[^"]+)"\s*,\s*"(?P<proto>[^"]+)"\s*,\s*[A-Z0-9_]+\s*,\s*'
    r"NDPI_PROTOCOL_CATEGORY_(?P<category>[A-Z0-9_]+)")
SECOND_LEVEL = {"co", "com", "net", "org", "ac", "edu", "gov", "gob"}


@dataclass(frozen=True)
class Suggestion:
    source: str        # adguard | ndpi | radar: the rule's `source`
    domain: str        # the domain the source lists
    service: str       # the source's name for the service ("" when it has none)
    category: str      # our vocabulary id, or "" when the source's does not map
    raw: str           # the source's own category, for the report
    accepts: tuple[str, ...] = ()  # every id of ours the source's category covers

    @classmethod
    def mapped(cls, source: str, domain: str, service: str, mapped, raw: str):
        ids = (mapped,) if isinstance(mapped, str) else tuple(mapped or ())
        ids = tuple(i for i in ids if i)
        return cls(source, domain, service, ids[0] if ids else "", raw, ids)


@dataclass
class Index:
    """Suggestions by the domain each source lists."""
    by_domain: dict[str, list[Suggestion]] = field(default_factory=dict)
    sources: set[str] = field(default_factory=set)

    def add(self, s: Suggestion) -> None:
        self.sources.add(s.source)
        bucket = self.by_domain.setdefault(s.domain, [])
        if s not in bucket:
            bucket.append(s)

    def lookup(self, host: str) -> list[Suggestion]:
        """The suggestions for the longest listed domain ``host`` is, or is under."""
        labels = host.lower().strip(".").split(".")
        for i in range(len(labels) - 1):
            found = self.by_domain.get(".".join(labels[i:]))
            if found:
                return found
        return []

    def exact(self, domain: str) -> list[Suggestion]:
        return self.by_domain.get(domain.lower().strip("."), [])

    def lists(self, source: str, suffix: str) -> bool:
        """Whether ``source`` lists the suffix itself or a host under it."""
        suffix = suffix.lower().strip(".")
        tail = "." + suffix
        return any(s.source == source for d, found in self.by_domain.items()
                   if d == suffix or d.endswith(tail) for s in found)


# ---------------------------------------------------------------- parsing

def _domain(text: str) -> str | None:
    text = text.lower().strip().strip(".")
    return text if HOSTNAME_RE.match(text) else None


def parse_adguard(text: str) -> list[Suggestion]:
    """services.json: each blocked service's `||domain^` rules and its group."""
    data = json.loads(text)
    out = []
    for svc in data.get("blocked_services") or []:
        if not isinstance(svc, dict):
            continue
        group = str(svc.get("group") or "")
        for rule in svc.get("rules") or []:
            m = re.fullmatch(r"\|\|([^/^$*|]+)\^", str(rule).strip())
            domain = _domain(m.group(1)) if m else None
            if domain:
                out.append(Suggestion.mapped("adguard", domain, str(svc.get("name") or ""),
                                             ADGUARD_GROUPS.get(group), group))
    return out


def parse_ndpi(text: str) -> list[Suggestion]:
    """ndpi_content_match.c.inc: the rows of the host_match[] table only."""
    start = text.find("host_match[]")
    if start < 0:
        return []
    end = text.find("};", start)
    out = []
    for m in NDPI_ROW_RE.finditer(text[start:end if end > 0 else None]):
        domain = _domain(m.group("host"))
        if domain:
            out.append(Suggestion.mapped("ndpi", domain, m.group("proto"),
                                         NDPI_CATEGORIES.get(m.group("category")),
                                         m.group("category")))
    return out


def parse_radar(domain: str, body: dict) -> list[Suggestion]:
    """A Radar ranking/domain answer: the domain's content categories."""
    result = body.get("result") if isinstance(body, dict) else None
    if not isinstance(result, dict):
        return []
    details = result.get("details_0") if isinstance(result.get("details_0"), dict) else result
    out = []
    for cat in details.get("categories") or []:
        if not isinstance(cat, dict):
            continue
        try:
            cid = int(cat.get("id"))
        except (TypeError, ValueError):
            continue
        out.append(Suggestion.mapped("radar", domain, "", RADAR_CATEGORIES.get(cid),
                                     f"{cid} {cat.get('name') or ''}".strip()))
    return out


def registrable(host: str) -> str:
    """A rough registrable domain (Radar ranks those): two labels, three
    under a second-level registry such as co.uk or com.ar."""
    labels = host.lower().strip(".").split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


# ---------------------------------------------------------------- fetching

def fetch(url: str, cache_dir: Path, name: str, headers: dict | None = None,
          opener=urllib.request.urlopen) -> str:
    """GET over HTTPS, cached under ``cache_dir/name`` (never the repository)."""
    path = cache_dir / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    if not url.startswith("https://"):
        raise ValueError("sources are fetched over HTTPS only")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with opener(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def _source_text(arg: str, url: str, cache_dir: Path, name: str, opener) -> str:
    if arg == "fetch":
        return fetch(url, cache_dir, name, opener=opener)
    return Path(arg).read_text(encoding="utf-8")


def radar_index(domains: list[str], token: str, cache_dir: Path, index: Index,
                delay: float, opener=urllib.request.urlopen, sleep=time.sleep) -> int:
    """Ask Radar for each domain, one request per ``delay`` seconds; returns
    how many were asked. A domain Radar refuses is skipped."""
    asked = 0
    for domain in domains:
        cached = (cache_dir / f"radar-{domain}.json").exists()
        try:
            text = fetch(RADAR_URL.format(domain=domain), cache_dir, f"radar-{domain}.json",
                         headers={"Authorization": f"Bearer {token}"}, opener=opener)
            body = json.loads(text)
        except (OSError, ValueError) as exc:
            print(f"radar: {domain}: skipped ({type(exc).__name__})", file=sys.stderr)
            body = {}
        for s in parse_radar(domain, body):
            index.add(s)
        index.sources.add("radar")
        if not cached:
            asked += 1
            sleep(delay)
    return asked


# ---------------------------------------------------------------- the report

def load_rules(path: Path = TAXONOMY) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_targets(path: Path) -> list[dict]:
    body = json.loads(path.read_text(encoding="utf-8"))
    targets = body.get("targets") if isinstance(body, dict) else body
    return [t for t in targets or [] if isinstance(t, dict)]


def _host(value) -> str:
    value = str(value or "").strip().lower()
    if "://" in value:
        value = value.split("://", 1)[1]
    return value.split("/", 1)[0].split(":", 1)[0] if value.count(":") <= 1 else value


def _is_unlabeled(target: dict) -> bool:
    cats = target.get("categories") or []
    return target.get("is_active", True) is not False and (
        not cats or list(cats) == ["unknown"])


def _since(rule: dict) -> dt.date | None:
    value = rule.get("since")
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        return None


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _suffixes(rule: dict) -> list[str]:
    value = (rule.get("match") or {}).get("host_suffix")
    values = value if isinstance(value, list) else [value] if value else []
    return [str(v) for v in values]


def snippet(host: str, s: Suggestion, today: dt.date) -> str:
    """A rule to paste into taxonomy.yaml, at confidence low."""
    lines = [f"  - match: {{host_suffix: [{s.domain}]}}"]
    if s.service:
        lines.append(f"    service: {json.dumps(s.service)}")
    lines += [f"    categories: [{s.category}]",
              f"    source: {s.source}",
              "    confidence: low",
              f"    since: {today.isoformat()}",
              f"    note: Proposed for {host} from {s.source} ({s.raw}); check it."]
    return "\n".join(lines)


@dataclass
class Report:
    unlabeled: list[tuple[str, str, list[Suggestion]]] = field(default_factory=list)
    disagreements: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)

    @property
    def failing(self) -> bool:
        return bool(self.disagreements or self.stale)


def build_report(rules: dict, targets: list[dict], index: Index, today: dt.date,
                 max_age: int) -> Report:
    report = Report()
    for t in sorted(targets, key=lambda t: str(t.get("name") or "")):
        if not _is_unlabeled(t):
            continue
        host = _host(t.get("host"))
        report.unlabeled.append((str(t.get("name") or ""), host,
                                 [s for s in index.lookup(host) if s.category] if host else []))

    for i, rule in enumerate(rules.get("rules") or [], 1):
        if not isinstance(rule, dict):
            continue
        cats = set(rule.get("categories") or [])
        service = str(rule.get("service") or "")
        suffixes = _suffixes(rule)
        source = rule.get("source")
        # A rule loses its evidence when its source lists none of its
        # suffixes any more; one suffix the source never had is the
        # maintainer's own addition, not a disagreement.
        if suffixes and source in index.sources and not any(
                index.lists(source, x) for x in suffixes):
            report.disagreements.append(
                f"rule {i} ({', '.join(suffixes)}, {service or 'the title'}): its source, "
                f"{source}, lists none of its hosts")
        for suffix in suffixes:
            where = f"rule {i} ({suffix}, {service or 'the title'})"
            # Only the domain itself: a source's entry for a host under it
            # (meet.google.com) or above it says nothing about this rule.
            # The rule's own source disagreeing is a disagreement; another
            # source's view is information (their categories are coarser
            # than ours: AdGuard's `streaming` holds music and video alike).
            for s in index.exact(suffix):
                own = s.source == source
                if s.accepts and not cats & set(s.accepts):
                    (report.disagreements if own else report.notes).append(
                        f"{where}: {s.source} puts {s.domain} in {s.category} ({s.raw}); "
                        f"the rule says {', '.join(sorted(cats))}")
                if s.service and service and _norm(s.service) not in _norm(service) \
                        and _norm(service) not in _norm(s.service):
                    report.notes.append(f"{where}: {s.source} calls {s.domain} {s.service!r}")
        since = _since(rule)
        if since is None or (today - since).days > max_age:
            first = next(iter((rule.get("match") or {}).values()), "?")
            report.stale.append(
                f"rule {i} ({first}): since {since or 'missing'}, older than {max_age} days")
    report.disagreements = list(dict.fromkeys(report.disagreements))
    report.notes = list(dict.fromkeys(report.notes))
    return report


def render(report: Report, sources: set[str], today: dt.date, targets_given: bool) -> str:
    out = [f"# Taxonomy refresh, {today.isoformat()}", "",
           f"Sources consulted: {', '.join(sorted(sources)) or 'none'}. "
           "Nothing here changes taxonomy.yaml: a rule worth keeping is pasted by hand, "
           "its confidence raised once checked, and reviewed in a pull request.", ""]
    out += ["## (a) Unlabeled targets", ""]
    if not targets_given:
        out.append("No --targets file given (`smoking-pi taxonomy list --json`).")
    elif not report.unlabeled:
        out.append("Every active target has a label.")
    for name, host, found in report.unlabeled:
        out.append(f"### {name} ({host or 'no host'})")
        out.append("")
        if not found:
            out.append("No source knows this host.")
        else:
            out.append("```yaml")
            out.append("\n".join(snippet(host, s, today) for s in found))
            out.append("```")
        out.append("")
    out += ["", "## (b) Disagreements", ""]
    out += [f"- {d}" for d in report.disagreements] or ["None."]
    if report.notes:
        out += ["", "Other sources, and other names (for information; not a failure):", ""]
        out += [f"- {d}" for d in report.notes]
    out += ["", "## (c) Stale rules", ""]
    out += [f"- {s}" for s in report.stale] or ["None."]
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------- main

def main(argv=None, opener=urllib.request.urlopen, sleep=time.sleep,
         today: dt.date | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="A maintainer tool, not a smoking-pi command: it needs the network and "
               "third-party sources, and its output is a pull request a person writes. "
               "See docs/taxonomy.md.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--taxonomy", type=Path, default=TAXONOMY, help=argparse.SUPPRESS)
    parser.add_argument("--targets", type=Path,
                        help="output of `smoking-pi taxonomy list --json` from an install")
    parser.add_argument("--adguard", nargs="?", const="fetch", metavar="FILE",
                        help="consult AdGuard HostlistsRegistry (a local services.json, "
                             "or fetched)")
    parser.add_argument("--ndpi", nargs="?", const="fetch", metavar="FILE",
                        help="consult nDPI's host_match (a local ndpi_content_match.c.inc, "
                             "or fetched)")
    parser.add_argument("--radar", action="store_true",
                        help="consult Cloudflare Radar (CLOUDFLARE_API_TOKEN)")
    parser.add_argument("--radar-delay", type=float, default=1.0,
                        help="seconds between Radar requests (default 1)")
    parser.add_argument("--radar-max", type=int, default=100,
                        help="most domains to ask Radar about in one run (default 100)")
    parser.add_argument("--cache-dir", type=Path,
                        help="where downloads are kept (default: a new temporary directory)")
    parser.add_argument("--max-age", type=int, default=180,
                        help="days after which a rule's `since` is stale (default 180)")
    parser.add_argument("--out", type=Path, help="also write the report here")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 when there are disagreements or stale rules")
    args = parser.parse_args(argv)
    today = today or dt.date.today()
    cache_dir = args.cache_dir or Path(tempfile.mkdtemp(prefix="build-taxonomy-"))

    rules = load_rules(args.taxonomy)
    targets = load_targets(args.targets) if args.targets else []
    index = Index()
    if args.adguard:
        for s in parse_adguard(_source_text(args.adguard, ADGUARD_URL, cache_dir,
                                            "adguard-services.json", opener)):
            index.add(s)
        index.sources.add("adguard")
    if args.ndpi:
        for s in parse_ndpi(_source_text(args.ndpi, NDPI_URL, cache_dir,
                                         "ndpi_content_match.c.inc", opener)):
            index.add(s)
        index.sources.add("ndpi")
    if args.radar:
        token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
        if not token:
            print("--radar needs CLOUDFLARE_API_TOKEN in the environment", file=sys.stderr)
            return 2
        # The unlabeled targets' domains first, then the rules' suffixes.
        first = sorted({registrable(_host(t.get("host"))) for t in targets
                        if _is_unlabeled(t) and _domain(_host(t.get("host")))})
        then = sorted({x for rule in rules.get("rules") or [] if isinstance(rule, dict)
                       for x in _suffixes(rule)})
        wanted = [d for d in dict.fromkeys(first + then) if _domain(d)][:args.radar_max]
        radar_index(wanted, token, cache_dir, index, args.radar_delay, opener, sleep)

    report = build_report(rules, targets, index, today, args.max_age)
    text = render(report, index.sources, today, bool(args.targets))
    sys.stdout.write(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    if args.check and report.failing:
        print(f"check: {len(report.disagreements)} disagreement(s), "
              f"{len(report.stale)} stale rule(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
