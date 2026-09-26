"""Adopt the DNS wizard's selection as measurement targets.

The DNS observer's wizard (dns-observer/wizard.py) picks which services the
house uses to measure: K comes from the data (coverage of the activity plus
one representative per network and CDN), not a constant. This module turns
that selection into targets in the ``dns_wizard`` category, each service
measured with the whole suite:

  ``_icmp``  FPing (the existing probe: fping measures hundreds of hosts at
             once);
  ``_tcp``   TCPPing, port 443 (the existing probe);
  ``_h1``, ``_h2``, ``_h3``  HTTP/1.1, /2 and /3, on the wizard's own Curl
             sub-probes (WizardHTTP1/2/3). They are copies of CurlHTTP1/2/3
             with more parallelism (forks 20) and a 5 s timeout, so ~50
             services fit a 300 s round even in the worst case (every request
             timing out: 3 batches x 5 pings x 5 s = 75 s), without touching
             the probes of the curated targets.

**Add only.** A service adopted once stays: nothing here removes or
changes a target. Until the design's churn thresholds come from a week of
data (tools/dns-explore), a stable list beats a moving one; removals with
hysteresis come later. The total is capped (DNS_WIZARD_MAX services).

Everything happens in one transaction and one regeneration, not one
SmokePing reload per target.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

CATEGORY = "dns_wizard"
CATEGORY_DISPLAY = "DNS wizard"
SNAPSHOT = Path(os.environ.get("DNS_WIZARD_FILE", "/dns-observer/wizard.json"))
# A snapshot older than this means the observer stopped: do not adopt from it.
MAX_AGE_S = 24 * 3600

# name suffix -> (probe to use, label for the title)
SUITE = (
    ("icmp", "FPing", "ICMP"),
    ("tcp", "TCPPing", "TCP 443"),
    ("h1", "WizardHTTP1", "HTTP/1.1"),
    ("h2", "WizardHTTP2", "HTTP/2"),
    ("h3", "WizardHTTP3", "HTTP/3"),
)
# The wizard's HTTP probes: the curated one each copies, and what changes.
WIZARD_PROBES = {"WizardHTTP1": "CurlHTTP1", "WizardHTTP2": "CurlHTTP2",
                 "WizardHTTP3": "CurlHTTP3"}
WIZARD_FORKS = 20
WIZARD_TIMEOUT = 5
NAME_MAX = 30  # what the web admin accepts for a target name


# Why adoption cannot go ahead, by code. The API answers with these fixed
# texts, never with an exception's (error-response contract, api.py).
UNAVAILABLE = {
    "no_snapshot": "no DNS wizard snapshot: is the DNS observer enabled (smoking-pi dns enable)?",
    "bad_snapshot": "the DNS wizard snapshot is not valid JSON",
    "stale_snapshot": "the DNS wizard snapshot is over 24 h old: the observer is not running",
    "no_selection": "the DNS wizard has not selected any service yet",
    "missing_probe": "a probe the suite needs (FPing, TCPPing or CurlHTTP1/2/3) is missing",
}


class Unavailable(Exception):
    """Adoption cannot go ahead; ``code`` is a key of UNAVAILABLE."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def target_base(service: str) -> str:
    """A stable, SmokePing-safe name stem for a service.

    ``W_`` + the service with dots as underscores, cut so the longest suffix
    still fits NAME_MAX; a cut name gets a short hash so two long services
    never collide. Stable across runs: a returning service keeps its RRDs.

    >>> target_base("netflix.com")
    'W_netflix_com'
    """
    stem = re.sub(r"[^A-Za-z0-9]", "_", service)
    room = NAME_MAX - len("W_") - len("_icmp")
    if len(stem) > room:
        digest = hashlib.sha1(service.encode()).hexdigest()[:4]
        stem = stem[: room - 5] + "_" + digest
    return "W_" + stem


def read_snapshot(path: Path | None = None, now: float | None = None) -> dict:
    """The wizard's snapshot, refusing a missing, broken or stale one."""
    path = path or SNAPSHOT
    try:
        snap = json.loads(path.read_text())
    except OSError:
        raise Unavailable("no_snapshot") from None
    except ValueError:
        raise Unavailable("bad_snapshot") from None
    if (now or time.time()) - float(snap.get("generated", 0)) > MAX_AGE_S:
        raise Unavailable("stale_snapshot")
    if not (snap.get("selection") or {}).get("services"):
        raise Unavailable("no_selection")
    return snap


