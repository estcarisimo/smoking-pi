# OpenClaw on another machine

The reference deployment runs OpenClaw and Smoking Pi on the same Raspberry
Pi, and everything in [openclaw-integration.md](openclaw-integration.md)
assumes that: the MCP server listens on `127.0.0.1:8090`, the OpenClaw
gateway on `127.0.0.1:18789`, and neither is reachable from anywhere else,
which is the whole security model. This page is for the other shape — the
Pi does the measuring, OpenClaw lives somewhere else (a desktop, a VPS, a
second Pi) — and shows how to connect the two **without giving up that
model**.

The rule every option here follows: **nothing new listens on a public
address.** Both services stay bound to loopback on their own host; a tunnel
or a private network carries the traffic between the two loopbacks. The
options differ in *what* carries it and who else can see it, and are listed
from least exposed to most.

---

## What actually crosses the wire

Two flows, in opposite directions. Both are needed for the full experience;
the first alone gives you "ask about the network", the second alone gives
you "be told about the network".

| Direction | Client | Server | Port | Authentication |
| --- | --- | --- | --- | --- |
| OpenClaw → Smoking Pi | the gateway's MCP client | `mcp-server` (`POST /mcp`) | 8090 | `Authorization: Bearer $MCP_API_TOKEN` |
| Smoking Pi → OpenClaw | `alerter` (incidents, digest) and `mcp-server` (`get_chart(deliver=true)`) | the gateway (`POST /tools/invoke`) | 18789 | `Authorization: Bearer $OPENCLAW_GATEWAY_TOKEN` |

Both are plain HTTP with a bearer token, so both can ride any TCP tunnel
unchanged. Both Pi-side services run with `network_mode: host`, so "the Pi's
loopback" means the host's `127.0.0.1`, which is exactly where an SSH `-R`
or a `cloudflared access` listener lands.

Not part of this: the deep links in chat answers (`PUBLIC_BASE_HOST` and
friends in `.env.template`). Those are for the *person* reading the chat and
have to be an address that person can open; they are independent of how the
two machines talk to each other.

---

## Option A — SSH tunnel (recommended)

One SSH session carries both directions. Nothing is installed, nothing new
listens, and — the part that makes this the default recommendation — **both
sides end up configured exactly as if they were on the same machine**, so
every command in the co-located guide works verbatim.

Run this on the **OpenClaw host** (it dials the Pi):

```bash
ssh -N \
  -L 8090:127.0.0.1:8090 \
  -R 18789:127.0.0.1:18789 \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
  smokingpi@smoking-pi
```

- `-L 8090:…` — the OpenClaw host's `127.0.0.1:8090` is now the Pi's MCP
  server. Register it with `openclaw mcp set smokeping '{"url":
  "http://127.0.0.1:8090/mcp", …}'` as in the co-located guide.
- `-R 18789:…` — the Pi's `127.0.0.1:18789` is now the OpenClaw gateway.
  `OPENCLAW_URL` on the Pi keeps its default of `http://127.0.0.1:18789`;
  only `OPENCLAW_GATEWAY_TOKEN` and `OPENCLAW_TO` need to be set, as always.
  sshd's default `GatewayPorts no` keeps that reverse listener on loopback;
  do not turn it on.
- `-N` — no shell, just the forwards.

### If the Pi can reach the OpenClaw host but not the other way round

Typical when OpenClaw is on a VPS and the Pi is behind a home NAT. Same
idea, mirrored, run on the **Pi**:

```bash
ssh -N \
  -R 8090:127.0.0.1:8090 \
  -L 18789:127.0.0.1:18789 \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
  openclaw@openclaw-host
```

