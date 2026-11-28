"""The lead (profile ``fleet-lead``): ``python -m fleet.lead``.

With a fleet initialized here (``smoking-pi fleet init``) and lead URLs
set, it serves the protocol over HTTPS (:mod:`fleet.service`, served by
:mod:`fleet.server`). Otherwise it says why not, writes its status and
idles, as before: no port, no network call.

States: ``no_fleet`` (nothing initialized: idle), ``state_corrupt``
(fleet.json unreadable: idle, and `fleet init` will not create a fleet over
it), ``ca_broken`` (the CA or the registry is missing or inconsistent:
idle), ``no_urls`` (FLEET_LEAD_URLS empty: nodes could not be told where
the lead is, and the server certificate would name nothing: idle),
``serving``.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time

from . import pki, protocol, runner, settings, statefile, status
from .layout import LeadPaths, lead_paths
from .protocol import valid_uuid
from .registry import (REGISTRY_PROBLEMS, Registry, RegistryError, RegistryMissing,
                       RegistryUnreadable)

log = logging.getLogger("fleet")

STATES = ("no_fleet", "state_corrupt", "ca_broken", "no_urls", "serving")

#: How often the serving lead checks its server certificate (it re-issues
#: it SERVER_RENEW_DAYS before expiry, or when FLEET_LEAD_URLS changed).
SERVER_CERT_CHECK_SECONDS = 3600


def read_fleet(paths: LeadPaths) -> dict | str:
    """fleet.json as a dict, statefile.ABSENT, or statefile.CORRUPT (also
    when it lacks a valid fleet_id)."""
    read = statefile.read_object(paths.fleet)
    if read == statefile.ABSENT:
        return read
    if read == statefile.CORRUPT or not valid_uuid(str(read.get("fleet_id", ""))):
        return statefile.CORRUPT
    return read


def open_state(paths: LeadPaths) -> tuple[pki.CA | None, Registry | None, str | None]:
    """(CA, registry, None) when both load, else (None, None, a literal
    sentence). The sentence is chosen by the exception's type from the
    modules' tables, never built from the exception object."""
    try:
        ca = pki.load(paths)
    except pki.CAMissing:
        return None, None, pki.CA_PROBLEMS[pki.CAMissing]
    except pki.CANotChained:
        return None, None, pki.CA_PROBLEMS[pki.CANotChained]
    except pki.CAKeyMismatch:
        return None, None, pki.CA_PROBLEMS[pki.CAKeyMismatch]
    except pki.CAError:
        return None, None, pki.CA_PROBLEMS[pki.RootKeyBroken]
    registry = Registry(paths.registry)
    try:
        registry.check()
    except RegistryMissing:
        return None, None, REGISTRY_PROBLEMS[RegistryMissing]
    except RegistryError:
        return None, None, REGISTRY_PROBLEMS[RegistryUnreadable]
    return ca, registry, None


def lead_facts(paths: LeadPaths, ca: pki.CA | None, registry: Registry | None) -> dict:
    """The non-secret facts `fleet status` shows about this lead."""
    out: dict = {}
    if ca is not None:
        out.update(root_sha256=ca.root_sha256, root_key_online=paths.root_key.exists(),
                   intermediate_not_after=pki.rfc3339(pki.not_after(ca.intermediate)))
    try:
        out["server_not_after"] = pki.rfc3339(pki.not_after(
            pki.load_cert(paths.server_cert.read_bytes())))
    except (OSError, ValueError):
        pass
    if registry is not None:
        try:
            out["nodes"] = registry.counts()
        except Exception:  # a locked or damaged registry must not stop the status
            out["nodes"] = None
    return out