def ensure_probes(session, probe_model) -> dict[str, object]:
    """The wizard's HTTP probes, created from the curated ones if missing.

    Returns every probe the suite needs, by name. Raises LookupError when a
    curated probe to copy (or FPing/TCPPing) is missing.
    """
    by_name = {p.name: p for p in session.query(probe_model).all()}
    for new, source in WIZARD_PROBES.items():
        if new in by_name:
            continue
        src = by_name.get(source)
        if src is None:
            logger.error("probe %s is missing: cannot derive %s", source, new)
            raise Unavailable("missing_probe")
        options = dict(src.options or {})
        options["timeout"] = WIZARD_TIMEOUT
        probe = probe_model(
            name=new, binary_path=src.binary_path, step_seconds=src.step_seconds,
            pings=src.pings, forks=WIZARD_FORKS, is_default=False,
            module=src.module, options=options,
        )
        session.add(probe)
        by_name[new] = probe
    session.flush()
    needed = {probe for _, probe, _ in SUITE}
    missing = sorted(needed - by_name.keys())
    if missing:
        logger.error("probes missing: %s", ", ".join(missing))
        raise Unavailable("missing_probe")
    return {name: by_name[name] for name in needed}


def ensure_category(session, category_model):
    cat = session.query(category_model).filter_by(name=CATEGORY).first()
    if cat is None:
        cat = category_model(
            name=CATEGORY, display_name=CATEGORY_DISPLAY,
            description="Services the house uses, picked by the DNS wizard",
        )
        session.add(cat)
        session.flush()
    return cat


def plan(snapshot: dict, existing_names: set[str], adopted: set[str],
         max_services: int) -> tuple[list[dict], list[str]]:
    """Which targets to create (add only), and which services are left out
    because the cap is reached. ``adopted`` holds the name stems
    (target_base) of the services already in the category."""
    to_create: list[dict] = []
    over_cap: list[str] = []
    stems = set(adopted)
    for s in snapshot["selection"]["services"]:
        base = target_base(s["service"])
        if base not in stems:
            if len(stems) >= max_services:
                over_cap.append(s["service"])
                continue
            stems.add(base)
        for suffix, probe, label in SUITE:
            name = f"{base}_{suffix}"
            if name in existing_names:
                continue
            to_create.append({
                "name": name, "host": s["host"], "probe": probe,
                "title": f"{s['service']} ({label})",
                "service": s["service"], "reason": s.get("reason", ""),
            })
    return to_create, over_cap


def adopt(session, models, snapshot: dict, *, max_services: int, dry_run: bool = False) -> dict:
    """Create the missing targets for the wizard's selection.

    ``models`` is (Target, TargetCategory, Probe). Commits unless ``dry_run``.
    """
    target_model, category_model, probe_model = models
    existing = {t.name: t for t in session.query(target_model).all()}
    cat = session.query(category_model).filter_by(name=CATEGORY).first()
    adopted = set()
    if cat is not None:
        adopted = {t.name.rsplit("_", 1)[0] for t in existing.values() if t.category_id == cat.id}
    to_create, over_cap = plan(snapshot, set(existing), adopted, max_services)
    result = {
        "selected": len(snapshot["selection"]["services"]),
        "already_adopted": len(adopted),
        "services_added": len({target_base(t["service"]) for t in to_create} - adopted),
        "targets_added": len(to_create),
        "over_cap": over_cap,
        "dry_run": dry_run,
        "targets": [{k: t[k] for k in ("name", "host", "probe", "reason")} for t in to_create],
    }
    if dry_run or not to_create:
        session.rollback()
        return result
    cat = ensure_category(session, category_model)
    probes = ensure_probes(session, probe_model)
    for t in to_create:
        session.add(target_model(
            name=t["name"], host=t["host"], title=t["title"],
            category_id=cat.id, probe_id=probes[t["probe"]].id, is_active=True,
        ))
    session.commit()
    return result


def main(argv: list[str]) -> int:
    """``python wizard_adopt.py [--dry-run]``: ask the running API to adopt.

    Runs inside the config-manager container (smoking-pi dns adopt), so the
    API token comes from the container's own environment.
    """
    import urllib.error
    import urllib.request

    dry = "--dry-run" in argv
    url = "http://127.0.0.1:5000/wizard/adopt" + ("?dry_run=1" if dry else "")
    req = urllib.request.Request(url, method="POST", data=b"{}",
                                 headers={"Content-Type": "application/json"})
    token = os.environ.get("CONFIG_API_TOKEN", "")
    if token:
        req.add_header("X-API-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read() or b"{}")
        except ValueError:
            body = {}
        print(f"refused: {body.get('error', exc.reason)}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as exc:
        print(f"the config-manager API did not answer: {type(exc).__name__}", file=sys.stderr)
        return 1
    verb = "Would add" if dry else "Added"
    print(f"{verb} {body['targets_added']} targets for {body['services_added']} services "
          f"(selected now: {body['selected']}; already measured: {body['already_adopted']}).")
    if body.get("over_cap"):
        print(f"Left out, cap reached (DNS_WIZARD_MAX): {', '.join(body['over_cap'])}")
    for t in body.get("targets", [])[:15]:
        print(f"  {t['name']:<30} {t['host']:<40} {t['probe']:<12} {t['reason']}")
    if len(body.get("targets", [])) > 15:
        print(f"  ... and {len(body['targets']) - 15} more")
    if not dry and body.get("reloaded") is False:
        print("Saved, but SmokePing did not confirm the reload: smoking-pi restart smokeping")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
