# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses semantic-ish versioning: minor bumps per completed
modernization sprint (or batch), patch bumps for hotfixes. Each released
version gets a matching GitHub release and git tag.

## [Unreleased]

- Added: optional bearer auth on the MCP server's HTTP transport. Setting
  `MCP_API_TOKEN` requires `Authorization: Bearer <token>` on every request;
  unset leaves the transport open exactly as before, and stdio is never gated.
  The tool surface includes mutations and the port is loopback-bound, so this
  gives it a credential of its own, separate from `CONFIG_API_TOKEN` and from
  OpenClaw's gateway token. `/health` stays open for liveness probes.
- Fixed: the alerter's `openclaw` delivery mode targeted `/hooks/agent`, which
  does not exist. A stock OpenClaw gateway (verified on 2026.7.1-2) is
  WebSocket-only and returns 404 for every HTTP path — `openclaw hooks`
  manages internal agent lifecycle hooks, not inbound HTTP. The path is now
  configurable via `OPENCLAW_HOOK_PATH` so it can point at an HTTP-RPC plugin
  or a bridge, and the alerter probes the endpoint at startup and logs an
  explicit error on 404 instead of failing silently on the first incident.
  `webhook` mode remains the portable option.
- Added: `docs/openclaw-integration.md` (MCP registration via `openclaw mcp
  set`, tool filtering, token separation, and what alert delivery actually
  requires) and `examples/openclaw/smokeping-monitoring/SKILL.md`, a
  placeholder-only skill giving an agent the loss conventions and the
  looks-broken-but-is-not cases.

- Added: IPv6 gating. config-manager checks global IPv6 reachability and omits
  `FPing6` targets from the generated `Targets` file when the host cannot reach
  the IPv6 internet, instead of charting a flat 100% loss that reads as an
  outage. The check requires a global-unicast address (ULA and link-local do
  not count — a router's `fd00::/8` prefix or a Tailscale address makes
  `scope global` non-empty on a host with no IPv6 at all), a usable default
  route, and an actual reply from a probe host. It runs inside the SmokePing
  container, whose `network_mode: host` namespace is what its probes see;
  config-manager's own bridge network has no IPv6 and would report a false
  negative. Rechecked every `IPV6_RECHECK_INTERVAL` (900 s) with config
  regenerated only when the verdict flips, so targets return on their own when
  IPv6 comes back. `IPV6_MODE=force|off` overrides; database rows are never
  modified. New `GET /ipv6-status` and `POST /ipv6-status/refresh` endpoints.
  Docs: `docs/ipv6-gating.md`.

- Fixed: the RRD → InfluxDB exporter divided the loss count by a fixed 20
  pings, but SmokePing's probes send 10 (FPing/FPing6) and 5 (DNS), so every
  loss ratio written since 2026-07 was understated — 2× for ping targets, 4×
  for DNS. A fully unreachable target recorded 0.5 instead of 1.0, which made
  the alerter's `target_down` and `ipv6_down` rules (threshold 0.999)
  impossible to trigger and halved the loss shown on every Grafana panel. The
  denominator is now read per RRD from its `ping1..pingN` data sources;
  `SMOKEPING_PINGS` remains only as a fallback. Points written before this fix
  keep their old scale.
- Fixed: alerter `microcut_burst` counted any `cpe_latency` window with loss
  above zero. CPE gateways rate-limit ICMP, so the 5 pps probe sees a constant
  loss floor (measured on the live link: p50 10%, p99 30%, never a fully lost
  window in 24 h) — every window qualified, and the rule fired permanently
  without ever recovering. A window now counts only above `MICROCUT_LOSS_PCT`
  (default 50%), which is what an actual brief cut looks like.
- Changed: alerter `DOWN_WINDOW` default 300 s → 900 s. SmokePing probes on a
  300 s step, so the old window could only ever contain one point and never
  met `target_down`'s 3-point minimum.
- Sprint 10: Alerting engine + OpenClaw delivery.
  - New `shared/modules/alerter/` service: deterministic (no-LLM) rules
    evaluated against InfluxDB every `ALERT_INTERVAL` (60 s) —
    `target_down`, `high_loss`, `microcut_burst`, `exporter_stale`, and an
    aggregate `ipv6_down` — with incident dedup/cooldown/recovery state
    persisted atomically to a named volume.
  - Delivery via `NOTIFY_MODE`: `off` (log-only default), `openclaw`
    (POST `/hooks/agent` on a local OpenClaw gateway), or `webhook`
    (generic JSON POST with optional bearer token); 3 retries with
    exponential backoff. Also forwards ai-insights `report-*.md` files
    (at most one per `REPORT_DELIVERY_INTERVAL`, truncated
    Telegram-friendly).
  - Pro compose: `alerter` service behind the opt-in `alerts` profile
    (`network_mode: host`, `alerter-state` volume, read-only `reports`
    mount); `.env.template` gains an all-empty alerting block; CI
    validates the `alerts` profile. Docs: `docs/alerting.md`.

