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

Messages are terse one-liners with an emoji severity prefix:
🔴 critical, 🟠 warning, ✅ recovery.

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
    "message": "🔴 critical [target_down]: <target> down ..."
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
  "message": "🟠 warning [high_loss]: <target>: mean loss 34.0% over 15m",
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
(default 86400 s), truncated to `REPORT_MAX_CHARS` (default 3500,
Telegram-friendly), prefixed with `Daily network health report:`. A missing
reports directory is skipped quietly.

## Environment reference

| Variable | Default | Purpose |
|----------|---------|---------|
| `NOTIFY_MODE` | `off` | `off`, `openclaw`, or `webhook` |
| `OPENCLAW_URL` | `http://127.0.0.1:18789` | OpenClaw gateway base URL |
| `OPENCLAW_HOOK_PATH` | `/tools/invoke` | Path appended to `OPENCLAW_URL`; override only for a proxy or bridge |
| `OPENCLAW_GATEWAY_TOKEN` | — | Gateway token (`gateway.auth.token` in `openclaw.json`) |
| `OPENCLAW_HOOK_TOKEN` | — | Legacy alias for the above; `OPENCLAW_GATEWAY_TOKEN` wins |
| `OPENCLAW_CHANNEL` | — | Delivery channel, e.g. `<telegram>` |
| `OPENCLAW_TO` | — | Recipient, e.g. `<your-chat-id>` |
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
| `REPORT_MAX_CHARS` | `3500` | Report truncation limit |
| `INFLUX_URL` | `http://localhost:8086` | InfluxDB (host network namespace) |
| `INFLUX_TOKEN` / `INFLUX_ORG` / `INFLUX_BUCKET` | — / `smokeping` / `smokeping` | InfluxDB auth/scope |

All keys ship EMPTY in `editions/pro/.env.template`; nothing
machine-specific is baked into the images or compose files.

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
   nothing compared the two. `doctor`'s `check_alerter_env_declared` now
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