Either way, only *one* side needs an SSH port reachable from the other. If
that reachability would mean an SSH port on the public internet, read
[Which one](#which-one) first — SSH on a public IP is the one public
listener in this option, and a VPN (Option B) removes it.

### Lock the key down

Give the tunnel its own key and let it do nothing but these two forwards.
On the side being dialed into, in `~/.ssh/authorized_keys`:

```text
restrict,port-forwarding,permitopen="127.0.0.1:8090",permitlisten="localhost:18789" ssh-ed25519 AAAA… openclaw-tunnel
```

`restrict` turns off everything (pty, agent/X11 forwarding, commands),
`port-forwarding` turns forwarding back on, and the two `permit*` entries
limit it to exactly the ports above. Swap the two port numbers when the Pi
is the dialing side. A dedicated user with `/usr/sbin/nologin` as its shell
is the next step if you want one.

The two entries are spelled differently on purpose. `permitopen` is
matched against the destination you typed in `-L` — `127.0.0.1:8090`,
literally. `permitlisten` is matched against the listen address the client
*sends*, and for an `-R` with no bind address ssh sends the name
`localhost`, which sshd treats as distinct from `127.0.0.1` and `::1`
(`authorized_keys(5)`). Write `permitlisten="127.0.0.1:18789"` and sshd
refuses the very forward this line exists to allow, with nothing in the
client's output to say why except `ExitOnForwardFailure`.

### Keep it up

A tunnel that dies with a laptop lid is a monitor that silently stops
alerting. `examples/openclaw/smoking-pi-tunnel.service` is a systemd unit
that restarts it forever; install it on the dialing host as a user service:

```bash
mkdir -p ~/.config/systemd/user
cp examples/openclaw/smoking-pi-tunnel.service ~/.config/systemd/user/
systemctl --user edit smoking-pi-tunnel   # set the host and, if needed, the key path
systemctl --user enable --now smoking-pi-tunnel
loginctl enable-linger "$USER"            # keep user services running with no session open
```

`autossh` is the traditional alternative; the unit's `Restart=always` plus
`ServerAliveInterval` does the same job with one fewer package.

### Verify

```bash
# On the OpenClaw host: MCP reachable through the tunnel, and it demands the token
curl -s -o /dev/null -w '%{http_code}\n' -XPOST http://127.0.0.1:8090/mcp      # 401
openclaw mcp probe smokeping                                                   # lists the tools

# On the Pi: the gateway reachable through the tunnel
cd editions/pro && docker compose logs alerter | grep -i preflight              # "Delivery preflight: … reachable, 'message' tool permitted" or the specific reason
```

`get_chart(target, deliver=true)` from the chat is the end-to-end check: it
uses both directions.

---

## Option B — a private network (Tailscale or WireGuard)

Put both machines on a mesh VPN and neither has to accept a connection from
the internet at all. Tailscale (WireGuard underneath) needs no open port on
either side — connections are brokered and, failing NAT traversal, relayed;
plain WireGuard needs one UDP port open on one side, but a WireGuard
listener does not answer packets that do not carry a valid key, so there is
nothing for a scanner to find and nothing for a flood to exhaust but
bandwidth.

Once the two are on the same tailnet, the simplest thing is **Option A over
it** — SSH to the tailnet address — and you are done, with no port exposed
even inside the tailnet. The two variants below skip SSH:

**B1 — keep loopback, let Tailscale front it (Serve).** On the Pi:

```bash
tailscale serve --bg --https=8443 8090
```

The MCP server is now `https://<pi-magicdns>:8443/mcp`, with a certificate
Tailscale issues and manages, reachable only from the tailnet; the process
itself still listens on `127.0.0.1`. Register that URL with OpenClaw. On the
OpenClaw host, set `gateway.tailscale.mode: "serve"` in `openclaw.json`
(OpenClaw's own [Tailscale page](https://docs.openclaw.ai/gateway/tailscale)
covers it) and point the Pi at it: `OPENCLAW_URL=https://<openclaw-magicdns>`.

**B2 — bind to the tailnet address directly.** No Serve, no TLS, one fewer
moving part; the price is that the services now listen on an address other
than loopback, and only the tailnet's ACL keeps that private. On the Pi, in
`editions/pro/.env`:

```bash
MCP_HOST=100.x.y.z        # the Pi's own tailnet IPv4 (`tailscale ip -4`), NEVER 0.0.0.0
```

and `docker compose up -d mcp-server`. On the OpenClaw host,
`gateway.bind: "tailnet"` with token auth, and on the Pi
`OPENCLAW_URL=http://<openclaw-tailnet-ip>:18789`.

Either way, write a tailnet ACL that lets exactly these two nodes talk on
exactly these ports, and nothing else; a tailnet that lets every device
reach every port is a LAN with extra steps.

**A trap specific to the Pi.** Docker copies the host's `/etc/resolv.conf`
into a container *once, when the container is created*. Tailscale's
MagicDNS puts `100.100.100.100` in that file while it is up. If the
SmokePing container is created while Tailscale is logged in and Tailscale is
later logged out, every hostname target goes to 100 % loss and every raw-IP
target stays fine — for as long as nobody notices. The reference Pi lost
nine of eighteen targets for ten days this way. Tell: hostname targets dead,
IP targets healthy. Fix: `docker compose up -d --force-recreate --no-deps
smokeping`. Or keep Tailscale's DNS out of the host resolver
(`tailscale up --accept-dns=false`) so the container never inherits it.

---

## Option C — Cloudflare Tunnel with an Access service token

For when neither machine can reach the other and you would rather not run a
VPN: both sides make only *outbound* connections to Cloudflare, and
Cloudflare's edge does the listening. A flood hits Cloudflare, not you, and a
request without a valid service token is rejected at the edge and never
reaches the Pi.

What you accept in exchange: a public hostname exists for each service, and
a third party terminates TLS — Cloudflare sees the traffic, which here means
your latency figures, target names and alert text. For many people that is
fine; it should be a decision, not a surprise.

**Pi → public hostname for the MCP server.** The repository's
[permanent tunnel guide](../shared/docs/cloudflare-tunnel-setup.md) sets up
`cloudflared` and a named tunnel for Grafana and web-admin; add an ingress
rule for the MCP server to the same tunnel:

```yaml
ingress:
  - hostname: mcp.example.com
    service: http://127.0.0.1:8090
  # …existing rules…
  - service: http_status:404
```

In Zero Trust, create a self-hosted Access application for
`mcp.example.com`, a **service token**, and a policy of type *Service Auth*
that allows it. OpenClaw sends the token as two headers next to the bearer:

```bash
openclaw mcp set smokeping '{
  "url": "https://mcp.example.com/mcp",
  "transport": "streamable-http",
  "headers": {
    "Authorization": "Bearer <your-mcp-token>",
    "CF-Access-Client-Id": "<service-token-id>",
    "CF-Access-Client-Secret": "<service-token-secret>"
  },
  "connectTimeout": 10,
  "timeout": 30
}'
```

Keep `MCP_API_TOKEN` set even though Access is in front: two independent
checks, one of which you control entirely.

**OpenClaw host → public hostname for the gateway, dialed from the Pi.** The
alerter sends only the bearer header, so it cannot present a service token
itself. Instead, run `cloudflared` on the Pi in *client* mode, which
presents the token and exposes the far gateway on the Pi's loopback — where
`OPENCLAW_URL` already points:

```bash
# On the OpenClaw host: tunnel ingress for the gateway, as a raw TCP service
#   - hostname: openclaw-gw.example.com
#     service: tcp://127.0.0.1:18789
# plus an Access application + service-token policy for that hostname.

# On the Pi:
TUNNEL_SERVICE_TOKEN_ID=<id> TUNNEL_SERVICE_TOKEN_SECRET=<secret> \
  cloudflared access tcp --hostname openclaw-gw.example.com --url 127.0.0.1:18789
```

That client process needs the same keep-alive treatment as the SSH tunnel;
a user unit for it is the SSH one with a different `ExecStart`:

```ini
[Service]
Environment=TUNNEL_SERVICE_TOKEN_ID=<id>
Environment=TUNNEL_SERVICE_TOKEN_SECRET=<secret>
ExecStart=/usr/local/bin/cloudflared access tcp --hostname openclaw-gw.example.com --url 127.0.0.1:18789
Restart=always
RestartSec=10
[Install]
WantedBy=default.target
```

(Put the token in the unit's drop-in via `systemctl --user edit`, not in a
file you might commit.)

OpenClaw's own [Cloudflare Access page](https://docs.openclaw.ai/gateway/cloudflare-access)
describes the same arrangement from its side and, importantly, says to keep
`gateway.bind: "loopback"` while doing it.

---

## What not to do

- **Forward 8090 or 18789 on your router.** A bearer token is one secret
  between the internet and a server that can add targets and restart
  SmokePing; it was designed to guard a loopback port from other local
  processes, not a public one from the world.
- **`MCP_HOST=0.0.0.0`**, or `gateway.bind: "lan"` on a machine you do not
  fully trust the LAN of. `MCP_HOST` exists for B2 (a tailnet address) and
  nothing else.
- **Tailscale Funnel or a Cloudflare tunnel with no Access policy** for
  either service. That is a public port with a nicer name.
- **A reverse proxy with basic auth on a public IP.** It works until the
  day it is the thing being brute-forced, and it puts the proxy's
  vulnerabilities in front of yours.

---

## Which one

| | A · SSH tunnel | B · Tailscale / WireGuard | C · Cloudflare Access |
| --- | --- | --- | --- |
| Public listener | SSH on one side, *only if* that side is on the internet; none on a LAN or VPN | None (Tailscale) / one silent UDP port (WireGuard) | None on your machines; hostnames at Cloudflare's edge |
| Exposure to floods | The SSH port, if public | Bandwidth only | Absorbed by Cloudflare |
| Third party in the path | None | Tailscale's control plane (coordination, not traffic; DERP relays see only encrypted traffic) | Cloudflare terminates TLS and sees the traffic |
| Extra software | None | Tailscale/WireGuard on both | `cloudflared` on both, a Cloudflare account and zone |
| Config on the Pi | None beyond the co-located guide | `MCP_HOST` (B2 only) or `tailscale serve` (B1) | An ingress rule and a client-mode `cloudflared` |
| Fits | Same LAN; anything already behind a VPN; a VPS you can SSH to | Two homes, laptop that roams, no port-forwarding allowed | Neither side can reach the other and you do not want a VPN |

Pick A when the two machines can already reach each other by any private
route. Pick B when they cannot, and A over B once they can. Pick C when you
have ruled out a VPN and are comfortable with Cloudflare in the path. In
every case the co-located guide's checks still apply afterwards: the
`401` without a token, `openclaw mcp probe`, the alerter's preflight line,
and a delivered chart.
