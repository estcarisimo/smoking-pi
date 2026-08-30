# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses semantic-ish versioning: minor bumps per completed
modernization sprint (or batch), patch bumps for hotfixes. Each released
version gets a matching GitHub release and git tag.

## [Unreleased]

### Added

- **A daily digest, so silence stops being ambiguous.** Alerts only fire when
  something breaks, which means a quiet channel means either "nothing
  happened" or "the monitoring stopped" — and those are the two states you
  most need to tell apart. `DIGEST_ENABLED` opts into a wall-clock summary of
  the last 24 hours, in the same shape as everything else this bot sends:
  traffic lights, bold sections, worst targets first.

  **Firing exactly once is the hard part**, because the loop wakes every 60
  seconds and has no idea what time it is between ticks. Each tick resolves
  the most recent scheduled instant at or before now — the *slot* — and state
  records which slot last fired, making delivery idempotent: ten ticks in a
  minute resolve one slot and send one message.

  The slot is persisted, **not the send time**. That distinction is the whole
  mechanism: anything recorded before the slot instant re-fires the same day,
  which is what a send-time-like value becomes after an NTP step backwards or
  on a container whose RTC starts behind. A reintroduction test pins it.
  Both DST transitions are covered — a nonexistent wall time resolves to one
  stable instant, an ambiguous one picks the same instant every tick, and a
  full day of five-minute ticks across a transition delivers once.

  Past `DIGEST_MAX_LATENESS` (4h) a slot is recorded as fired **without**
  sending. A Pi that was off for two days must not deliver 08:30's digest at
  19:00, and must not deliver two.

  **It never claims health it did not verify.** If the aggregate query raises
  or returns zero targets, nothing is sent and the slot is retired with an
  error. "All clear" when the truth is "InfluxDB did not answer" would turn a
  broken monitor into a reassuring message — the exact failure the digest
  exists to catch, and the first thing its tests assert.

  Counts come from a new `state["history"]`, appended on each alert or
  recovery the alerter decides to send — whether or not delivery then
  succeeded — and pruned to 200 entries and 48 hours. Necessary because
  `reconcile()` pops a record on recovery, so by 08:30 an incident that fired
  and cleared at 03:00 has left no other trace.

### Fixed

- **`show-tunnel-urls.sh` reported the first hostname a tunnel ever had.** A
  quick tunnel gets a brand new `*.trycloudflare.com` name every time
  cloudflared reconnects, and every one stays in the container log; the script
  took `head -1`, so it printed the oldest — dead, and entirely plausible
  looking. Found with 12 distinct URLs in one log while it reported #1. It now
  takes the last, and warns when a tunnel has rotated, since anything saved
  earlier into `.env`, a bookmark or a chat is already a dead link.

- **A text-only message could not be delivered silently.** `silent` was set
  only on the image path and only from `ALERT_SILENT`, so a text-only digest
  would ring a phone at 08:30 regardless. It is now a per-message property
  that wins over the env default — quiet is a fact about *this* message, not
  about the deployment — and it survives the text-only retry after an image
  send fails, which previously dropped it.

