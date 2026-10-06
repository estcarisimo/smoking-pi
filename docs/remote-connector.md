# Connecting any assistant

Every assistant that talks to Smoking Pi is an **MCP client**: OpenClaw on
the Pi, a cloud assistant or agent (Claude, ChatGPT, Grok, Meta's, or the
next one). There is one way in for all of them, and nothing here is specific
to one: an assistant that can add a *remote MCP server* connects with a URL
and a pairing code. One that cannot needs an adapter of its own, not a
change here.

- An assistant **on the Pi itself** (OpenClaw on the reference deployment)
  uses the local token: `sudo smoking-pi connect openclaw`
  ([openclaw-integration.md](openclaw-integration.md)).
- An assistant **anywhere else** — one that runs in the cloud and can add a
  *remote MCP server* or *custom connector* — signs in with a one-time
  pairing code: `sudo smoking-pi connect NAME`. This page.

What every assistant gets is the same: the server's own instructions and the
tool descriptions, sent when it connects. There is no per-assistant prompt
to write or keep in step.

Pro edition only (the MCP server is a Pro service). Off until you turn it on.

## What a remote assistant can do

**Read, never change.** It gets the read tools: `diagnose_loss`,
`get_latency_stats`, `get_loss_events`, `get_microcut_stats`,
`get_wifi_stats`, `system_status`, `list_targets`, `list_alert_state` and
`get_chart`. The tools that change something — adding, removing or pausing
targets, applying the configuration, muting alerts — answer *read-only* to
it, and `get_chart` returns its picture but does not post it into your chat.
Changes stay with the web admin and with the assistant on the Pi.

Each assistant has its own tokens: one-hour access tokens and a refresh
token that is replaced every time it is used. `sudo smoking-pi disconnect
NAME` cuts one off; the others keep working.

## Turning it on

You need an HTTPS address that reaches this Pi's MCP server: a **tunnel**
that runs on the Pi, accepts HTTPS at a public name and forwards to
`http://127.0.0.1:8090`. Any tunnel that does that works (Cloudflare Tunnel,
Tailscale Funnel and ngrok are common choices); set it up by its own
instructions, with three requirements:

1. **It forwards to `127.0.0.1:8090` on the Pi**, and nothing else from this
   hostname. Do not change `MCP_HOST`: the server stays on loopback and the
   tunnel is the only way to it.
2. **It forwards every path** (`/mcp`, `/authorize`, `/token`, `/register`,
   `/revoke`, `/connector/pair` and `/.well-known/...`): the sign-in lives
   next to the server.
3. **It keeps the `Host` header** of the public name. The server refuses a
   request whose host is neither loopback nor that name.

Then tell Smoking Pi the address (no path):

```bash
sudo smoking-pi config set MCP_PUBLIC_URL https://mcp.example.com
```

That recreates the MCP server with sign-in on. Check it from anywhere:

```bash
curl -s https://mcp.example.com/.well-known/oauth-authorization-server
```

It answers JSON naming that address as its `issuer`. A request to `/mcp`
without a token gets `401`.

## Connecting an assistant

On the Pi:

```bash
sudo smoking-pi connect grok
```

```text
Connector URL:  https://mcp.example.com/mcp
Pairing code:   K7QM-4XRA   (for 'grok', valid 10 minutes, once)
```

The name is a label you choose; it is how you will recognize and disconnect
this assistant later. In the assistant, add a custom connector (or remote
MCP server) with that URL. It opens a Smoking Pi page in your browser that
asks for the code; type it, and the assistant is connected. The page names
the code's label (`grok` here) and where it will send you back; check both.
The code works once, for ten minutes, and five wrong tries burn it — make a
new one with the same command.

Then ask it something the measurements answer: *what happened last night,
and was it me or the internet?* It should call `diagnose_loss`.

```bash
sudo smoking-pi connect              # who is connected, and the URL
sudo smoking-pi disconnect grok      # sign one out
```

To turn remote access off altogether, stop the tunnel and clear the address:
`sudo smoking-pi config unset MCP_PUBLIC_URL`. Every remote token stops
working with it.

## How it works

The sign-in is OAuth 2.1, the method the MCP specification gives remote
servers, which is why assistants can connect with nothing but a URL. The MCP
SDK serves the endpoints (server metadata, client registration,
authorization, token, revocation) and checks the bearer on every `/mcp`
request; `shared/modules/mcp-server/connector.py` decides who gets a token.

- **There are no accounts.** The only credential is the pairing code, and
  only someone with `sudo` on the Pi can make one.
- **Tokens are stored hashed** in the `mcp-connector` volume
  (`/var/lib/mcp-connector/connector.json`). Losing that volume signs every
  remote assistant out, and nothing else.
- **Registration is open,** as the specification requires, and harmless on
  its own: a registered client has no token until someone types a pairing
  code. A registration that never pairs is dropped after a day, and at most
  50 wait at once.
- **The local token (`MCP_API_TOKEN`) keeps full access on this machine
  only.** A request that came through the tunnel carries the public name in
  its `Host` header, and the local token is not honored for it: a leaked
  local token is not a key to the Pi from the internet.
- **The sign-in page shows what only you control:** the name the live code
  was made for, and the address the sign-in returns to. If either is not
  what you expect, do not type the code. The page cannot be framed or
  cached.
- **A known trade-off:** anyone who can reach the URL can use up the live
  code's five tries, or fill the 50 waiting registrations. Nothing is
  exposed by it; the sign-in fails and you make a new code. Counting tries
  per sign-in instead would let someone guessing open as many sign-ins as
  they like.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `Remote assistants are off` | `MCP_PUBLIC_URL` is not set |
| The assistant cannot reach the URL | The tunnel is down, or forwards somewhere other than `127.0.0.1:8090` |
| `421` or `403` from `/mcp` | The tunnel rewrote the `Host` header; keep the public name |
| The metadata names another address | `MCP_PUBLIC_URL` differs from the address the assistant uses |
| `401` with the local token through the tunnel | By design: the local token works on the Pi only |
| *This sign-in expired* | More than ten minutes passed, or the server restarted mid-sign-in; start again from the assistant |
| The code is accepted but the assistant never finishes connecting | Smoking Pi before 2.24.2: the browser blocked the return to the assistant. Upgrade, make a new code and reconnect |
| The assistant says a tool is read-only | It is: make the change in the web admin |
