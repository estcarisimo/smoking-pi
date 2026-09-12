## What does this change?

<!-- A short description, and the issue it closes if there is one. -->

## Why?

<!-- What was wrong, what it would have cost, what this does about it. For
     changes to probes, alert rules, exporters or generated SmokePing config,
     say how you verified the new behaviour against a real stack — see
     AGENTS.md. -->

## Checklist

- [ ] The changed module's `pytest tests/` passes
- [ ] `ruff check --select E9,F63,F7,F82 shared/modules editions` passes
- [ ] `python -m doctor --repo-root .` passes (after `pip install -e shared/modules/doctor`)
- [ ] A new setting is in `.env.template` **and** the compose file
- [ ] Error responses use `error_response()` — no `str(e)` on the wire
- [ ] Added an entry under `## [Unreleased]` in `CHANGELOG.md`, if user-visible
- [ ] Updated the README or `docs/`, if behaviour or usage changed
- [ ] Read and answered the Copilot review

## Notes for the reviewer

<!-- Anything surprising, deliberately out of scope, or worth a closer look. -->