- **Every link is offered twice: at home, and from anywhere.** Deep links were
  built from a single base URL, which forced a choice nobody can make
  correctly — the same person reads an answer from the couch and from a train.
  A LAN address is the better link at home (one hop, and up even when
  Cloudflare isn't) and a dead link on cellular.

  `TUNNEL_BASE_HOST` (plus `GRAFANA_TUNNEL_URL` / `WEB_ADMIN_TUNNEL_URL`)
  configures the from-anywhere address separately, and every entry in a `links`
  object gains a `_tunnel` twin: same panel, same window, different host.
  `system_status` twins its three entry points the same way.

  Three behaviours keep a twin from lying about where it goes: a tunnel with no
  LAN address configured *becomes* the primary link rather than leaving a
  tunnel-only Pi with no links at all; two tiers resolving to the same base are
  not twinned, because two labels on one URL invite a reader to try "the other
  one"; and the ClickHouse gate covers both tiers, since a twinned 404 is still
  a 404.

  In alerts only the *graph* gets a twin. A Grafana deep link is ~120
  characters and a caption budget is 1024, so mirroring all four would spend a
  quarter of it saying the same thing twice.

- **The monitoring skill now specifies the shape of a report, not just its
  content.** `examples/openclaw/smokeping-monitoring/SKILL.md` gained a report
  template — traffic lights per section (🟢 nothing to do, 🟡 real but minor,
  🔴 acted on), bold headings, one fact per bullet with its number and window,
  a verdict as the headline rather than a summary, and a graphs section
  offering both links.

  Three rules exist because the first real reports broke them. **Headings are
  the channel's bold, never `###`** — Telegram does not render Markdown
  headings, so `### DNS` arrives as literal hashes or flat text, which is what
  made an otherwise correct report read as one wall. **Every section is
  bullets, one fact and one line each** — a semicolon or a second measurement
  means it is two bullets, with a worked wrong/right pair in the file.
  **A bullet that names a moment carries that moment's link**, taken from the
  `worst_windows` entries `get_microcut_stats` already zooms to ±15 minutes;
  the week-long overview URL drops the reader into 168 hours to hunt for a
  spike the agent had already found.

  **The template is a structure, not a script.** It is written in English
  because the tools and metric names are; the skill directs the agent to answer
  in whatever language the question arrived in, and marks what never gets
  translated — target names (they are database keys: a translated
  `CloudflareDNS` cannot be looked up, muted, or found on a dashboard), numbers
  with their units, the traffic lights, and the links.

  It also names the four values an operator must tune before installing — the
  host's names, the CPE loss floor, the timezone, the example target names —
  rather than saying "replace the placeholders" and marking none of them. A
  wrong loss floor makes the agent confidently wrong rather than obviously
  broken, which is the harder failure to notice.

- **`shared/scripts/install-openclaw-skill.sh`** installs the skill, backs up
  an existing one, and optionally reloads the gateway. `--check` exits
  non-zero when the installed copy is stale — the failure this guards against
  is invisible, since an agent running a stale skill keeps answering, just in
  the old shape, and the install looks like it silently did nothing. The
  README and `docs/openclaw-integration.md` now point at it.

- **Alerts arrive with the graph.** Each one carries a rendered PNG: median
  latency over packet loss for the target, its same-category peers as grey
  context, and a marked line at the moment the incident started — so the
  question a Grafana trip is usually made to answer (*since when, and is it
  just this one?*) is answered in the notification.

  Rendered with matplotlib in-process, **not** `grafana-image-renderer`: that
  is a headless Chromium at ~400 MB resident and seconds of CPU per render, on
  a Pi that has already hit its soft thermal limit. Bytes are posted as base64
  in the invoke body, so there is no shared filesystem between the container
  and the gateway, no path translation and no retention sweep.

  Design decisions that are load-bearing rather than cosmetic:

  - **Two stacked panels, never twin y-axes.** Latency and loss have different
    scales; a dual axis invents a correlation that is not in the data.
  - **The loss axis is pinned 0–100.** Autoscaled, 4% loss looks catastrophic,
    and loss is the axis a reader interprets absolutely.
  - **Emphasis, not eight hues.** The story is one target, so it wears a status
    colour and the peers recede.
  - **Digest bars are one hue.** Colouring each bar darker-where-bigger
    double-encodes bar length on nominal categories; over-threshold rows are
    marked with a glyph and a status-coloured value instead, so colour never
    carries meaning alone — and it keeps discriminating when everything is over
    the line.

  A chart never costs an alert: rendering is wrapped, matplotlib is imported
  lazily, and a failed image send retries **exactly once** as text rather than
  doubling the retry budget.

- **Alerts now answer the question they raise.** Every notification carried a
  measurement (`amazon: mean loss 22.4% over 15m`) and left the reader to work
  out whether it was their line, their ISP, or that one site. Each alert now
  leads with a verdict: `🌐 Not you — 12 of 16 destinations affected but your
  local link is clean, so this is upstream.`

  It is deterministic and costs **no additional queries** — the breadth,
  microcut and liveness rows were already fetched by the rules and thrown
  away, which matters on a Pi that has hit its thermal limit.
  `evaluate_with_context()` returns them; `evaluate()` is unchanged.

  Two properties are load-bearing:

  - `exporter_stale` outranks every other scope unconditionally. Announcing a
    network fault from an *absence* of measurements is the worst thing this
    could do, and when the exporter stalls every target reads as 100% lost
    precisely because nothing is arriving.
  - Hosts that never answer ICMP are excluded from breadth entirely. A target
    pointed at a host that does not respond charts a permanent flat 100%;
    counting those turns one slow site into an ISP outage. Anything at 100%
    whose incident predates `VERDICT_STALE_DOWN_HOURS` is dropped from both
    numerator and denominator. This is not hypothetical — see the DNS note
    below, where nine such targets would have made every verdict wrong.

  Every verdict logs its own inputs, so one you disagree with is diagnosable
  from `docker compose logs alerter` without reproducing the moment.

### Changed


- **Code that two images need now lives in `shared/modules/common/`**, copied
  into each image at build time (`context: ../../shared`). Containers cannot
  import across each other, so the Flux helpers were already triplicated
  between the alerter, ai-insights and the mcp-server, and the deep-link UID
  maps existed twice. Extracted rather than copied a fourth time: `tsdb.py`
  (queries, the loss-unit clamp), `aggregates.py` (the per-target and CPE
  rollups) and `links.py` (Grafana/web-admin URLs).

  The old module paths remain as re-export shims, so no call site changed.
  Two tests did, and the reason is worth writing down: a shim forwards public
  names, but **`monkeypatch.setattr(shim, "query_influx", ...)` does not
  intercept the real function**, because the moved code resolves that name in
  its own globals. The ai-insights collector tests were silently querying the
  live InfluxDB instead of their stub. They now patch the module that owns the
  code. The alerter's lazy-client test moved for the same reason — asserting
  `_influx_client is None` through a shim would hold forever whether or not a
  client had been built.

### Fixed

- **The smokeping container's resolver is pinned instead of inherited.** Docker
  writes `/etc/resolv.conf` once, at container creation, and never again — so a
  container inherits whatever the host's resolver happened to be at that
  instant and holds it forever.

  This deployment captured a Tailscale MagicDNS address. Tailscale later logged
  out, and from then on **nine of eighteen targets reported 100% loss for ten
  days** — every hostname target, while every raw-IP target stayed green.
  Nothing surfaced it, because "100% loss" and "that host is down" are
  indistinguishable; the alerter dutifully tracked the incidents to
  `notified_count=253` with delivery switched off.

  `dns:` is honoured even under `network_mode: host`. Public resolvers by
  default (`SMOKEPING_DNS`, `SMOKEPING_DNS_FALLBACK` to override) so this does
  not depend on any particular LAN addressing. Only the smokeping service is
  pinned: the bridge-network services must keep Docker's embedded resolver for
  service-name lookups.

  The real lesson is the missing signal, not the missing config — a *live*
  doctor check for "a target that has never produced a non-100% point" would
  have caught this in an hour instead of ten days. `docs/doctor.md` lists the
  live checks as still unbuilt.

- **Chart timestamps matched the data, not the label.** matplotlib formats
  dates with `rcParams["timezone"]` (UTC) regardless of each datetime's own
  tzinfo, so the x-axis rendered UTC beneath a footer naming the local zone —
  an hour's silent offset, which is exactly what makes someone mis-correlate an
  incident with whatever they were doing at the time. Caught by rendering the
  chart and looking at it.

- **Messages are composed to a budget instead of truncated.** Telegram allows
  4096 characters for a message but only **1024 for a caption**, so an alert
  carrying a chart has a quarter of the room. Sections now carry a priority
  and the least valuable are dropped until the message fits — mute hint, then
  the breadth recap (the verdict line already states that number), then the
  links, which survive longest because a static image caption cannot be
  explored and a link is the way out of it. The headline, verdict and numbers
  are never dropped.

  Only if those alone overflow is text trimmed, and then on a line boundary.
  `reports_watcher` previously did a hard `content[:max_chars]` slice, which
  the moment messages became HTML could cut a tag in half — Telegram rejects
  that with a 400, which `/tools/invoke` reports as HTTP 200 with
  `{"ok": false}`, so the notifier would burn three retries and log a
  permanent delivery failure for what is really a formatting bug. That cap
  also now bounds the **whole** message including its header; it previously
  delivered 3530 characters at a documented limit of 3500.

  Every interpolated value is escaped: target names are user-editable and
  `a<b&c` is a legal one today.

- **Several alerter tests depended on the developer's own shell.** The env
  scrub list in `conftest.py` was missing `STALE_WINDOW`, `ALERT_RESOLVE_AFTER`,
  `ALERT_MAX_PER_HOUR` and `MICROCUT_LOSS_PCT`, so an exported value silently
  changed what they asserted.

- **Deep links no longer point at dashboards that do not exist.** Every
  dashboard UID in `links.py` comes from the InfluxDB provisioning tree, but
  the ClickHouse tree is a parallel set with different UIDs and no CPE
  dashboard at all. Under `TSDB_TYPE=clickhouse` every link the MCP tools
  emitted resolved to a Grafana 404 — while looking entirely valid in the
  answer. Links are now gated on the active backend, the same doctrine the
  module already applied to an unset base URL: no link beats a broken one.
  `system_status()` distinguishes the two reasons, since telling someone to
  set `PUBLIC_BASE_HOST` when it is already set is its own dead end.

- **The flap fix below did not reach the deployed container.** Raising
  `DEFAULT_DOWN_WINDOW` from 900 to 1200 fixed nothing in practice, because
  `docker-compose.yml` pinned `DOWN_WINDOW=${DOWN_WINDOW:-900}` and a Compose
  default silently wins over a module default. Both files read as correct on
  their own; only the pair was wrong. `STALE_WINDOW`, added in the same batch,
  was declared in no compose file, no `.env.template` and no doc at all.

  Fixed, and — more usefully — made impossible to repeat. The instrumentation
  doctor gained **`alerter-env-defaults-match`**, which extracts every env var
  the alerter reads out of its own source with `ast` (including the
  `os.environ.get(...) or DEFAULT_X` idiom) and asserts that each Compose
  `${VAR:-x}` default equals the `DEFAULT_*` constant behind it. Reverting the
  compose line to 900 now fails CI with the specific pair that disagrees.
  A companion **`alerter-env-declared`** warns when a knob exists only in
  Python — discoverable in neither compose, `.env.template`, nor the
  `docs/alerting.md` table.

  `docs/alerting.md` also contradicted itself on this value (900 in the rules
  table, 1200 in the flap-damping section), and `exporter_stale`'s alert text
  still claimed a hardcoded "10m" after the window became configurable and
  moved to 1200 s. The message now reports the window it actually queried.

- **`COMPOSE_PROFILES` is now persisted, not passed once.** Starting an
  optional service with `COMPOSE_PROFILES=mcp docker compose up -d mcp-server`
  leaves it running but unmanaged: the next `docker compose down` removes it
  and the following `up -d` does not bring it back, with nothing to say so.
  That is how this deployment ended up running an MCP server that Compose no
  longer knew about — and when it goes, OpenClaw silently falls back to
  answering from the shell instead of the monitoring data. `setup.sh` now
  writes `COMPOSE_PROFILES` into the generated `.env` alongside `TSDB_TYPE`,
  and `.env.template` documents the full profile list.

- **A flapping incident sent unbounded notifications.** Found the hard way: a
  `target_down` incident on a test target alternated alert/recovery on a
  five-minute cycle and delivered ~48 messages every two hours to a real
  phone, for four hours.

  Three defects at three layers, all now fixed:

  1. `DOWN_WINDOW` was 900 s with `DOWN_MIN_POINTS=3` on a 300 s probe step —
     *exactly* three points, so ordinary window jitter yielded two, the rule
     stopped matching, and the incident looked resolved. Now 1200 s (four
     points where three are required).
  2. Recovery deleted the incident record outright, so the next appearance
     took the first-seen path and alerted immediately. `ALERT_COOLDOWN` only
     ever suppressed *continuously* active incidents and did nothing in the
     one case where it matters. An incident must now be absent for
     `ALERT_RESOLVE_AFTER` (default 900 s) before it counts as recovered;
     reappearing inside that window is silent.
  3. New `ALERT_MAX_PER_HOUR` (default 6): a hard ceiling per incident key,
     independent of the lifecycle logic, so a future lifecycle bug cannot
     reach a phone at that volume. `0` disables it.

  Verified by replaying the observed flap pattern against both versions: 48
  notifications per two hours before, at most 3 after.

- **Alert delivery to OpenClaw now works.** `NOTIFY_MODE=openclaw` was
  unusable: it POSTed to `{OPENCLAW_URL}/hooks/agent`, a path that exists on no
  OpenClaw build. The resulting 404 was read as *"the gateway is WebSocket-only
  and has no HTTP ingress at all"*, and the mode was documented as needing an
  HTTP-RPC plugin or a bridge that was never written — so the alerting engine
  ran for a week evaluating rules correctly and telling nobody.

  The gateway does serve HTTP, multiplexed onto the same port: `POST
  /tools/invoke` is always enabled. Alerts are now delivered by invoking
  OpenClaw's `message` tool through it, authenticated with the Gateway token
  (`OPENCLAW_GATEWAY_TOKEN`; `OPENCLAW_HOOK_TOKEN` still accepted). Verified
  against OpenClaw 2026.7.1-2. The generalisation from one missing route to a
  whole missing protocol is called out in both docs, since the shape of that
  mistake is more useful than the fix.

- **A refused send counted as a delivered alert.** `/tools/invoke` answers
  **HTTP 200 with `{"ok": false}`** when the tool itself fails — a blocked
  tool, a bad channel, an unknown recipient. The notifier checked only the
  status code, so every one of those would have been recorded as success. It
  now inspects the body and retries, for this endpoint only (a generic webhook
  keeps status-code semantics).

- **The preflight could not tell a blocked tool from a missing endpoint.** Both
  answer 404. Reporting the wrong one is exactly the inference that caused the
  bug above, so the body is now read to separate "your tool policy blocks
  `message`" from "that route does not exist", alongside 401 for a rejected
  Gateway token and a connection error for a gateway that is down.

- `NOTIFY_MODE=openclaw` now refuses to run with `OPENCLAW_TO` unset instead of
  posting a message with no recipient and reporting success.


## [2.6.0] — 2026-08-09

Answers that come with the graph, and a tool that checks the monitoring is
actually monitoring.

Two additions that point in the same direction. Deep links close the gap
between "median 8 ms, 0% loss" and the panel that shows what the number is
hiding. The instrumentation doctor closes a wider one: nothing in CI could
tell whether a dashboard, a datasource, or an exporter still agreed with its
neighbours, which is how v2.5.0 shipped a Grafana that would not start.

Upgrade notes:

- **Deep links are off until you configure them.** Set `PUBLIC_BASE_HOST` on
  the mcp-server service to the address a reader can actually open. Unset is a
  supported state, not a broken one — tool responses simply carry no links.
- **The `Grafana dashboards & provisioning` CI job now runs the doctor.** It
  checks strictly more than the validator it replaces, so a repo that passed
  before may now fail — that is the point. Run it locally with
  `PYTHONPATH=shared/modules/doctor python -m doctor`.

### Added

- **An instrumentation doctor** (`shared/modules/doctor/`, `docs/doctor.md`),
  which verifies the monitoring wiring rather than the network: the case where
  a measurement or panel looks fine and silently charts nothing. Nine static
  checks compare each stage of the pipeline against the next — dashboard
  queries against the vocabulary the exporters actually write, datasource
  references against what provisioning declares, dashboard files against the
  paths Grafana scans.

  It replaces the inline JSON/YAML validator in the `Grafana dashboards &
  provisioning` CI job, so every PR is now gated on it. Notably it catches the
  two-datasources-claim-default configuration that made v2.5.0 unable to start
  Grafana, which nothing in CI would have caught before.

  The vocabulary is read out of the exporter source with `ast` rather than
  copied into a list, since a copied list drifts silently — which is the bug
  class the tool exists for. Every check is proved by a test that reintroduces
  the bug and asserts the doctor fails; a doctor that only ever passes is
  indistinguishable from one that does nothing.

  The live checks (target present in the DB but missing from the RRDs or the
  TSDB, queries that error or return empty, loss outside its declared range)
  are not built yet — `docs/doctor.md` lists them.

- **Deep links in MCP tool responses.** Each target now carries a `links`
  object — the Grafana panel scoped to that target and time window, the
  per-ping detail, the side-by-side against its peers, and the web-admin page
  for editing it — so an assistant can hand over the graph instead of only the
  median. `get_microcut_stats` zooms each of its worst-5 windows to a
  ±15-minute range around when it happened.

  Off until configured, deliberately: this host answers on a LAN IP, a
  Tailscale name and possibly a tunnel hostname, and a link to the wrong one
  looks right in the transcript and fails silently on the reader's phone.
  Set `PUBLIC_BASE_HOST` (standard ports appended) or `GRAFANA_PUBLIC_URL` /
  `WEB_ADMIN_PUBLIC_URL` where a proxy hides the ports. Unset means no links
  at all rather than a guess; `system_status()` is the one place that says so.

### Changed

- `get_loss_events` returns a `by_target` rollup (event count, worst loss, the
  span covered, links) alongside the raw events. It was returning up to 500
  individual points with no summary, and a hundred loss points on one target
  is one story rather than a hundred.

### Fixed

- The web-admin login redirect dropped the query string — it redirected to
  `next=request.path`, so opening `/targets/?q=Amazon` while logged out landed
  on the unfiltered list of every target after logging in. Uses `full_path`
  now. Absolute and protocol-relative `next` values are still rejected.
- `/targets/` now honours a `?q=` parameter by pre-filtering the list, so the
  links above open on the target being discussed.

## [2.5.1] — 2026-08-07

A hotfix release. **v2.5.0 does not start Grafana in the default InfluxDB
mode** — a fresh install comes up with no Grafana at all — so 2.5.0 should not
be deployed. Alongside that, two nightly jobs turned out to have been failing
silently (the IPv6 gate was being undone every night; the OCA refresh had not
completed since 2026-08-03), the MCP integration was found not to be wired
despite reading as healthy, and the Pi was found thermally throttled by its
own steady-state logging and probe rates.

The common thread is instrumentation that reports success without doing the
work — none of these four had a failure signal a person would notice. Each fix
adds one.

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
