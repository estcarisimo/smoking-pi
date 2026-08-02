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
| `target_down` | critical | ALL loss points for a target (`latency` + `dns_latency`) in the last window are >= 99.9% loss, with at least 3 points | `DOWN_WINDOW` (300 s) |
| `high_loss` | warning | Mean loss for a target over 15 min exceeds the threshold (targets already down are excluded) | `HIGH_LOSS_PCT` (20 %) |
| `microcut_burst` | warning | Per target+protocol in `cpe_latency`: number of 10 s windows with loss > 0 in the last 60 min reaches the burst count | `MICROCUT_BURST_N` (6) |
| `exporter_stale` | critical | Zero `latency` points written in the last 10 min (global — the RRD exporter is probably stalled) | — |
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

POSTs to `{OPENCLAW_URL}/hooks/agent` with
`Authorization: Bearer {OPENCLAW_HOOK_TOKEN}` and body:

```json
{
  "message": "🔴 critical [target_down]: <target> down ...",
  "name": "SmokePing Alerts",
  "wakeMode": "now",
  "deliver": true,
  "channel": "<your-channel>",
  "to": "<your-chat-id>"
}
```

Example `.env` values (placeholders — substitute your own):

```bash
NOTIFY_MODE=openclaw
OPENCLAW_URL=http://127.0.0.1:18789
OPENCLAW_HOOK_TOKEN=<your-openclaw-hook-token>
OPENCLAW_CHANNEL=<telegram>
OPENCLAW_TO=<your-chat-id>
```

Gateway setup (tokens, channels, pairing) is covered in
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
| `OPENCLAW_HOOK_TOKEN` | — | Bearer token for `/hooks/agent` |
| `OPENCLAW_CHANNEL` | — | Delivery channel, e.g. `<telegram>` |
| `OPENCLAW_TO` | — | Recipient, e.g. `<your-chat-id>` |
| `ALERT_WEBHOOK_URL` | — | Generic webhook endpoint |
| `ALERT_WEBHOOK_TOKEN` | — | Optional bearer token for the webhook |
| `ALERT_INTERVAL` | `60` | Seconds between evaluations |
| `ALERT_COOLDOWN` | `3600` | Seconds before re-notifying an active incident |
| `ALERT_STATE_FILE` | `/var/lib/alerter/state.json` | Incident/report state (atomic writes) |
| `DOWN_WINDOW` | `300` | `target_down` window (seconds) |
| `HIGH_LOSS_PCT` | `20` | `high_loss` threshold (percent) |
| `MICROCUT_BURST_N` | `6` | Lossy windows per 60 min to flag a burst |
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
