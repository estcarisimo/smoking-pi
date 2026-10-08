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
2. On the Pi, set it. It is asked for at a prompt, never typed on the
   command line, and only the services that read it are recreated:

   ```bash
   sudo smoking-pi config set ANTHROPIC_API_KEY
   ```

3. Turn on the `ai` service, keeping the others as they are:

   ```bash
   sudo smoking-pi enable ai
   ```

   A new install can choose it directly: `sudo smoking-pi install
   --profiles ai`.

Optional settings, each with `sudo smoking-pi config set KEY VALUE`:

| Key | Default | What it does |
|---|---|---|
| `AI_MODEL` | `claude-haiku-4-5-20251001` | The model that writes reports and answers chat |
| `REPORT_INTERVAL` | `86400` | Seconds between reports (daily) |
| `AI_REPORTS_PER_DAY` | `8` | Hard cap on report API calls per day |
| `AI_MAX_INPUT_CHARS` | `20000` | Prompt-size guardrail |

From a clone or a package alike: `sudo smoking-pi config set
ANTHROPIC_API_KEY` (asked for, never on the command line), then
`sudo smoking-pi enable ai`, which records the profile so every later
`down` and `up` keeps the service.

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

The loss-event threshold and the definition of a microcut are the shared
ones (`common/aggregates.py`, `common/microcuts.py`), the same code the MCP
server, the alerter and the reports read — see
[Detection reliability](detection-reliability.md). `get_microcut_stats`
answers with cuts and their durations plus the gateway's loss floor, and
`get_loss_events` counts two or more lost pings as an event (15% on a
10-ping probe, whatever the probe sends), so the in-UI
assistant and the OpenClaw one say the same thing about the same night.

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
