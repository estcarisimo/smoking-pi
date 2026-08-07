# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses semantic-ish versioning: minor bumps per completed
modernization sprint (or batch), patch bumps for hotfixes. Each released
version gets a matching GitHub release and git tag.

## [Unreleased]

### Fixed

- **v2.5.0 will not start Grafana in the default InfluxDB mode** (PR #29). The
  ClickHouse work set `isDefault: true` on the ClickHouse datasource, on the
  reasoning that ClickHouse mode provisions that file alone. InfluxDB mode,
  however, bind-mounts the whole datasources directory read-only, so *both*
  files are provisioned, two datasources claim default, and Grafana refuses to
  start. The `isDefault: false` that was replaced existed for exactly this
  reason. It stayed latent through the release because Grafana had not been
  restarted since the merge. The committed file is `false` again and the
  ClickHouse entrypoint flips it while building its own writable tree.
  **A fresh v2.5.0 install in the default mode comes up with no Grafana; use
  2.5.1.**
- **The IPv6 gate was undone every night** (PR #29). `ipv6_check` held its
  verdict in a module-level variable, but config is generated in more than one
  process: the API regenerates in-process, while the nightly OCA refresh runs
  `oca_fetcher.py` as a *subprocess* that imports `ConfigGenerator` and
  regenerates there too. That subprocess started with an empty cache, hit the
  deliberate unknown-means-allowed rule, and wrote IPv6 targets back in — with
  the generator's log going to captured subprocess output, so nothing appeared
  to have run. The verdict is now persisted to a JSON file with an atomic
  replace and `get_status` falls back to it. Fail-open is kept but scoped
  correctly: unknown now means *nobody has ever checked*, not *this process
  has not checked*.
- **The nightly OCA refresh had been failing** with `UniqueViolation` on
  `targets_name_key` since at least 2026-08-03 (PR #29). The replace deletes
  then re-adds in one transaction, but SQLAlchemy's unit of work emits saves
  before deletes within a flush, so new rows collided with old rows not yet
  deleted — guaranteed whenever the OCA list came back unchanged, which is the
  normal case. An explicit flush after the deletes orders them correctly while
  keeping the single transaction that stops a mid-way failure from emptying
  the category.
- **An agent with shell access answered network questions by running `ping`
  instead of using the MCP server** (PR #29), even with the server registered
  and healthy. Three causes: the OpenClaw skill was never installed (now a
  documented required step, with the exact symptom of skipping it); FastMCP
  was constructed with no `instructions`, so a client saw nine getters with no
  hint that this host holds months of continuous measurement; and the tool
  docstrings read like one-shot getters — `get_latency_stats` even offered
  "how is my connection to 8.8.8.8?" as an example, precisely the question
  being answered with `ping`. Docstrings now state the measurement cadence,
  say to prefer recorded history over a live probe, and carry the two
  looks-broken-but-isn't cases (ICMP-dark hosts, the CPE rate-limit floor).
- **The MCP integration could not be verified, only guessed at** (PR #29). The
  access log showed just `POST /mcp 200`, which cannot distinguish an agent
  invoking a tool from an agent merely connecting — so a plausible-sounding
  answer produced from the shell read as proof the tools were wired. Every
  tool now logs name, compacted args, result shape and duration (args named
  token/password/secret/key redacted, long values truncated), and the
  verification step in `docs/openclaw-integration.md` requires a `tool=` line
  in the server log. The real root cause is documented too: a gateway already
  running when the server is registered never picks it up — the Codex runtime
  fingerprints the server set per thread, so `openclaw mcp reload`, a gateway
  restart and a fresh session are required, while a separate config-reading
  poller keeps `mcp probe`/`mcp doctor` green.

### Changed

- **Steady-state load on the Pi cut substantially** (PR #29), after the host
  was found to have hit its soft temperature limit and frequency-capped
  (`throttled=0xe0000`). Measured 65.9 °C → 60.9 °C.
  - Grafana ran at `debug`, writing ~1,100 log lines every three minutes to an
    SD card. Now `info`, env-overridable. Measured 1151 → 178 lines/3 min.
  - Container logs were unbounded (json-file default, no daemon config). All
    Pro services now rotate at 10 MB × 3 via a compose anchor.
  - Dashboard provisioning re-walked the directory and rebuilt the search
    index every 30 s; now 300 s.
  - The microcut detector never idled — it slept `PROBE_WINDOW - elapsed`,
    which is ~0, so it probed back-to-back forever at 5 pps (~432k
    packets/day) and pressured the gateway's ICMP rate limiter, making part of
    the "constant loss floor" self-inflicted. `CPE_PROBE_IDLE` (default 20 s)
    gives a 1-in-3 duty cycle; measured cadence 10 s → 30 s. Set it to `0` to
    restore the old behaviour. `MICROCUT_BURST_N` rescaled 6 → 2 to match,
    since it counts *observed* windows.
  - `rrd2influx` re-fetched all 40 RRDs every 60 s although SmokePing writes
    on a 300 s step, so most cycles spawned 40 `rrdtool` processes for
    nothing. It now skips RRDs whose mtime has not advanced past their last
    export, failing open on a `stat` error so a target can never silently stop
    exporting.

## [2.5.0] — 2026-08-03

Alerting, IPv6 gating, MCP hardening, Grafana 12, and a working ClickHouse
backend (Sprints 10–13).

The alerting engine is the headline, but building it surfaced a set of
measurement bugs that had been quietly corrupting the data it was meant to
watch. Loss was understated 2–4× in InfluxDB and overstated 10× in ClickHouse,
both because the exporters guessed at how many pings a probe sends instead of
reading it from the RRD. Two of the five new alert rules could never have
fired against that data, and a third fired permanently. Everything below was
verified against the live stack or a throwaway copy of it, not just unit
tests.

Upgrade notes:

- **Loss values change scale.** InfluxDB `latency`/`dns_latency` loss written
  before this release is understated (half the true value for ping targets, a
  quarter for DNS); points after it are correct. Grafana panels will show a
  step at the cutover. Nothing rewrites history.
- **IPv6 targets may disappear.** On a host with no global IPv6 they are
  omitted from the generated config rather than charting 100% loss. Database
  rows are untouched and return automatically. `IPV6_MODE=force` keeps the old
  behaviour.
- **Grafana jumps three majors** (10.4.2 → 12.4.3). The database migrates in
  place; dashboards need no changes.
- New opt-in profiles: `alerts` (alerting engine) and the existing `mcp`.
  `MCP_API_TOKEN` is optional — unset preserves the current unauthenticated
  transport.


- Fixed: ClickHouse mode never had a schema. `shared/modules/clickhouse/init/`
  is mounted at `/docker-entrypoint-initdb.d`, but Docker seeds a fresh named
  volume from the image and the official ClickHouse image ships a populated
  `/var/lib/clickhouse`, so its entrypoint reports "directory appears to
  contain a database" and skips the init hook entirely — silently. The exporter
  now applies `CREATE DATABASE / TABLE IF NOT EXISTS` on connect, which is
  idempotent and independent of whether the hook fires.
- Fixed: `rrd2clickhouse.py` computed `packet_loss = loss * 100`, but the RRD
  `loss` source is a COUNT of lost pings — a fully lost 10-ping cycle was
  recorded as 1000%. It now divides by the RRD's own `ping1..pingN` count, the
  same denominator `rrd2influx.py` uses. `packets_sent`/`packets_received` were
  derived from that broken percentage and are now taken directly.
- Fixed: the ClickHouse exporter never set `measurement_type` (so DNS probes
  were indistinguishable from ping targets) and took `category` from the
  immediate parent directory rather than the top-level section, using raw
  directory names where the InfluxDB exporter uses a mapped vocabulary. Both
  now share `DNS_DIRS`/`CATEGORY_MAP`, and a test asserts the two exporters
  agree.
- Fixed: the ClickHouse Grafana datasource was configured with a top-level
  `url`, which the official plugin ignores in favour of `host`/`port` in
  `jsonData` — its health check failed with "invalid server host". The
  unavailable `vertamedia-clickhouse-datasource` entry is gone.
- Changed: the ClickHouse datasource plugin is baked into the Grafana image at
  build time instead of downloaded on boot, and staged into the data volume on
  first start, so a Pi that boots before its network is up still gets a working
  datasource.
- Fixed: the ClickHouse dashboards were never provisioned — their provider
  config sits in a directory Grafana does not scan, and the read-only
  `provisioning/` bind mount makes it impossible to add one in place (Docker
  cannot create a mountpoint for a file inside a read-only mount). In
  ClickHouse mode the entrypoint now builds a writable provisioning tree
  containing only the ClickHouse datasource and dashboards.
- Fixed: all eight ClickHouse dashboards targeted the unavailable vertamedia
  plugin, filtered on raw directory names (`category = 'DNS_Resolvers'`) that
  the exporter does not write, and asked for `measurement_type = 'latency'` on
  DNS panels. They now use the official plugin's `rawSql` form and the
  exporter's vocabulary. Fourteen panels also used `target = ${target:sqlstring}`,
  which is a syntax error whenever the variable expands to more than one value
  — reachable via the "All" option — and now use `IN (...)`.
- Changed: ClickHouse is no longer marked experimental/unmaintained in the
  README. All 58 dashboard queries were executed against a real ClickHouse
  instance in a throwaway Compose project; docs in `docs/clickhouse.md`.

- Changed: Grafana 10.4.2 -> 12.4.3. All nine dashboards use only `timeseries`,
  `stat`, and `row` panels, so nothing needed a schema rewrite. Verified by
  running 12.4.3 against a copy of the live database in a scratch Compose
  project before touching the real one: migrations clean, 9/9 dashboards
  provisioned, InfluxDB and PostgreSQL datasources healthy, and an identical
  15-frame result from the same query on both versions. 13.0.2 was tested the
  same way and also worked, but logs a plugin-install error on every boot that
  12.4.3 does not, so the mature line won.
- Fixed: the Grafana entrypoint reset the admin password with `grafana-cli`,
  which Grafana 11 deprecated and 13 removed. On 13 the step failed and was
  swallowed as a passing warning, which would have silently pinned the admin
  password to whatever the database already held — changing
  `GF_SECURITY_ADMIN_PASSWORD` in `.env` would have stopped working with no
  clear signal. It now prefers `grafana cli`, falls back to the old binary for
  older base images, and reports a real error when both fail.

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
