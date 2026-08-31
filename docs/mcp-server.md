# SmokePing MCP Server

An [MCP](https://modelcontextprotocol.io/) server that makes the Pi's SmokePing
stack AI-operable. It wraps two backends:

- the **config-manager REST API** (target CRUD, config generation, SmokePing
  restart, service status)
- **InfluxDB 2.x** (latency / loss / microcut time series)

Source: `shared/modules/mcp-server/`.

## Tools

| Tool | What it does |
|---|---|
| `list_targets()` | All monitoring targets with category, host, active state |
| `add_target(name, host, category, title, probe)` | Add a host to monitoring (validates name/category, resolves probe) |
| `remove_target(name)` | Delete a target by name |
| `toggle_target(name)` | Pause/resume monitoring for a target |
| `apply_config()` | Regenerate SmokePing config + restart the service |
| `system_status()` | Health of config-manager, database, SmokePing container |
| `get_latency_stats(target, hours)` | Median/p95 latency (ms) and mean loss % per target |
| `get_loss_events(hours, min_loss_pct)` | Windows where packet loss exceeded a threshold, plus a per-target rollup |
| `get_microcut_stats(hours)` | CPE microcut summary per target+protocol, plus worst 5 windows |

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `CONFIG_API_URL` | `http://config-manager:5000` | config-manager base URL (`http://localhost:5000` from the host) |
| `CONFIG_API_TOKEN` | *(unset)* | If set, sent as `Authorization: Bearer <token>` on every API request |
| `INFLUX_URL` | `http://influxdb:8086` | InfluxDB 2.x URL (`http://localhost:8086` from the host) |
| `INFLUX_TOKEN` | *(unset)* | InfluxDB API token (see `.env` / `./show-passwords.sh`) |
| `INFLUX_ORG` | `smokeping` | InfluxDB organization |
| `INFLUX_BUCKET` | `smokeping` | InfluxDB bucket |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `http` (streamable-http) |
| `MCP_PORT` | `8090` | Listen port for the http transport (binds 0.0.0.0) |
| `PUBLIC_BASE_HOST` | *(unset)* | Host the *reader* reaches this Pi on; enables deep links (below) |
| `GRAFANA_PUBLIC_URL` | *(unset)* | Full Grafana base URL; wins over `PUBLIC_BASE_HOST` |
| `WEB_ADMIN_PUBLIC_URL` | *(unset)* | Full web-admin base URL; wins over `PUBLIC_BASE_HOST` |
| `TUNNEL_BASE_HOST` | *(unset)* | Host reachable from *outside* the home network; adds a `_tunnel` twin to every link |
| `GRAFANA_TUNNEL_URL` | *(unset)* | Full from-anywhere Grafana base URL; wins over `TUNNEL_BASE_HOST` |
| `WEB_ADMIN_TUNNEL_URL` | *(unset)* | Full from-anywhere web-admin base URL; wins over `TUNNEL_BASE_HOST` |

## Deep links

Tool responses can carry a `links` object pointing at the Grafana panel for the
target being discussed, the per-ping detail, a side-by-side against its peers,
and the web-admin page for editing it:

```json
{"target": "Amazon", "median_ms": 21.4, "avg_loss_pct": 0.0,
 "links": {
   "graph": "http://192.168.86.27:3000/d/smokeping-lat-pct-v28?var-target=Amazon&from=now-24h&to=now",
   "per_ping_detail": "http://192.168.86.27:3000/d/individual-pings-v1?...",
   "compare_with_peers": "http://192.168.86.27:3000/d/top_sites-side-by-side-v1?...",
   "edit": "http://192.168.86.27:8080/targets/?q=Amazon"}}
```

`get_microcut_stats` goes further and zooms each of its worst-5 windows to a
±15-minute range around when it happened, so the link opens on the event rather
than on the day containing it.

### The `from`/`to` contract

Two forms, both emitted by `links.py` and both accepted by Grafana:

| Form | Emitted when | Example |
|---|---|---|
| Relative | a `hours=` lookback | `from=now-24h&to=now` |
| Absolute | an event time (`at=`) | `from=1788032400000&to=1788048900000` |

Absolute is **epoch milliseconds** — 13 digits. This is the one part of a
generated URL that is safe for a consumer to rewrite: the host cannot be
guessed and the dashboard UIDs are a pinned contract, but re-timing an
existing link to frame a different window invents nothing. The agent skill
uses this to link an incident it is recalling rather than one a tool just
returned.

A ten-digit seconds epoch is read as milliseconds and lands in January 1970 —
valid-looking, silently wrong, and not something Grafana will complain about.

**This is off until you configure it, on purpose.** The Pi has no canonical
hostname — a LAN IP, a Tailscale name, and possibly a Cloudflare tunnel all
reach it, and which one works depends on where the person reading the answer is
standing. A guessed `http://localhost:3000` would look right in the transcript
and fail silently on someone's phone, so with nothing configured the tools
return numbers and no links at all. `system_status()` reports that deep links
are unconfigured; the measurement tools stay quiet about it rather than
repeating the notice on every call.

**Deep links are also disabled under `TSDB_TYPE=clickhouse`.** Every dashboard
UID above comes from the InfluxDB provisioning tree; the ClickHouse tree is a
parallel set with different UIDs and no CPE dashboard at all, so each of these
links would resolve to a Grafana 404 while looking perfectly valid in the
answer. Same doctrine as the unset base URL: no link beats a broken one.
`system_status()` says which of the two reasons applies.

Set one variable for the common case:

```bash
# editions/pro/.env — standard ports (:3000, :8080) are appended
PUBLIC_BASE_HOST=192.168.86.27
PUBLIC_BASE_HOST=smokingpi.tailnet-name.ts.net
```

Or set both URLs where a proxy or tunnel hides the ports:

```bash
GRAFANA_PUBLIC_URL=https://grafana.example.com
WEB_ADMIN_PUBLIC_URL=https://admin.example.com
```

Then `COMPOSE_PROFILES=mcp docker compose up -d mcp-server` to pick it up.

### Two tiers: at home, and from anywhere

The variables above answer "where is the reader standing" once. In practice the
same person asks from the couch and from a train, and there is no single right
answer: a LAN address is the better link at home — one hop, and up even when
Cloudflare isn't — and a dead link on cellular.

So the tunnel address is configured separately, and every link is emitted
twice:

```bash
PUBLIC_BASE_HOST=192.168.86.27               # at home
TUNNEL_BASE_HOST=https://smokingpi.example.com   # from anywhere
```

```json
{"links": {
   "graph": "http://192.168.86.27:3000/d/smokeping-lat-pct-v28?var-target=Amazon&...",
   "graph_tunnel": "https://smokingpi.example.com/d/smokeping-lat-pct-v28?var-target=Amazon&...",
   "edit": "http://192.168.86.27:8080/targets/?q=Amazon",
   "edit_tunnel": "https://smokingpi.example.com/targets/?q=Amazon"}}
```

Same panel, same window, different host. `system_status()` twins its three
entry points the same way (`grafana_overview_tunnel`, …).

Three behaviours worth knowing, each of which exists to keep a link from
lying:

- **Tunnel only is fine.** With `TUNNEL_BASE_HOST` set and `PUBLIC_BASE_HOST`
  empty, the tunnel *becomes* the primary link — a Pi reachable only through a
  tunnel gets links rather than silence — and no `_tunnel` twins are emitted.
- **Identical bases are not twinned.** Configuring the same address in both
  tiers produces one link, not two. Two labels on one URL invites a reader to
  try "the other one" when there isn't one.
- **Quick tunnels expire.** `./shared/scripts/create-tunnel.sh` prints a fresh
  `*.trycloudflare.com` hostname on every restart; pasting one into `.env`
  works until the next restart, after which the twins 404. Use a named tunnel
  if these links are going into alerts.

`./shared/scripts/show-tunnel-urls.sh` prints the current hostnames.

Two things worth knowing:

- The dashboard UIDs are pinned in `links.py` and asserted against the
  provisioned dashboard JSON by a unit test, so renaming a dashboard's UID
  fails CI rather than producing links that 404.
- The database's category vocabulary (`top_sites`, `netflix_oca`,
  `dns_resolvers`) differs from the category tag the exporter writes into
  InfluxDB (`topsites`, `netflix`, `dns`). `links.py` maps from the *database*
  vocabulary, because that is what `list_targets` returns.

