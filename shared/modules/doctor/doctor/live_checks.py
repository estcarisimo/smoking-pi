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

import hashlib
import pathlib
import shutil
import subprocess

from . import sources
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


def run_all(repo, docker: Docker | None = None) -> list[CheckResult]:
    docker = docker or Docker()
    return [
        check_deployed_code_current(repo, docker),
        check_container_dns_fresh(repo, docker),
        check_uplink_interface(),
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
            if other != iface and not sources.is_virtual(other)
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
