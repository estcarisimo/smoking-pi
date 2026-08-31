"""Live checks — the ones that need a running stack.

The static checks compare one file against another and run in CI. These two
compare what is *deployed* against what the repository says, and can only run
on the machine actually running the stack.

Both exist because the corresponding failure happened here, and both share a
shape that makes them worth automating: **the broken thing keeps looking
healthy.** A container running three-week-old code starts, logs cleanly and
answers requests. A container holding a dead resolver pings raw IPs happily
and only fails on hostnames. Nothing goes red, so nobody looks.

- ``deployed-code-current`` — the running container's Python matches the
  repository. This is commit ``dde5e36`` ("the flap fix never reached the
  deployed container"), and it recurred: an image failed to rebuild, the
  failure was masked by a shell pipeline's exit code, ``docker compose up -d``
  recreated the container from the stale image, and everything reported
  success while the fix sat only on disk.

- ``container-dns-fresh`` — a container's resolver still matches the host's.
  Docker writes ``/etc/resolv.conf`` **once, at container creation**. A
  container created while a VPN was up freezes that VPN's resolver, which dies
  silently when the VPN goes away. This cost nine of eighteen targets for ten
  days: every hostname target read 100% loss, every raw-IP target was fine,
  and "100% loss" is indistinguishable from "the target is down".

Docker is invoked through an injected runner so these are testable without a
daemon, and every check SKIPS rather than fails when Docker is unavailable —
running the doctor on a laptop must not report a broken deployment.
"""

from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess

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
DEPLOYED_MODULES = {
    "alerter": "alerter",
    "mcp-server": "mcp-server",
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

    Uses sha256sum from the image (present in the python:slim base). A missing
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

    for module, service in sorted(DEPLOYED_MODULES.items()):
        module_dir = repo.root / "shared/modules" / module
        if not module_dir.is_dir():
            continue
        container = docker.container_for_service(service)
        if not container:
            # Not running is not drift. A profile that is switched off is a
            # deployment choice, not a fault.
            continue
        checked_containers += 1

        for label, source_dir, container_path in (
            (module, module_dir, "/app"),
            (f"{module}:{COMMON_DIR}", repo.root / "shared/modules" / COMMON_DIR,
             "/app/common"),
        ):
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
                            f"rebuild: docker compose build {module}",
                            where=f"{container}:{container_path}/{name}",
                        )
                    )
                elif got != want:
                    findings.append(
                        Finding(
                            f"{name} differs from the repository — the "
                            f"container is running older code; rebuild: "
                            f"docker compose build {module}",
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

    Only flags a resolver the host does NOT have. A container legitimately
    pinned to a different resolver (compose `dns:`) is a deliberate choice
    and is reported as a warning, not a failure, because it may be correct.
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

    findings: list[Finding] = []
    checked = 0
    for container in sorted(containers):
        rc, text = docker.run(["exec", container, "cat", "/etc/resolv.conf"])
        if rc != 0 or not text.strip():
            # Distroless or shell-less images cannot be inspected this way.
            # Silence beats a finding we cannot substantiate.
            continue
        checked += 1
        stale = [
            ns
            for ns in _nameservers(text)
            if ns not in host_ns and not _is_loopback(ns)
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
    ]
