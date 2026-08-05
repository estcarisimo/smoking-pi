# OpenClaw Integration

Two independent integrations, useful separately:

1. **Ask about the network** — register the MCP server with OpenClaw so you can
   ask "any microcuts today?" from Telegram and get a real answer. This works
   against a stock OpenClaw gateway.
2. **Be told about the network** — have the alerter push incidents into a chat.
   This needs an HTTP ingress that a stock gateway does **not** provide; see
   [Alert delivery](#alert-delivery) before configuring it.

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

Verify the whole path end to end:

```bash
openclaw agent --agent main --session-key smokeping-check \
  -m "Using the smokeping tools, how has my connection been in the last 6 hours?"
```

A correct answer cites your target names and a time window. An answer full of
speed-test megabits and a single ping run means the skill is not loading.

---

## 2. Alert delivery

### What a stock gateway actually provides

The OpenClaw gateway is a **WebSocket** service. Verified on 2026.7.1-2: it
listens on `127.0.0.1:18789`, serves `/` and `/docs`, and returns **404 for
`/hooks/agent` and every other HTTP path**. `openclaw hooks` manages *internal
agent lifecycle hooks* (`boot-md`, `command-logger`, …) — it has nothing to do
with inbound HTTP.

So `NOTIFY_MODE=openclaw` will not work against a stock gateway. Point it at a
real HTTP ingress:

- an OpenClaw HTTP-RPC plugin (see `openclaw plugins list` for what your build
  offers — `@openclaw/admin-http-rpc` exposes an admin HTTP RPC endpoint), or
- a small bridge of your own that accepts a POST and shells out to
  `openclaw message send --channel telegram --target <chat-id> --message ...`,
  which is the supported send path in this version.

`OPENCLAW_HOOK_PATH` sets the path (default `/hooks/agent`) so you can mount
whichever route your ingress actually serves.

The alerter probes the configured endpoint at startup and logs an explicit
error when it 404s, so a wrong assumption shows up immediately in
`docker compose logs alerter` rather than as silent non-delivery:

```
ERROR alerter.notifier: Delivery preflight: http://127.0.0.1:18789/hooks/agent
returned 404. The endpoint does not exist — a stock OpenClaw gateway is
WebSocket-only and serves no HTTP hook route.
```

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