The web-admin AI chat does **not** yet include links in its tool results. It
has its own copy of the tool surface, and the pages it renders already build
correct Grafana links client-side from `window.location.hostname` — which needs
no configuration at all, since the reader is by definition already on the host
that works.

## Option A — stdio, for Claude Code on the Pi

Install the dependencies once (system-wide, in a venv, or with `pipx`):

```bash
cd ~/smoking-pi/shared/modules/mcp-server
python3 -m venv ~/.mcp-smokeping-venv
~/.mcp-smokeping-venv/bin/pip install .
```

Then register the server with Claude Code. Because the tools talk to
containers from the host, point the URLs at localhost and pass the InfluxDB
token (find it in `editions/pro/.env` or via `./show-passwords.sh`):

```bash
claude mcp add smokeping \
  -e CONFIG_API_URL=http://localhost:5000 \
  -e INFLUX_URL=http://localhost:8086 \
  -e INFLUX_ORG=smokeping \
  -e INFLUX_BUCKET=smokeping \
  -e INFLUX_TOKEN=<your-influxdb-token> \
  -- ~/.mcp-smokeping-venv/bin/python3 /home/smokingpi/smoking-pi/shared/modules/mcp-server/main.py
```

(`python3 /path/to/main.py` works too if the deps are installed system-wide.
Add `-e CONFIG_API_TOKEN=...` if the config-manager API is protected.)

