"""Live checks — the ones that need the running host.

The static checks compare one file against another and run in CI. These
three ask the machine itself: two compare what is *deployed* against what
the repository says, and the third asks the kernel which interface the
measurements leave by.

All three exist because the corresponding failure happened here, and they
share a shape that makes them worth automating: **the broken thing keeps
looking healthy.** A container running three-week-old code starts, logs
cleanly and answers requests. A container holding a dead resolver pings raw
IPs happily and only fails on hostnames. A Pi measuring through a Wi-Fi hop
nobody knew about draws exactly the same graphs. Nothing goes red, so nobody
looks.

- ``deployed-code-current`` — the running container's Python matches the
  repository. This is commit ``dde5e36`` ("the flap fix never reached the
  deployed container"), and it recurred: an image failed to rebuild, the
  failure was masked by a shell pipeline's exit code, ``docker compose up -d``
  recreated the container from the stale image, and everything reported
  success while the fix sat only on disk.

- ``uplink-interface`` — the interface the measurements actually leave by is
  named, and is a real one. Every latency figure this stack records crosses
  the host's uplink, and nothing said which it was: the reference Pi spent a
  year measuring *through Wi-Fi* with `eth0` dark before anyone noticed. It
  warns when the default route sits on a tunnel or a Docker bridge, because
  then the numbers describe that tunnel and the Wi-Fi verdict — which needs
  the wireless interface to *be* the uplink — goes quiet without saying so.

- ``container-dns-fresh`` — a container's resolver still matches the host's.
  Docker writes ``/etc/resolv.conf`` **once, at container creation**. A
  container created while a VPN was up freezes that VPN's resolver, which dies
  silently when the VPN goes away. This cost nine of eighteen targets for ten
  days: every hostname target read 100% loss, every raw-IP target was fine,
  and "100% loss" is indistinguishable from "the target is down".

Docker is invoked through an injected runner so these are testable without a
daemon, and the two Docker checks SKIP rather than fail when Docker is
unavailable — running the doctor on a laptop must not report a broken
deployment. ``uplink-interface`` is not part of that guarantee: it asks the
kernel, not Docker, so it answers on any Linux host and skips only where
``/proc/net`` is absent.
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import time

from . import dashboard_queries, sources
from .report import CheckResult, Finding, Status, result, skipped

# module directory in the repo -> its docker-compose SERVICE name. Only
# modules whose image copies source in; a bind-mounted service cannot drift
# this way.
#
# Service, not container name, deliberately. Only `mcp-server` sets an
# explicit `container_name`; the alerter's is `<project>-alerter-1`, where
# the project defaults to the directory but is overridable by
# COMPOSE_PROJECT_NAME or `-p`. Hardcoding `pro-alerter-1` meant that any
# non-default project name made this check quietly report "nothing is
# running" while the alerter was up -- a drift check that silently stops
# checking, which is worse than not having it.
# module directory -> (compose service, path of the module's .py files in the
# container, whether the image also carries shared/modules/common at
# <path>/common). The exporters are baked into the smokeping image at
# /exporters (packaged mode runs that copy; from a clone the checkout is
# bind-mounted over it, so the comparison is trivially true there).
DEPLOYED_MODULES = {
    "alerter": ("alerter", "/app", True),
    "dns-observer": ("dns-observer", "/app", False),
    "inference": ("inference", "/app", True),
    "mdns": ("mdns", "/app", False),
    "netmeter": ("netmeter", "/app", False),
    "mcp-server": ("mcp-server", "/app", True),
    "smokeping-exporters": ("smokeping", "/exporters", False),
}

# Compose stamps this on every container it creates.
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"

# Shared package copied into those images alongside the module's own source.
COMMON_DIR = "common"


class Docker:
    """Thin wrapper so the checks can be tested without a daemon."""

    def __init__(self, binary: str = "docker", timeout: int = 30):
        self.binary = binary
        self.timeout = timeout

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def run(self, args: list[str]) -> tuple[int, str]:
        """Return (returncode, stdout). Never raises."""
        try:
            proc = subprocess.run(
                [self.binary, *args],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
            return proc.returncode, proc.stdout
        except (OSError, subprocess.SubprocessError):
            return 1, ""

    def service_for_container(self, container: str) -> str | None:
        """The compose service a container belongs to, or None if not compose.

        Needed to phrase a remediation an operator can paste: `docker compose
        up` takes SERVICE names, so suggesting the container name fails with
        "no such service" on every container whose name differs from its
        service -- which is most of them.
        """
        code, out = self.run(
            [
                "inspect",
                "-f",
                f'{{{{index .Config.Labels "{COMPOSE_SERVICE_LABEL}"}}}}',
                container,
            ]
        )
        if code != 0:
            return None
        name = out.strip()
        return name if name and name != "<no value>" else None

    def container_for_service(self, service: str) -> str | None:
        """The running container for a compose service, whatever it is named.

        Filtering on the compose label rather than guessing
        ``<project>-<service>-1`` keeps this working under any project name.
        """
        code, out = self.run(
            [
                "ps",
                "--filter",
                f"label={COMPOSE_SERVICE_LABEL}={service}",
                "--format",
                "{{.Names}}",
            ]
        )
        if code != 0:
            return None
        names = [n for n in out.split() if n]
        return names[0] if names else None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _repo_py_files(directory: pathlib.Path) -> dict[str, pathlib.Path]:
    """Top-level .py files, by name. Tests and caches are not deployed."""
    if not directory.is_dir():
        return {}
    return {
        p.name: p
        for p in sorted(directory.glob("*.py"))
        if not p.name.startswith("test_")
    }


def _container_hashes(docker: Docker, container: str, path: str) -> dict[str, str]:
    """sha256 of every .py directly under `path` inside the container.

    Uses sha256sum from the image (present in the python:slim base of the
    Python modules and in the Alpine base of the smokeping image). A missing
    tool yields {}, which the caller reports as "could not verify" rather than
    as drift -- claiming drift we did not measure would be its own version of
    the bug these checks exist to catch.
    """
    code, out = docker.run(
        [
            "exec",
            container,
            "sh",
            "-c",
            f"cd {path} 2>/dev/null && sha256sum *.py 2>/dev/null",
        ]
    )
    if code != 0 or not out.strip():
        return {}
    hashes: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            digest, name = parts[0], parts[1].lstrip("*")
            hashes[name] = digest
    return hashes


def check_deployed_code_current(
    repo, docker: Docker | None = None
) -> CheckResult:
    """Every deployed module's Python matches the repository.

    A stale image is invisible: the container starts, logs cleanly, serves
    requests, and runs code nobody has looked at in weeks.
    """
    docker = docker or Docker()
    if not docker.available():
        return skipped("deployed-code-current", "docker not on PATH")

    # Probe the daemon once, so "docker ps failed" cannot masquerade as
    # "nothing is running". `docker` being on PATH says nothing about the
    # daemon being reachable or the user being in the docker group, and
    # reporting a clean SKIP in that case hides an inability to verify --
    # which is the failure this check exists to catch, wearing a disguise.
    probe_code, _ = docker.run(["ps", "--format", "{{.Names}}"])
    if probe_code != 0:
        return skipped(
            "deployed-code-current",
            "docker ps failed (daemon down, or no permission) — cannot verify",
        )

    findings: list[Finding] = []
    compared = 0
    checked_containers = 0

    for module, (service, code_path, has_common) in sorted(DEPLOYED_MODULES.items()):
        module_dir = repo.root / "shared/modules" / module
        if not module_dir.is_dir():
            continue
        container = docker.container_for_service(service)
        if not container:
            # Not running is not drift. A profile that is switched off is a
            # deployment choice, not a fault.
            continue
        checked_containers += 1

        comparisons = [(module, module_dir, code_path)]
        if has_common:
            comparisons.append(
                (f"{module}:{COMMON_DIR}",
                 repo.root / "shared/modules" / COMMON_DIR,
                 f"{code_path}/{COMMON_DIR}")
            )
        for label, source_dir, container_path in comparisons:
            repo_files = _repo_py_files(source_dir)
            if not repo_files:
                continue
            deployed = _container_hashes(docker, container, container_path)
            if not deployed:
                findings.append(
                    Finding(
                        f"could not read {container_path} in {container} — "
                        f"cannot verify {label} is current",
                        where=container,
                    )
                )
                continue

            for name, path in repo_files.items():
                compared += 1
                want = _sha256(path.read_bytes())
                got = deployed.get(name)
                if got is None:
                    findings.append(
                        Finding(
                            f"{name} exists in the repo but not in "
                            f"{container_path} — the image predates it; "
                            f"rebuild: docker compose build {service}",
                            where=f"{container}:{container_path}/{name}",
                        )
                    )
                elif got != want:
                    findings.append(
                        Finding(
                            f"{name} differs from the repository — the "
                            f"container is running older code; rebuild: "
                            f"docker compose build {service}",
                            where=f"{container}:{container_path}/{name}",
                        )
                    )

    if checked_containers == 0:
        return skipped(
            "deployed-code-current",
            "no deployed module containers are running",
        )
    return result(
        "deployed-code-current",
        findings,
        f"{compared} deployed files match the repository",
    )


def _nameservers(text: str) -> list[str]:
    return [
        line.split()[1]
        for line in text.splitlines()
        if line.strip().startswith("nameserver") and len(line.split()) > 1
    ]


def _recreate_hint(docker: Docker, container: str) -> str:
    """A recreate command that will actually run for this container.

    `docker compose up` takes SERVICE names, so the container name is the
    wrong argument for every container whose name differs from its service --
    which, since only one service here sets `container_name`, is most of them.
    Non-compose containers get the plain docker form instead.
    """
    service = docker.service_for_container(container)
    if service:
        return f"docker compose up -d --force-recreate --no-deps {service}"
    return f"docker rm -f {container} && recreate it however you start it"


def _is_loopback(address: str) -> bool:
    """Docker's embedded resolver, and any other loopback nameserver.

    Every container on a user-defined bridge network gets ``127.0.0.11``,
    Docker's own DNS, which forwards to whatever the daemon currently
    resolves with -- so it is always fresh by construction and never the
    stale-snapshot failure this check hunts. Flagging it would fire on every
    healthy compose deployment, which is how a check gets ignored.
    """
    return address.startswith("127.") or address in ("::1", "0:0:0:0:0:0:0:1")


def check_container_dns_fresh(
    repo, docker: Docker | None = None, host_resolv: pathlib.Path | None = None
) -> CheckResult:
    """No running container is holding a resolver the host has abandoned.

    Docker writes a container's /etc/resolv.conf once, at creation. A
    container created while a VPN was up keeps that VPN's resolver forever,
    and it dies silently when the VPN goes away -- hostname targets read 100%
    loss while raw-IP targets stay perfectly healthy, which reads as an
    outage rather than a DNS fault.

    Only flags a resolver the host does NOT have, MINUS the ones Compose
    pins deliberately via `dns:`. That subtraction matters more than it
    looks: the smokeping service pins public resolvers on purpose, precisely
    so it cannot inherit a resolver that later evaporates. Without it this
    check warns about that pin on every single run, and a warning that is
    always present is one you stop reading — which would cost exactly the
    10-day silent outage it exists to catch.

    A resolver that is pinned but absent from the host is therefore fine. One
    that is neither pinned nor the host's is the real signal: nobody
    configured it and the host has moved on. Still a warning rather than a
    failure, because an unusual resolver may yet be correct.
    """
    docker = docker or Docker()
    if not docker.available():
        return skipped("container-dns-fresh", "docker not on PATH")

    host_path = host_resolv or pathlib.Path("/etc/resolv.conf")
    try:
        # Loopback stubs are dropped from the HOST side too, not just the
        # container side. On a systemd-resolved host /etc/resolv.conf is just
        # `nameserver 127.0.0.53`, and Docker never hands that to a container
        # -- it substitutes the real upstreams. Comparing against the stub
        # would therefore mark every container stale on the most common Linux
        # configuration there is, which is a check nobody would keep enabled.
        host_ns = {
            ns for ns in _nameservers(host_path.read_text())
            if not _is_loopback(ns)
        }
    except OSError:
        return skipped("container-dns-fresh", f"cannot read {host_path}")
    if not host_ns:
        # Only a stub, or nothing: there is no upstream to compare against,
        # so any answer here would be invented.
        return skipped(
            "container-dns-fresh",
            f"no non-loopback nameserver in {host_path} to compare against",
        )

    code, out = docker.run(["ps", "--format", "{{.Names}}"])
    if code != 0:
        return skipped("container-dns-fresh", "docker ps failed")
    containers = [c for c in out.split() if c]
    if not containers:
        return skipped("container-dns-fresh", "no running containers")

    pinned_by_service = sources.compose_declared_dns(repo.compose)

    findings: list[Finding] = []
    checked = 0
    for container in sorted(containers):
        rc, text = docker.run(["exec", container, "cat", "/etc/resolv.conf"])
        if rc != 0 or not text.strip():
            # Distroless or shell-less images cannot be inspected this way.
            # Silence beats a finding we cannot substantiate.
            continue
        checked += 1
        # Ask Compose which service this is rather than parsing the container
        # name: `container_name:` overrides break any naming convention, and
        # this project uses one (smokeping-mcp-server for service mcp-server).
        service = docker.service_for_container(container)
        pinned = pinned_by_service.get(service, set()) if service else set()
        stale = [
            ns
            for ns in _nameservers(text)
            if ns not in host_ns and ns not in pinned and not _is_loopback(ns)
        ]
        if stale:
            findings.append(
                Finding(
                    f"resolver {', '.join(stale)} is not one the host uses "
                    f"({', '.join(sorted(host_ns))}). If that resolver is "
                    f"gone, hostname targets fail while IP targets look "
                    f"fine. Recreate: {_recreate_hint(docker, container)}",
                    where=container,
                )
            )

    if checked == 0:
        return skipped(
            "container-dns-fresh", "no container exposed /etc/resolv.conf"
        )
    return result(
        "container-dns-fresh",
        findings,
        # Not "use the host's resolvers": loopback entries are accepted
        # without appearing in the host's file, so that phrasing would claim
        # a guarantee the check does not make.
        f"no stale resolvers in {checked} inspected containers",
        status=Status.WARN,
    )


# Run inside the config-manager container, with the app's own code and URL:
# can it reach its database? Prints "db", "no-url" (an edition without the
# database) or "yaml:<ErrorType>" (it cannot, so the API runs in YAML mode).
_DB_MODE_PROBE = (
    "import os\n"
    "u = os.environ.get('DATABASE_URL')\n"
    "if not u:\n print('no-url'); raise SystemExit\n"
    "import models\n"
    "u = getattr(models, 'normalize_database_url', lambda x: x)(u)\n"
    "from sqlalchemy import create_engine, text\n"
    "try:\n"
    " e = create_engine(u, connect_args={'connect_timeout': 5})\n"
    " with e.connect() as c: c.execute(text('select 1'))\n"
    " print('db')\n"
    "except Exception as exc:\n"
    " print('yaml:' + type(exc).__name__)\n"
)


def check_config_manager_database(docker: Docker | None = None) -> CheckResult:
    """config-manager uses the database it is configured with.

    From v2.13.0 to v2.13.6 it did not. SQLAlchemy 2.1 changed the driver a
    bare postgresql:// URL picks, to one the image lacks. Every connection
    failed, the API fell back to YAML mode, and the database's targets
    silently stopped being measured: five of the reference Pi's own, for a
    day and a half. The API answered, the Targets file was written, and
    SmokePing measured something, so nothing looked wrong.
    """
    name = "config-manager-database"
    docker = docker or Docker()
    if not docker.available():
        return skipped(name, "docker is not available here")
    container = docker.container_for_service("config-manager")
    if not container:
        return skipped(name, "config-manager is not running")
    code, out = docker.run(["exec", container, "python", "-c", _DB_MODE_PROBE])
    mode = out.strip().splitlines()[-1] if out.strip() else ""
    if code != 0 or not (mode in ("db", "no-url") or mode.startswith("yaml:")):
        return result(name, [Finding("could not ask config-manager which mode it runs in; "
                                     "see smoking-pi logs config-manager")], "",
                      status=Status.WARN)
    if mode.startswith("yaml:"):
        return result(name, [Finding(
            f"DATABASE_URL is set but config-manager cannot reach its database "
            f"({mode[5:]}), so it runs in YAML mode: the database's targets are not "
            "what SmokePing measures (smoking-pi logs config-manager)",
        )], "")
    return result(name, [], "using the database" if mode == "db" else "no database configured")


# Per series over the last day: points measured, and whether every one of
# them lost every ping. Loss is a 0-1 ratio in these four measurements
# (cpe_latency is percent, and its gateway has a loss floor by design).
_SILENT_FLUX = """
silent = from(bucket: "BUCKET")
  |> range(start: -24h)
  |> filter(fn: (r) => r._field == "loss" and (r._measurement == "latency"
       or r._measurement == "dns_latency" or r._measurement == "http_latency"
       or r._measurement == "tcp_latency"))
  |> map(fn: (r) => ({r with _value: if r._value >= 0.999 then 1 else 0}))
  |> group(columns: ["_measurement", "target"])
  |> reduce(identity: {n: 0, dead: 0},
            fn: (r, accumulator) => ({n: accumulator.n + 1, dead: accumulator.dead + r._value}))
  |> filter(fn: (r) => r.n >= MIN_POINTS and r.dead == r.n)
  |> group()
  |> keep(columns: ["target"])
