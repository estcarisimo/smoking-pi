# OpenClaw Integration

Two independent integrations, useful separately:

1. **Ask about the network** — register the MCP server with OpenClaw so you can
   ask "any microcuts today?" from Telegram and get a real answer. This works
   against a stock OpenClaw gateway.
2. **Be told about the network** — have the alerter push incidents into a chat.
   This works against a stock gateway too, via its `POST /tools/invoke`
   endpoint; it needs the Gateway token and the `message` tool permitted by
   tool policy. See [Alert delivery](#2-alert-delivery). With OpenClaw 2026.10
   or later the agent can write the message itself, so you can reply to it:
   [Push through the agent](#push-through-the-agent).

## The short way

If OpenClaw runs on the same machine as the stack, the mechanical part of
section 1 is one command:

```bash
smoking-pi openclaw
```

It generates `MCP_API_TOKEN` if there is none, records the `mcp` profile in
the env file, starts the server, checks that the port refuses an
unauthenticated request, registers `smokeping` with OpenClaw and installs the
skill. It also sets up [the push through the agent](#push-through-the-agent),
and [the heartbeat and the weekly](#heartbeat-and-weekly-through-the-agent) as
OpenClaw automations.
Then, after you start a **new** chat session:

```bash
smoking-pi openclaw --check
```

which asks the agent a question, reads the MCP server's log — not the
answer — and says which way alerts go. See [Verify with evidence](#verify-with-evidence-not-with-the-answer)
for why that distinction is the whole point.

On a package install, run both with `sudo`: the env file is root's. The
OpenClaw steps still run as you, the user who ran `sudo`, because the
gateway, its `~/.openclaw` and usually its `openclaw` command (under
`~/.nvm`, which `sudo` drops from PATH) are yours. The command looks for
`openclaw` on PATH, then in nvm's default Node version, then in
`~/.local/bin`, `~/.npm-global/bin`, `~/.volta/bin` and `~/.bun/bin`, and
says which one it used.

The rest of this page is what those steps do, which is worth reading when
something does not work, and is the only path if OpenClaw is on another
machine ([remote-openclaw.md](remote-openclaw.md) first, then section 1 by
hand against the tunnelled port).

All commands below use placeholders. Nothing machine-specific is committed to
this repository — generate your own tokens and substitute your own ids.

Everything here assumes OpenClaw and Smoking Pi share a machine. If OpenClaw
lives elsewhere, read [remote-openclaw.md](remote-openclaw.md) first: it
connects the two loopbacks with a tunnel so that this page then applies
unchanged.

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
sudo smoking-pi enable mcp
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
  "connectionTimeoutMs": 5000,
  "requestTimeoutMs": 30000
}'

# OpenClaw 2026.11 refuses `mcp set` while the gateway runs; the same entry
# through the gateway: openclaw config set mcp.servers.smokeping '{…}' --strict-json
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

With `--reload`, it then asks the gateway whether it answers
(`openclaw gateway status --require-rpc`). If none answers (not running,
or it refused the probe), it says so and exits 3: the skill is installed,
and the gateway reads it when it starts. `smoking-pi openclaw` reports that case in its step 5 instead of
"reloaded the gateway".

Re-run it after **any** change to the skill. A stale copy is the failure mode
worth guarding against, because it is invisible — the agent keeps answering,
just in the old shape, and the install looks like it silently did nothing.
The skill carries the answering guide, which changes in most releases, so
`smoking-pi upgrade` does this for you: when the installed skill differs
from the new release's, it refreshes it, reloads the gateway, and says so
([Upgrades](upgrades.md#with-the-command)). When it cannot, it says to run
`smoking-pi openclaw` (with `sudo` on a package install), which installs
the current skill as its step 5. To check by hand:

```bash
./shared/scripts/install-openclaw-skill.sh --check    # exits non-zero if stale
```

It backs up an existing skill to `SKILL.md.bak` before overwriting, in case
you tuned it in place. The skill is the answering guide every assistant gets
from the server (`shared/modules/common/guide.py`) plus a short OpenClaw
header; the one value worth tuning is the trigger `description:`, in
`openclaw-header.md` — then rebuild with
`python3 shared/scripts/build-openclaw-skill.py`. The manual
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
carries a report template — traffic lights, bold sections, one fact per
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

The web admin shows the same evidence without a terminal. Step 4 of the
welcome tour (the dashboard's **Welcome tour** button reopens it) reads the
MCP server's `tool=` lines through config-manager's `GET /assistant`. It
says *Not set up*, *Stopped*, *Not used yet*, or *Connected* with the last
tool called and when. Container logs start over when the container is
recreated, so *Not used yet* means no call since the server last started,
and the step shows that time.

### Checking the assistant

A connected agent can still answer badly: the wrong tool, a number it made
up, loss as a ratio where the person reads percent, an internal name like
`Netflix_1_DFW`, a time in a zone nobody lives in, a chart nobody asked for.
The question bank measures that. It is twenty questions a person at home
actually asks, five of them in Spanish, each with the tools a right answer
needs, the facts it must contain and the anti-patterns that fail it
(`shared/modules/mcp-server/evals/questions.yaml`; the file's header says
how each question is written).

`shared/scripts/assistant-eval.sh` asks them, on the Pi with the gateway,
as the user that runs `openclaw` (its bin directory on `PATH`):

```bash
shared/scripts/assistant-eval.sh --out ~/assistant-eval/run1
```

Each question is its own agent session and is never delivered to a chat.
The script waits for memory before each one, starts no turn in the four
minutes before the gateway's heartbeats at :00 and :30 (a turn longer than
that can still overlap one), and reads the MCP server's `tool=` lines inside
that turn as the evidence of what was called. The report
(`<out>/report.md`) has a row per question: the tools called, pass or fail,
and every check that failed, then each answer in full with ids, tokens and
tunnel hosts masked. Only the report is masked: `<out>/raw` holds the
replies as they came, so it stays on the Pi. One question acts: `mute-cpe` mutes the gateway's
alerts for two hours, and the run ends by saying so. `--only id,id` asks a
few; re-scoring a run needs no agent:

```bash
python3 shared/modules/mcp-server/evals/report.py --raw ~/assistant-eval/run1/raw \
  --zone America/Chicago --judgments judgments.yaml > report.md
```

The checks are mechanical and say why each failure failed; `--judgments`
records a human verdict where a check is wrong about an answer, and the
report marks that row *judged*. Run the bank after changing the answering
guide (`shared/modules/common/guide.py`), a tool's description, the skill's
header, or the agent's model, and fix what fails in those files, not in a
prompt of one assistant's own.

---

## 2. Alert delivery

### What a stock gateway actually provides

The gateway listens on `127.0.0.1:18789` and multiplexes **both** WebSocket and
HTTP on that port. The HTTP surface includes `POST /tools/invoke`, which is
always enabled, plus the OpenAI-compatible `/v1/*` routes. Verified on
2026.7.1-2.

> **This document previously claimed the gateway was WebSocket-only and served
> no HTTP at all.** That was wrong, and it cost the alerting engine a week of
> being unable to deliver. The claim came from probing `/hooks/agent` on
> 2026.7.1-2, getting a 404, and generalizing from one missing route to the
> entire surface. The correct conclusion from a single 404 is that one path is
> absent, not that a protocol is. It was absent only then, or only unconfigured:
> disabled hooks fall through to the gateway's other routes and answer 404, and
> current versions have [`/hooks/agent`](#push-through-the-agent). This page
> also said for a while that the path exists on no build, which is wrong now.
>
> `openclaw hooks` really does manage *internal agent lifecycle hooks*
> (`boot-md`, `command-logger`, …) and has nothing to do with inbound HTTP.
> Inbound hooks are the `hooks` block of `openclaw.json`.

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
   *and* tool policy. A filtered tool answers **HTTP 404** with
   `{"ok":false,"error":{"message":"Tool not available: message"}}` — allow it
   via `tools.allow` in `openclaw.json`.

   That 404 is the same status a missing route returns, which is why the
   preflight reads the body rather than the status: `Tool not available` means
   the gateway is reachable and the path is right, and only the policy is in
   the way. Diagnosing it as a bad `OPENCLAW_HOOK_PATH` sends you to the wrong
   file.
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

### Push through the agent

With the relay above the agent never sees what was sent: it is a fixed text
going through a tool. Ask it "what was that?" in Telegram and it has no idea.
`OPENCLAW_PUSH=agent` hands an opening episode to the agent instead, and the
agent writes the message:

```bash
sudo smoking-pi openclaw --push agent     # sudo on a package install
```

Plain `smoking-pi openclaw` does the same unless you chose the relay. It needs
OpenClaw 2026.10 or later. With one `openclaw config patch`, it adds to `hooks` in your `openclaw.json`
(`enabled`, a generated `token` of its own, `allowRequestSessionKey` and
`allowedSessionKeyPrefixes` for your conversation's key alone, `allowedAgentIds`
when you had no hooks yet), restarts
the gateway only if OpenClaw says the change needs it (`hooks` reload without one), writes `OPENCLAW_HOOKS_TOKEN` and `OPENCLAW_PUSH` to the env file,
and recreates the alerter. `--push message` goes back, and later runs of
`smoking-pi openclaw` respect that.

Hooks you already use keep working: your token and lists are kept and added to.
OpenClaw refuses to start unless the prefix list admits the key it generates
for a hook that names none (`hook:`, or your `defaultSessionKey`), so that is
always in the list it writes. If your list was empty, which means unrestricted,
it becomes that plus this conversation's key, so a mapping with a static session
key outside those would need adding by hand. A token that is a reference to a
secret is not readable here: the command says so and alerts stay on the relay
until you give the value with `smoking-pi config set OPENCLAW_HOOKS_TOKEN`.

```bash
sudo smoking-pi openclaw --check
# Alert push: agent (hooks accepted)
# Alert push: message relay (hooks: not enabled on the gateway, 404)
```

**What it sends.** `POST {OPENCLAW_URL}/hooks/agent`, with
`Authorization: Bearer {OPENCLAW_HOOKS_TOKEN}` and an `Idempotency-Key` that is
the delivery's key in the ledger (the incident and which of its messages):

```json
{
  "message": "<the facts, then the task>",
  "name": "Smoking Pi degradation",
  "agentId": "main",
  "sessionMode": "persistent",
  "sessionKey": "agent:main:main",
  "deliver": true,
  "channel": "telegram",
  "to": "<chat id>",
  "waitForCompletion": true,
  "timeoutSeconds": 150,
  "wakeMode": "next-heartbeat"
}
```

The facts are the episode's: what kind of message, since and until when, the
probable cause and its confidence, up to three signals and the strongest one
against, how many destinations, what the watchdog did, a link to the range, and
the zone they are all in. The task is short: tell the person in 2 to 5
sentences, without jargon, using the tools if it needs more and inventing
nothing. The alerter still decides what is worth saying
([what is pushed](alerting.md#what-is-pushed-and-what-is-recorded)); the agent
only says it.

**Why that session.** A hook runs in a fresh session by default, and a follow-up
in Telegram would have no context. Direct messages all share the main session
under OpenClaw's default `session.dmScope`, so the push runs there, as
`agent:main:main`. With another `dmScope` your chat has a key of its own, and
`smoking-pi openclaw` warns: name it with
`smoking-pi config set OPENCLAW_SESSION KEY`, then run
`smoking-pi openclaw --push agent`, which allows that key.

**Why it waits.** An HTTP 200 from the hook only says the turn was admitted, not
that the model answered: an agent whose model refuses every turn would lose
every alert in silence. So the request stays open until the turn settles, at
most 150 s for the turn itself, and the alerter reads the result.

**When an opening fails, the relay delivers.** A hook that is not set up (404), a
rejected token (401), any other refusal, no answer, a failed turn, a failed
delivery, or a turn that ended without saying anything: the same delivery goes
through the `message` relay, once, as the fixed text. If the request itself
times out, the agent may still answer later, so that one case can show you the
same event twice; losing an alert would be worse. The log says which path it
was, and so does `transport` in `smoking-pi alerts history --json`
(`openclaw-agent` or `openclaw`):

```
INFO alerter.notifier: Delivery path: agent, degradation (run 0f3c…)
WARNING alerter.notifier: Agent push failed (HTTP 404: Not Found); delivering through the message relay
INFO alerter.notifier: Delivery path: message relay (sent), after the agent push failed
```

**What goes which way.** What the agent delivers makes a sound, because the hook
has no silent option. [The push policy](alerting.md#what-is-pushed-and-what-is-recorded)
lets only an opening sound, so:

| Message | By day | In quiet hours |
| --- | --- | --- |
| Internet down, material degradation (opening) | the agent writes and delivers it | the relay |
| An update, an end, the monitor's message, a recurring window | the relay, with its own `silent`; the agent gets an undelivered note | the relay |
| The weekly summary | the `Smoking Pi weekly` automation, [below](#heartbeat-and-weekly-through-the-agent) | — |
| Outbox and quiet-hours summaries | the relay | the relay |

The note carries the same facts with `deliver: false` in the same session, and
tells the agent to send nothing and reply `NO_REPLY`. That is why a question like
"is it over?" finds it knows, and also why each of those messages costs one
model turn nobody sees. The note goes after the relay and only when the relay
delivered; if it fails, the log says so and the person has been told all the
same. In quiet hours the agent gets no note, so it does not know of what
happened during them unless it looks it up with its tools.

`wakeMode: "next-heartbeat"` is there for failed turns. OpenClaw leaves a
`Hook ... (error)` event in the session when a turn fails, and the default would
run a new turn on it at once, which could answer in the chat. This way it waits
for the next heartbeat, and the skill tells the agent not to bring it up.

The hook takes no attachment, so the chart of a newly opened episode follows
through the relay, as one image with a short caption of its own.

**The preflight** posts an empty body at startup. The gateway checks the token
first and the body after, so an accepted token answers `400 message required`
and no turn runs:

```
INFO alerter.notifier: Delivery preflight: http://127.0.0.1:18789/hooks/agent
accepts the hooks token; openings go through the agent, the rest through the relay
(message relay ready as the fallback)
```

`smoking-pi alerts --openclaw --test` goes the same way, and through the agent it can take
a minute or two: a real turn runs.

**The skill has a part to play.** OpenClaw marks hook content as external data
whose instructions carry no authority, which is right for an inbox and is a
problem here. The installed skill says that a turn named `Smoking Pi ...` is the
owner's own alerter and how to treat each kind, so re-run `smoking-pi openclaw` if yours
is older (`smoking-pi upgrade` refreshes it too).

### Heartbeat and weekly through the agent

Pushes cover what the alerter decides to say. Two things are left: something
worth a word that the alerter does not push (a recurring loss window that
appeared or went away, a cluster of short losses, the monitor blind with
nothing said), and the week. `smoking-pi openclaw` sets up both as OpenClaw
automations (OpenClaw 2026.11 or later) that post to the chat the pushes use,
`OPENCLAW_TO`:

```bash
sudo smoking-pi openclaw automations                 # create or update both
sudo smoking-pi openclaw automations --interval 3h   # look every 3 hours by day
sudo smoking-pi openclaw automations --disable       # remove both
sudo smoking-pi openclaw --check                     # how they are, and their last look
```

| Automation | When (in `DIGEST_TZ`) | Reads | Speaks |
| --- | --- | --- | --- |
| `Smoking Pi heartbeat` | every hour from 07:00 to 22:00 (`--interval 3h`: 07, 10, 13, 16, 19, 22; `6h`: 07, 13, 19) | `get_updates_since` | only on `worth_mentioning`, at most once a day, never at night |
| `Smoking Pi weekly` | `WEEKLY_DAY` at `WEEKLY_AT` (Monday 08:00), and the two hours after it as retries | `get_weekly_summary("last")` | only when the week has something material, once |

The night is the alerter's quiet hours when both are set
(`smoking-pi alerts --quiet 23:00-07:00`), else 23:00–07:00, and the crons
are given to OpenClaw with `--tz` set to the alerter's zone (`DIGEST_TZ`, then
`TZ`, then UTC), so they never follow the gateway host's clock. `smoking-pi alerts --weekly`,
`--quiet` and `--digest-tz` update the automations when they exist, and so does
`smoking-pi openclaw --push`.

**Each one is a check first, and a model turn only when there is news.** An
automation has a condition script that the gateway runs on schedule before any
model call. The script calls the MCP tool itself and wakes the agent only when
the tool says so; otherwise the run ends there, with no model turn and no
message. The agent then calls the same tool, writes the message, and its reply
is posted. Both scripts are stored in the job (`openclaw automations get <id>`
shows them) and both turns are limited to the read-only tools they need
(`--tools`) and run without the workspace's bootstrap files
(`--light-context`).

**Why a repeat cannot happen.** The heartbeat's script keeps its own state in
the gateway (OpenClaw's `trigger.state`): a cursor, the `as_of` of its last
look, and the day it last spoke. Each look asks `get_updates_since` from the
cursor, so windows meet without overlapping and each is looked at once. The tool already leaves out what the alerter
pushed, held or queued (`already_pushed`, `material_pushed_already`), and what
is worth mentioning is counted in the window it ended in. And once it has
spoken on a day, it holds every other `worth_mentioning` until the next day
without moving its cursor, so the next day's first look still covers what it
held; `--check` shows that as `held: spoke today`. The weekly's script
remembers the week it narrated, which is why its two later hours only retry a
look or a turn that failed. None of this lives in a prompt, which an isolated
turn would not remember. At night the script reads nothing and keeps its
cursor, so the first look of the morning covers the night; at most two days
back are looked at.

Editing a job resets that state (OpenClaw 2026.11.1), so the command leaves a
job alone when nothing about it changed. A real change (`--interval`, the
quiet hours, the zone, `OPENCLAW_TO`) does edit it: the first look after that
covers the last interval only, and the heartbeat may speak once more that day.

Two limits, on purpose. A turn the check woke counts as the day's message even
if the agent then replies `NO_REPLY` (it calls the tool again and the window
may have changed), and a message lost in delivery is not sent again: both
leave the day spent rather than risk saying a thing twice.

**The weekly is the agent's or the alerter's, never both, and never
neither.** The `Smoking Pi weekly` automation is made only with
`NOTIFY_MODE=openclaw`, `OPENCLAW_PUSH=agent` and the weekly on as the alerter
reads it: `WEEKLY_ENABLED=true`, or, on an install upgraded with the retired
daily digest's `DIGEST_ENABLED=true` and no `WEEKLY_*` key, that
([Upgrades](upgrades.md#settings-retired-or-renamed)). When
the command has made it, it writes `OPENCLAW_WEEKLY=agent` and recreates the
alerter, which then does not send its own weekly and logs `Weekly for slot ...
handed to the agent automation (OPENCLAW_PUSH=agent, OPENCLAW_WEEKLY=agent):
not sent by the alerter`. Removing it (`--disable`, `--push message`, the
weekly turned off, `disable mcp`) writes `OPENCLAW_WEEKLY=alerter` and the
alerter sends it again. The alerter cannot see OpenClaw, so the push alone is
not enough: an install with the agent push whose gateway never got the
automation keeps the alerter's weekly.

**OpenClaw's own heartbeat.** A stock gateway runs `Heartbeat (main)`, a model
turn every 30 minutes (every hour with some providers) that reads
`HEARTBEAT.md`, not this Pi, and delivers nothing by default. It is OpenClaw's
job, not this command's: the command leaves it alone and says that it runs. To
save the quota it costs:

```bash
sudo smoking-pi openclaw automations --disable-builtin-heartbeat
```

which runs `openclaw config set agents.defaults.heartbeat.every 0m`, OpenClaw's
documented off switch (`openclaw config unset agents.defaults.heartbeat.every`
turns it back on). Deleting its job with `openclaw automations rm` does not
last: the gateway creates it again.

**Quota.** What a model turn costs was measured on the staging Pi with
`gpt-6.1-sol`: about 15,000 tokens (7,500 of input, half of it served from
cache, and 60 to 200 of output) for either automation. A look that finds
nothing costs none.

| | Looks a day | Model turns |
| --- | --- | --- |
| Heartbeat, `--interval 1h` (default) | 16 | 0 on a quiet day, at most 1 |
| Heartbeat, `--interval 3h` | 6 | same |
| Heartbeat, `--interval 6h` | 3 | same |
| Weekly | 1 a week | 0 on a quiet week, at most 1 |
| OpenClaw's `Heartbeat (main)` at 30 min | — | 48 a day, about 720,000 tokens |

So the interval does not change the quota. It changes how soon something worth
saying is said, and how often the gateway starts a check, which is a few
seconds of work for the gateway and two file reads for the MCP server
(`get_updates_since` queries no database; `get_weekly_summary` takes about 3
seconds of InfluxDB a week). A forced run, `openclaw automations run <id>`,
skips the check and always runs the model turn; the prompt then tells the agent
to reply `NO_REPLY` itself when there is nothing.

**The prompts**, as the command writes them with the defaults (the window, the
night and the zone are filled in from the env file). The heartbeat's check
appends the window it looked at, `Window: since="..."`, to the message:

<!-- heartbeat-prompt -->
```text
You are the Smoking Pi heartbeat: an unattended check of this home's internet monitor. Your final reply is posted to the owner's chat exactly as you write it.
1. Call smokeping__get_updates_since with since set to the window given at the end of this message (if none is given, since="1h") and include_non_material=true.
2. If the call fails, or its verdict is nothing_new or material_pushed_already, reply exactly NO_REPLY.
3. If the local time in its as_of is between 23:00 and 07:00 (America/Chicago), reply exactly NO_REPLY: nothing unsolicited at night.
4. Otherwise the verdict is worth_mentioning: write 1 to 3 short sentences in plain English about what its reasons point to (a recurring loss window that appeared or went away, a cluster of short losses and where they were, the monitor blind with nothing said, or an episode nobody was told about). Say where in the tool's words: your Wi-Fi, your line, your provider, or the destinations it names. Give times as the tool prints them, and end with the episode's link when it has one.
Never say that all is well, never repeat anything listed in already_pushed, never give a cause the tool did not give. No greeting, no headings, no lists.
```
<!-- /heartbeat-prompt -->

<!-- weekly-prompt -->
```text
You are the Smoking Pi weekly: an unattended look back at this home's internet over the last week. Your final reply is posted to the owner's chat exactly as you write it.
1. Call smokeping__get_weekly_summary with week="last".
2. If the call fails or has_material is false, reply exactly NO_REPLY.
3. Otherwise narrate the week in 6 to 10 sentences of plain English prose, without bullet points, headings or jargon. Say whether it was a good, fair or bad week (verdict good, mixed or bad) against the week before, by cause in the words of each by_cause cause_text (your Wi-Fi, your line, your provider, specific destinations), with minutes this week and the week before. Name the recurring patterns that are still active, with their hours and days. Say how much of the week the monitor measured (monitor.measured_pct), why it was blind when it was, and what the watchdog did. Name at most 3 destinations with the most loss of their own. End with links.grafana_week when there is one.
Give times as the tool prints them. Never invent a number the tool did not give.
```
<!-- /weekly-prompt -->

**Checking them.** `smoking-pi openclaw --check` ends with:

```
Automations:
  Smoking Pi heartbeat: on, '0 7-22 * * *' (America/Chicago), to the alerts' chat (OPENCLAW_TO)
    last run: none yet (the agent runs only when the check finds something)
    last check: 2026-11-07 17:00 CST, nothing_new at 2026-11-07T17:00-06:00
  Smoking Pi weekly: on, '0 8,9,10 * * 1' (America/Chicago), to the alerts' chat (OPENCLAW_TO)
    last run: none yet (the agent runs only when the check finds something)
  OpenClaw's own Heartbeat (main): on, every 30m, a model turn each time that does not look at this Pi
```

`last run` is a model turn and what became of its message (`delivered`, or
`suppressed` for a `NO_REPLY`); `last check` is the check's latest look and
the tool's verdict. The MCP server's log has one line per look, whoever made it:

```
INFO mcp.tools: tool=get_updates_since args=include_non_material=False since=2026-11-07T16:00:01-06:00 -> verdict=nothing_new in 14ms
```

A check that fails (the MCP server down for an upgrade, say) is a failed run
to OpenClaw, and its own failure policy applies: by default it tells the same
chat after two failures in a row, once per incident, and says nothing more
until a run succeeds. A check never fails quietly into "nothing to say".

`smoking-pi disable mcp` and `smoking-pi purge` remove both automations: their
checks call the MCP server, and every run would fail without it.

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
  token, `OPENCLAW_HOOKS_TOKEN` and OpenClaw's own `gateway.auth.token` should
  all be different
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