def describe(cfg: settings.Settings, paths=None) -> tuple[dict, str]:
    paths = paths or lead_paths()
    read = read_fleet(paths)
    fleet: dict = read if isinstance(read, dict) else {}
    problems = list(cfg.problems)
    ca = registry = None
    if read == statefile.CORRUPT:
        state = "state_corrupt"
        problems.append("fleet.json is unreadable, not a JSON object, or has no valid fleet_id")
        line = ("fleet lead: the fleet file is corrupt; nothing is served, and no fleet "
                "will be created over it. Idle.")
    elif read == statefile.ABSENT:
        state = "no_fleet"
        line = (f"fleet lead: no fleet initialized (mode {cfg.lead_mode}); nothing is "
                "served: 'smoking-pi fleet init' creates one. Idle.")
    else:
        ca, registry, problem = open_state(paths)
        if problem is not None:
            state = "ca_broken"
            problems.append("lead state: " + problem)
            line = (f"fleet lead: fleet {fleet['fleet_id']} is initialized here, but "
                    + problem + "; nothing is served. Idle.")
        else:
            if not cfg.lead_urls:
                state = "no_urls"
                problems.append("FLEET_LEAD_URLS is empty: nodes need to know where the lead is")
                line = (f"fleet lead: fleet {fleet['fleet_id']} is initialized here, but "
                        "FLEET_LEAD_URLS is empty; nothing is served. Idle.")
            else:
                state = "serving"
                line = (f"fleet lead: serving fleet {fleet['fleet_id']} "
                        f"({fleet.get('name') or 'unnamed'}, mode {cfg.lead_mode}) on port "
                        f"{settings.LEAD_LISTEN_PORT} for {', '.join(cfg.lead_urls)}")
    report = status.snapshot(
        role="lead", state=state, lead_mode=cfg.lead_mode, node_id=cfg.node_id or None,
        node_name=cfg.node_name, fleet_id=fleet.get("fleet_id"),
        lead_urls=list(cfg.lead_urls), problems=problems,
        lead=dict(lead_facts(paths, ca, registry), fleet_name=fleet.get("name"))
        if fleet else None)
    return report, line


def housekeeping(report: dict, paths: LeadPaths, ca: pki.CA, lead_id: str,
                 hosts: list[str], registry: Registry, stop: threading.Event,
                 tick: float = status.TICK_SECONDS) -> None:
    """Runs in the serving worker: the status file every tick (the
    healthcheck reads it), the server certificate every hour. Never
    raises: a failed round is logged by type and retried."""
    report = dict(report, started=time.time(), protocol=protocol.PROTOCOL_MAX)
    last_cert_check = time.monotonic()
    while True:
        try:
            if time.monotonic() - last_cert_check >= SERVER_CERT_CHECK_SECONDS:
                last_cert_check = time.monotonic()
                if pki.ensure_server_cert(paths, ca, lead_id, hosts):
                    log.info("re-issued this lead's server certificate")
            status.write(dict(report, at=time.time(),
                              lead=dict(report.get("lead") or {},
                                        **lead_facts(paths, ca, registry))),
                         paths.root)
        except Exception as err:  # keep serving; the healthcheck will tell
            log.warning("housekeeping failed (%s)", type(err).__name__)
        if stop.wait(tick):
            return


def build(cfg: settings.Settings, paths: LeadPaths):
    """The WSGI service for a ``serving`` lead, and what housekeeping needs."""
    from .service import Fleet, LeadService

    fleet_file = read_fleet(paths)
    assert isinstance(fleet_file, dict)
    ca, registry, problem = open_state(paths)
    if problem is not None:
        raise RuntimeError("build() is only called for a serving lead")
    fleet = Fleet(fleet_id=fleet_file["fleet_id"], lead_id=str(fleet_file.get("lead_id", "")),
                  name=str(fleet_file.get("name") or ""))
    hosts = pki.url_hosts(cfg.lead_urls)
    if pki.ensure_server_cert(paths, ca, fleet.lead_id, hosts):
        log.info("issued this lead's server certificate for %s", ", ".join(hosts))
    service = LeadService(fleet, ca, registry, cfg.lead_urls, cfg.lead_mode)
    return service, ca, registry, fleet, hosts


def main() -> int:
    runner.configure_logging()
    os.umask(0o077)
    cfg = settings.load()
    paths = lead_paths()
    report, line = describe(cfg, paths)
    # A dedicated lead may have no node id of its own: not a problem here.
    problems = tuple(p for p in cfg.problems if not p.startswith("FLEET_NODE_ID"))
    if report["state"] != "serving":
        runner.run(report, line, problems=problems)
        return 0
    from . import server

    for problem in problems:
        log.warning("setting ignored: %s", problem)
    service, ca, registry, fleet, hosts = build(cfg, paths)
    log.info("%s (lead list epoch %d)", line, service.endpoints.epoch)

    def worker_started(_worker) -> None:
        threading.Thread(target=housekeeping, name="fleet-housekeeping", daemon=True,
                         args=(report, paths, ca, fleet.lead_id, hosts, registry,
                               threading.Event())).start()

    server.serve(service, paths, worker_started)
    return 0


if __name__ == "__main__":
    sys.exit(main())
