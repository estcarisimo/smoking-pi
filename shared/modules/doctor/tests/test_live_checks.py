"""Tests for the live checks.

Docker is injected as a fake, so these run in CI with no daemon. Each check
is tested twice: that it passes on a healthy stack, and — more importantly —
that it actually CATCHES the failure it was written for. A check that cannot
be shown to fire is decoration.
"""

from __future__ import annotations

import hashlib
import pathlib

import pytest

from doctor import live_checks
from doctor.report import Status


class FakeDocker:
    """Scripted docker. `responses` maps a joined argv to (rc, stdout)."""

    def __init__(self, responses: dict[str, tuple[int, str]], present: bool = True):
        self.responses = responses
        self.present = present
        self.calls: list[str] = []

    def available(self) -> bool:
        return self.present

    def run(self, args):
        key = " ".join(args)
        self.calls.append(key)
        for pattern, value in self.responses.items():
            if key.startswith(pattern):
                return value
        return 1, ""

    def container_for_service(self, service):
        code, out = self.run([
            "ps", "--filter",
            f"label=com.docker.compose.service={service}",
            "--format", "{{.Names}}",
        ])
        names = [n for n in out.split() if n] if code == 0 else []
        return names[0] if names else None


class Repo:
    def __init__(self, root):
        self.root = pathlib.Path(root)


@pytest.fixture
def repo(tmp_path):
    """A repo with one deployed module and the shared common package."""
    alerter = tmp_path / "shared/modules/alerter"
    common = tmp_path / "shared/modules/common"
    alerter.mkdir(parents=True)
    common.mkdir(parents=True)
    (alerter / "main.py").write_text("print('main')\n")
    (alerter / "digest.py").write_text("print('digest')\n")
    (alerter / "test_ignored.py").write_text("# tests are not deployed\n")
    (common / "links.py").write_text("print('links')\n")
    return Repo(tmp_path)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _hashes_for(repo, names_and_text):
    return "\n".join(f"{_sha(t)}  {n}" for n, t in names_and_text)


# Deliberately NOT `pro-alerter-1`: the container name depends on the compose
# project, so the checks resolve it by service label instead.
ALERTER_PS = "ps --filter label=com.docker.compose.service=alerter"
MCP_PS = "ps --filter label=com.docker.compose.service=mcp-server"


def _healthy_alerter(repo, container="pro-alerter-1"):
    return {
        ALERTER_PS: (0, f"{container}\n"),
        MCP_PS: (0, "\n"),
        "exec pro-alerter-1 sh -c cd /app ": (
            0,
            _hashes_for(repo, [("main.py", "print('main')\n"),
                               ("digest.py", "print('digest')\n")]),
        ),
        "exec pro-alerter-1 sh -c cd /app/common ": (
            0,
            _hashes_for(repo, [("links.py", "print('links')\n")]),
        ),
    }


# ---------------------------------------------------------------------------
# deployed-code-current
# ---------------------------------------------------------------------------


def test_matching_code_passes(repo):
    docker = FakeDocker(_healthy_alerter(repo))
    res = live_checks.check_deployed_code_current(repo, docker)
    assert res.status is Status.OK
    assert "3 deployed files" in res.summary


def test_stale_container_is_caught(repo):
    """The failure this check exists for: dde5e36, and today's stale image."""
    responses = _healthy_alerter(repo)
    responses["exec pro-alerter-1 sh -c cd /app "] = (
        0,
        _hashes_for(repo, [("main.py", "print('OLD')\n"),
                           ("digest.py", "print('digest')\n")]),
    )
    res = live_checks.check_deployed_code_current(repo, FakeDocker(responses))
    assert res.status is Status.FAIL
    assert len(res.findings) == 1
    assert "main.py" in res.findings[0].render()
    assert "docker compose build alerter" in res.findings[0].render()


def test_a_file_missing_from_the_image_is_caught(repo):
    """A new module in the repo but not the image — exactly PR 4's situation."""
    responses = _healthy_alerter(repo)
    responses["exec pro-alerter-1 sh -c cd /app "] = (
        0, _hashes_for(repo, [("main.py", "print('main')\n")]),
    )
    res = live_checks.check_deployed_code_current(repo, FakeDocker(responses))
    assert res.status is Status.FAIL
    assert "digest.py" in res.findings[0].render()
    assert "predates it" in res.findings[0].render()


def test_drift_in_the_shared_common_package_is_caught(repo):
    responses = _healthy_alerter(repo)
    responses["exec pro-alerter-1 sh -c cd /app/common "] = (
        0, _hashes_for(repo, [("links.py", "print('STALE')\n")]),
    )
    res = live_checks.check_deployed_code_current(repo, FakeDocker(responses))
    assert res.status is Status.FAIL
    assert "/app/common/links.py" in res.findings[0].where


