"""Findings and report formatting for the instrumentation doctor.

Output is deliberately one line per check, in the shape of `openclaw doctor`:
a status, the check name, and — when something is wrong — the specific
mismatch rather than a generic "failed".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


_MARK = {
    Status.OK: "ok  ",
    Status.WARN: "warn",
    Status.FAIL: "FAIL",
    Status.SKIP: "skip",
}


@dataclass
class Finding:
    """One thing that is wrong, stated specifically enough to act on."""

    message: str
    where: str | None = None

    def render(self) -> str:
        return f"{self.where}: {self.message}" if self.where else self.message


@dataclass
class CheckResult:
    name: str
    status: Status
    summary: str
    findings: list[Finding] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "check": self.name,
            "status": self.status.value,
            "summary": self.summary,
            "findings": [
                {"message": f.message, "where": f.where} for f in self.findings
            ],
        }


def result(
    name: str,
    findings: list[Finding],
    ok_summary: str,
    status: Status = Status.FAIL,
) -> CheckResult:
    """Build a result: `status` when there are findings, OK when there are not."""
    if not findings:
        return CheckResult(name, Status.OK, ok_summary)
    noun = "problem" if len(findings) == 1 else "problems"
    return CheckResult(name, status, f"{len(findings)} {noun}", findings)


def skipped(name: str, why: str) -> CheckResult:
    return CheckResult(name, Status.SKIP, why)


class Report:
    def __init__(self, results: list[CheckResult]):
        self.results = results

    @property
    def failed(self) -> bool:
        return any(r.status is Status.FAIL for r in self.results)

    @property
    def exit_code(self) -> int:
        return 1 if self.failed else 0

    def counts(self) -> dict[str, int]:
        counts = {s.value: 0 for s in Status}
        for r in self.results:
            counts[r.status.value] += 1
        return counts

    def render_text(self, verbose: bool = False) -> str:
        lines = []
        width = max((len(r.name) for r in self.results), default=0)
        for r in self.results:
            lines.append(f"[{_MARK[r.status]}] {r.name.ljust(width)}  {r.summary}")
            if r.findings and (verbose or r.status is not Status.OK):
                for finding in r.findings:
                    lines.append(f"          - {finding.render()}")
        counts = self.counts()
        lines.append("")
        lines.append(
            f"{counts['ok']} ok, {counts['warn']} warn, "
            f"{counts['fail']} fail, {counts['skip']} skipped"
        )
        return "\n".join(lines)

    def render_json(self) -> str:
        return json.dumps(
            {
                "ok": not self.failed,
                "counts": self.counts(),
                "checks": [r.as_dict() for r in self.results],
            },
            indent=2,
        )