any = from(bucket: "BUCKET")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "latency" and r._field == "loss")
  |> first()
  |> group()
  |> limit(n: 1)
  |> map(fn: (r) => ({r with target: "ANY_POINT"}))
  |> keep(columns: ["target"])
union(tables: [silent, any])
"""
# The marker row `any` adds when a single latency point was written in the
# day. Without it the answer above is empty for the wrong reason: nothing is
# silent because nothing was written, and "every series answered" was the
# doctor's verdict on an exporter that never wrote a point.
ANY_POINT = "__any_latency_point__"
# Most of a day of 300 s rounds (288): a target added this morning is not
# silent yet, it is new.
SILENT_MIN_POINTS = 200
SILENT_SHOWN = 12
# What SmokePing loads: on Standard and Pro a symlink to the Targets file
# config-manager generates (SMOKING_PI_OUTPUT_DIR on the host, wherever that
# is), on Basic the edition's own. Read inside the container, so a relocated
# output directory needs no guessing here.
SMOKEPING_TARGETS = "/config/Targets"
_TARGET_LINE = re.compile(r"^\++\s*(\S+)\s*$")
_INCLUDE_LINE = re.compile(r"^@include\s+(\S+)\s*$")


def _target_names(text: str) -> set[str]:
    return {m.group(1) for m in map(_TARGET_LINE.match, text.splitlines()) if m}


def _measured_targets(docker: Docker) -> set[str] | None:
    """Every section and target name SmokePing measures, or None if unknown.

    The InfluxDB ``target`` tag is the RRD's stem, which is the name on the
    target's ``+``/``++`` line. Section names come along; no series is
    tagged with one, so they change nothing. Files the Targets file
    ``@include``s count too: the CPE gateway (``CPE_IPv4``, ``CPE_IPv6``)
    lives in CPE_Targets, which cpe_discovery writes and which may not exist
    yet, so an unreadable include adds nothing rather than failing. An
    unreadable Targets file, or one naming nothing, is None, not "nothing is
    measured": a check that drops every finding because it could not read a
    file would be the silence it exists to end.
    """
    container = docker.container_for_service("smokeping")
    if not container:
        return None
    code, out = docker.run(["exec", container, "cat", SMOKEPING_TARGETS])
    if code != 0:
        return None
    names = _target_names(out)
    for path in (m.group(1) for m in map(_INCLUDE_LINE.match, out.splitlines()) if m):
        code, included = docker.run(["exec", container, "cat", path])
        if code == 0:
            names |= _target_names(included)
    return names or None


def check_silent_series(docker: Docker | None = None) -> CheckResult:
    """Series that answered nothing for a whole day.

    A host that does not serve a probe's layer -- bare amazon.com drops ICMP,
    many sites have no HTTP/3, a CDN fallback name has no web server -- charts
    a flat 100% loss forever. On a dashboard, and to the assistant, that is an
    outage that never ends. The Standard/Pro seed shipped one (Amazon), and
    the DNS wizard adopted 72 on the reference Pi, before anything said so.
    """
    name = "silent-series"
    docker = docker or Docker()
    if not docker.available():
        return skipped(name, "docker is not available here")
    container = docker.container_for_service("influxdb")
    if not container:
        return skipped(name, "no InfluxDB here (ClickHouse, or not running)")
    flux = (_SILENT_FLUX.replace("MIN_POINTS", str(SILENT_MIN_POINTS))
            .replace("ANY_POINT", ANY_POINT))
    # The token and names stay in the container: $VARS expand there, never on
    # this host's command line.
    script = ('influx query --raw --org "$DOCKER_INFLUXDB_INIT_ORG" '
              '--token "$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN" '
              '"$(printf %s "$1" | sed "s/BUCKET/$DOCKER_INFLUXDB_INIT_BUCKET/")"')
    code, out = docker.run(["exec", container, "sh", "-c", script, "sh", flux])
    if code != 0:
        return result(name, [Finding("could not query InfluxDB for silent series")], "",
                      status=Status.WARN)
    rows = {row.split(",")[-1].strip() for row in out.splitlines()
            if row.startswith(",,") and row.split(",")[-1].strip()}
    if ANY_POINT not in rows:
        return result(name, [Finding(
            "InfluxDB holds no latency point from the last day: the exporter is not "
            "writing, whatever the dashboards' age says. Its log: "
            "`sudo smoking-pi logs smokeping`")], "", status=Status.WARN)
    silent = sorted(rows - {ANY_POINT})
    if silent:
        # A target deactivated this morning (`dns adopt` does it to every
        # silent layer) keeps a day of dead points in the window, and the
        # finding would advise the very command that just removed it.
        measured = _measured_targets(docker)
        if measured is not None:
            silent = [t for t in silent if t in measured]
    if not silent:
        return result(name, [], "every series answered in the last day")
    adopted = [t for t in silent if t.startswith("W_")]
    others = [t for t in silent if not t.startswith("W_")]
    findings = []
    if others:
        findings.append(Finding(
            f"{len(others)} target(s) answered nothing for a day: "
            f"{', '.join(others[:SILENT_SHOWN])}{' ...' if len(others) > SILENT_SHOWN else ''}. "
            "Their charts show a permanent outage. Check that the host answers that probe "
            "(bare amazon.com does not answer ping; www.amazon.com does)"))
    if adopted:
        findings.append(Finding(
            f"{len(adopted)} layer(s) the DNS wizard adopted answered nothing for a day "
            f"({', '.join(adopted[:SILENT_SHOWN])}{' ...' if len(adopted) > SILENT_SHOWN else ''}): "
            "their hosts do not serve that layer. `sudo smoking-pi dns adopt` deactivates "
            "them (history kept)"))
    return result(name, findings, "", status=Status.WARN)


# Run inside the config-manager container: its own /measurements, the check
# behind the web admin's Measurements card. The token is read there, so it
# never reaches this host's command line.
_MEASUREMENTS_PROBE = (
    "import os, urllib.request\n"
    "r = urllib.request.Request('http://127.0.0.1:5000/measurements')\n"
    "t = os.environ.get('CONFIG_API_TOKEN')\n"
    "if t: r.add_header('Authorization', 'Bearer ' + t)\n"
    "print(urllib.request.urlopen(r, timeout=25).read().decode())\n"
)
MEASURED_SHOWN = 12


def _names(rows: list[dict], state: str) -> list[str]:
    return [str(r.get("name")) for r in rows if r.get("state") == state]


def _shown(names: list[str]) -> str:
    more = " ..." if len(names) > MEASURED_SHOWN else ""
    return ", ".join(names[:MEASURED_SHOWN]) + more


# Run inside the SmokePing container where there is no config-manager to ask
# (Basic): the files /measurements reads, one exec, split on a line no
# SmokePing config holds.
_SPLIT = "--smoking-pi-doctor--"
_SMOKEPING_FILES = (
    f"cat /config/Targets; echo {_SPLIT}; cat /config/Probes 2>/dev/null; "
    f"echo {_SPLIT}; cat /config/CPE_Targets 2>/dev/null; echo {_SPLIT}; "
    "find /data -name '*.rrd' -printf '%T@ %P\\n'"
)
FRESHNESS = pathlib.Path("shared/modules/config-manager/freshness.py")


def _freshness(repo):
    """config-manager's freshness module, by path: the one definition of
    fresh/pending/stale/missing, shipped in the package's tree as in a clone."""
    path = pathlib.Path(repo.root) / FRESHNESS
    spec = importlib.util.spec_from_file_location("_cm_freshness", path)
    if spec is None or spec.loader is None or not path.is_file():
        return None
    module = importlib.util.module_from_spec(spec)
    # Its dataclasses look their module up in sys.modules while being built.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    # A damaged tree is this check's warning, not the doctor's crash.
    except (SyntaxError, ImportError, OSError, NameError, AttributeError, ValueError):
        sys.modules.pop(spec.name, None)
        return None
    return module


