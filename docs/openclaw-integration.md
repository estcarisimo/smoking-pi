# OpenClaw Integration

Two independent integrations, useful separately:

1. **Ask about the network** — register the MCP server with OpenClaw so you can
   ask "any microcuts today?" from Telegram and get a real answer. This works
   against a stock OpenClaw gateway.
2. **Be told about the network** — have the alerter push incidents into a chat.
   This works against a stock gateway too, via its `POST /tools/invoke`
   endpoint; it needs the Gateway token and the `message` tool permitted by
   tool policy. See [Alert delivery](#alert-delivery).

All commands below use placeholders. Nothing machine-specific is committed to
this repository — generate your own tokens and substitute your own ids.

---

## 1. MCP server registration

### Secure the port first

The MCP server exposes the whole tool surface, including mutations (add and
remove targets, restart SmokePing). Compose binds it to `127.0.0.1:8090`, so
the boundary is the host — but any process on that host can reach it. Give it
its own credential:

```bash
openssl rand -hex 32          # put the result in editions/pro/.env
```

```bash
# editions/pro/.env
MCP_API_TOKEN=<your-mcp-token>
```

```bash
cd editions/pro
COMPOSE_PROFILES=mcp docker compose up -d --build mcp-server
```

With `MCP_API_TOKEN` set, every HTTP request needs
`Authorization: Bearer <token>`; without it the transport is unauthenticated
exactly as before. The stdio transport is never gated — the caller already had
to be able to spawn the process.

Verify both paths:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -XPOST http://127.0.0.1:8090/mcp
# 401

curl -s -o /dev/null -w '%{http_code}\n' -XPOST http://127.0.0.1:8090/mcp \
  -H "Authorization: Bearer $MCP_API_TOKEN"
# not 401
```

### Register with OpenClaw

```bash
openclaw mcp set smokeping '{
  "url": "http://127.0.0.1:8090/mcp",
  "transport": "streamable-http",
  "headers": {"Authorization": "Bearer <your-mcp-token>"},
  "connectTimeout": 5,
  "timeout": 30
}'

openclaw mcp doctor smokeping        # static config problems
openclaw mcp probe smokeping         # connect and list capabilities
openclaw mcp reload                  # pick up the new config
```

The full tool surface — including mutations — is available by default, on the
assumption that OpenClaw's agent confirms destructive actions with you in chat
before running them. To expose only read tools instead:

```bash
openclaw mcp tools smokeping --exclude 'add_target,remove_target,restart_smokeping'
```

Use `openclaw mcp show smokeping` to check what ended up in the config, and
`openclaw mcp status` for transport state without connecting.

### Install the skill — do not skip this

**Registering the MCP server is not enough.** An agent that also has shell
access will answer "how is my internet?" by running `ping` and `curl`, because
that is the obvious move and nothing has told it otherwise. It will sound
confident and it will be describing one instant, not your recorded history.
The skill is what redirects it. Symptom of a missing skill: the agent gives
you live speed-test numbers and never mentions your targets.

```bash
./shared/scripts/install-openclaw-skill.sh            # copy + next steps
./shared/scripts/install-openclaw-skill.sh --reload   # and reload the gateway
```

Re-run it after **any** change to the skill. A stale copy is the failure mode
worth guarding against, because it is invisible — the agent keeps answering,
just in the old shape, and the install looks like it silently did nothing:

```bash
./shared/scripts/install-openclaw-skill.sh --check    # exits non-zero if stale
```

It backs up an existing skill to `SKILL.md.bak` before overwriting, since the
four deployment-specific values are easy to have tuned in place. The manual
equivalent, if you prefer it:

```bash
mkdir -p ~/.openclaw/skills/smokeping-monitoring
cp examples/openclaw/smokeping-monitoring/SKILL.md \
   ~/.openclaw/skills/smokeping-monitoring/
openclaw skills list          # confirm smokeping-monitoring is listed
```

The skill's `description` is the part that matters most: OpenClaw uses it to
decide whether to load the skill at all, so it is written to trigger on the
words people actually use ("how is my connection", "cómo está mi conexión",
the host name) and to say explicitly what it is *not* for. If the agent still
reaches for the shell, widen that description rather than the body.

**The skill is written in English and does not make the answers English.** It
carries a report template — traffic lights, `###` sections, one fact per
bullet, both links — and directs the agent to answer in whatever language the
question arrived in, translating the headings and keeping what is not prose:
target names (database keys — a translated `CloudflareDNS` cannot be looked up
or muted), numbers with their units, the lights, and the URLs. Translating the
skill file itself would pin every reply to that one language; leave it alone
and ask in yours.

Re-copy the file after any change to it, and start a **new** chat session:
skills, like the tool set, are cached per session.

### Reload after registering — the gateway caches the tool set

Registering an MCP server does **not** reach a gateway that is already running.
The Codex runtime fingerprints the server set per thread, so a long-lived
gateway and existing chat sessions keep the tool list they started with. This
cost four days of a working server that no agent could see:

```bash
openclaw mcp reload                              # drop cached MCP runtimes
systemctl --user restart openclaw-gateway        # or your service manager
# then start a NEW chat session — existing threads keep the stale set
```

Symptom of skipping it: the agent says the tools "aren't exposed to it", or
quietly answers with live pings while `openclaw mcp probe` reports the server
healthy. Both can be true at once — a separate poller reads the config fresh,
so `probe` and `doctor` pass while agent sessions get nothing.

