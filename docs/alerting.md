# Alerting Engine (`alerter`)

Deterministic alerting for the Pro edition: a small Python service that
evaluates rules against InfluxDB every minute and delivers notifications via
an [OpenClaw](https://openclaw.ai) hook or a generic webhook. No LLM
involved — the rules are plain thresholds, so behaviour is predictable and
testable.

Module: `shared/modules/alerter/`. Opt-in via the `alerts` Compose profile:

```bash
cd editions/pro
COMPOSE_PROFILES=alerts docker compose up -d alerter
```

The service runs with `network_mode: host`, so it reaches InfluxDB on
`localhost:8086` and a locally running OpenClaw gateway on
`127.0.0.1:18789` without extra wiring.

## Rules

| Rule | Severity | Condition | Tunables (default) |
|------|----------|-----------|--------------------|
| `target_down` | critical | ALL loss points for a target (`latency` + `dns_latency`) in the last window are >= 99.9% loss, with at least 3 points | `DOWN_WINDOW` (1200 s) |
| `high_loss` | warning | Mean loss for a target over 15 min exceeds the threshold (targets already down are excluded) | `HIGH_LOSS_PCT` (20 %) |
| `microcut_burst` | warning | Per target+protocol in `cpe_latency`: number of 10 s windows whose loss exceeds `MICROCUT_LOSS_PCT` in the last 60 min reaches the burst count | `MICROCUT_BURST_N` (2), `MICROCUT_LOSS_PCT` (50 %) |
| `exporter_stale` | critical | Zero `latency` points written in the last window (global — the RRD exporter is probably stalled) | `STALE_WINDOW` (1200 s) |
| `ipv6_down` | warning | Every IPv6 target (name ends in `6`, or an `fping6`-ish category) at 100% loss for 15 min while at least one IPv4 target is healthy; emits ONE aggregate incident | — |

Loss semantics match the exporters: `latency`/`dns_latency` loss is a 0-1
ratio (legacy packet counts 0..20 are clamped), `cpe_latency` loss is a
percent 0-100.

## Incident lifecycle

State lives in a JSON file (`ALERT_STATE_FILE`, default
`/var/lib/alerter/state.json` on the `alerter-state` volume; falls back to
`/tmp/alerter-state.json` if unwritable). Writes are atomic.

- **First seen** → notification fires immediately.
- **Still active** → silent until `ALERT_COOLDOWN` (default 3600 s) has
  elapsed since the last notification, then re-notifies.
- **Cleared** → a single recovery notice (only if the incident ever fired).

Per incident the state tracks `first_seen`, `last_seen`, `last_notified`,
and `notified_count`.

## Notifier modes (`NOTIFY_MODE`)

Messages carry a severity headline (🔴 critical, 🟡 warning, ✅ recovery), the
verdict line, the numbers, a breadth recap, deep links, and a mute hint —
composed to fit the channel's budget. See [The verdict](#the-verdict-is-it-me-or-the-internet)
and [Message rendering](#message-rendering) below.

### `off` (default)

Log-only. Rules are still evaluated and incidents logged, so you can watch
`docker compose logs alerter` before wiring up delivery.

### `openclaw`

Invokes OpenClaw's `message` tool over the Gateway's HTTP endpoint,
`POST {OPENCLAW_URL}/tools/invoke`, with
`Authorization: Bearer {OPENCLAW_GATEWAY_TOKEN}` and body:

```json
{
  "name": "message",
  "args": {
    "action": "send",
    "channel": "telegram",
    "to": "telegram:123456789",
    "message": "🔴 <b>critical</b> — <target>\n🌐 Not you — 12 of 16 ..."
  }
}
```

`/tools/invoke` is always enabled on a stock gateway and is multiplexed onto
the same port as the WebSocket protocol. It is gated by Gateway auth plus
tool policy — so the `message` tool must also be permitted by
`tools.allow` in `openclaw.json`.

Example `.env` values:

```bash
NOTIFY_MODE=openclaw
OPENCLAW_URL=http://127.0.0.1:18789
OPENCLAW_GATEWAY_TOKEN=<gateway.auth.token from ~/.openclaw/openclaw.json>
OPENCLAW_CHANNEL=telegram
OPENCLAW_TO=telegram:<your-chat-id>
```

Find your recipient with `openclaw gateway call sessions-list` and read
`deliveryContext.to`.

The alerter runs with `network_mode: host`, so the loopback default reaches
the gateway with no extra plumbing.

> **Earlier versions of this document said a stock gateway serves no HTTP at
> all.** That was wrong. It came from probing `/hooks/agent` — a path that
> exists on no OpenClaw build — getting a 404, and generalising from one
> missing route to the whole surface. `/tools/invoke`, `/v1/*` and the other
> documented HTTP APIs were there the entire time.

Two failure modes are specific to this endpoint, and the alerter checks for
both at startup:

- It answers **HTTP 200 with `{"ok": false}`** when the tool itself fails.
  Trusting the status code alone would count a refused send as a delivered
  alert, so the body is inspected and a failure is retried.
- It answers `Tool not available: message` when tool policy filters the tool
  out, rather than when it is missing. The preflight distinguishes that from
  a bad token (401) and an unreachable gateway.

A healthy preflight logs:

```
INFO alerter.notifier: Delivery preflight: http://127.0.0.1:18789/tools/invoke
reachable, 'message' tool permitted (HTTP 200)
```

Use `webhook` mode below if you want something with no OpenClaw version
dependency. Full recipe, including MCP registration:
[docs/openclaw-integration.md](openclaw-integration.md).

### `webhook`

POSTs JSON to `ALERT_WEBHOOK_URL`, optionally with
`Authorization: Bearer {ALERT_WEBHOOK_TOKEN}`:

```json
{
  "type": "alert",
  "rule": "high_loss",
  "severity": "warning",
  "target": "<target>",
  "message": "🟡 <b>warning</b> — <target>\n🎯 Just that site — 1 of 16 ...",
  "state": {"first_seen": 0, "last_seen": 0, "last_notified": 0, "notified_count": 1},
  "ts": "2026-01-01T00:00:00+00:00"
}
```

`type` is `alert`, `recovery`, or `report`.

Delivery (both HTTP modes): 10 s timeout, 3 attempts with exponential
backoff. Failures are logged, never crash the loop.

## Daily report delivery

If the `ai-insights` service (profile `ai`) is writing Markdown reports to
the shared `reports` volume, the alerter delivers the newest `report-*.md`
through the same notifier — at most one per `REPORT_DELIVERY_INTERVAL`
(default 86400 s), prefixed with `Daily network health report:` and trimmed to
`REPORT_MAX_CHARS` (default 3500, Telegram-friendly). The limit bounds the
**whole** message including that header, and trims on a line boundary. A
missing reports directory is skipped quietly.

## Environment reference

| Variable | Default | Purpose |
|----------|---------|---------|
| `NOTIFY_MODE` | `off` | `off`, `openclaw`, or `webhook` |
| `OPENCLAW_URL` | `http://127.0.0.1:18789` | OpenClaw gateway base URL |
| `OPENCLAW_HOOK_PATH` | `/tools/invoke` | Path appended to `OPENCLAW_URL`; override only for a proxy or bridge |
| `OPENCLAW_GATEWAY_TOKEN` | — | Gateway token (`gateway.auth.token` in `openclaw.json`) |
| `OPENCLAW_HOOK_TOKEN` | — | Legacy alias for the above; `OPENCLAW_GATEWAY_TOKEN` wins |
| `OPENCLAW_CHANNEL` | `telegram` | Delivery channel |
| `OPENCLAW_TO` | — | Recipient in OpenClaw's address form, e.g. `telegram:123456789` — a bare chat id will not deliver. Find it with `openclaw gateway call sessions-list` |
| `ALERT_WEBHOOK_URL` | — | Generic webhook endpoint |
| `ALERT_WEBHOOK_TOKEN` | — | Optional bearer token for the webhook |
| `ALERT_INTERVAL` | `60` | Seconds between evaluations |
| `ALERT_COOLDOWN` | `3600` | Seconds before re-notifying an active incident |
| `ALERT_RESOLVE_AFTER` | `900` | Seconds an incident must be absent before a recovery notice fires (flap damping) |
| `ALERT_MAX_PER_HOUR` | `6` | Hard ceiling on notifications per incident per rolling hour; `0` disables |
| `ALERT_STATE_FILE` | `/var/lib/alerter/state.json` | Incident/report state (atomic writes) |
| `DOWN_WINDOW` | `1200` | `target_down` window (seconds). SmokePing probes on a 300 s step and `DOWN_MIN_POINTS` is 3, so this must span **strictly more** than 3 steps — at exactly 3 the rule stops matching whenever jitter costs it one point, and the incident flaps. See [Flap damping](#flap-damping-and-why-the-cooldown-alone-is-not-enough) |
| `STALE_WINDOW` | `1200` | `exporter_stale` window (seconds); same step arithmetic as `DOWN_WINDOW` |
| `HIGH_LOSS_PCT` | `20` | `high_loss` threshold (percent) |
| `MICROCUT_BURST_N` | `2` | Microcut windows per 60 min to flag a burst. Counts OBSERVED windows, so it tracks the probe duty cycle: the detector samples a 10 s window every `CPE_PROBE_WINDOW + CPE_PROBE_IDLE` seconds (~120 windows/hour by default). Rescale it if you change `CPE_PROBE_IDLE` |
| `MICROCUT_LOSS_PCT` | `50` | Loss percent above which a 10 s CPE window counts as a microcut. CPE gateways rate-limit ICMP, giving a constant single-digit loss floor, so counting any loss at all would flag that floor permanently |
| `REPORTS_DIR` | `/reports` | Where ai-insights reports are read from |
| `REPORT_DELIVERY_INTERVAL` | `86400` | Min seconds between report deliveries |
| `REPORT_MAX_CHARS` | `3500` | Truncation limit for a delivered report, **including** the header — it bounds the whole message, since that is what the channel budget applies to |
| `ALERT_MARKUP` | `html` | `html` (Telegram's parse mode) or `plain` |
| `VERDICT_BROAD_PCT` | `60` | Share of measurable targets impaired before a problem counts as broad |
| `VERDICT_MIN_TARGETS` | `3` | Below this many measurable targets, breadth means nothing |
| `VERDICT_IMPAIRED_LOSS_PCT` | `10` | Mean loss percent at which a target counts as impaired |
| `VERDICT_STALE_DOWN_HOURS` | `6` | A target at 100% for longer than this is treated as a host that never answered ICMP, and excluded from breadth |
| `ALERT_MUTES_FILE` | `/var/lib/alerter-mutes/mutes.json` | Suppression windows. **Written by mcp-server, read-only here** — see [Muting](#muting-alerts-without-losing-them) |
| `INFLUX_URL` | `http://localhost:8086` | InfluxDB (host network namespace) |
| `INFLUX_TOKEN` / `INFLUX_ORG` / `INFLUX_BUCKET` | — / `smokeping` / `smokeping` | InfluxDB auth/scope |

All keys ship EMPTY in `editions/pro/.env.template`; nothing
machine-specific is baked into the images or compose files.

## The verdict: is it me or the internet?

Every alert carries one line that answers the question the alert actually
raises. `amazon: mean loss 22.4% over 15m` tells you something happened;
`🌐 Not you — 12 of 16 destinations affected but your local link is clean`
tells you what to do about it.

It is deterministic, and it costs **no extra queries**. Everything it needs is
already fetched by the rules and was previously thrown away: the mean-loss row
for every target (breadth), the CPE microcut counts (the local link), and
exporter liveness. That matters on a Pi that has already hit its thermal limit.

Scopes, in precedence order — the first match wins:

| Scope | Meaning |
|---|---|
| `monitoring` | `exporter_stale` fired. **Outranks everything unconditionally**: announcing a network fault from an *absence* of data is the worst thing this can do |
| `local_link` | Broad impairment *and* the first hop is dropping |
| `isp_upstream` | Broad impairment with a clean first hop |
| `ipv6` | Every impaired target is IPv6 while IPv4 is healthy |
| `dns` | Every impaired target is a resolver |
| `remote_target` | One or two impaired, peers in the same category fine |
| `unclear` | States the numbers and claims nothing |

Two deliberate properties:

- **The CPE floor cannot trigger `local_link`.** The gateway rate-limits ICMP,
  so `cpe_latency` sits at a permanent single-digit loss floor (observed p50
  10%, p99 30%) with nothing wrong. The verdict reads `micro_rows`, which only
  ever contains windows above `MICROCUT_LOSS_PCT` (50%) — the floor is
  invisible to it by construction. Never feed raw CPE loss in here.
- **Hosts that never answer ICMP are excluded from breadth.** Bare
  `amazon.com` does not respond and charts a permanent flat 100%. Counting
  such targets turns one slow site into an ISP outage — with six of them and
  one genuinely slow site, 7 of 10 looks broad. Anything at 100% whose
  `target_down` incident is older than `VERDICT_STALE_DOWN_HOURS` is dropped
  from both the numerator and the denominator.

Every verdict logs its own inputs at INFO (`verdict inputs: 12/16 impaired
(75.0%), cpe_cutting=…, excluded_chronic=…`), so a verdict you disagree with
can be diagnosed from `docker compose logs alerter` without reproducing the
moment it was made.

## The daily digest

Alerts only fire when something breaks, so on a good day the channel is
silent — and silence has two meanings: nothing happened, or the monitoring
stopped. The digest resolves that on a schedule.

Off by default. It needs `NOTIFY_MODE` set to deliver anywhere.

| Variable | Default | Meaning |
|---|---|---|
| `DIGEST_ENABLED` | `false` | Opt in |
| `DIGEST_AT` | `08:30` | Wall-clock `HH:MM`; anything else disables with a warning |
| `DIGEST_TZ` | *(unset)* | Zone for `DIGEST_AT`; falls back to `TZ`, then UTC |
| `DIGEST_WINDOW_HOURS` | `24` | How far back it summarises |
| `DIGEST_MAX_LATENESS` | `14400` | Seconds a missed slot may still be delivered |
| `DIGEST_SILENT` | `true` | Deliver without a notification sound |
| `DIGEST_HISTORY_MAX` | `200` | Cap on the retained notification history |

### Fires once, however the clock behaves

The loop wakes every `ALERT_INTERVAL` seconds and has no idea what time it
is between ticks, which is where double-sends live. Each tick resolves the
most recent scheduled instant at or before now — the **slot** — and state
records which slot last fired. Firing is idempotent on that: ten ticks in the
same minute resolve the same slot and do nothing.

**The slot is persisted, not the send time.** That is the whole mechanism.
Anything recorded *before* the slot instant — which is what a send-time-like
value becomes after an NTP correction backwards, or on a container whose RTC
starts behind — re-fires the same day. A test pins this by reintroduction.

Past `DIGEST_MAX_LATENESS` the slot is recorded as fired **without sending**.
A Pi that was off for two days must not deliver 08:30's digest at 19:00, and
must not deliver two. Both DST transitions are covered by tests: a
nonexistent wall time resolves to one stable instant, and an ambiguous one
picks the same instant every tick.

Rate limiting is structural rather than a parallel budget — one slot per day,
idempotent on the persisted slot, at most 3 delivery attempts before the slot
is retired. There is nothing to tune.

### It never claims health it did not verify

If the aggregate query raises, or returns zero targets, **nothing is sent**
and the slot is retired with `last_error`. Reporting "all clear" when the
truth is "InfluxDB did not answer" would convert a broken monitor into a
reassuring message — precisely the failure the digest exists to catch.

### What it says

The same shape as everything else: traffic lights, bold sections, worst
targets first, capped at five. Counts come from `state["history"]`, appended
on each alert or recovery the alerter decides to send — regardless of
whether delivery then succeeded — and pruned to `DIGEST_HISTORY_MAX` and 48
hours — necessary because `reconcile()` pops a record on recovery, so by
08:30 an incident that fired and cleared at 03:00 has left no other trace.

With the `ai` profile enabled, `reports_watcher` also delivers LLM-written
reports. Both paths then run; they are independent.

## Message rendering

Messages are Telegram HTML. Two budgets apply: 4096 characters for a plain
message, **1024 for a caption** on a message carrying an image — so a chart
costs three quarters of the room.

Rather than truncate, sections are dropped by priority until the message fits,
so what survives is always the headline, the verdict and the numbers. Drop
order is the mute hint, then the breadth recap (the verdict line already gives
that number), then the links — links last, because a static image caption
cannot be explored and the link is the way out of it. Only if the headline
alone still overflows is text trimmed, and then on a line boundary: an HTML tag
cut in half makes Telegram reject the whole message with a 400, which
`/tools/invoke` reports as HTTP 200 with `{"ok": false}`.

Every interpolated value is escaped. Target names are user-editable, and
`a<b&c` is a legal one.

### The links row

`graph · per-ping · peers · edit`, followed by `🌐 anywhere` when a tunnel is
configured (`TUNNEL_BASE_HOST` — see
[Two tiers](mcp-server.md#two-tiers-at-home-and-from-anywhere)). An alert is
read on a phone that may or may not be on the home network, and a LAN URL is a
dead link from a train.

Only the *graph* gets a from-anywhere twin, not all four. A Grafana deep link
runs to about 120 characters; mirroring the whole row would spend a quarter of
a caption budget saying the same thing twice. Whoever is on cellular wants the
picture, and the rest of the dashboard is one tap from it.

Set `ALERT_MARKUP=plain` for a channel that does not parse HTML.

## Testing

One-shot evaluation (no loop) with whatever `NOTIFY_MODE` is configured:

```bash
cd editions/pro
COMPOSE_PROFILES=alerts docker compose run --rm alerter python main.py --once
```

With `NOTIFY_MODE=off` this prints the active incidents without sending
anything — a safe dry run. Unit tests live in
`shared/modules/alerter/tests/` and run in CI (module auto-discovery):

```bash
cd shared/modules/alerter
pip install -r tests/requirements.txt
pytest -q
```


## Muting alerts without losing them

Mutes suppress the *sending* of a notification. Incidents are still evaluated,
still recorded, still counted, and still shown in the digest — the only thing
that changes is whether a phone buzzes.

Control is natural language onto MCP tools rather than buttons on a message:
`mute_alerts`, `unmute_alerts`, `ack_incident`, `list_alert_state`. Telegram
buttons cannot call back without an OpenClaw channel plugin, and the tools are
the better interface anyway — "mute amazon for two hours, I'm reflashing the
router" carries a scope, a duration and a reason that no button could.

### Single-writer, so there is no lock

Two containers share this state. Neither locks, and neither needs to:

| file | writer | readers |
|---|---|---|
| `/var/lib/alerter/state.json` | alerter | alerter (rw), mcp-server (**ro**) |
| `/var/lib/alerter-mutes/mutes.json` | mcp-server | mcp-server (rw), alerter (**ro**) |

Neither container ever read-modify-writes a file the other writes, so the race
is eliminated by construction — there is no lock to acquire and none to leak.
The `:ro` bind mounts in `docker-compose.yml` make that an OS-enforced
invariant rather than a convention a later change can quietly break.

Cross-container *reads* are safe because both writers use a temp file in the
same directory plus `os.replace`. That was introduced as crash-safety; it is
now also the concurrency contract, because `os.replace` is atomic within a
filesystem — a reader sees the whole old file or the whole new one, never a
torn one. **Do not "optimise" either writer into an in-place write.**

Expiry is evaluated at read time and pruned lazily on the next write, so
readers never need to write a file their mount forbids them from writing.

### Muting is the one feature that can cause a missed outage

So every mitigation lives here rather than being left to operator discipline:

- **24-hour cap** (`mutes.MAX_HOURS`). A longer request is clamped, not
  rejected, and the response says what it actually granted.
- **Every digest lists active mutes** and how many alerts each has swallowed
  (`muted_suppressed_count`) — the number that reveals a mute set two days ago
  and forgotten.
- **Recoveries are never muted for an incident that was already announced.**
  If you were told something broke, you get told when it is fixed.
- **An incident muted from first sight produces no recovery either**, because
  `notified_count` stays 0 and the recovery branch gates on it. Announcing the
  end of something nobody heard about is just noise.
- **`unmute_alerts(all=True)`** clears everything in one call.
- **A missing or corrupt mutes file means everything alerts.** Delivery must
  never depend on this file being readable; the failure mode has to be noise,
  not silence.

There is deliberately **no catch-up on unmute**: a still-active incident
re-alerts once on the normal cooldown path, and suppressed alerts are dropped
rather than queued. A burst of stale notifications on unmute would be its own
kind of failure.

### Where the check sits, and why

In `state.reconcile()`, the mute is consulted **after** the cooldown check and
**after** `_rate_limited()`, and never calls `_record_notification()`. Each
position matters:

- After the rate limiter, so an alert the ceiling already blocked is not also
  counted as one the mute suppressed. (This is an accounting guarantee, not a
  delivery one — either order stops the send.)
- Before `_record_notification()`, because the budget counts what was actually
  sent. A muted incident consumes none of it.
- Inside the incident loop, so `last_seen`, the `missing_since` clearing and
  the severity refresh still run. Skipping them would leave the incident
  looking brand new when the mute lifts, sending it down the first-seen path
  and alerting immediately — reintroducing exactly the flapping described
  below.

## Flap damping, and why the cooldown alone is not enough

`ALERT_COOLDOWN` only suppresses repeats for an incident that stays
*continuously* active. It does nothing for one that oscillates, because a
recovery used to delete the incident record outright — so the next appearance
looked brand new, took the first-seen path, and alerted immediately.

That is not hypothetical. A `target_down` incident on an unroutable test target
flapped on a five-minute cycle and sent **48 notifications every two hours**,
indefinitely, to a real phone. The trigger was `DOWN_WINDOW=900` with
`DOWN_MIN_POINTS=3` on a 300 s probe step: 900/300 is *exactly* three points, so
any timing skew produced two, the rule stopped matching, and the incident
"recovered" — then re-fired one cycle later.

Three changes, at three different layers:

1. **`DOWN_WINDOW` is now 1200 s** — four points where three are required, so
   one point of slack absorbs the jitter. If you retune either value, keep
   `DOWN_WINDOW / 300` strictly greater than `DOWN_MIN_POINTS`.

   Raising the constant in `evaluator.py` was not enough on its own:
   `docker-compose.yml` pinned `DOWN_WINDOW=${DOWN_WINDOW:-900}`, so the
   deployed container kept getting 900 and the fix did nothing where it
   mattered. **A compose default silently overrides a module default**, and
   nothing compared the two. `doctor`'s `check_alerter_env_defaults_match` now
   does, and fails CI when they disagree — the fix for the fix.
2. **An incident must be absent for `ALERT_RESOLVE_AFTER` (default 900 s)
   before it counts as recovered.** Reappearing inside that window is silent:
   it never recovered, so there is nothing to announce, and the cooldown keeps
   governing re-alerts. This protects every rule, not just the one that flapped.
3. **`ALERT_MAX_PER_HOUR` (default 6) is a hard ceiling per incident key**,
   independent of the lifecycle logic above. It is a blast-radius limit rather
   than a tuning knob — unreachable in normal operation, and it caps the damage
   if the lifecycle is ever wrong again.

Layer 1 fixes the specific trigger; layer 2 fixes the class of bug; layer 3
bounds the cost of the next one. Set `ALERT_MAX_PER_HOUR=0` to disable the
ceiling if you genuinely want unbounded delivery.

Note also that `high_loss` and `target_down` both match a fully-down target, so
a real outage produces two notifications by design — one warning, one critical.