def test_unreadable_container_reports_uncertainty_not_drift(repo):
    """Claiming drift we did not measure would be the same class of bug."""
    responses = _healthy_alerter(repo)
    responses["exec pro-alerter-1 sh -c cd /app "] = (1, "")
    res = live_checks.check_deployed_code_current(repo, FakeDocker(responses))
    assert res.status is Status.FAIL
    assert "cannot verify" in res.findings[0].render()


def test_a_stopped_container_is_not_drift(repo):
    """A profile that is off is a deployment choice, not a fault."""
    docker = FakeDocker({ALERTER_PS: (0, "\n"), MCP_PS: (0, "\n")})
    assert live_checks.check_deployed_code_current(repo, docker).status is Status.SKIP


def test_no_docker_skips(repo):
    docker = FakeDocker({}, present=False)
    res = live_checks.check_deployed_code_current(repo, docker)
    assert res.status is Status.SKIP
    assert "docker" in res.summary


def test_tests_are_not_compared(repo):
    """test_*.py lives in the repo but never in the image; comparing it would
    report permanent, unfixable drift."""
    docker = FakeDocker(_healthy_alerter(repo))
    res = live_checks.check_deployed_code_current(repo, docker)
    assert res.status is Status.OK


# ---------------------------------------------------------------------------
# container-dns-fresh
# ---------------------------------------------------------------------------


@pytest.fixture
def host_resolv(tmp_path):
    p = tmp_path / "resolv.conf"
    p.write_text("search lan\nnameserver 192.168.86.1\n")
    return p


def _dns_docker(container_resolv: dict[str, str]):
    responses = {"ps --format {{.Names}}": (0, "\n".join(container_resolv) + "\n")}
    for name, text in container_resolv.items():
        responses[f"exec {name} cat /etc/resolv.conf"] = (0, text)
    return FakeDocker(responses)


def test_matching_resolver_passes(repo, host_resolv):
    docker = _dns_docker({"c1": "nameserver 192.168.86.1\n"})
    res = live_checks.check_container_dns_fresh(repo, docker, host_resolv)
    assert res.status is Status.OK


def test_dockers_embedded_resolver_is_not_stale(repo, host_resolv):
    """127.0.0.11 is on every compose container and forwards to the daemon.

    Flagging it fired on all six healthy containers when this check was first
    run for real — which is how a check gets ignored.
    """
    docker = _dns_docker({"c1": "nameserver 127.0.0.11\noptions ndots:0\n"})
    res = live_checks.check_container_dns_fresh(repo, docker, host_resolv)
    assert res.status is Status.OK
    assert res.findings == []


def test_frozen_tailscale_resolver_is_caught(repo, host_resolv):
    """The ten-day outage: MagicDNS frozen at container creation, then dead."""
    docker = _dns_docker({
        "pro-smokeping-1": (
            "nameserver 100.100.100.100\n"
            "nameserver fd7a:115c:a1e0::53\n"
            "search tail6998da.ts.net lan\n"
        ),
        "healthy": "nameserver 192.168.86.1\n",
    })
    res = live_checks.check_container_dns_fresh(repo, docker, host_resolv)
    assert res.status is Status.WARN
    finding = res.findings[0].render()
    assert "100.100.100.100" in finding
    assert "pro-smokeping-1" in res.findings[0].where
    assert "force-recreate" in finding


def test_a_shell_less_image_is_skipped_not_flagged(repo, host_resolv):
    docker = FakeDocker({
        "ps --format {{.Names}}": (0, "distroless\n"),
        "exec distroless cat /etc/resolv.conf": (1, ""),
    })
    res = live_checks.check_container_dns_fresh(repo, docker, host_resolv)
    assert res.status is Status.SKIP


def test_unreadable_host_resolv_skips(repo, tmp_path):
    docker = _dns_docker({"c1": "nameserver 1.1.1.1\n"})
    missing = tmp_path / "nope.conf"
    res = live_checks.check_container_dns_fresh(repo, docker, missing)
    assert res.status is Status.SKIP


def test_run_all_returns_both_checks(repo, host_resolv):
    results = live_checks.run_all(repo, FakeDocker({}, present=False))
    assert [r.name for r in results] == [
        "deployed-code-current",
        "container-dns-fresh",
    ]
    assert all(r.status is Status.SKIP for r in results)


def test_a_custom_compose_project_name_is_still_found(repo):
    """The container name depends on the compose project, so it is not a key.

    Hardcoding `pro-alerter-1` meant `COMPOSE_PROJECT_NAME=home` made this
    check report "nothing is running" while the alerter was up — a drift
    check that silently stops checking, which is worse than not having one.
    """
    responses = {
        k.replace("pro-alerter-1", "home-alerter-1"): v
        for k, v in _healthy_alerter(repo, container="home-alerter-1").items()
    }
    res = live_checks.check_deployed_code_current(repo, FakeDocker(responses))
    assert res.status is Status.OK, [f.render() for f in res.findings]
    assert "3 deployed files" in res.summary
