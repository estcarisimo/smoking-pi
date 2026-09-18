# Contributing to Smoking Pi

Thanks for your interest in improving this project. It started as a SmokePing
container for one Raspberry Pi and grew into a multi-edition monitoring stack
with alerting, dashboards and an MCP server, so contributions of every size
are welcome — including documentation fixes, a test for something that has
none, and "this sentence in the docs was wrong".

Please also read the [Code of Conduct](CODE_OF_CONDUCT.md).

## Requirements

- **Docker Engine with the Compose v2 plugin** (`docker compose version`).
  Everything runs in containers; nothing is installed on the host.
- **Python 3.14** for running a module's tests locally. The images ship 3.14
  and CI tests on 3.14 — an older interpreter may pass locally and fail in CI.
- [uv](https://docs.astral.sh/uv/) is recommended but not required; every
  module that has tests has a `pyproject.toml` that works with plain `pip`
  too (`common/` is a shared package copied into images and has none — it is
  tested through the modules that use it).
- A Linux host (a Raspberry Pi is the reference target, ARM64; x86-64 works).
  The `smokeping` and `alerter` services use `network_mode: host`, which
  Docker Desktop on macOS does not fully support.

## Setting up

```bash
git clone https://github.com/estcarisimo/smoking-pi.git
cd smoking-pi

# Bring up the Pro edition; setup.sh writes .env with generated secrets
cd editions/pro && ./setup.sh
```

Each test-bearing module under `shared/modules/` is its own package. Work in
the module you are changing; `pytest` and `ruff` live in the `dev` extra, so
ask for it:

```bash
cd shared/modules/alerter
uv sync --extra dev         # or: python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
uv run pytest tests/ -q     # ~220 tests, a few seconds, no network
                            # (without uv: pytest tests/ -q inside the venv)
```

The module directories with tests, and what they are:

| Module | What it is | Tests |
| --- | --- | --- |
| `config-manager` | Flask API: YAML ↔ PostgreSQL ↔ generated SmokePing config | ~75 |
| `web-admin` | Flask UI: targets, sources, countries, AI assistant | ~105 |
| `alerter` | Rules, verdict, charts, digest, delivery | ~220 |
| `mcp-server` | MCP tools over the config API and InfluxDB | ~135 |
| `smokeping-exporters` | RRD → InfluxDB/ClickHouse, CPE microcut detector | ~40 |
| `doctor` | Static + live instrumentation checks | ~55 |
| `ai-insights` | Periodic AI health reports | ~10 |
| `common` | Code two containers share (Flux, charts, links, mutes, OpenClaw) | tested through its users |

## Development workflow

```bash
# In a module directory (after `uv sync --extra dev` or `pip install -e ".[dev]"`)
uv run pytest tests/ -q
uv run ruff check .                 # the module's full ruff config (line length 88)

# Repository-wide, what CI enforces on every file
ruff check --select E9,F63,F7,F82 shared/modules editions

# The doctor is its own package (it needs PyYAML). Install it once into
# whichever environment you use, then it runs from anywhere:
pip install -e shared/modules/doctor          # or, with uv: cd shared/modules/doctor && uv sync
python -m doctor --repo-root . --verbose      # dashboards, exporters, compose defaults, env docs agree
python -m doctor --repo-root . --live         # ...and against the running stack
```

CI runs, on every PR: ruff (critical rules), shell syntax for every `.sh`,
`docker compose config` for all three editions, Docker builds, the doctor's
static checks, every module's test suite on Python 3.14, and CodeQL. All of
it has to be green.

### Tests do not touch the network, the database, or Docker

Every suite mocks InfluxDB, the config-manager API, OpenClaw, and the Docker
socket. `web-admin` has a test that fails if the Docker SDK is even importable
from the app. Follow the fixtures in each module's `tests/conftest.py`; if a new
test needs an environment variable, add it to the conftest's scrub list so the
suite stays hermetic regardless of the developer's shell.

Tests that **reintroduce a bug** are the house style: when you fix something,
add the test that fails with the fix reverted, and say so in its docstring.

### Do not commit runtime churn

A running stack rewrites `editions/<edition>/config-manager/config/*.yaml` and
`config-manager/output/*` (PostgreSQL is the source of truth; the YAML is
import/export). Those files will show as modified on a host that runs the
stack. Never `git add -A`; add the files you changed by name. The `tests/
fixtures/` directories hold the goldens.

### Secrets

`.env` files hold real passwords and tokens and are gitignored. Every value in
`.env.template` ships empty and is documented inline. If a change adds a
setting, add it to `.env.template` **and** the compose file — the doctor's
`alerter-env-declared` check fails when they disagree.

## Where things are

```text
editions/{basic,standard,pro}/   Compose files, setup.sh, per-edition config
shared/modules/<module>/         one container image each; see the table above
shared/modules/common/           the only code shared between images (copied in at build)
shared/scripts/                  setup helpers, container management, tunnels, skill install
docs/                            alerting, MCP, OpenClaw, doctor, ClickHouse, IPv6, upgrades
examples/openclaw/               the agent skill
```

**Containers cannot import across each other.** If two images need the same
code it goes in `shared/modules/common/`, which each Dockerfile copies in. The
build context for those images is `shared/`, not the module directory.

### Things that look wrong but are load-bearing

A few facts in this codebase are easy to "fix" into a bug. They are documented
where they live; the short list:

- **Loss units differ by measurement.** `latency` and `dns_latency` store loss
  as a 0–1 ratio; `cpe_latency` stores a 0–100 percent. Latency is seconds in
  the first two and milliseconds in `cpe_latency`.
- **Two category vocabularies.** The database says `top_sites` /
  `netflix_oca` / `dns_resolvers`; the InfluxDB tag says `topsites` /
  `netflix` / `dns`. `common/links.py` maps between them. Do not unify one
  side without checking both consumers.
- **Error responses never carry exception text.** Routes return a static
  message plus an `error_id`; the detail goes to the log under that id (see
  `error_response()` in `config-manager/api.py` and `web-admin/app/errors.py`).
  CodeQL flags anything derived from the exception object, so the message has
  to be chosen in code.
- **Request-named files go through `safe_path.confine()`** in web-admin; a
  regex allow-list alone is not enough.
- **The CPE gateway rate-limits ICMP**, so `cpe_latency` has a permanent loss
  floor. Thresholds that count "any loss" on it are a bug.
- **`get_chart` is the only MCP tool that returns an image**, and only when
  called. Do not attach images to other tools' answers.

## Pull requests

1. Branch from `main`.
2. Keep the change focused; separate mechanical cleanups from behavioural
   changes.
3. Make sure the module's tests, ruff, and the doctor pass locally.
4. Add an entry under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md) for
   anything user-visible. Entries here say **why** — what was wrong, what it
   would have cost, and what the change does about it — not just what moved.
