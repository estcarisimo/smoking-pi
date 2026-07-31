# AI Insights & In-UI Assistant

Two AI features share one Anthropic API key:

1. **ai-insights** (`shared/modules/ai-insights/`) — a small container that
   periodically pulls latency/loss/microcut aggregates from InfluxDB, asks
   Claude for a plain-language network health report, and writes it as
   Markdown into a shared `reports` volume.
2. **web-admin AI pages** (`/ai/reports` and `/ai/chat`) — the web UI renders
   the generated reports and offers an assistant chat that can inspect stats
   and (with your confirmation) manage targets.

Both are **optional**: without `ANTHROPIC_API_KEY` the ai-insights container
logs a notice and exits cleanly, and the web-admin AI pages render a
"not configured" explanation instead of erroring.

## Enabling

1. Get an API key from <https://console.anthropic.com/> .
2. Add it to the edition's `.env` (next to the generated passwords):

   ```bash
   ANTHROPIC_API_KEY=sk-ant-api03-...
   # optional overrides
   AI_MODEL=claude-haiku-4-5-20251001
   REPORT_INTERVAL=86400        # seconds between reports (daily)
   AI_REPORTS_PER_DAY=8         # hard cap on API calls per day
   AI_MAX_INPUT_CHARS=20000     # prompt-size guardrail
   ```

3. Add the compose snippet below and start with the `ai` profile:

   ```bash
   COMPOSE_PROFILES=ai docker compose up -d
   ```

## Docker Compose snippet

> The `editions/*/docker-compose.yml` files are owned by an in-flight PR, so
> this snippet is documented here rather than committed. Merge it into the
> edition compose file (Pro shown; adjust build path per edition), and pass
> the same `ANTHROPIC_API_KEY`/`AI_MODEL`/`REPORTS_DIR` env vars to the
> existing `web-admin` service plus the read-only `reports` mount.

```yaml
services:
  ai-insights:
    build: ../../shared/modules/ai-insights
    container_name: ai-insights
    profiles: [ai]
    restart: unless-stopped
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
      - AI_MODEL=${AI_MODEL:-claude-haiku-4-5-20251001}
      - REPORT_INTERVAL=${REPORT_INTERVAL:-86400}
      - AI_REPORTS_PER_DAY=${AI_REPORTS_PER_DAY:-8}
      - REPORTS_DIR=/reports
      - INFLUX_URL=http://influxdb:8086
      - INFLUX_TOKEN=${INFLUX_TOKEN}
      - INFLUX_ORG=${INFLUX_ORG:-smokeping}
      - INFLUX_BUCKET=${INFLUX_BUCKET:-smokeping}
    volumes:
      - reports:/reports
    networks:
      - smokeping-net

  web-admin:
    # ... existing definition; ADD:
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
      - AI_MODEL=${AI_MODEL:-claude-haiku-4-5-20251001}
      - REPORTS_DIR=/reports
    volumes:
      - reports:/reports:ro

volumes:
  reports:
```

**Post-merge step:** once the compose PR lands, also add
`ANTHROPIC_API_KEY=`, `AI_MODEL=`, and (optionally) `REPORT_INTERVAL=` /
`AI_REPORTS_PER_DAY=` placeholders to `editions/*/.env.template` so
`setup.sh` carries them into generated `.env` files.

## What the reporter does

Once per `REPORT_INTERVAL` (daily by default) the container:

1. Queries InfluxDB for the last 24 h: per-target median/p95 latency,
   mean/max packet loss, count of >=5 % loss samples (from `latency` and
   `dns_latency`, converting seconds→ms and loss ratio→percent), plus a CPE
   microcut summary from `cpe_latency` (loss already 0–100 %).
2. Renders a compact text summary — capped at the worst ~30 targets by loss
   and truncated at `AI_MAX_INPUT_CHARS` — and sends it to Claude with a
   fixed "network health analyst" system prompt.
3. Writes `report-YYYYMMDD-HHMMSS.md` and refreshes `latest.md` under
   `/reports`. web-admin lists and renders these at **AI → Reports**.

## The in-UI assistant

**AI → Assistant** in web-admin is a chat backed by the same Claude model.
It has read tools (list targets, latency stats, loss events, microcut stats,
system status) that run immediately, and mutating tools (add/remove/toggle
target) that are **never executed directly** — the UI shows a confirmation
card and only executes after you approve. Config regeneration happens
automatically on target changes, so there is no `apply_config` tool.

Responses are non-streaming (Haiku answers small tool-augmented prompts in
a couple of seconds), and the conversation history lives in the browser
tab — reloading the page starts a fresh conversation.

## Cost expectations

The default model is Claude Haiku 4.5 (about $1 per million input tokens,
$5 per million output tokens). A daily report consumes roughly 2–4 k input
tokens and <1 k output tokens — **around a tenth of a cent per report, i.e.
pennies per month**. Chat usage is similarly cheap; the `AI_REPORTS_PER_DAY`
cap (default 8) bounds the reporter even if it is misconfigured to run in a
tight loop.

## Environment variables

| Variable | Default | Used by | Meaning |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | *(unset — AI disabled)* | both | Anthropic API key |
| `AI_MODEL` | `claude-haiku-4-5-20251001` | both | Claude model ID |
| `REPORTS_DIR` | `/reports` | both | Report directory (rw for ai-insights, ro for web-admin) |
| `REPORT_INTERVAL` | `86400` | ai-insights | Seconds between reports in `--loop` mode |
| `REPORT_WINDOW_HOURS` | `24` | ai-insights | Lookback window per report |
| `AI_REPORTS_PER_DAY` | `8` | ai-insights | Daily report cap (state file in `REPORTS_DIR`) |
| `AI_MAX_INPUT_CHARS` | `20000` | ai-insights | Prompt-size cap (truncated with a note) |
| `INFLUX_URL` / `INFLUX_TOKEN` / `INFLUX_ORG` / `INFLUX_BUCKET` | see compose | both | InfluxDB 2.x connection |
