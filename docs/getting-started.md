# Getting started

This page takes a Raspberry Pi with nothing on it to a stack that is
measuring your internet connection and drawing it, in seven numbered steps.
Each step says what you should see when it worked, so you never have to
guess whether to go on. Budget **20 minutes**, most of it waiting for
Docker to pull images.

At the end you will have:

- SmokePing measuring 21 targets every 300 seconds — 10 pings per cycle
  for ICMP, 5 for DNS, HTTP and TCP,
- Grafana dashboards on `:3000` and a web admin on `:8080`,
- a `smoking-pi` command for everything afterwards — start, stop, upgrade,
  backup, passwords, doctor,
- optionally, an assistant you can ask *"how's my internet?"* (step 7).

The guide is written for **Raspberry Pi OS (64-bit)**, the reference
platform. Ubuntu 22.04/24.04 and Debian 12/13 work the same way and are
checked on every release; the one difference is in step 1. macOS is
[untested](#macos).

!!! note "Already have a clone running?"
    Nothing here asks you to move. A checkout keeps working exactly as it
    did — see [From a clone](#from-a-clone) in step 2, and
    [Upgrading](upgrades.md) for going from one release to the next.

---

## Step 0 — What you need

| | Minimum | Comfortable |
|---|---|---|
| Board | Raspberry Pi 4, 2 GB | Raspberry Pi 5, 4 GB |
| Storage | 16 GB SD card | 32 GB+, or an SSD over USB |
| OS | Raspberry Pi OS 64-bit (Bookworm), Ubuntu 22.04+, Debian 12+ | same |
| Network | Ethernet or Wi-Fi, outbound ICMP not blocked | Ethernet |

Two things are worth knowing before you start:

- **Outbound ICMP is the whole point.** If your network blocks pings, every
  target will read 100% loss and nothing else will be wrong. Test it first:
  `ping -c3 google.com`.
- **A year of measurements is a few GB.** They live in Docker volumes, which
  survive `apt remove` and are only ever deleted when you ask explicitly
  (`smoking-pi purge`). An SD card will do; an SSD will do it longer.

---

## Step 1 — Docker with the Compose plugin

Smoking Pi runs everything in containers, so Docker Engine and the
**Compose v2 plugin** are the only prerequisites. Check what you have:

```bash
docker compose version
```

Expected:

```
Docker Compose version v2.38.2
```

If that prints `docker: 'compose' is not a docker command` (or Docker is
missing entirely), install it from Docker's own repository:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
newgrp docker
```

!!! warning "Raspberry Pi OS and Debian 12 need Docker's repository"
    Debian 12 — today's Raspberry Pi OS — ships **no Compose v2 at all**:
    `apt install docker.io` gets you a 20.10 daemon and the Python
    `docker-compose` 1.29, which cannot read these compose files. This is
    measured, not assumed; the full host matrix is in
    [Supported hosts](packaging.md#supported-hosts). Debian 13, Ubuntu
    22.04 and 24.04 have a usable Compose v2 in the distribution.

The `usermod` line lets you run Docker without `sudo`. It takes effect on
your next login; `newgrp docker` gives it to you in the current shell.

---

## Step 2 — Install Smoking Pi

### From the apt repository (recommended)

Three lines add the repository, one installs the package:

```bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://estcarisimo.github.io/smoking-pi/apt/smoking-pi.gpg | sudo tee /etc/apt/keyrings/smoking-pi.gpg >/dev/null
echo "deb [signed-by=/etc/apt/keyrings/smoking-pi.gpg] https://estcarisimo.github.io/smoking-pi/apt ./" | sudo tee /etc/apt/sources.list.d/smoking-pi.list
sudo apt update && sudo apt install smoking-pi
```

Check it:

```bash
smoking-pi version
```

Expected: the release number, e.g. `2.12.0`.

The package installs the code under `/opt/smoking-pi`, a `smoking-pi`
command in `/usr/bin`, and a systemd unit. It does **not** start anything
yet — step 3 does that, after you have chosen what to run.

!!! tip "One-off install without the repository"
    Every [GitHub release](https://github.com/estcarisimo/smoking-pi/releases)
    attaches the same `.deb`:
    `sudo apt install ./smoking-pi_<version>_all.deb`. You lose
    `apt upgrade` as the way to get the next release.

### From a clone

A checkout is the development path and the way to run an unreleased branch.
It needs `git` and nothing else:

```bash
git clone https://github.com/estcarisimo/smoking-pi.git
cd smoking-pi
./packaging/smoking-pi version
```

From a clone the command is `./packaging/smoking-pi` (or put it on your
`PATH`), the images are **built locally** rather than pulled, and the state
stays beside the edition's compose file instead of under `/etc`. Everything
else in this guide is identical.

### macOS

A Homebrew formula exists and has **never been run on a Mac**:

```bash
brew tap estcarisimo/smoking-pi https://github.com/estcarisimo/smoking-pi
brew install smoking-pi
```

It needs Docker Desktop, and be aware of what it costs you: on Docker
Desktop `network_mode: host` is the Linux VM, not your Mac, so Pro's first
hop and Wi-Fi measurements describe the VM. Basic and Standard are
unaffected. If you try it, [say how it went](https://github.com/estcarisimo/smoking-pi/issues).

---

## Step 3 — Choose what to run

```bash
sudo smoking-pi install
```

This asks three questions (with `whiptail` menus if it is installed,
`--edition`/`--database`/`--profiles` flags if you prefer, `--yes` for
nothing at all), then generates every password and API token, detects your
timezone, starts the containers and prints the URLs and credentials.

**Which edition?**

| Edition | For | What you get |
|---|---|---|
| **basic** | One box, one person | SmokePing, targets in a YAML file, the classic web UI, RRD storage |
| **standard** | A small team | + web admin with login, PostgreSQL as the source of truth, a REST API |
| **pro** | Everything | + Grafana, InfluxDB or ClickHouse, HTTP/TCP/DNS/IPv6 probes, Wi-Fi stats, alerting, MCP server, AI reports, the doctor |

Pick **pro** unless you know you want less; this guide assumes it. You can
move up later with `shared/scripts/migrate-to-edition.sh`.

**Which time-series backend?** InfluxDB, unless you have a reason —
ClickHouse works but is the [less traveled path](clickhouse.md).

**Which optional services?** All three are off by default and all three
need something from you before they are useful:

| Profile | What it adds | Needs |
|---|---|---|
| `mcp` | An MCP server, so an assistant can query your history | nothing; step 7 |
| `alerts` | Downtime, microcut and Wi-Fi alerts | `NOTIFY_MODE` and its keys ([Alerting](alerting.md)) |
| `ai` | Written health reports | `ANTHROPIC_API_KEY` ([AI reports](ai-insights.md)) |

You can turn any of them on afterwards:

```bash
sudo smoking-pi config set COMPOSE_PROFILES influxdb,mcp,alerts
```

The same command changes any setting in the env file by name, and it
recreates just the services that read it. `sudo smoking-pi config list`
shows them all, with secrets hidden. A secret such as `ANTHROPIC_API_KEY`
is typed at a prompt, never on the command line:

```bash
sudo smoking-pi config set ANTHROPIC_API_KEY
```

Expected, at the end: what to open, as the last thing on the screen.

```text
Open Smoking Pi:
  Web admin  http://192.168.1.27:8080/   (user admin)
  Grafana    http://192.168.1.27:3000/   (user admin)
  Passwords: smoking-pi passwords --show-secrets (on this machine)
Open it on the computer you are connected from (192.168.1.30), not in this terminal.
Also, from most computers on this network: http://raspberrypi.local:8080/
```

If you installed over SSH, the address is the one your computer reached
the Pi at, not `localhost` — which, typed on your laptop, is your laptop.
`install` waits up to two minutes for the page to answer before printing it,
and says so if it still doesn't: the first start can take longer while
images download. `sudo smoking-pi url` prints the same block, and checks
again, whenever you need it.

Above it is a banner with every service's status. The
secrets are set but **not printed** — an install transcript is the last
place a password should live. Read them when you need them:

```bash
sudo smoking-pi passwords --show-secrets
```

Without `--show-secrets` the same banner shows every secret as
`set (hidden)`, which is still the answer to "did the token get
generated?". `--show-secrets` refuses to write into a pipe or a file
unless you add `--force`, so a password does not end up in a log or a
pasted issue by accident.

!!! danger "install runs once, deliberately"
    Run over an existing env file, `install` refuses and tells you to use
    `smoking-pi up`. This is not caution for its own sake: it would
    regenerate every secret, and the database volumes still hold the old
    ones — a stack that no longer starts. Starting over on purpose is
    `smoking-pi purge --config`.

---

## Step 4 — Start it at boot

```bash
sudo systemctl enable --now smoking-pi
```

Expected:

```bash
systemctl is-enabled smoking-pi   # enabled
systemctl is-active smoking-pi    # active
```

The unit is a `oneshot` that runs `smoking-pi up` and stays "active": it
brings the stack up after a reboot and hands the rest to Docker's own
restart policies. From a clone there is no unit — use `smoking-pi up`, or
install one from `packaging/systemd/`.

---

## Step 5 — Check that it is actually working

Four checks, in the order that finds problems fastest.

!!! note "Packaged: these commands need `sudo`"
    The package keeps your secrets in `/etc/smoking-pi/env`, inside a
    directory that is `0750` and owned by root — which is the point of
    putting them there. Every `smoking-pi` command that reads it (`status`,
    `passwords`, `up`, `logs`, `doctor --live`, …) therefore runs under
    `sudo`; without it Compose stops at `permission denied` on the env file.
    From a clone the env file is yours and no `sudo` is needed anywhere.

**1. Every container is up.**

```bash
sudo smoking-pi status
```

Expected, for Pro on InfluxDB with no optional profiles — **six**
services, five of them `(healthy)` (SmokePing has no health check; each
optional profile from step 3 adds one more service). Columns trimmed here;
the real output also carries `IMAGE`, `COMMAND`, `CREATED` and `PORTS`:

```
NAME                   SERVICE          STATUS
pro-config-manager-1   config-manager   Up 2 minutes (healthy)
pro-grafana-1          grafana          Up 2 minutes (healthy)
pro-influxdb-1         influxdb         Up 2 minutes (healthy)
pro-postgres-1         postgres         Up 2 minutes (healthy)
pro-smokeping-1        smokeping        Up 2 minutes
pro-web-admin-1        web-admin        Up 2 minutes (healthy)
```

A container that is `Restarting` is the one to look at:
`sudo smoking-pi logs <service>`.

**2. The URLs answer.**

```bash
sudo smoking-pi url
```

It prints the address to open and exits non-zero if nothing answers there.
`sudo smoking-pi passwords` lists every service's address.
For Pro: SmokePing on `http://<pi>/`, the web admin on `http://<pi>:8080`,
Grafana on `http://<pi>:3000`, InfluxDB on `http://<pi>:8086`. Log in to
Grafana with `admin` and the password from `sudo smoking-pi passwords
--show-secrets`.

**3. Data arrives — after five minutes, not before.**

SmokePing's step is 300 seconds, so the first point of every graph exists
five minutes after the containers start, and the graphs are empty until
then. That is the single most common "it's broken" that isn't. Wait, then
open SmokePing and look at *top\_sites → Google*, or, in Grafana, the
**SmokePing Latency & Loss – Percentiles / Mean** dashboard.

Standard and Pro answer this for you: the web admin's dashboard has a
**Measurements** card that checks, for every configured target, when
SmokePing last wrote its data. *Measuring* means every target updated in
the last two steps. Anything else is listed by name: *stopped updating*
(it had data, and has not for a while), *no data* (SmokePing never wrote
any), or *waiting* (added a moment ago, before its first step). The same
check is `GET /measurements` on the config API. "SmokePing: Running" above
it only means the container is up.

**4. The instrumentation agrees with itself.**

```bash
sudo smoking-pi doctor --live
```

The doctor checks that the dashboards, the exporters, the containers and
DNS actually describe the same system — the class of problem where
everything is "up" and the graphs are still wrong. Expected: every check
`ok`. What each one means is in [Instrumentation doctor](doctor.md).

---

## Step 6 — Measure what you care about

The install seeds **21 targets** — 5 top sites, 3 DNS resolvers, 9 HTTP
(three sites × three versions), 3 TCP handshakes and one example of your
own — and **7 probes** (ping v4 and v6, DNS, HTTP/1.1, HTTP/2, HTTP/3,
TCP). Your own targets go in through the web admin on `:8080` —
*Targets → Add* — which writes to PostgreSQL, regenerates SmokePing's
config and reloads it. The first hop is not in that list because nobody
has to add it: CPE discovery traceroutes out every hour, finds the first
responsive hop and injects it as a target of its own.

**The first login is a short tour.** On Standard and Pro, the web admin
opens on a three-step welcome the first time: whether it is measuring,
what your own network suggests adding (below), and the targets the
install set up, each with a switch to pause the ones you do not care
about. The install still seeds its targets, so it measures from the first
minute; the tour shows them rather than replacing them, and nothing is
added or paused unless you choose it. *Finish* or *Skip for now* and it
stays out of the way; the dashboard's **Welcome tour** button brings it
back.

**Start with what your own network suggests.** The seeded targets are the
same on every install; your router and the resolvers your network hands
out are not. On Pro, where SmokePing shares the host's network, the web
admin's dashboard has a **Your connection** card that reads them from the
host itself:

- the interface every measurement leaves by, and whether it is Wi-Fi;
- **your router** (the default gateway). Loss or delay there is your Wi-Fi
  or your LAN, and a problem that is fine at the router but bad further
  out is past your home. It is not seeded, because every home's router
  has a different address;
- the ISP's first hop, which CPE discovery already measures;
- the DNS resolvers the host was given. A local cache or a VPN stub
  (`127.0.0.53`, Tailscale's `100.100.100.100`) is not worth measuring and
  says so.

Each one says whether it is measured already, and under which name. What
is not has an **Add…** button that opens *Targets → Add* filled in: you
see the target, change it if you like, and save it through the same
validation as anything you type. Nothing is added for you. The same list
is `GET /recommendations` on the config API. On Standard, whose SmokePing
sits on a Docker network, the card says it cannot see the host's router
instead of showing Docker's.

Useful to know on the first day:

- **Netflix's Open Connect appliances** serving your network are discovered
  for you; you do not have to find them.
- **HTTP targets are per-version.** Adding `example.com` over HTTP/2 and
  HTTP/3 gives you two curves and the bare TCP handshake underneath as the
  floor ([HTTP and TCP probes](http-probes.md)).
- **IPv6 targets are gated**: a v6 probe is only configured if the host
  really has working v6, so you do not get a wall of red
  ([IPv6 gating](ipv6-gating.md)).
- **If the Pi is on Wi-Fi**, the uplink itself is measured — signal,
  bitrate, throughput, disconnects — so a bad hour can be blamed on the air
  rather than the ISP ([Wi-Fi uplink stats](wifi.md)).

---

## Step 7 — Connect an assistant (optional)

Smoking Pi is complete without this. What it adds is being able to ask, in
a chat window, *"was the line bad last night?"* and get an answer from
months of recorded history rather than a live `ping`.

It is two pieces: the **MCP server** (the `mcp` profile from step 3), which
exposes your measurements as tools, and an **MCP client** — [OpenClaw](https://github.com/openclaw/openclaw)
is the one with a ready-made skill.

- Both on this Pi: `smoking-pi openclaw` does it — the token, the profile,
  registration and the skill — and `smoking-pi openclaw --check` proves the
  agent is really using your history. `smoking-pi install` offers it at the
  end, and it is the same command afterwards.
  [OpenClaw integration](openclaw-integration.md) explains each step for
  when one of them does not work.
- OpenClaw on another machine: [OpenClaw on another machine](remote-openclaw.md).
- Any other MCP client: [MCP server](mcp-server.md) lists the 15 tools.

!!! warning "Put authentication in front of anything you expose"
    The MCP endpoint and the web admin are on your LAN as installed.
    Reaching them from outside goes through a
    [Cloudflare tunnel](cloudflare-tunnel-setup.md) with authentication in
    front — never a port forward.

---

## When something goes wrong

| What you see | Why | What to do |
|---|---|---|
| `docker: 'compose' is not a docker command` | Debian 12 / Raspberry Pi OS ships no Compose v2 | Step 1 — install from Docker's repository |
| `permission denied ... /var/run/docker.sock` | Your user is not in the `docker` group | `sudo usermod -aG docker "$USER"`, then log out and in |
| `install` says the edition is already installed | An env file exists; re-running would rotate every secret | `sudo smoking-pi up` to start it, or `sudo smoking-pi purge --config` to start over — that deletes the measurements |
| Graphs empty five minutes in | Nothing yet: the step is 300 s | Wait one step, then check `sudo smoking-pi logs smokeping` |
| Every target at 100% loss | ICMP blocked upstream, or no route | `ping -c3 google.com` from the host; if that fails it is the network, not Smoking Pi |
| Grafana rejects the password | Grafana keeps the first-boot password in its volume | `sudo smoking-pi restart grafana`, wait 30 s, try the password from `sudo smoking-pi passwords --show-secrets` again. If it still refuses, Grafana's volume predates that password — reset the account: `sudo docker compose exec -T grafana grafana cli admin reset-admin-password --password-from-stdin` |
| SmokePing's port is already taken | Pro's SmokePing is on the host network, port 80 | Free port 80, or use Basic/Standard, which map a port you can change |
| An optional service runs but does nothing | Its profile is on and its key is not set | `sudo smoking-pi alerts` (`alerts`: it sets the mode and its keys, and tests delivery) or `sudo smoking-pi config set ANTHROPIC_API_KEY` (`ai`). Neither crash-loops on a missing key — they log it and stay up, so `status` looks healthy while nothing is delivered |
| A container restarting in a loop | Its own logs say which | `sudo smoking-pi logs <service>` — read the last start, not the whole file |
| The page loads on the Pi but not from your laptop | A firewall between the two, or the laptop is on another network (a guest Wi-Fi) | Tunnel through SSH, which already works: `ssh -L 8080:localhost:8080 <user>@<pi>`, then open `http://localhost:8080/` on the laptop. `sudo smoking-pi url` prints this line with your addresses filled in |
| `smoking-pi` not found after `apt install` | A shell that cached its `PATH` | `hash -r`, or open a new shell |

Still stuck? `sudo smoking-pi doctor --live` is written for exactly this and
names the component rather than the symptom. Failing that,
[open an issue](https://github.com/estcarisimo/smoking-pi/issues) with its
output.

---

## What to read next

- [Upgrading](upgrades.md) — `apt upgrade && sudo smoking-pi upgrade`, and
  the two majors that are a data migration.
- [Alerting](alerting.md) — alerts that lead with a verdict: is it me or the
  internet?
- [Instrumentation doctor](doctor.md) — what each check proves.
- [Packaging](packaging.md) — the layout, the supported hosts, and the
  lifecycle end to end.

Backups are one command and worth doing before any upgrade:

```bash
sudo smoking-pi backup
```
