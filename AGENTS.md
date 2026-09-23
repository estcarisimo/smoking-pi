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
# Per module (cd shared/modules/<module>); pytest/ruff are in the `dev` extra
uv sync --extra dev                           # or: pip install -e ".[dev]"
uv run pytest tests/ -q                       # seconds; mocks everything external
uv run ruff check .                           # module config, line length 88

# Repository-wide
ruff check --select E9,F63,F7,F82 shared/modules editions      # what CI enforces
pip install -e shared/modules/doctor                            # once; the doctor needs PyYAML
python -m doctor --repo-root .                                  # static checks (CI runs this)
python -m doctor --repo-root . --live                           # ...against the running stack

# Deploy one service, from editions/pro
docker compose build <service> && docker compose up -d <service>
```

Python 3.14. PR CI is the cheap checks only: ruff, shell syntax, compose
config ×3 with the packaging guards, doctor, every module's tests, the strict
docs build, CodeQL. All must be green; CodeQL on a PR is diff-only, so
"0 results" on a PR means *no new alerts*, not "fixed". **CI builds no
images on a PR** — `release.yml` builds all nine for arm64+amd64 from the
release tag and publishes them to GHCR, and `docs.yml` deploys the site from
the same tag. A Dockerfile change is proven by the deploy on the reference
Pi before merge, not by CI.

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
| `docs/` | getting-started, alerting, mcp-server, openclaw-integration, remote-openclaw, wifi, http-probes, doctor, clickhouse, ipv6-gating, upgrades, packaging, release-acceptance |
| `packaging/` | The shipped install path since v2.12.0: the `smoking-pi` CLI, the systemd unit, the `.deb` builder, the apt repository builder, the Homebrew formula and their tests — see `docs/packaging.md` |

## Constraints

**American English, everywhere a name or an instruction lives.** Function,
method, variable and parameter names, agent instructions (this file, the
OpenClaw skill, `CLAUDE.md`), docs and comments use US spelling:
`initialize`, `analyze`, `normalize`, `color`, `behavior`, `center`, `gray`.
This is a project-wide rule, not a preference; a review should flag
`colour` the way it flags a failing test. Existing *public* names are not
renamed for spelling alone — an API field or env var that is already
shipped keeps its spelling until a change that breaks it anyway, and then
the rename is documented with the old name still accepted for a release.
(`cancelled` in the web-admin AI result is the one such holdout.)

**Containers cannot import across each other.** Shared code goes in
`shared/modules/common/`, which each Dockerfile copies in. Build context for
those images (alerter, ai-insights, mcp-server, web-admin) and for smokeping
(which bakes in `shared/modules/smokeping-exporters`) is `shared/` in every
edition's compose file. Of those, web-admin and smokeping are in the CI
`docker-build` matrix (via `include` entries that set the context); the
other three are not docker-built in CI at all. **Source bind-mounts are
development overlays**: anything mounted from `shared/modules` must also be
baked into the image, and `docker-compose.packaged.yml` must drop it —
`packaging/check-packaged-override.py` (run in CI) enforces both halves.

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
(normalize + base-directory prefix). config-manager resolves `config_type`
through the `CONFIG_FILES` literal table. A regex allow-list alone is not a
sanitizer to CodeQL and should not be one to you.

**CodeQL is `.github/workflows/codeql.yml`, not GitHub's default setup**
(since 2026-09-23). Default setup analyzed only PRs targeting `main`, so a
stacked PR showed every check green and had no CodeQL at all. The workflow
runs on every PR whatever its base; `CodeQL analysis (all)` says it ran, and
GitHub's own `CodeQL` check says whether it found something new. Keep its
languages and `/language:<lang>` categories as they are: they are what
default setup used, and changing a category orphans every existing alert.

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

**Never commit runtime state or secrets.** A running stack rewrites
`editions/*/config-manager/{config,output}`; those are gitignored (`.gitkeep`
only) and relocatable via `SMOKING_PI_CONFIG_DIR`/`_OUTPUT_DIR`/`_ENV_FILE`
(docs/packaging.md). The seed YAML is `config-manager/templates/`, the one
copy. Add files by name, never `git add -A`. `.env` is gitignored;
`.env.template` values ship empty. A new setting goes in `.env.template`
*and* the compose file, or the doctor fails.

**Stateful majors are not routine bumps.** `postgres` and `influxdb` major
versions are excluded from Dependabot on purpose; a PG major over a live
volume initializes an empty cluster while the real data sits orphaned, and CI
cannot catch it because CI has no data volume.

**The OpenClaw skill is a copy.** After editing
`examples/openclaw/smokeping-monitoring/SKILL.md`, run
`./shared/scripts/install-openclaw-skill.sh --reload`; `--check` reports a
stale copy. The gateway caches the tool set per session.

## Process

Every change: branch → PR → CI green → **independent review** → merge →
deploy on the reference Pi (`docker compose build <svc> && up -d <svc>`) →
smoke test → doctor `--live`. The CHANGELOG entry under `[Unreleased]` says
what was wrong, what it would have cost, and what the change does about it.

**Every PR gets an independent review before merge.** Not a self-review:
a fresh model session that has not seen the authoring conversation (the
maintainer uses a cold Sonnet session per PR), or a human. The reviewer reads
the whole diff against this file's contracts and reports findings with
severity; the author addresses each one or says why not, and records the
review's findings and their resolution in a PR comment. GitHub Copilot review
is not requested — it is costly and its request API silently no-ops on this
repository. Nobody can formally approve a sole-maintainer PR, so a green
review is the CI checks plus an answered review, not an "Approved" badge.

Commits follow Conventional Commits; the subject is a sentence about the
outcome. Releases: `release/vX.Y.Z` branch converts `[Unreleased]` to a dated
section with an intro and updates `version`/`date-released` in
`CITATION.cff` (CI checks they match), merge, `git tag -a`,
`gh release create` (notes = the changelog section). The tag triggers
`release.yml` — nine images × two architectures to
`ghcr.io/estcarisimo/smoking-pi/<service>:<version>` (it refuses a tag that
disagrees with `CITATION.cff`), then the `.deb` built from the tagged tree
and installed with each supported host's own `apt` (Ubuntu 22.04/24.04
VMs, both architectures, Basic started with the release's images; Debian
12/13 containers, package-level — `docs/packaging.md`, *Supported hosts*),
and, only after every host passed, attached to the release you created (if
the release does not exist yet when `attach` runs, the `.deb` is the run's
`smoking-pi-deb` artifact: create the release and re-run that job) — and
`docs.yml` (the site). Watch both to green before announcing.

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