def _started_at(docker: Docker, container: str) -> float | None:
    """The container's start, as epoch seconds; Docker prints nanoseconds,
    which fromisoformat on older Pythons refuses, so seconds are enough."""
    code, out = docker.run(["inspect", "-f", "{{.State.StartedAt}}", container])
    try:
        stamp = datetime.datetime.strptime(out.strip()[:19] + "+0000", "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None
    return stamp.timestamp() if code == 0 else None


def _measured_by_smokeping(docker: Docker, repo) -> dict | None:
    """/measurements' body, computed from the SmokePing container itself;
    None when there is no SmokePing container."""
    container = docker.container_for_service("smokeping")
    if not container:
        return None
    module = _freshness(repo) if repo is not None else None
    if module is None:
        return {"available": False, "reason": f"{FRESHNESS} is missing or unreadable here"}
    code, out = docker.run(["exec", container, "sh", "-c", _SMOKEPING_FILES])
    parts = out.split(_SPLIT + "\n")
    if code != 0 or len(parts) != 4:
        return {"available": False,
                "reason": "could not read SmokePing's Targets and RRD files"}
    targets, probes, cpe, found = parts
    body = module.report(targets_text=targets, cpe_text=cpe, probes_text=probes,
                         find_output=found, now=time.time(),
                         started_at=_started_at(docker, container))
    return {"available": True, **body}


def check_targets_measured(docker: Docker | None = None, repo=None) -> CheckResult:
    """Every configured target has SmokePing data, and recent data.

    A clean install once came up with every container healthy and every
    other check green while SmokePing measured the image's sample Targets:
    22 of 22 configured targets had no RRD at all (#303, #304). The doctor
    read InfluxDB, which held points, and the dashboards' age, which was
    new. This asks the question directly, through config-manager's
    /measurements: the configured targets against the RRD files SmokePing
    writes. A target still inside its first two steps since it or SmokePing
    started is ``pending`` there, not missing, so a stack that just started
    is not a failure.

    Basic has no config-manager, and was the edition this check skipped
    (#320): there it reads the same files from the SmokePing container and
    classifies them with config-manager's own freshness module.
    """
    name = "targets-measured"
    docker = docker or Docker()
    if not docker.available():
        return skipped(name, "docker is not available here")
    container = docker.container_for_service("config-manager")
    if container:
        code, out = docker.run(["exec", container, "python", "-c", _MEASUREMENTS_PROBE])
        try:
            body = json.loads(out) if code == 0 else None
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return result(name, [Finding("could not ask config-manager what is measured; "
                                         "see sudo smoking-pi logs config-manager")], "",
                          status=Status.WARN)
    else:
        body = _measured_by_smokeping(docker, repo)
        if body is None:
            # Not running is not "nothing to check": a SmokePing that exited
            # (Basic's entrypoint exits on a bad targets.yaml) measures nothing.
            code, out = docker.run(["ps", "-a", "--filter",
                                    f"label={COMPOSE_SERVICE_LABEL}=smokeping",
                                    "--format", "{{.Names}} {{.Status}}"])
            if code == 0 and out.strip():
                return result(name, [Finding(
                    f"SmokePing's container is not running ({out.strip().splitlines()[0]}): "
                    "nothing is measured. Its log: `sudo smoking-pi logs smokeping`")], "")
            return skipped(name, "no SmokePing container here")
    if not body.get("available"):
        reason = body.get("reason", "no reason given")
        return result(name, [Finding(
            f"cannot tell what is measured: {reason}")],
            "", status=Status.WARN)
    rows = [r for r in body.get("targets", []) if isinstance(r, dict)]
    if not rows:
        # "All 0 targets are measured" would be the green-while-nothing-is-
        # measured this check exists to end (an empty generated Targets).
        return result(name, [Finding(
            "no configured target: SmokePing has nothing to measure. Check the "
            "targets (the web admin, or targets.yaml on Basic), or "
            f"`sudo smoking-pi logs {'config-manager' if container else 'smokeping'}`")],
            "", status=Status.WARN)
    missing, stale = _names(rows, "missing"), _names(rows, "stale")
    pending = _names(rows, "pending")
    findings = []
    if missing and len(missing) == len(rows):
        findings.append(Finding(
            f"none of the {len(rows)} configured targets has any SmokePing data: "
            "SmokePing is not measuring this configuration. "
            "`sudo smoking-pi restart smokeping`, then this again after two steps; "
            "its log: `sudo smoking-pi logs smokeping`"))
    elif missing:
        findings.append(Finding(
            f"{len(missing)} configured target(s) never got SmokePing data: "
            f"{_shown(missing)}"))
    if stale:
        findings.append(Finding(
            f"{len(stale)} target(s) stopped getting SmokePing data: {_shown(stale)}"))
    total = len(rows)
    if pending:
        ok = (f"all {total} configured targets measured or starting "
              f"({len(pending)} waiting for their first step)")
    else:
        ok = f"all {total} configured targets are measured"
    return result(name, findings, ok)


def run_all(repo, docker: Docker | None = None) -> list[CheckResult]:
    docker = docker or Docker()
    return [
        check_deployed_code_current(repo, docker),
        check_container_dns_fresh(repo, docker),
        check_config_manager_database(docker),
        check_targets_measured(docker, repo),
        check_silent_series(docker),
        check_targets_in_tsdb(docker, repo),
        check_monitor_radio(docker, repo),
        check_uplink_interface(),
        check_avahi_host_name(),
        check_disk_free(docker),
        dashboard_queries.check_dashboard_queries_run(repo, docker),
    ]


PROC_ROUTE = pathlib.Path("/proc/net/route")
PROC_IPV6_ROUTE = pathlib.Path("/proc/net/ipv6_route")
SYS_NET = pathlib.Path("/sys/class/net")


def check_uplink_interface(
    proc_route: pathlib.Path = PROC_ROUTE,
    proc_route6: pathlib.Path = PROC_IPV6_ROUTE,
    sys_net: pathlib.Path = SYS_NET,
) -> CheckResult:
    """Name the interface every measurement crosses, and say what kind it is.

    Three states are worth a word rather than silence:

    * **wireless** — the Wi-Fi collector applies, its dashboards have data,
      and the verdict can say "it's your Wi-Fi, not the ISP".
    * **wired** — no Wi-Fi statistics, on purpose. Without this line an empty
      Wi-Fi dashboard is indistinguishable from a broken collector.
    * **virtual** — the default route is on a Docker bridge, a VPN tunnel or
      Tailscale. The latency figures then describe that path, and the Wi-Fi
      verdict is off (it requires the wireless interface to carry the default
      route) with nothing anywhere saying why. That is the failure this check
      exists for; the others are context.
    """
    if not proc_route.is_file() and not proc_route6.is_file():
        return skipped("uplink-interface", "no /proc/net routing table (not Linux?)")

    iface = sources.default_route_iface(proc_route)
    family = "IPv4"
    if iface is None:
        iface, family = sources.default_route_iface6(proc_route6), "IPv6"
    if iface is None:
        return result(
            "uplink-interface",
            [Finding("no default route on this host — nothing can be measured")],
            "",
            status=Status.WARN,
        )

    if sources.is_virtual(iface):
        return result(
            "uplink-interface",
            [
                Finding(
                    f"the {family} default route is on {iface}, a tunnel or "
                    f"virtual bridge — every latency figure describes that "
                    f"path, and the Wi-Fi verdict is disabled because no "
                    f"wireless interface carries the default route"
                )
            ],
            "",
            status=Status.WARN,
        )

    kind = "wireless" if sources.is_wireless(iface, sys_net) else "wired"
    detail = f"measuring over {iface} ({kind}, {family})"
    if family == "IPv4":
        standby = [
            other
            for other in sources.default_route_ifaces(proc_route)[1:]
            if not sources.is_virtual(other)
        ]
        if standby:
            # Both links up is normal, not a warning. It is worth a word
            # because the day the first one drops, every series moves to
            # the standby, and the dashboards mark it ("Uplink changed").
            detail += (
                f"; {', '.join(standby)} also has a default route, at a higher "
                f"metric, and takes over if {iface} goes down"
            )
    return result("uplink-interface", [], detail)


PROC = pathlib.Path("/proc")
# The state word changes (running, registering, collision...); the name in
# brackets is what the check needs. Matching only "running" skipped the check
# on the reference Pi as "Avahi is not running" while its title stayed at
# "registering [smokingpi.local]" and D-Bus said RUNNING (2026-10-11 UTC).
AVAHI_TITLE = re.compile(r"^avahi-daemon: [a-z-]+ \[([^\]]+)\]")


def avahi_host_name(proc: pathlib.Path = PROC) -> tuple[str | None, bool]:
    """The name the host's Avahi answers for, from its process title
    (``avahi-daemon: running [smokingpi.local]``, or another state word such
    as ``registering``; a title with no name in brackets, like the chroot
    helper's, is not it), and whether any other
    process's title could be read at all (``hidepid`` on /proc, or a PID
    namespace without the host's processes, hides them)."""
    try:
        entries = list(proc.iterdir())
    except OSError:
        return None, False
    readable = False
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        readable = True
        title = raw.replace(b"\0", b" ").decode(errors="replace").strip()
        match = AVAHI_TITLE.match(title)
        if match:
            return match.group(1), True
    return None, readable


def check_avahi_host_name(proc: pathlib.Path = PROC,
                          hostname: str | None = None) -> CheckResult:
    """Whether the host's Avahi still answers for ``<hostname>.local``.

    On a host conflict Avahi renames itself to ``<hostname>-2.local`` and
    stays there until it restarts; the plain name then resolves nowhere.
    The reference Pi did this eight seconds after boot, twice, with no
    other host holding the name. ``smoking-pi.local`` (the mdns service)
    is not affected; this is about the host's own name, which people and
    old bookmarks still use (docs/mdns.md). Only that renaming pattern is a
    warning: a name set on purpose (``host-name=`` or ``domain-name=`` in
    avahi-daemon.conf) is reported, not judged.
    """
    name, readable = avahi_host_name(proc)
    if name is None:
        why = ("Avahi is not running on this host" if readable else
               "no other process is visible here (hidepid on /proc, or a container)")
        return skipped("avahi-host-name", why)
    host = (hostname or socket.gethostname()).split(".")[0].lower()
    expected = f"{host}.local"
    if name.lower() == expected:
        return result("avahi-host-name", [], f"Avahi answers for {name}")
    if not re.fullmatch(rf"{re.escape(host)}-\d+\.local", name.lower()):
        return result("avahi-host-name", [],
                      f"Avahi answers for {name} (a name set in its configuration)")
    return result(
        "avahi-host-name",
        [Finding(
            f"Avahi answers for {name}, not {expected}: it renamed itself after a "
            f"name conflict, so {expected} resolves nowhere. `sudo systemctl "
            f"restart avahi-daemon` takes it back until the next conflict; "
            f"smoking-pi.local (the mdns service) still works (docs/mdns.md)")],
        "",
        status=Status.WARN,
    )


# The filesystem under Docker's data root. On 2026-10-16 the reference
# Pi's upgrade to v2.38.2 pulled thirteen more images onto a card that
# already held every release's since 2.13.8 (513 images, 92 GB of 117):
# PostgreSQL crash-looped on postmaster.pid, Grafana's SQLite reported the
# disk full, and InfluxDB refused every WAL write until its container was
# restarted, even after space was freed. Sixteen minutes of measurements
# lost, and nothing had said the card was filling.
DISK_WARN_FRACTION = 0.10
DISK_WARN_BYTES = 5 * 10**9
DISK_FAIL_FRACTION = 0.02
DISK_FAIL_BYTES = 10**9
DEFAULT_REGISTRY = "ghcr.io/estcarisimo/smoking-pi"


def _gigabytes(n: int) -> str:
    gb = n / 1e9
    return f"{gb:.1f} GB" if gb < 10 else f"{gb:.0f} GB"


def _mount_point(path: str, ismount) -> str:
    """The mount the path sits on: the first ancestor that is a mount point."""
    current = os.path.abspath(path)
    while not ismount(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return current


def _old_image_tags(docker: Docker, registry: str) -> list[str] | None:
    """The registry's image tags on disk that no container uses, or None when
    nothing of the project runs (then every tag would look old)."""
    code, used = docker.run(["ps", "-a", "--format", "{{.Image}}"])
    if code != 0:
        return None
    prefix = registry + "/"
    in_use = {ref.rpartition(":")[2] for ref in used.split() if ref.startswith(prefix)}
    if not in_use:
        return None
    code, listing = docker.run([
        "image", "ls", "--filter", f"reference={registry}/*",
        "--format", "{{.Repository}}:{{.Tag}}",
    ])
    if code != 0:
        return None
    old = {ref.rpartition(":")[2] for ref in listing.split()} - in_use
    # A <none> tag is what a pull left behind: counted, named last.
    return sorted(old - {"<none>"}) + (["<none>"] if "<none>" in old else [])


def check_disk_free(
    docker: Docker | None = None,
    statvfs=os.statvfs,
    ismount=os.path.ismount,
    registry: str | None = None,
) -> CheckResult:
    """Whether the filesystem holding Docker's data root is about to fill.

    Warns under 10 % or 5 GB free, fails under 2 % or 1 GB. When the registry's
    images carry tags no container runs, the message says that
    ``smoking-pi upgrade`` removes them: since that command removes the
    images of other releases, old tags on disk mean an upgrade that was
    interrupted or predates it.
    """
    name = "disk-free"
    docker = docker or Docker()
    if not docker.available():
        return skipped(name, "docker is not on PATH")
    code, out = docker.run(["info", "-f", "{{.DockerRootDir}}"])
    root = out.strip()
    if code != 0 or not root:
        return skipped(name, "could not ask Docker for its data root (is the daemon running?)")
    # The mount, not the data root itself: /var/lib/docker is root's, mode
    # 0710, so a docker-group user without sudo cannot statvfs it, and the
    # person who can reach the daemon would be the one with no warning.
    mount = _mount_point(root, ismount)
    try:
        st = statvfs(mount)
    except OSError:
        return skipped(name, f"cannot read the filesystem at {mount} holding {root}")
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    if total <= 0:
        return skipped(name, f"the filesystem at {mount} reports no size")
    fraction = free / total
    registry = registry or os.environ.get("SMOKING_PI_REGISTRY", DEFAULT_REGISTRY)
    old = _old_image_tags(docker, registry) or []
    state = (f"{mount} (Docker's data root) has {_gigabytes(free)} free of "
             f"{_gigabytes(total)} ({fraction:.0%})")
    old_note = ""
    if old:
        shown = ", ".join(old[:5]) + (", …" if len(old) > 5 else "")
        old_note = (f"; images of {len(old)} other release(s) are on disk "
                    f"(tags {shown}): `smoking-pi upgrade` removes them")
    if fraction >= DISK_WARN_FRACTION and free >= DISK_WARN_BYTES:
        return result(name, [], state + old_note)
    critical = fraction < DISK_FAIL_FRACTION or free < DISK_FAIL_BYTES
    status = Status.FAIL if critical else Status.WARN
    cost = ("a full card stops PostgreSQL, Grafana and InfluxDB, and InfluxDB "
            "keeps refusing writes until its container is restarted")
    return result(
        name,
        [Finding(f"{state}: {cost}{old_note}")],
        "",
        status=status,
    )


# As silent-series runs Flux: the token, org and bucket stay in the
# InfluxDB container ($VARS expand there), never on this host's command
# line; the query (no secret in it) is $1, BUCKET replaced there.
_INFLUX_QUERY = ('influx query --raw --org "$DOCKER_INFLUXDB_INIT_ORG" '
                 '--token "$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN" '
                 '"$(printf %s "$1" | sed "s/BUCKET/$DOCKER_INFLUXDB_INIT_BUCKET/g")"')


def _influx_rows(docker: Docker, container: str, flux: str) -> list[dict] | None:
    """A Flux query's rows as dicts (annotated CSV parsed), or None on error."""
    code, out = docker.run(["exec", container, "sh", "-c", _INFLUX_QUERY, "sh", flux])
    if code != 0:
        return None
    rows: list[dict] = []
    header: list[str] | None = None
    for record in csv.reader(out.splitlines()):
        if not record or record[0].startswith("#"):
            header = None if not record else header
            continue
        if len(record) > 1 and record[1] == "result":
            header = record
            continue
        if header:
            rows.append(dict(zip(header, record)))
    return rows


def _epoch(text: str) -> float | None:
    try:
        return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


# Newest point per target tag, over every measurement that has a loss field
# (latency, dns_latency, http_latency, tcp_latency, cpe_latency).
_LAST_POINT_FLUX = """
from(bucket: "BUCKET")
  |> range(start: -RANGEs)
  |> filter(fn: (r) => r._field == "loss" and exists r.target)
  |> keep(columns: ["_time", "_value", "target"])
  |> group(columns: ["target"])
  |> last()
  |> group()
  |> keep(columns: ["_time", "target"])
"""
# A target is late in the TSDB when its newest point is older than this many
# of its own steps, plus the exporter's slack.
TSDB_LATE_STEPS = 3
TSDB_SLACK_S = 600


def _measurement_rows(docker: Docker, repo) -> list[dict] | None:
    """/measurements' rows (config-manager, else SmokePing's own files)."""
    container = docker.container_for_service("config-manager")
    if container:
        code, out = docker.run(["exec", container, "python", "-c", _MEASUREMENTS_PROBE])
        try:
            body = json.loads(out) if code == 0 else None
        except ValueError:
            body = None
    else:
        body = _measured_by_smokeping(docker, repo)
    if not isinstance(body, dict) or not body.get("available", True):
        return None
    return [r for r in body.get("targets", []) if isinstance(r, dict)]


def check_targets_in_tsdb(docker: Docker | None = None, repo=None,
                          now=time.time) -> CheckResult:
    """Every target SmokePing measures has recent points in InfluxDB.

    ``targets-measured`` reads SmokePing's RRD files and ``silent-series``
    reads InfluxDB: a target with fresh RRDs whose points never reach the
    TSDB (the exporter skipping a file, a name it cannot parse, a write
    refused) passes both, while every dashboard, alert and assistant answer
    sees nothing for it. This compares the two: each target ``fresh`` in
    the RRDs must have a point in InfluxDB within three of its steps (plus
    ten minutes for the exporter's cycle).
    """
    name = "targets-in-tsdb"
    docker = docker or Docker()
    if not docker.available():
        return skipped(name, "docker is not available here")
    influx = docker.container_for_service("influxdb")
    if not influx:
        return skipped(name, "no InfluxDB here (ClickHouse backend, or not running): "
                             "only InfluxDB is compared")
    rows = _measurement_rows(docker, repo)
    if rows is None:
        return skipped(name, "could not read what SmokePing measures (see targets-measured)")
    fresh = {}
    for row in rows:
        if row.get("state") == "fresh" and row.get("name"):
            step = row.get("step") if isinstance(row.get("step"), int) else 300
            fresh[str(row["name"])] = max(fresh.get(str(row["name"]), 0), step)
    if not fresh:
        return skipped(name, "no target has fresh SmokePing data to compare "
                             "(see targets-measured)")
    window = max(TSDB_LATE_STEPS * max(fresh.values()) + TSDB_SLACK_S, 1800)
    answer = _influx_rows(docker, influx, _LAST_POINT_FLUX.replace("RANGE", str(window)))
    if answer is None:
        return result(name, [Finding("could not query InfluxDB for each target's newest "
                                     "point")], "", status=Status.WARN)
    newest = {r.get("target"): _epoch(r.get("_time", "")) for r in answer}
    moment = now()
    absent, late = [], []
    for target, step in sorted(fresh.items()):
        seen = newest.get(target)
        if seen is None:
            absent.append(target)
        elif moment - seen > TSDB_LATE_STEPS * step + TSDB_SLACK_S:
            late.append(target)
    findings = []
    if absent and len(absent) == len(fresh):
        findings.append(Finding(
            f"none of the {len(fresh)} targets SmokePing measures has a point in InfluxDB "
            f"in the last {window // 60} min: the exporter is not writing. Its log: "
            "`sudo smoking-pi logs smokeping`"))
    elif absent:
        findings.append(Finding(
            f"{len(absent)} target(s) SmokePing measures have no point in InfluxDB in the "
            f"last {window // 60} min, so no dashboard, alert or answer sees them: "
            f"{_shown(absent)}"))
    if late:
        findings.append(Finding(
            f"{len(late)} target(s) reach InfluxDB late (newest point older than "
            f"{TSDB_LATE_STEPS} steps): {_shown(late)}"))
    return result(name, findings,
                  f"all {len(fresh)} freshly measured targets have recent points in InfluxDB")


# Deaf-radio spans: runs of consecutive deaf samples (common/microcuts.
# uplink_flux, the one definition the alerter and the assistant use).
# Consecutive means closer than DEAF_GAP_FACTOR sampling intervals, the
# interval measured from the day's samples (WIFI_SAMPLE_INTERVAL is 10 s by
# default, 30 s on the staging Pi). A lone sample whose counter did not
# move is not a hang; a run shorter than one SmokePing step is not
# reported: the hangs this exists for lasted hours.
DEAF_GAP_FACTOR = 1.5
DEAF_MIN_S = 300
DEAF_RANGE = "-24h"
WATCHDOG_TIMER = pathlib.Path("/etc/systemd/system/smoking-pi-watchdog.timer")
COMMON_PARENT = pathlib.Path("shared/modules")

# Per interface: whether its newest sample carried the default route, when
# it was taken, and how many samples the day holds (the sampling interval).
_UPLINK_NEWEST_FLUX = """
samples = from(bucket: "BUCKET")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "wifi_link" and r._field == "uplink")
  |> keep(columns: ["_time", "_value", "interface"])
  |> group(columns: ["interface"])
newest = samples |> last()
n = samples |> count() |> map(fn: (r) => ({interface: r.interface, n: r._value}))
join(tables: {newest: newest, n: n}, on: ["interface"]) |> group()
"""


def _deaf_flux(repo) -> str | None:
    """microcuts.uplink_flux over the last day, from the repository's common/."""
    root = pathlib.Path(getattr(repo, "root", ".")) / COMMON_PARENT
    if not (root / "common" / "microcuts.py").is_file():
        return None
    saved_path, saved_bucket = list(sys.path), os.environ.get("INFLUX_BUCKET")
    try:
        sys.path.insert(0, str(root))
        os.environ["INFLUX_BUCKET"] = "BUCKET"
        microcuts = importlib.import_module("common.microcuts")
        return microcuts.uplink_flux(DEAF_RANGE)
    except (ImportError, AttributeError, SyntaxError):
        return None
    finally:
        sys.path[:] = saved_path
        if saved_bucket is None:
            os.environ.pop("INFLUX_BUCKET", None)
        else:
            os.environ["INFLUX_BUCKET"] = saved_bucket


def _deaf_spans(times: list[float], interval: float) -> list[tuple[float, float]]:
    """(start, seconds) of each run of consecutive deaf samples, oldest first."""
    spans: list[list[float]] = []
    for t in sorted(times):
        if spans and t - spans[-1][1] <= DEAF_GAP_FACTOR * interval:
            spans[-1][1] = t
        else:
            spans.append([t, t])
    return [(lo, hi - lo + interval) for lo, hi in spans]


def _utc(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC")


def check_monitor_radio(docker: Docker | None = None, repo=None, now=time.time,
                        watchdog_timer: pathlib.Path = WATCHDOG_TIMER) -> CheckResult:
    """The uplink radio heard throughout the last day.

    A Wi-Fi radio can hang with the link "up" and associated: it hears
    nothing, every probe times out, and the charts draw an outage of the
    network that is this host's own (21 h, 3 h 20 min and 17 h on the
    staging Pi; 5 of 6 confirmed microcuts on the reference Pi in
    October 2026). Alerts and the digest set those minutes apart as
    ``monitor_deaf``; this says, in the doctor, whether it happened in the
    last day and whether it is happening now. Deaf is
    ``common/microcuts.uplink_flux``: the received-packet counter did not
    move between two samples, or the radio lost its association, while
    its interface carried the default route.
    """
    name = "monitor-radio"
    docker = docker or Docker()
    if not docker.available():
        return skipped(name, "docker is not available here")
    influx = docker.container_for_service("influxdb")
    if not influx:
        return skipped(name, "no InfluxDB here (ClickHouse backend, or not running): "
                             "the Wi-Fi samples are read from InfluxDB")
    newest_rows = _influx_rows(docker, influx, _UPLINK_NEWEST_FLUX)
    if newest_rows is None:
        return result(name, [Finding("could not query InfluxDB for the Wi-Fi samples")], "",
                      status=Status.WARN)
    uplink = []
    for r in newest_rows:
        when = _epoch(r.get("_time", ""))
        try:
            count = int(float(r.get("n", "0")))
        except ValueError:
            count = 0
        if r.get("_value") in ("1", "1.0") and when is not None and count > 0:
            uplink.append((r.get("interface", "?"), when, count))
    if not uplink:
        return skipped(name, "the newest Wi-Fi sample does not carry the default route "
                             "(a wired uplink, or the Wi-Fi collector is off)")
    interface, newest, count = max(uplink, key=lambda item: item[1])
    # The day's samples over the day: the collector's interval, a little
    # long when it started within the day (which only merges more).
    interval = min(max(86400 / count, 1.0), 120.0)
    flux = _deaf_flux(repo)
    if flux is None:
        return result(name, [Finding(f"cannot load {COMMON_PARENT}/common/microcuts.py, "
                                     "the deaf-radio definition")], "", status=Status.WARN)
    deaf_rows = _influx_rows(docker, influx, flux)
    if deaf_rows is None:
        return result(name, [Finding("could not query InfluxDB for deaf Wi-Fi samples")], "",
                      status=Status.WARN)
    times = []
    for r in deaf_rows:
        when = _epoch(r.get("_time", ""))
        if when is not None and r.get("interface", interface) == interface:
            times.append(when)
    spans = [s for s in _deaf_spans(times, interval) if s[1] >= DEAF_MIN_S]
    moment = now()
    findings = []
    if moment - newest > 5 * 60:
        findings.append(Finding(
            f"the newest Wi-Fi sample of {interface} is {int((moment - newest) // 60)} min "
            "old: the collector stopped, so a deaf radio would go unseen. Its log: "
            "`sudo smoking-pi logs smokeping`"))
    ongoing = bool(spans) and spans[-1][0] + spans[-1][1] >= newest - DEAF_GAP_FACTOR * interval
    if spans:
        total = sum(s[1] for s in spans)
        start, longest = max(spans, key=lambda s: s[1])
        watchdog = ("" if watchdog_timer.exists() else
                    "; `sudo smoking-pi watchdog enable` reloads a hung radio and "
                    "reboots if that fails")
        findings.append(Finding(
            f"{interface} heard nothing for {int(total // 60)} min in the last day "
            f"({len(spans)} span(s), longest {int(longest // 60)} min from {_utc(start)}): "
            "the measurements in those minutes describe this host, not the network"
            + watchdog))
    if ongoing:
        findings.insert(0, Finding(
            f"{interface} is deaf now (since {_utc(spans[-1][0])}): every measurement "
            "this host takes describes its own radio"))
        return CheckResult(name, Status.FAIL, "the uplink radio is deaf now", findings)
    return result(name, findings,
                  f"{interface} heard throughout the last day", status=Status.WARN)