5. Describe what you changed and why. If it touches measurement, alert rules,
   or the generated SmokePing config, say how you verified it against a real
   stack.

Every PR gets an automatic **GitHub Copilot review**. Read it and either
address each comment or reply saying why not; do not merge over it unread.

### Commit messages

New commits follow [Conventional Commits](https://www.conventionalcommits.org/):

```text
feat: a chart you can hand to someone who cannot log in
fix: doctor warned about the very DNS pin that was added to fix DNS
docs: a README that reads like the project it describes
ci: give GITHUB_TOKEN only what the workflow uses
chore(docker): bump grafana/grafana-oss
release: v2.8.0
```

The subject line is a sentence about the outcome, not a label; the body says
why. The history before mid-2026 predates this convention.

### Releases

Maintainer's job: a `release/vX.Y.Z` branch converts `[Unreleased]` into a
dated section with an intro paragraph, merges, then `git tag -a` and `gh
release create` with notes. Minor bump per batch of features, patch for a
hotfix. Every release is deployed on the reference Pi and smoke-tested before
the tag. The release branch also updates `version` and `date-released` in
[CITATION.cff](CITATION.cff) — CI fails if they lag the newest CHANGELOG
section.

## Reporting bugs

Open an issue using the bug report template. Because behaviour depends on the
edition, the database backend and the host, please include:

- the edition and the git tag or commit (`git describe --tags`)
- `docker compose ps` and the relevant `docker compose logs <service>` lines
- for an alert or chart problem, the `error_id` from the response, so the
  log line can be found
- the doctor's output (`python -m doctor --repo-root . --live`, after
  `pip install -e shared/modules/doctor`) if the monitoring itself looks
  wrong

Please redact hostnames, IP addresses and tokens you would rather not publish.

For security issues, do **not** open a public issue — see
[SECURITY.md](SECURITY.md).
