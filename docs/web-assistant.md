# Web admin assistant

The web admin has an assistant chat at `/ai/chat` that can inspect your stats
and, with your confirmation, manage targets. It is **optional**: without
`ANTHROPIC_API_KEY` the page says so and everything else works. For an
assistant in your own chat, with charts and alerts, use
[MCP](mcp-server.md) or [OpenClaw](openclaw-integration.md) instead.

## Enabling

1. Get an API key from <https://console.anthropic.com/> .
2. On the Pi, set it. It is asked for at a prompt, never typed on the
   command line, and only the services that read it are recreated:

   ```bash
   sudo smoking-pi config set ANTHROPIC_API_KEY
   ```

No service to enable: the chat is part of the web admin. To use another
model, `sudo smoking-pi config set AI_MODEL <model>` (default
`claude-haiku-4-5-20251001`).

## What it does

**AI** in web-admin is a chat backed by a Claude model. It has read tools (list targets, latency stats, loss events, microcut stats,
system status, `diagnose_loss`, `get_incident_history`,
`get_notification_history`) that run
immediately, and mutating tools (add/remove/toggle target) that are **never
executed directly** — the UI shows a confirmation
card and only executes after you approve. Config regeneration happens
automatically on target changes, so there is no `apply_config` tool.

The loss-event threshold and the definition of a microcut are the shared
ones (`common/aggregates.py`, `common/microcuts.py`), the same code the MCP
server and the alerter read — see
[Detection reliability](detection-reliability.md). `get_microcut_stats`
answers with cuts and their durations plus the gateway's loss floor, and
`get_loss_events` counts two or more lost pings as an event (15% on a
10-ping probe, whatever the probe sends), so the in-UI
assistant and the OpenClaw one say the same thing about the same night.

It is told how to answer by the same guide every MCP client and the
OpenClaw skill get (`shared/modules/common/guide.py`), after a short header
of its own: target changes need a confirmation, and tools the guide names
that this chat lacks (`get_chart`, `get_wifi_stats`, the mute and ack
tools) are pointed to Grafana instead. `diagnose_loss` and
`get_incident_history` answer through `common/incident_answers.py`, and
`get_notification_history` through `common/notification_ledger.py` (the
alerter's state volume, mounted read-only), the MCP server's own code
paths, and the compose file gives web-admin the same
`WIDESPREAD_PCT` / `VERDICT_BROAD_PCT` / `WIFI_WEAK_*` thresholds. Until
2026-10-13 the chat had a two-sentence prompt of its own and no diagnosis:
it read raw loss events and attributed them itself, so it could still call
the Pi's hung radio an outage after every other assistant had stopped.

Responses are non-streaming (Haiku answers small tool-augmented prompts in
a couple of seconds), and the conversation history lives in the browser
tab — reloading the page starts a fresh conversation.

## Cost expectations

The default model is Claude Haiku 4.5 (about $1 per million input tokens,
$5 per million output tokens). Every model round resends the shared guide and
the tool list (about 4.6 k input tokens before the conversation itself), and
a question that calls two or three tools takes three or four rounds, so expect
one to two cents per question, more for a week-long `diagnose_loss` (up to 50
incidents with their evidence). It costs nothing until someone asks.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(unset: the chat is off)* | Anthropic API key |
| `AI_MODEL` | `claude-haiku-4-5-20251001` | Claude model ID |
| `INFLUX_URL` / `INFLUX_TOKEN` / `INFLUX_ORG` / `INFLUX_BUCKET` | see compose | InfluxDB 2.x connection |

## The daily report it replaced

Earlier releases had an `ai-insights` service that wrote a daily health
report, shown here and pushed by the alerter. It was retired: `smoking-pi upgrade` removes its
container and image, and its `reports` volume stays until `smoking-pi purge`.
