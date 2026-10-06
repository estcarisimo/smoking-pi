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
`http://127.0.0.1:8090`.

### With Tailscale: one command

```bash
sudo smoking-pi connect --tailscale
```

It does the whole tunnel, and stops to say why if a step fails:

1. It installs Tailscale with Tailscale's own installer, if it is missing
   and you agree (`--yes` agrees in advance).
2. It keeps Tailscale away from this Pi's DNS (`--accept-dns=false`). When
   a Tailscale that owns the Pi's DNS logs out, the containers keep a
   resolver that no longer answers.
3. It signs the Pi in to your Tailscale account, printing the link to open
   in any browser.
4. It turns on **Funnel** for `127.0.0.1:8090`. The first time on a
   tailnet, Tailscale prints a page that allows Funnel and waits for you.
5. It reads the Pi's real name (`<machine>.<tailnet>.ts.net`) from Tailscale
   and sets `MCP_PUBLIC_URL` to it.
6. It checks the address from outside: it must answer as this server.

`sudo smoking-pi connect --tailscale --off` turns Funnel off and clears the
address. If your tailnet already has a device with the Pi's name, the new
one gets `-1` added; remove the old device in Tailscale's admin console
*before* connecting assistants, because the address is what they keep.

### With any other tunnel

Any tunnel that publishes the server works (Cloudflare Tunnel and ngrok are
common choices). Set it up by its own instructions, with three
requirements:

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
Connecting 'grok': Grok, or an agent built on it. It gets read-only access.

Connector URL:  https://mcp.example.com/mcp
Pairing code:   K7QM-4XRA   (valid 10 minutes, once)

  1. Add the URL wherever the agent takes a remote MCP server (a custom connector or MCP server).
  2. Its sign-in opens a Smoking Pi page: type the code there.
The Smoking Pi page names 'grok'; if not, do not type the code.
```

The name is a label you choose; it is how you will recognize and disconnect
this assistant later. In the assistant, add a custom connector (or remote
MCP server) with that URL. It opens a Smoking Pi page in your browser that
asks for the code; type it, and the assistant is connected. The page names
the code's label (`grok` here) and where it will send you back; check both.
The code works once, for ten minutes, and five wrong tries burn it — make a
new one with the same command.

If `MCP_PUBLIC_URL` is not set yet, `connect NAME` says so and, in a
terminal with Tailscale installed, offers to run `connect --tailscale`
first, then goes on to the pairing.

### Assistant by assistant

The name also picks the steps it prints: for a name below, or one that starts
with it (`claude-work`, `cursor.laptop`), you get that assistant's menus,
where its instructions go, and where its sign-in should say it returns to.
Any other name gets the general steps above. `--as` picks the steps for any
name (`sudo smoking-pi connect laptop --as claude-code`), and
`smoking-pi connect --list` shows them all.

| Name | Assistant | Where the connector goes | The sign-in returns to |
| --- | --- | --- | --- |
| `claude` | Claude (claude.ai and the Claude apps) | claude.ai: Settings > Connectors > Add custom connector | claude.ai |
| `claude-code` | Claude Code | `claude mcp add --transport http smoking-pi URL`, then `/mcp` > Authenticate | localhost |
| `chatgpt` | ChatGPT | Settings > Apps & Connectors, with Developer mode on | chatgpt.com |
| `cursor` | Cursor, or an agent on Cursor's agent platform | Cursor Settings > Tools & MCP, or `~/.cursor/mcp.json` | cursor.com, or the Cursor app |
| `grok` | Grok, or an agent built on it | wherever the agent takes a remote MCP server | depends on the platform it runs on |
| `openclaw` | OpenClaw on this machine | `sudo smoking-pi connect openclaw` (local token, no pairing code) | — |

Menus move between versions. If a step does not match what you see, look
for *custom connector* or *remote MCP server*. Adding an assistant to this
list is one entry in `shared/modules/mcp-server/assistants.py`; nothing in
the server changes.

### Tell the assistant when to use it

The server tells every assistant *how* to answer from its tools. What it
cannot do is make the assistant think of Smoking Pi when you ask about your
internet in passing. For that, paste this paragraph into the assistant's own
instructions (called *custom instructions*, *rules* or a *system prompt*,
depending on the assistant). `smoking-pi connect NAME` prints it too:

```text
You have a Smoking Pi connector: read-only measurements of my home internet,
recorded continuously (latency, loss, outages, Wi-Fi, DNS). For any question
about my internet, Wi-Fi, an outage, or whether a problem was mine or my
provider's, use the Smoking Pi tools before answering, and follow the
instructions the Smoking Pi server gives. Give numbers with their time
window. If the tools cannot answer, say so instead of guessing. It cannot
change anything: for changes, point me to the Smoking Pi web admin.
```

Then ask it something the measurements answer: *what happened last night,
and was it me or the internet?* It should call `diagnose_loss`. Check that
it did:

```bash
sudo smoking-pi connect grok --check
```

```text
'grok' is connected and using the tools: it last called diagnose_loss at 2026-10-06 14:05.
```

Signed in is not the same as used: an assistant can hold a token and still
answer from what it already knows. The server records each connector's last
tool call, and `--check` reads that record. It fails (exit status 1), saying
why, when a code is still waiting to be typed, when the assistant signed in
but has not called a tool, or when it has no live token any more.

```bash
sudo smoking-pi connect              # who is connected, the URL, and each one's last tool call
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