### Verify with evidence, not with the answer

**Do not judge this by reading the agent's reply.** A well-primed agent
produces a fluent, accurate-sounding answer — correct target names, sensible
caveats — entirely from its shell, while the MCP server sits untouched. That
false positive is exactly how the breakage above went unnoticed.

Ask the question, then check the server:

```bash
openclaw agent --agent main --session-key smokeping-check \
  -m "how has my connection been in the last 6 hours?"

cd editions/pro
docker compose logs mcp-server --since 5m | grep 'tool='
```

A working integration logs the calls it made:

```
tool=system_status args=- -> ok in 83ms
tool=get_latency_stats args=hours=6 -> 19 stats in 40ms
tool=get_microcut_stats args=hours=6 -> 1 stats in 42ms
```

**No `tool=` line means it is not wired**, no matter how good the answer reads.
That is the only check that distinguishes the two cases.

---

## 2. Alert delivery

### What a stock gateway actually provides

The gateway listens on `127.0.0.1:18789` and multiplexes **both** WebSocket and
HTTP on that port. The HTTP surface includes `POST /tools/invoke`, which is
always enabled, plus the OpenAI-compatible `/v1/*` routes. Verified on
2026.7.1-2.

> **This document previously claimed the gateway was WebSocket-only and served
> no HTTP at all.** That was wrong, and it cost the alerting engine a week of
> being unable to deliver. The claim came from probing `/hooks/agent` — a path
> that exists on no OpenClaw build — getting a 404, and generalising from one
> missing route to the entire surface. The correct conclusion from a single 404
> is that one path is absent, not that a protocol is.
>
> `openclaw hooks` really does manage *internal agent lifecycle hooks*
> (`boot-md`, `command-logger`, …) and has nothing to do with inbound HTTP —
> that part was right, and is what made the wrong inference look plausible.

`NOTIFY_MODE=openclaw` sends by invoking the `message` tool:

```bash
curl -s -X POST http://127.0.0.1:18789/tools/invoke \
  -H "Authorization: Bearer $(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.openclaw/openclaw.json')))['gateway']['auth']['token'])")" \
  -H 'Content-Type: application/json' \
  -d '{"name":"message","args":{"action":"send","channel":"telegram",
       "to":"telegram:<chat-id>","message":"hello from the alerter"}}'
```

A successful call returns `{"ok":true,"result":{...,"messageId":"1544"}}`.

Two requirements beyond the token:

1. **Tool policy must permit `message`.** The endpoint is gated by Gateway auth
   *and* tool policy. A filtered tool answers HTTP 200 with
   `{"ok":false,"error":{"message":"Tool not available: message"}}` — allow it
   via `tools.allow` in `openclaw.json`.
2. **`OPENCLAW_TO` must be the OpenClaw address form**, e.g.
   `telegram:123456789`. Find it with `openclaw gateway call sessions-list` and
   read `deliveryContext.to`.

The alerter runs `network_mode: host`, so the loopback default needs no extra
plumbing.

### Verifying delivery, not guessing at it

The alerter probes `/tools/invoke` at startup by invoking `message` with no
arguments. A reachable, permitted tool rejects that with its own validation
error — which is the proof we want: endpoint answered, token accepted, tool not
filtered. Healthy output:

```
INFO alerter.notifier: Delivery preflight: http://127.0.0.1:18789/tools/invoke
reachable, 'message' tool permitted (HTTP 200)
```

The preflight distinguishes three failures that would otherwise look identical:
`401` (wrong Gateway token), `Tool not available` (policy blocks the tool), and
a connection error (gateway down).

Note the endpoint answers **HTTP 200 with `{"ok": false}`** when a tool fails.
A notifier that trusts the status code alone counts every refused send as a
delivered alert, so the alerter inspects the body and retries on `ok: false`.

### The portable alternative

`NOTIFY_MODE=webhook` posts a documented JSON payload to any endpoint, with an
optional bearer token. It works with ntfy, a Slack bridge, Home Assistant, or
the bridge script above, and it has no OpenClaw version dependency:

```bash
NOTIFY_MODE=webhook
ALERT_WEBHOOK_URL=https://your-endpoint.example/hook
ALERT_WEBHOOK_TOKEN=<optional-bearer-token>
```

See [docs/alerting.md](alerting.md) for the payload shape and the rule set.

---

## Security notes

- **Separate tokens.** `MCP_API_TOKEN`, `CONFIG_API_TOKEN`, any alert-delivery
  token, and OpenClaw's own `gateway.auth.token` should all be different
  values. Never reuse the gateway token for an application integration: it is
  the credential for the gateway's full control surface, and a token that
  leaks from one place should not compromise the others.
- **Loopback only.** Both the MCP port and the OpenClaw gateway bind to
  `127.0.0.1`. Keep it that way; reach them remotely over Tailscale or an SSH
  tunnel rather than by binding to `0.0.0.0`.
- **Nothing real in git.** `.env` is gitignored and every value in
  `.env.template` ships empty. The skill in `examples/` uses placeholders. Keep
  your actual tokens, chat ids, and hostnames out of commits.
- **Mutations are real.** The MCP tool surface can change monitoring config and
  restart SmokePing. If the chat channel is shared, or you would rather not
  rely on the agent's confirmation step, use the `--exclude` filter above.
