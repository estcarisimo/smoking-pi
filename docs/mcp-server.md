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
| `get_loss_events(hours, min_loss_pct)` | Windows where packet loss exceeded a threshold |
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

Ready-to-paste snippet for `editions/pro/docker-compose.yml` (`services:`
section). It is opt-in via the `mcp` profile so the default stack is
unchanged:

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
      - INFLUX_TOKEN=${INFLUXDB_ADMIN_TOKEN:-}
      - INFLUX_ORG=${INFLUXDB_ORG:-smokeping}
      - INFLUX_BUCKET=${INFLUXDB_BUCKET:-smokeping}
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

> Note: the env var names on the right-hand side (`INFLUXDB_ADMIN_TOKEN`,
> `INFLUXDB_ORG`, `INFLUXDB_BUCKET`) match the Pro edition's `.env` generated
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