## [2.4.0] — 2026-07-31

AI insights & in-UI assistant (Sprint 9) — final sprint of the 2026-07
modernization plan.

- Sprint 9: AI insights & in-UI assistant.
  - New `shared/modules/ai-insights/` service: pulls latency/loss/microcut
    aggregates from InfluxDB and writes plain-language Markdown health
    reports via Claude (Haiku by default; `AI_MODEL` overridable). Guardrails:
    input-size cap, `AI_REPORTS_PER_DAY` cap, clean no-op when
    `ANTHROPIC_API_KEY` is unset. Compose snippet documented in
    `docs/ai-insights.md` (compose/editions files owned by an in-flight PR).
  - web-admin: new AI section — `/ai/reports` renders the generated reports
    (tiny built-in safe Markdown renderer, empty-state help when disabled)
    and `/ai/chat` is an assistant that reuses the MCP tool surface
    (stats read tools run inline; add/remove/toggle target require an
    explicit confirmation card before executing). No `apply_config` tool —
    regeneration is automatic.
- Pro compose: `ai-insights` service behind the opt-in `ai` profile with
  a shared `reports` volume (PR #21); `.env.template` gains
  ANTHROPIC_API_KEY and AI tuning knobs (PR #21). Enable with
  COMPOSE_PROFILES=influxdb,ai.

## [2.3.0] — 2026-07-31

Editions repair, AI-operability, and three production fixes found while
deploying (Sprints 7–8).

### Fixed (post-deploy)
- **OCA refresh data loss** (PR #19): the daily Netflix OCA refresh could
  replace the `netflix_oca` targets with an empty set when the fetch
  failed (this happened in production while the locator install was
  broken). Empty fetches are now refused and the DB replace is a single
  transaction. Golden tests moved to immutable fixtures so live runtime
  state can never fail CI (PR #19).
- **Slow container boot** (PR #18): the exporter cont-init script blocked
  s6 service startup (~5 min to web UI after recreate); loops are now
  detached and the sleeps removed — boot-to-web measured at 9 s.

- Sprint 8 (PR #13): MCP server module — manage targets and query latency
  data conversationally from Claude Code / Claude Desktop.
- Sprint 7: editions repair & compose unification.
  - Pro: generated `Targets`/`Probes` now reach SmokePing via a read-only
    directory mount (`/config/generated`) plus a `05-link-generated-config.sh`
    cont-init symlink script, replacing single-file bind mounts that went
    stale after config-manager's atomic (inode-swapping) writes.
  - Standard: fixed broken data path (config-manager's output volume never
    reached SmokePing) using the same shared-volume + symlink pattern; added
    a persistent `/app/config` bind mount, `COMPOSE_PROJECT_NAME` /
    `CONFIG_API_TOKEN` / `CONFIG_DIR` / `OUTPUT_DIR` env wiring, and
    `CONFIG_API_TOKEN` pass-through to web-admin.
  - Exporter dependencies (python3, rrdtool, traceroute, influxdb-client,
    PyYAML) baked into the smokeping image at build time; cont-init/
    entrypoint installs now only run as a fallback (no more apk/pip on
    every boot).
  - config-manager image: real multi-stage build — production stage copies
    the venv and oca-locator checkout from the builder instead of
    re-installing git/docker.io and re-cloning (~300 MB smaller);
    `netflix_oca_locator` is now installed into the venv the app actually
    runs from. Docker packages dropped entirely (only the Python docker
    SDK is used).
  - Pinned floating images: `linuxserver/smokeping:version-2.9.0-r0`,
    `cloudflare/cloudflared:2026.5.0`; removed obsolete compose `version:`
    key; `PUID`/`PGID` are now `${PUID:-1000}`-style overridable in all
    editions; InfluxDB gained a compose healthcheck.
  - Pro: `mcp-server` service wired in behind the `mcp` profile
    (127.0.0.1:8090); CI now validates `COMPOSE_PROFILES=mcp`.
  - `setup.sh` scripts use `docker compose` (v2) instead of `docker-compose`.

## [2.2.0] — 2026-07-30

Web-admin hardening and UX overhaul (Sprints 4–5).

### Security (PR #14)
- Password-hash auth (`WEB_ADMIN_PASSWORD_HASH`) with constant-time
  plaintext fallback; no hardcoded default credentials; `SECRET_KEY`
  required; CSRF protection on every state-changing request; per-IP
  login lockout; open-redirect fix; Cloudflare token out of URL query.
- Single authenticated data path: web-admin only talks to the
  config-manager API (Bearer token); YAML fallback dual-writes and the
  silently-broken dashboard delete removed; Docker socket removed from
  web-admin entirely; scheduler runs exactly once.
- API token auth active end-to-end on the Pro deployment.

### UX (PR #16)
- Bootstrap + icons vendored locally — the UI works during the exact
  internet outages it exists to diagnose.
- Toast/confirm/fetch helpers replace all alert()/confirm() dialogs.
- Targets: search, category/status filters, inactive targets visible
  with activate toggle, edit dialog, bulk delete, CSV export.
- Add-target: identical client/server name validation, inline errors,
  IPv6-aware hostname checks, auto-regeneration feedback.
- Dashboard: full target lists with per-target SmokePing and Grafana
  links; shows Database/YAML mode.
- Top Sites: loading-modal deadlock fixed, honest selection counters,
  review-before-update, real CrUX per-country lists; broken countries
  page removed.

## [2.1.0] — 2026-07-29

Modernization phase 1 (Sprints 1–3, 6 + CI). PostgreSQL becomes the real
source of truth and the Grafana/exporter pipeline is made trustworthy.

### Added
- **CPE microcut detection** (PR #7): hourly traceroute discovery of the
  ISP CPE (IPv4+IPv6), 5 pps windowed probing, `cpe_latency` measurement
  in InfluxDB, and a dedicated Grafana dashboard.
- **CI pipeline** (PRs #8, #9): ruff critical rules, per-edition
  `docker compose config` validation (incl. ClickHouse overlay), shell
  syntax checks, Grafana dashboard/provisioning validation with
  duplicate-uid detection, auto-discovered per-module pytest suites, and
  Docker image build checks.
- **API auth** (PR #10): optional shared-token auth on the config-manager
  API (`CONFIG_API_TOKEN`, Bearer / X-API-Token).
- First real test suites: config-manager (32), smokeping-exporters (30).

### Changed
- **PostgreSQL activated as the configuration source of truth** (PR #10):
  idempotent YAML→DB migration runs at startup; YAML becomes
  import/export. Config generation is atomic, file-locked, in-process
  (the subprocess fork-chain "zombie generator" is gone), and fully
  data-driven (hardcoded big-name template blocks removed). Gunicorn
  replaces the Flask dev server. FPing6 seeded (fixes IPv6 target 500s).
- **Grafana overhaul** (PR #11): single dashboard provider
  (`foldersFromFilesStructure`), dashboards organized into folders,
  entrypoint rewritten with proper signal handling (`exec`), Dockerfile
  HEALTHCHECK, dead `$status` variable and user-specific exclusions
  removed, CPE microcut dashboard finished (target variable, thresholds,
  proper panel options).
- **Exporter rewrite** (PR #11): `rrd2influx.py` now fetches incrementally
  since the last exported timestamp with downtime backfill (≤24h) and
  writes **loss as a ratio 0–1** (was a raw lost-ping count that rendered
  1 lost ping as 100%). Historical points keep the old scale.

### Fixed
- **6-week outage root cause** (PR #7): no `restart:` policy on any core
  Pro service — a reboot left the whole stack down. All services now
  `restart: unless-stopped`.
- **All 8 InfluxDB dashboards rendered empty** (PR #7): queries targeted
  bucket `latency`; the deployment writes to `smokeping` (67 refs fixed).
- Atomic writes preserved root ownership on host bind mounts, breaking
  git and host tooling (PR #12); DB reads had no `order_by`, making
  generated config nondeterministic (PR #12).
- Env template drift (PR #8): `TIMEZONE`→`TZ` (timezone config was a
  silent no-op in basic/standard), wrong `POSTGRES_DB` in standard.

### Removed
- ~2,300 lines of committed runtime artifacts (PR #8): YAML backups,
  `cookies.txt`, OCA dumps, orphaned modules (`api_docs.py`, stub
  compose file, broken test scripts). Backup retention added so they
  cannot accumulate again.

### Deprecated
- ClickHouse support marked experimental/unmaintained (parked; known
  datasource-plugin and schema mismatches documented in README).

## [2.0.0] — historical

Pre-modernization state: three Docker editions (Basic/Standard/Pro),
config-manager + web-admin + Grafana/InfluxDB stack as of PR #6.