## Option B — Docker Compose service (streamable-http)

The `mcp-server` service is wired into `editions/pro/docker-compose.yml`.
It is opt-in via the `mcp` profile so the default stack is unchanged:

```yaml
  mcp-server:
    build: ../../shared/modules/mcp-server
    container_name: smokeping-mcp-server
    restart: unless-stopped
    profiles: [mcp]
    ports:
      - "127.0.0.1:8090:8090"
    environment:
      - MCP_TRANSPORT=http
      - MCP_PORT=8090
      - CONFIG_API_URL=${CONFIG_API_URL:-http://config-manager:5000}
      - CONFIG_API_TOKEN=${CONFIG_API_TOKEN:-}
      - INFLUX_URL=${INFLUX_URL:-http://influxdb:8086}
      - INFLUX_TOKEN=${INFLUX_TOKEN:-}
      - INFLUX_ORG=${INFLUX_ORG:-smokeping}
      - INFLUX_BUCKET=${INFLUX_BUCKET:-smokeping}
    depends_on:
      - config-manager
```

Start it with:

```bash
cd editions/pro
COMPOSE_PROFILES=mcp docker compose up -d mcp-server
```

The MCP endpoint is then `http://127.0.0.1:8090/mcp` (bound to localhost
only). Register it with any streamable-http-capable MCP client, e.g.:

```bash
claude mcp add --transport http smokeping http://127.0.0.1:8090/mcp
```

> Note: the env var names on the right-hand side (`INFLUX_TOKEN`,
> `INFLUX_ORG`, `INFLUX_BUCKET`) match the Pro edition's `.env` generated
> by `setup.sh`; adjust if your `.env` uses different names.

## Example conversation prompts

Once connected, ask things like:

- "Were there microcuts last night?"
- "Add monitoring for 9.9.9.9" (then: "call it quad9 and put it in the dns
  category")
- "Which targets had the worst p95 latency in the last 6 hours?"
- "Did we drop packets to Google DNS this week? Show me when."
- "Pause monitoring for the netflix targets, then apply the config."
- "Is the monitoring stack healthy? SmokePing seems down."

## Development

```bash
cd shared/modules/mcp-server
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q                       # unit tests (no network needed)
.venv/bin/ruff check --select E9,F63,F7,F82 .
```
