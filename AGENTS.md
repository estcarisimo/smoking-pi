# AGENTS.md

Guidance for AI coding agents working in this repository. Humans should read
[CONTRIBUTING.md](CONTRIBUTING.md), which covers the same ground in more
detail. `CLAUDE.md` is a shorter orientation for Claude Code specifically;
where they overlap, this file wins.

## What this project is

A Docker Compose network-monitoring stack around SmokePing, in three editions
(Basic, Standard, Pro), built for a Raspberry Pi. Pro adds a web admin,
PostgreSQL as the config source of truth, Grafana over InfluxDB or ClickHouse,
an alerting engine, an MCP server, AI reports, and an instrumentation doctor.
It is a *measurement* system: much of its value is months of continuous
recorded history, which constrains what may be changed casually.

## Commands

```bash
# Per module (cd shared/modules/<module>)
uv run pytest tests/ -q                       # seconds; mocks everything external
uv run ruff check .                           # module config, line length 88

# Repository-wide
ruff check --select E9,F63,F7,F82 shared/modules editions      # what CI enforces
PYTHONPATH=shared/modules/doctor python -m doctor --repo-root . # static checks (CI runs this)

# Against a running stack, from editions/pro
PYTHONPATH=../../shared/modules/doctor python -m doctor --repo-root ../.. --live
docker compose build <service> && docker compose up -d <service>
```

Python 3.14. CI: ruff, shell syntax, compose config ×3, Docker builds, doctor,
every module's tests, CodeQL. All must be green; CodeQL on a PR is diff-only,
so "0 results" on a PR means *no new alerts*, not "fixed".

## Layout

| Path | Role |
| --- | --- |
| `editions/{basic,standard,pro}/` | Compose files, `setup.sh`, `.env.template`, per-edition config |
| `shared/modules/config-manager/` | Flask API; YAML ↔ PostgreSQL ↔ generated SmokePing `Targets`/`Probes` |
| `shared/modules/web-admin/` | Flask UI; talks to config-manager and PostgreSQL |
| `shared/modules/alerter/` | Rules → verdict → chart → delivery (OpenClaw / webhook), daily digest |
| `shared/modules/mcp-server/` | MCP tools over the config API and InfluxDB; deep links |
| `shared/modules/smokeping-exporters/` | RRD → InfluxDB/ClickHouse; CPE microcut detector |
| `shared/modules/doctor/` | Static + live checks that the pieces agree |
| `shared/modules/common/` | The only code shared between images: Flux, charts, links, mutes, OpenClaw client |
| `shared/modules/grafana/provisioning/` | Dashboards as JSON; separate trees for InfluxDB and ClickHouse |
| `docs/` | alerting, mcp-server, openclaw-integration, doctor, clickhouse, ipv6-gating, upgrades |

## Constraints

**Containers cannot import across each other.** Shared code goes in
`shared/modules/common/`, which each Dockerfile copies in. Build context for
those images is `shared/`.

**Never make real network, database or Docker calls in tests.** Every suite
mocks them; `web-admin` fails a test if the Docker SDK is importable. New env
vars a test reads go into the module conftest's scrub list.

**Error responses never derive from the exception object.** Use
`error_response(status, message, exc)` (`config-manager/api.py`,
`web-admin/app/errors.py`): static message + `error_id`, detail to the log.
CodeQL taints `str(e)`, f-strings with `e`, and attribute reads on `e`; a
regex or type check does not clear it. Validation reasons are lists of
literal strings, not exception text.

**Request-named files go through `web-admin/app/services/safe_path.confine()`**
(normalise + base-directory prefix). config-manager resolves `config_type`
through the `CONFIG_FILES` literal table. A regex allow-list alone is not a
sanitizer to CodeQL and should not be one to you.

**Do not "unify" the two category vocabularies.** Database:
`top_sites`/`netflix_oca`/`dns_resolvers`; InfluxDB tag:
`topsites`/`netflix`/`dns`. `common/links.py` maps between them; both sides
have consumers.

**Units.** `latency`/`dns_latency`: latency in seconds, loss as a 0–1 ratio.
`cpe_latency`: latency in milliseconds, loss as 0–100 percent. Probes send 10
pings (FPing) or 5 (DNS) on a 300 s step; any alert window must span ≥ 3
steps. The CPE gateway rate-limits ICMP, giving it a permanent loss floor —
`MICROCUT_LOSS_PCT` exists so that floor is not an alert.

**`get_chart` is the only MCP tool that returns an image, and only on
request.** The server instructions and the OpenClaw skill both say so. Do not
attach images to other tools, and do not add public/snapshot URLs — Grafana
snapshots are deliberately disabled (see CHANGELOG 2.7.0).

**Never commit runtime churn or secrets.** A running stack rewrites
`editions/*/config-manager/{config,output}`; add files by name, never
`git add -A`. `.env` is gitignored; `.env.template` values ship empty. A new
setting goes in `.env.template` *and* the compose file, or the doctor fails.

**Stateful majors are not routine bumps.** `postgres` and `influxdb` major
versions are excluded from Dependabot on purpose; a PG major over a live
volume initialises an empty cluster while the real data sits orphaned, and CI
cannot catch it because CI has no data volume.

**The OpenClaw skill is a copy.** After editing
`examples/openclaw/smokeping-monitoring/SKILL.md`, run
`./shared/scripts/install-openclaw-skill.sh --reload`; `--check` reports a
stale copy. The gateway caches the tool set per session.

## Process

Every change: branch → PR → CI green → **read the Copilot review** → merge →
deploy on the reference Pi (`docker compose build <svc> && up -d <svc>`) →
smoke test → doctor `--live`. The CHANGELOG entry under `[Unreleased]` says
what was wrong, what it would have cost, and what the change does about it.

**Always request a Copilot review.** PRs on this repository get one
automatically; if a PR shows no `copilot-pull-request-reviewer` review, request
it from the Reviewers menu before merging. Address every comment or say why
not. Copilot cannot formally approve, so a green review is the CI checks plus
an answered review, not an "Approved" badge.

Commits follow Conventional Commits; the subject is a sentence about the
outcome. Releases: `release/vX.Y.Z` branch converts `[Unreleased]` to a dated
section with an intro, merge, `git tag -a`, `gh release create`.

## Gotchas

- `gh pr checks --json bucket` is invalid in some `gh` builds and yields empty
  output silently — a polling loop then looks like "no checks yet" forever.
  Use plain `gh pr checks <n>`.
- The Pi has **no global IPv6**. IPv6 targets are gated out of the generated
  config automatically (`IPV6_MODE`); IPv6 targets at 100% loss in old data
  are real, not a bug to fix.
- Bare `amazon.com` does not answer ICMP and charts a flat 100% forever; use
  `www.` hosts. A dead-flat 100% with no variance is a monitoring artifact.
- Grafana 13 removed the standalone `grafana-cli` binary; use `grafana cli`.
- ClickHouse's `/docker-entrypoint-initdb.d` never runs (Docker seeds the
  volume from the image); the exporter creates the schema on connect.
- The `mcp-server` and `alerter` services run on the host network because
  the OpenClaw gateway binds loopback only. `MCP_HOST=127.0.0.1` keeps the
  MCP server off the LAN; do not change either without reading the comment.
