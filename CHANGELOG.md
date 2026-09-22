# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses semantic-ish versioning: minor bumps per completed
modernization sprint (or batch), patch bumps for hotfixes. Each released
version gets a matching GitHub release and git tag.

## [Unreleased]

### Added

- **`smoking-pi openclaw` — the assistant connection as a command, ending
  in proof.** `smoking-pi install` used to ask "Connect a chat assistant?"
  and, on yes, print the path to a document. That was the whole feature.
  Meanwhile `docs/openclaw-integration.md` is six steps, and the
  interesting thing about them is that four have a failure mode that looks
  like success: registering the MCP server without installing the skill
  leaves the agent answering from its own shell; a running gateway keeps
  its cached tool set, so a correct registration reaches nothing until it
  is reloaded and a new session started; and `openclaw mcp probe` reports
  healthy in both cases. The command now does the mechanical part — the
  token, the `mcp` profile, starting the server, checking the port refuses
  an unauthenticated request, registering, installing the skill — and
  `smoking-pi openclaw --check` asks the agent a question and then greps
  the MCP server's own log for `tool=` lines. **The answer is never the
  test**: a well-primed agent produces a fluent, accurate-sounding reply
  from `ping` while the server sits untouched, and that false positive is
  how four days of a dead integration went unnoticed. When there is no
  `tool=` line the command says so and lists the three causes in order.
  The MCP token reaches curl on stdin (`-K -`), never in argv, for the
  reason PR #96 established — a command line is readable by every account
  on the host — and a test asserts both halves, because one that only
  checks the credential is *absent* passes just as happily when it never
  arrived. A token that is not generated (someone wrote it by hand) is
  refused if it holds characters that would break the registration JSON,
  rather than producing a malformed payload that reads like a connectivity
  failure.
- **The install now distinguishes "no OpenClaw" from "OpenClaw on my
  laptop".** The old yes/no could not: it printed the same-machine
  document either way — which is the wrong advice for the remote case,
  where a tunnel between the two loopbacks has to exist first. The prompt
  is a three-way choice (here / another machine / not now); *here* runs
  the connector, *another machine* points at `docs/remote-openclaw.md`,
  *not now* names the command. Every branch ends with a working install:
  the stack is already measuring by the time the question is asked, and
  nothing about the assistant is required. `--yes` never prompts and still
  names the command.

- **A getting-started guide** (`docs/getting-started.md`, in the site nav
  right after Home). Seven numbered steps from a bare Raspberry Pi to a
  stack that is measuring: what you need, Docker with Compose v2 (and why
  Raspberry Pi OS needs Docker's own repository), `apt install` or a clone,
  `smoking-pi install` with what each answer means, the systemd unit, and
  then the part no install guide had: **how to tell it is actually
  working** — the six containers and which five report healthy, the URLs,
  the fact that every graph is empty until the first 300-second step
  completes, and `doctor --live`. It ends with a symptom → cause → fix
  table of the nine things that actually go wrong, from
  `docker: 'compose' is not a docker command` to a Grafana password that
  looks wrong because it lives in the volume.
- **A doctor check that the MCP tool table is the tool list**
  (`mcp-tools-documented`). It compares the functions registered with
  `@mcp.tool()` against the rows of the table in `docs/mcp-server.md`, in
  both directions: an undocumented tool is one nobody knows to ask for, and
  a documented tool that no longer exists reads as a promise. Static, so it
  runs in CI.
- **The doctor names the interface every measurement crosses**
  (`uplink-interface`, a live check). Nothing said this before, and the
  omission cost a year: the reference Pi has `eth0` with no carrier and its
  default route on `wlan0`, so every latency figure recorded since the stack
  went up had crossed a Wi-Fi hop nobody was measuring. It now prints
  `measuring over wlan0 (wireless, IPv4)`, says **wired** explicitly — so an
  empty Wi-Fi dashboard is distinguishable from a broken collector — and
  **warns** when the default route has moved onto a Docker bridge, a VPN
  tunnel, Tailscale or WireGuard. That last case is the one it exists for:
  the latency figures then describe that path, and the Wi-Fi verdict is off,
  because it requires the wireless interface to carry the default route.
  Nothing anywhere said so.

### Changed

- **`show-passwords.sh` no longer prints your secrets unless you ask.** It
  ran at the end of every install and on every `smoking-pi passwords`, and
  it printed all nine of them — the Grafana password, the config-manager
  and MCP tokens, the InfluxDB admin password and API token, the PostgreSQL
  password and the `DATABASE_URL` that embeds it, the web-admin password
  and the Flask `SECRET_KEY` — with no way to ask for anything less. The
  default view now shows each one as `set (hidden)`; `--show-secrets`
  prints the values. Two things deliberately did *not* move behind the
  flag: whether a secret is **set at all**, because `unset` means an
  unauthenticated endpoint and that is a warning, not a credential; and
  everything that was never secret — URLs, usernames, container state,
  port checks. `smoking-pi passwords` forwards its flags, so
  `smoking-pi passwords --show-secrets` is the whole interface.
- **`--show-secrets` refuses a pipe, a file or a capture unless forced.**
  When stdout is not a terminal it exits 3 with an explanation instead of
  printing, because a redirect outlives the screen — a log, a transcript,
  an assistant reading the run. `--force` says you meant it. The install
  tail is unaffected: it ends on the hidden view and one line saying where
  the values are.
- **The env file's permissions are checked.** Hiding secrets on screen
  means little if any account on the host can read the file they all live
  in, so a mode looser than `x00` is called out with the `chmod` to fix it.

### Fixed

- **Two health checks put a credential on the command line.** The InfluxDB
  and ClickHouse checks passed their token and password as `curl -H` and
  `curl -u` arguments, and a command line is readable by every account on
  the host (`ps`, `/proc/*/cmdline`) no matter what the output does — so
  these leaked on *every* run, including the hidden one, and including the
  one at the end of `install`. Both now pass the credential to curl on
  stdin (`-K -`). Note for anyone touching this again: curl's config
  syntax **must** be quoted here. `header = A: B` unquoted parses as a
  key/value line and the header is dropped in silence, which looks exactly
  like an authentication failure; a quote or backslash inside the value
  needs escaping in turn. Both are covered by tests that fail on the
  unquoted form.
- **The PostgreSQL health check had never once succeeded.** It ran
  `docker exec grafana-influx_postgres_1`, a container name that stopped
  existing when the editions split (and Compose v2 joins names with
  dashes, not underscores). Every healthy Pro stack was told
  `PostgreSQL connection failed`, `Config-manager will use YAML fallback
  mode`, and to delete a volume. It now goes through Compose
  (`compose exec -T postgres`), which resolves the container whatever the
  project is called.
- **The InfluxDB and ClickHouse credential checks passed with the wrong
  credentials.** Both used `curl -s`, which exits 0 on an HTTP 401, so
  `InfluxDB token is valid` was printed for any token at all — the single
  thing the check exists to catch. Both now use `curl -sf`. (Verified: a
  deliberately wrong token against the reference Pi's InfluxDB returned
  401 and the check reported success.)
- **`init-passwords-docker.sh` carried the same dead names, and worse
  advice.** It looked for `grafana-influx_*` volumes, so its "existing
  volumes detected" warning had never fired on a real install; and when it
  did fire it said to delete the InfluxDB volume because "SmokePing will
  repopulate data automatically", which is false — that history does not
  come back. It now resolves the project the way Compose does, leads with
  the non-destructive fix, and states the real consequence. It also
  honours `SMOKING_PI_ENV_FILE`: it wrote `./.env` unconditionally, so on
  a packaged install (env at `/etc/smoking-pi/env`) it generated a second
  set of secrets that nothing reads.
- **The troubleshooting advice named volumes that are not yours and one
  script that does not exist.** `grafana-influx_grafana-data`,
  `_influxdb-data` and `_postgres-data` belong to a Compose project this
  repository has not used in a long time; on a current install the names
  are `<project>_*`, which the script now computes the way Compose does.
  Fixing the names alone would have turned dead advice into destructive
  advice — "delete `influxdb-data`" is a year of measurements and
  "delete `postgres-data`" is every target — so the remediation changed
  too: `sync-influx-token.sh` for a token mismatch (it existed all along;
  the `verify-influxdb.sh` the script pointed at never did),
  `grafana cli admin reset-admin-password` for a Grafana login, logs and a
  restart for PostgreSQL, with the destructive option named as destructive
  where it is genuinely the last resort. `./verify-postgres.sh` is now
  offered only for Pro, which is the only edition that ships it.
- **Pro's documentation still named a Compose project that has not existed
  in years.** Eleven references to `grafana-influx-<service>-1` containers
  and `grafana-influx_*` volumes survived in `editions/pro/README.md`,
  `DNS_MONITORING.md` and `README-Zero-Touch.md` — the scripts were cleaned
  out separately, the prose was not. Every diagnostic command in the DNS and
  IPv6 troubleshooting sections therefore ended in `No such container`,
  which is the least useful possible answer to "why is there no data". They
  now go through Compose (`docker compose logs smokeping`,
  `docker compose exec -T influxdb ...`), which resolves the service
  whatever the project is called, with a line saying to run them from
  `editions/pro` and pointing at the `smoking-pi` command as the equivalent
  that works from anywhere. The stale Compose v1 `docker-compose` calls in
  the same blocks went with them.
- **The backup recipe backed up nothing, and correcting it alone would have
  been worse.** `docker run --rm -v influxdb-data:/data ... tar czf` names an
  unprefixed volume; the real one is `<project>_influxdb-data`, and
  `docker run -v` **creates** a missing volume instead of failing — so the
  command has always produced a valid-looking tarball of an empty directory,
  a backup that only reveals itself at the restore. The section now leads
  with `smoking-pi backup`, which needs no volume names at all, keeps the
  by-hand form with the `PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}"`
  idiom the scripts use, and says to check the tarball is not empty.
  Alongside it, two recipes offered `docker compose down -v` as "fresh
  start" and "stop & delete everything" with no statement of what `-v`
  costs. They are now split: `down` (no `-v`) is the reset almost everyone
  means and keeps the data, and the destructive one is labeled as such,
  preceded by a backup, and says what goes — `influxdb-data` is every sample
  ever recorded and `postgres-data` is every target, category and source,
  and neither comes back.
- **"Switching databases" told you to delete every target you had.** Pro's
  README opened the backend switch with `docker-compose down -v`, and `-v`
  takes `postgres-data` — the target, category and source list, which is not
  backend-specific and has nothing to do with the switch. Nothing about
  changing backends requires deleting a volume (`docs/clickhouse.md` has
  said so all along: each backend keeps its own history and the provisioning
  tree rebuilds itself). It is now `docker compose down`, with the reason
  `-v` does not belong there stated next to it.
- **Pro's directory layout diagram described a repository that no longer
  exists.** It was rooted at `grafana-influx/` and showed `smokeping/`,
  `influxdb/` and `grafana/` as children of the edition; the images moved to
  `shared/modules/` when the editions split, and seven services the diagram
  never mentioned have shipped since. Redrawn as the two real trees, so it
  answers the question it is there for: what is in `editions/pro`, and where
  do the images come from. The "Comparison with Minimal" table beside it
  compared against an edition name retired at the same time — it is Basic,
  and Basic has neither the Web Admin nor the Config Manager the table
  listed as "Optional".
- **The MCP tool table was four tools short.** `mute_alerts`,
  `unmute_alerts`, `ack_incident` and `list_alert_state` shipped with the
  alerting work and were explained in `docs/alerting.md`, but the MCP
  server's own page — the one someone reads to find out what the server can
  do — still listed eleven of the fifteen. Added, with their real
  signatures, and the check above makes the drift impossible to repeat.
- **The packaged commands were written without `sudo`, and would have failed
  on the first try.** `/etc/smoking-pi` is `0750` and root-owned — which is
  the point of keeping the secrets there — so `smoking-pi status` as an
  ordinary user stops at `permission denied` on the env file rather than
  degrading. Corrected in the new guide, in `docs/packaging.md`'s lifecycle
  table and in the README's lifecycle line, with the reason stated once
  rather than a bare `sudo` everywhere.
- **The install said an optional profile without its key would crash-loop.**
  Neither does: `ai-insights` logs the missing `ANTHROPIC_API_KEY` and exits
  0, and the alerter's `NOTIFY_MODE` defaults to `off`. The truth is worse
  and worth saying — the container stays up and healthy while nothing is
  ever delivered — so the note in `smoking-pi install` and the guide's
  troubleshooting row now say that instead.
- **The site's front door still described a pre-package project.**
  `docs/index.md` said "Nothing is published to a package registry: install
  from a clone" and that whether this stack could be an `apt install` was
  "answered, and parked" — both true until v2.12.0 shipped the apt
  repository the same day. It now leads with the package and links the new
  guide; the README's Quick Start puts `apt` first and the clone second,
  where it belongs as the development path.
- **A v6-only host could never say "it's your Wi-Fi".** The uplink was read
  from `/proc/net/route`, which has no IPv6 at all. With no IPv4 default
  route there was no row to find, so every Wi-Fi sample was tagged
  `uplink=0` — and the Wi-Fi verdict requires the uplink — while the
  interface selection silently fell back to "the first wireless one". It now
  falls back to `/proc/net/ipv6_route`, where `::/0` also appears twice on
  `lo` as an unreachable route, so the flags decide rather than the
  destination — `RTF_UP` alone. A default route installed **on-link, with no
  gateway** (what `wg-quick` writes; a PPP peer route has the same shape) is
  a real default route: on a real kernel `ip -6 route add default dev wg0`
  produces flags `0x00000001`, no `RTF_GATEWAY`. The first version of this
  fix required a gateway and would have dropped exactly that route, turning
  the most useful thing the check can say — *"your uplink is a tunnel"* —
  into *"no default route on this host"*. The rows that must be excluded do
  not set `RTF_UP` at all, so requiring it loses nothing.
- **An unreachable IPv4 default route could be reported as the uplink.**
  `ip route add unreachable default` is listed in `/proc/net/route` like any
  other default route, with the interface name literally `*` and
  `RTF_REJECT` set. With a lower metric than the real route it would have
  been picked, and `*` reported as the interface being measured.
- **Two default routes were resolved by file order, not by metric.** With
  Ethernet and Wi-Fi both up only the lowest metric carries traffic. The
  kernel does emit the prefix metric-ascending — verified by adding the
  high-metric route first in a throwaway namespace and reading the file back
  — so the old code was right by accident, on undocumented behavior nothing
  pinned. The metric is now compared, with a test.
- **`WIFI_INTERFACE` pointing at a non-wireless interface failed silently.**
  A typo, or a wired interface asked to report Wi-Fi statistics, disabled the
  collector with no message — indistinguishable from "no wireless hardware".
  It now logs which it is, and the wireless interfaces it did find.

## [2.12.0] — 2026-09-22

Smoking Pi is a package: `sudo apt install smoking-pi`.

Until now the only way to run it was a clone and an edition's `setup.sh`,
with the state the stack rewrites living inside the source tree. This
release is the packaging backlog of `docs/packaging.md`, all eight items:
the state is relocatable, the containers run the code baked into their
images, every service's image is built for arm64 and amd64 on the release
tag and pulled by version, a `smoking-pi` command does the lifecycle
(`install`, `upgrade`, `backup`, `restore`, `purge`), a `.deb` is built
from the tagged tree, and a signed apt repository on GitHub Pages
publishes it. Homebrew has a formula, untested on a Mac.

What makes it a release rather than a build: before deciding anything,
the package was installed with each supported host's own `apt` — Debian
12 (today's Raspberry Pi OS) ships no Compose v2 at all, Debian 13 split
the Docker CLI out of `docker.io`, and the first `Depends` line failed on
three of the four hosts. The release workflow now installs the `.deb` on
Ubuntu 22.04 and 24.04 VMs (both architectures — starting Basic with the
release's own images, the unit enabled and stopped) and on Debian 12 and
13 containers, then installs the previous release first and upgrades over
it; the `.deb` is attached only when all of them pass. Writing that
upgrade test found two ways the package would have broken every user's
`apt upgrade` and fixed them before any `.deb` had shipped. `apt remove`
and `apt purge` never delete a measurement, and say so. A Raspberry Pi is
still the only proof of a Raspberry Pi: `docs/release-acceptance.md` is
the checklist every release goes through on the reference Pi, and the
Validation section in these notes is its record.

Also in this release: the helper scripts told the truth for the first
time in a while (SmokePing's port, health checks that never worked on a
stock Raspberry Pi OS because `nc` is not there, a tunnel target that
could never resolve), and `manage-containers.sh` applies the ClickHouse
overlay.

### Added

- **The release builds, checks and attaches the `.deb` (packaging backlog
  #5, first half).** The package existed as a trial builder nobody ran;
  now `release.yml` builds it from the tagged tree, installs it on the
  runner and checks what a package can prove without Docker — the version
  it reports is the tag's, `smoking-pi paths` shows the packaged layout
  with the images pinned to that version, the systemd unit verifies, the
  doctor's static checks pass from `/opt`, `install` refuses over an
  existing env file, and removal keeps `/etc/smoking-pi` and
  `/var/lib/smoking-pi` — then keeps it as a workflow artifact and
  attaches it to the GitHub release the maintainer created for the tag.
  The workflow never creates a release. What remains of #5 is the signed
  apt repository on Pages.
- **A Homebrew formula (packaging backlog #8), untested on a Mac.**
  `Formula/smoking-pi.rb`: `brew tap estcarisimo/smoking-pi
  https://github.com/estcarisimo/smoking-pi && brew install smoking-pi`
  installs the release tarball under the Cellar with the command on
  PATH, Homebrew's bash/coreutils/gnu-sed ahead of macOS's (bash 3.2,
  BSD `sed -i` and `readlink`), a venv with PyYAML for the doctor, and
  `brew services` for `smoking-pi up` at login. The caveats say what the
  doc says: on macOS Pro's host-network measurements see Docker's Linux
  VM. `packaging/homebrew/bump.sh vX.Y.Z` is the maintainer's post-release
  step; CI proves the formula parses and its checksum is the tarball's,
  which is all that can be proven without a Mac.
- **Script hygiene (packaging backlog #7): no script guesses a container
  name, and the helper scripts tell the truth.** `sync-influx-token.sh`
  and `verify-postgres.sh` ask Compose for the container
  (`COMPOSE_PROJECT_NAME` decides the names) and honor the relocated env
  file; `create-tunnel.sh` reads the edition and project from Compose's
  labels and tunnels to **service** names on the project's network — its
  Pro SmokePing target (`pro-smokeping-1` on `pro_default`) could never
  have resolved, because that service runs on the host network; it now
  goes through the host gateway. `show-passwords.sh` printed SmokePing on
  8080/8081/8081 for Basic/Standard/Pro when the compose files map
  80/8081/80, and its health checks used `nc`, which a stock Raspberry Pi
  OS does not have, so every port read "not accessible" for as long as
  the script existed — bash's `/dev/tcp` now, only this edition's ports,
  service state from `compose ps`. Every `docker-compose` (v1) call and
  hint became `docker compose` or the `smoking-pi` command;
  `manage-containers.sh` is v2-only (v1 cannot parse these files), adds
  the ClickHouse overlay when the profile is recorded (it never did: a
  restart quietly rendered the InfluxDB stack), and `--edition` works
  from the repository root as the README always claimed.
  `shared/docs/maintenance.md` rewritten around the command and Compose
  labels; the `your-repo` placeholder URLs are the real ones.
- **`sudo apt install smoking-pi` (packaging backlog #5, second half).**
  GitHub Pages now serves a signed apt repository under `/apt` next to
  the docs site: `docs.yml` runs when the Release workflow has finished
  for a `vX.Y.Z` tag (not on the tag push, which raced the `attach` job),
  downloads every release's `.deb`, builds a flat repository
  (`packaging/apt/build-repo.sh`) signed with the `APT_SIGNING_KEY`
  secret, and deploys it with the site as one artifact. Without the
  secret the site is published with no `/apt` at all — an unsigned
  repository would only teach people `[trusted=yes]`. CI builds and
  signs a repository with a throwaway key on every PR and installs from
  it with `apt`. The README has the three lines a user types; Raspberry
  Pi OS and Debian 12 users install Docker from Docker's repository
  first, because Debian 12 ships no Compose v2. Publishing waits for the
  maintainer's key (`docs/packaging.md`, *The apt repository*).
- **The release proves the upgrade, not just the install — and the
  upgrade test found two ways the package would have broken it.** Once a
  release carries a `.deb`, each `host` job first installs *that* one and
  starts Basic on it, then installs the new package over it and runs
  `smoking-pi upgrade`: the secrets must survive byte for byte and every
  container must run the new images. Proven locally first, on Debian 12
  with Docker's engine under a nested daemon, which is where the two
  findings came from. (1) The package version lived in
  `/etc/default/smoking-pi`, a conffile that `install` had just started
  editing; dpkg keeps an edited conffile on upgrade, so every upgrade
  would have kept pulling the first release's images. The packaged
  command now takes its version from the tree it installed. (2) Worse: an
  edited conffile plus a shipped conffile that changes by one character
  stops the upgrade at dpkg's conffile prompt, which under a
  non-interactive `apt` is a failed upgrade for every user. `install` now
  records the edition in `/etc/smoking-pi/edition` (the unit no longer
  hard-codes `pro`), nothing edits the conffile, and the check fails if
  its md5 ever differs from the one dpkg recorded.
- **Uninstall policy (packaging backlog #6).** `apt remove` keeps
  everything; `apt purge` removes the regenerable directories and the
  conffile but never the Docker volumes nor `/etc/smoking-pi/env` — the
  file holds the credentials the volumes are locked with, so deleting it
  alone would turn a year of kept measurements into unreadable ones —
  and `postrm` says so. `smoking-pi purge --config` is the explicit way.
  Documented in `docs/upgrades.md`, *Uninstalling*.
- **Release acceptance on a Raspberry Pi** (`docs/release-acceptance.md`):
  the checklist every release goes through on the reference Pi before the
  tag — backup and rollback first, upgrade, reboot recovery, doctor, data
  for every probe, detections, 24 hours of stability — and the
  *Validation* section its GitHub release notes end with, untested
  combinations named. What the release workflow proves on Ubuntu VMs and
  Debian containers is written next to what only the Pi shows.
- **The release installs the `.deb` on every supported host, with that
  host's own `apt`.** Measured first, in containers of each OS: Debian 12
  (today's Raspberry Pi OS) ships no Compose v2 at all, Debian 13 split
  the Docker CLI out of `docker.io` and calls its Compose v2
  `docker-compose`, and apt takes the first installable alternative — so
  the original `Depends` line would have failed on three of the four
  supported hosts, or paired Debian's 20.10 daemon with Docker's Compose 5.
  The corrected line (`docker-ce | docker.io`, the CLI named explicitly,
  `docker-compose (>= 2)`) resolves on all of them. `release.yml` now
  checks it on every tag (first exercised by the throwaway runs
  `test-d0b5662` and `test-host-matrix`, before any release): Ubuntu 22.04
  and 24.04 VMs, amd64 and arm64,
  install the package, start the Basic edition with the release's images,
  enable and stop the unit; Debian 12 and 13 containers on both
  architectures check the package without a daemon, every edition
  rendered with that Debian's Compose. The `.deb` is attached to the
  release only after all of them pass. One script does the whole check
  (`packaging/tests/check-package.sh`), locally too. Also found by the
  new check: `install` never recorded the chosen edition, so a packaged
  Basic install would have been started as Pro by the systemd unit — it
  is written to `/etc/default/smoking-pi` now. `docs/packaging.md`,
  *Supported hosts*, has the measured matrix and what a container does
  not prove about a Raspberry Pi.

- **The `smoking-pi` command does the lifecycle (packaging backlog #4).**
  The prototype could start, stop and install; a backup was a `pg_dumpall`
  and a `docker run … tar` typed from `docs/upgrades.md`, a restore was
  nowhere written down, and "start over" meant finding the right `docker
  volume rm`. Now: `upgrade` (the release's images pulled, or rebuilt with
  fresh bases from a clone; `up -d`; the doctor), `backup` (dump, then the
  volumes the active services mount as tarballs with the stack stopped,
  env file, config), `restore` (each tarball's Compose volume key resolved
  to the volume this stack mounts — fixed `name:` included — after you
  type the project name; env and config where missing), `purge` (after
  typing the project name; `--config` for a clean `install`), and `install
  --profiles mcp,alerts,ai` (or a whiptail checklist).
  `packaging/tests/cli.bats`, 22 tests against a stubbed docker, runs in
  CI. Verified on the reference
  Pi: an offline backup (156 s, ~1 min stopped), a restore into a scratch
  project, a purge of it, an upgrade against the published throwaway
  images (28 s, doctor 13 ok).

- **Published multi-arch images and a release-only build (packaging
  backlog #3).** Nothing built the nine images anywhere but on the target
  host: CI built four of them, amd64 only, on every PR, and published
  none, so a first start on a Pi was a 20-minute build and an `apt install`
  would have been too. `.github/workflows/release.yml` now builds all nine
  for `linux/arm64` and `linux/amd64` on every `vX.Y.Z` tag — natively,
  each architecture on its own GitHub-hosted runner, pushed by digest and
  merged into one manifest per service — to
  `ghcr.io/estcarisimo/smoking-pi/<service>:<version>` (and `:latest`),
  refusing a tag that disagrees with `CITATION.cff` (a `test-*` tag runs the
  same pipeline for a throwaway image tag). Every built service in
  the compose files names that image next to its `build:` with
  `pull_policy: missing`, which makes Compose pull first and build only if
  the pull fails: `SMOKING_PI_VERSION` unset means `:dev`, a tag never
  published, so a clone still builds what it checked out; the package sets
  its version in `/etc/default/smoking-pi` and pulls. `smoking-pi paths`
  prints which. `packaging/check-images.py` (CI) fails when the compose
  files, the Dockerfiles and the workflow matrix stop agreeing. In line
  with the release-only CI decision, PR CI no longer builds images and the
  docs site deploys from the release tag rather than every push to `main`;
  a Dockerfile change is proven by the deploy on the reference Pi before
  merge. On a development host the next `up -d` recreates every container
  under the new image names (`docs/upgrades.md`).

### Fixed

- **Installed by the package, `smoking-pi` looked for the tree in `/`.**
  The command took its home as one directory above itself — right from a
  checkout (`packaging/smoking-pi`), and `/` from `/usr/bin/smoking-pi`;
  the `/opt/smoking-pi` fallback the usage text promised was never
  written, so a packaged `smoking-pi version` said `unknown` and `doctor`
  found nothing to check. The first run of the release's package job
  caught it. Now: the checkout when there is an `editions/` beside it,
  `/opt/smoking-pi` otherwise (the package's defaults file documents the
  knob, commented out — set there it would also capture a checkout's own
  script on the same host); the job checks `paths` reports
  `/opt/smoking-pi`.
- **`smoking-pi help` printed `dev: command not found`** — the usage text
  is a heredoc, and the previous change put a backticked word in it. Quoted.

- **`shared/modules/influxdb/Dockerfile` was never in git.** A `.gitignore`
  rule for InfluxDB *data* directories (`influxdb/`) matched the module
  directory too, so every clone but the reference Pi lacked the file the
  Pro compose file builds from — `docker compose up` failed on
  `influxdb` — no CI job built that image, and Dependabot's docker
  ecosystem (with its careful influxdb major-version guard) watched a file
  it could not see. The first run of the release workflow found it. The
  directory is un-ignored and the Dockerfile tracked; `check-images.py`
  now fails CI when an edition builds from a directory without a
  Dockerfile in the checkout.

### Changed

- **Packaged mode runs no source bind-mounts (packaging backlog #2).**
  From a clone, seven directories of `shared/modules` are bind-mounted into
  the Pro containers as development overlays — the exporters, three Grafana
  provisioning trees, the web-admin `app` package, the PostgreSQL and
  ClickHouse init SQL. For a package that would mean `apt upgrade` changing
  code under running processes. Every one of them is already baked into its
  image except the exporters, which now are (`COPY` into the smokeping
  image from a `shared/` build context, like the four Python images).
  `docker-compose.packaged.yml` (Pro; a one-service one for Standard)
  replaces those services' volume lists without the code mounts, and
  `SMOKING_PI_PACKAGED=1` — set by the package in `/etc/default/smoking-pi`
  — makes the `smoking-pi` command, `setup.sh` and `manage-containers.sh`
  add it, last, so it also wins over the ClickHouse overlay. Because a
  Compose override can only replace a volume list, not remove one entry,
  `packaging/check-packaged-override.py` renders both stacks with every
  profile on and fails if the override drops or adds anything but the code
  mounts; CI runs it for both editions. The doctor's `deployed-code-current`
  now hashes `/exporters` in the smokeping container too, so a stale
  smokeping image in packaged mode is reported like a stale alerter image.
  From a clone nothing changes. `docs/packaging.md`, *Packaged mode*.

- **Relocatable state (packaging backlog #1).** The YAML config-manager
  edits, the SmokePing config it generates and the `.env` with the secrets
  all lived inside the source tree — the first two *tracked in git*, so the
  reference Pi's `git status` had shown the running stack's rewrites as
  modified files since the day it went live, and a package upgrade would
  have replaced them. Three variables now decide where that state lives:
  `SMOKING_PI_CONFIG_DIR`, `SMOKING_PI_OUTPUT_DIR` (read by the compose
  files' bind mounts) and `SMOKING_PI_ENV_FILE` (passed as `--env-file` by
  `setup.sh`, `generate-passwords.sh`, `show-passwords.sh`,
  `manage-containers.sh` and the `smoking-pi` command). Unset, everything
  stays where it was — beside the edition's compose file — but untracked:
  `editions/pro/config-manager/{config,output}` keep only a `.gitkeep`. A
  fresh config directory is seeded by config-manager's bootstrap from
  `templates/`, as it always was for a missing file; the image no longer
  copies a `config/` directory, and the stale
  `shared/modules/config-manager/{config,output}` copies (five probes,
  untouched since Sprint 3) are deleted — `templates/` is the one seed set
  and a test holds it to the current probe list. The `.deb` sets the
  packaged layout (`/etc/smoking-pi/{env,config}`,
  `/var/lib/smoking-pi/output`) in `/etc/default/smoking-pi` and creates the
  directories; CI renders both compose files with that layout and fails if
  a mount is hardcoded again. `docs/packaging.md`, *Relocatable state*.
  **Upgrading a running host:** `git pull` past this change deletes the
  now-untracked files from the working tree; recreate config-manager and
  it regenerates everything from the database — `docs/upgrades.md`,
  *Pulling past v2.11*.

## [2.11.0] — 2026-09-20

The detectors say what happened, once; the documentation has a site; and
ClickHouse mode writes rows.

After weeks of use the reference Pi's alerts were mostly false in a
specific way — one event, one cause, reported once per target. The night
its Wi-Fi radio hung (still associated, receiving nothing for 3 h 20 min)
produced about a hundred and ten messages; a three-minute blink produced
thirty-six; and the assistant listed every single lost ping as a loss
event, and called the gateway's ICMP rate-limit floor "strong microcuts"
for weeks. This release treats downtime and microcuts as two separate
investigations, each with weeks of the Pi's own data as evidence, a
definition, and tests that replay the real nights; the microcut definition
was also run against the Pi's InfluxDB before merging, in the MCP tool and
again in the in-UI assistant. A loss that hits most targets in the same
probe cycle is one incident, named as a hung radio when the Wi-Fi counters
show one; a
microcut is a run of windows above the threshold, confirmed or possible,
with a duration, beside the floor it stands on. The same definition is
read by the alerter, the MCP server, the in-UI assistant, the digest and
the AI report. The radio itself is documented, with the power-save
hypothesis and the reasons the one-line fix is left to the operator.

The guides are now a site — [estcarisimo.github.io/smoking-pi](https://estcarisimo.github.io/smoking-pi/)
— built strictly in CI and deployed from `main`. And ClickHouse mode,
revived in 2.5 and believed to work since, had never written a row; it
now does, verified end to end against a ClickHouse 24.1 from the Pi's
RRDs — on a throwaway stack, not on the reference Pi, which stays on
InfluxDB.

### Added

- **Microcuts are cuts, not windows.** The other half of the detection
  reliability work. On a gateway that rate-limits ICMP, 98% of the 10 s
  CPE windows show some loss and the daily p90 sits at 14–22% with nothing
  wrong — and `get_microcut_stats` counted "windows with any loss" and
  always returned a top-5, so every answer for weeks said "strong
  microcuts, worst 82%" about the floor's tail; the alerter's
  `microcut_burst` counted windows above 50% and called two isolated ones
  23 minutes apart a burst. Fourteen days of data held 17 isolated windows
  and one real cut: six consecutive windows at 100%. One definition now
  lives in `common/microcuts.py` and every consumer reads it: a cut is a
  run of consecutive windows above `MICROCUT_LOSS_PCT`, confirmed with two
  or more windows or a 100% window, possible when it is one isolated window
  below that; the floor is reported as p50/p90 beside it. The tool returns
  `cuts` with durations and zoomed links, the per-target floor, only cut
  windows in `worst_windows`, and a note stating the floor when there were
  no cuts; the alert says *"1 cut of 2 min 40 s (6 windows, all at 100%)"*
  and fires on a confirmed cut or `MICROCUT_BURST_N` (now 3) possible ones;
  the digest and the AI report carry the same fields. Verified against the
  Pi's data before merging: over seven days, one 3 h 25 min total cut (the
  hung radio), the 2 min 40 s cut, one possible cut.

- **Downtime detection reports one event once.** After weeks of use the
  reference Pi's downtime alerts were mostly false in a specific way: one
  event with one cause reported once per target. The night its Wi-Fi radio
  hung (associated at −49 dBm, zero packets received for 3 h 20 min) the
  alerter sent 18 `target_down` criticals and 18 `high_loss` warnings, each
  re-sent every cooldown — about 110 messages — with a verdict of "local
  link cutting out"; a three-minute blink on 2026-09-19 produced 18
  warnings and 18 recoveries; and the assistant's `get_loss_events` listed
  every single lost ping (60–300 a day across every target) as an event.
  Now: a loss that hits most targets in the same probe cycle is ONE
  incident — `uplink_down` (critical) while every target sits at 100%,
  named as a hung radio when the Wi-Fi counters show one; `outage`
  (warning, no recovery) for a brief cut that already ended — and the
  per-target incidents it would fan out into are not sent. `high_loss`
  needs the loss to persist for two cycles. `get_loss_events` defaults to
  15% (two or more lost pings), counts the single-ping background instead
  of listing it, folds consecutive points into `episodes` with durations,
  and reports `widespread` runs with their cause. The evidence and the numbers
  behind every threshold are in `docs/detection-reliability.md`; the
  microcut half is the entry above.

- **A documentation site.** Thirteen guides lived under `docs/` and three
  more under `shared/docs/`, reachable only by knowing the path, with
  cross-links nobody checked. `mkdocs.yml` now builds them into a site at
  [estcarisimo.github.io/smoking-pi](https://estcarisimo.github.io/smoking-pi/)
  (Material theme, search, a landing page, the changelog included by
  snippet). CI builds it with `--strict`, so a broken link or a dangling
  anchor fails the PR — the first build found one. `docs.yml` deploys the
  same build to GitHub Pages on every push to `main`. The two tunnel guides
  moved from `shared/docs/` into `docs/` so they are on the site; the
  maintenance guide stays out until it is rewritten for the edition layout
  (packaging backlog #7), because a published page that names containers
  that do not exist is worse than none.

### Changed

- **When the radio hangs.** The two September hangs of the reference Pi's
  Wi-Fi radio (associated, transmitting, receiving nothing; 21 h and
  3 h 20 min) have a likely cause — `brcmfmac` with power save on — and a
  one-line fix that is deliberately not applied. `docs/wifi.md` now records
  the signature, what is known against what is only suspected, why the
  change is the operator's (it is a host setting reached over the link it
  drops, and it moves the latency floor the Pi measures), how to try it so
  it reverts itself, and the decision to leave it on.

### Fixed

- **The in-UI assistant reads the same microcut definition.** The
  web-admin chat kept its own copy of `get_microcut_stats` that could not
  import `common`, so after the MCP tool, the alerter and the reports were
  fixed it still counted every window with any loss and returned a top-5 —
  the same "strong microcuts, worst 82%" about the gateway's floor, now
  from the other assistant. The web-admin image builds from `shared/` like
  its three siblings and copies `common/` in; the tool folds cuts, reports
  the floor as p50/p90 and states it in a note when there were none, and
  `get_loss_events` defaults to the shared 15% instead of 5%, so a single
  lost ping out of ten is background there too. Verified against the Pi's
  InfluxDB beside the MCP tool: identical cuts and floor over 24 h and 7 d.

- **ClickHouse mode never wrote a row.** Since the Sprint 13 revival the
  exporter connected without a database so it could create the schema
  first — and then never selected it, so every insert went to
  `default.latency`, which does not exist; a second defect sent an
  `rrd_file` column the table has no room for, which ClickHouse answers by
  rejecting the whole batch. The schema check in `docs/clickhouse.md`
  passed throughout, so nothing looked wrong until the HTTP dashboard's
  ClickHouse variant was finally run against a live ClickHouse. The
  exporter now selects the database once it exists and writes a declared
  column list that a test holds against its own `CREATE TABLE`. Verified
  against ClickHouse 24.1 from the reference Pi's RRDs: all four
  measurement types land, and the *HTTP by Version* ClickHouse dashboard's
  variable and panels return rows through Grafana's query API.

## [2.10.0] — 2026-09-19

The web is measured the way it is served, and two roadmap questions get
their answers.

Ping told you the path was fine; it never told you whether a page would
load, or over which protocol. This release fetches `https://<host>/` over
HTTP/1.1, HTTP/2 and HTTP/3 as three separate probes, with the negotiated
version *enforced* — a server that quietly downgrades produces a loss, not
a mislabeled sample — and times the bare TCP handshake beside them so the
chart shows what each protocol adds on top of the connection. HTTP/3 needed
a curl no distribution ships, so the image carries one, pinned by hash. The
probes reach the exporters, a side-by-side dashboard (written for both
backends, so far run only against InfluxDB), the add-target form and the
assistant's deep links.

Two questions the roadmap kept open are now closed with a document each:
the last-mile signal the customer gateway would have to report, and does
not; and what a package of this stack would have to be — one that manages
the Compose deployment — with the trial `.deb` and the backlog that trial
produced.

### Added

- **HTTP/1.1, HTTP/2, HTTP/3 and TCP probes.** Four new probes, all
  running inside the SmokePing container: `CurlHTTP1`, `CurlHTTP2` and
  `CurlHTTP3` fetch `https://<host>/` over one HTTP version each, and
  `TCPPing` times the SYN/SYN-ACK handshake to port 443. The version is
  enforced, not requested: the probe's own `-w` format prints the
  negotiated version and `expect` turns a downgrade into a loss, so an
  HTTP/2 series never quietly contains HTTP/1.1 samples. HTTP/3 needs a
  curl with a QUIC backend, which neither Alpine nor `curlimages/curl`
  ship, so the image adds a static `stunnel/static-curl` build pinned by
  version and sha256 per architecture — and the 1.1 and 2 probes use the
  same binary so the comparison is protocol, not TLS stack. Sub-probes
  (`+ Curl` / `++ CurlHTTP2`) are now expressible in `probes.yaml` via a
  `module` key; the `probes` table gains `module` and `options` columns,
  added to existing tables on startup. An already-migrated deployment
  picks up the new probes, the `http`/`tcp` categories and their example
  targets on its next start. Exporters classify `HTTP/` and `TCP/` RRDs as
  `http_latency` / `tcp_latency` with the version in `probe_type`; a
  Grafana dashboard (InfluxDB and ClickHouse) overlays the three versions
  per site with the TCP floor underneath; web-admin's add form offers
  *HTTPS fetch* (with a version) and *TCP connect*; the assistant's deep
  links for the new measurements resolve to that dashboard. The ClickHouse
  dashboard variant follows the plugin's documented format and has not yet
  run against a live ClickHouse. `docs/http-probes.md`.
- **The CPE last-mile question, answered.** `docs/cpe-last-mile.md`
  records the read-only exploration of the reference gateway (a Nest Wifi
  Pro in front of a transparent Fiber Jack), documents the one endpoint it
  exposes (`/api/v1/status`: WAN state, lease, first ISP hop, uptime — no
  physical layer), and parks the feature: PHY numbers reach the customer
  only when the ISP's own device is the router.
- **The packaging question, answered.** The roadmap asked whether a stack
  of ten Compose services fits apt and Homebrew, and what would have to
  change. `docs/packaging.md` inventories the system as a package sees it
  (nine images built on the target, a checkout that is a runtime
  dependency, git-tracked directories the running stack mutates, no unit,
  no registry), weighs four shapes, and picks one: a package that manages
  the Compose deployment, never native packages of four upstream projects.
  An end-to-end trial backs it — `packaging/deb/build.sh` builds a 1.4 MB
  `.deb` from `git archive HEAD` in two seconds, and `docker compose
  config` resolves the relative `../../shared` paths from `/opt/smoking-pi`
  unchanged — and the trial's failures are the backlog: relocatable state
  first (`.env` and the mutated config directories out of the tree, which
  also ends the runtime churn in `git status`), no source bind-mounts in
  packaged mode, multi-arch images on GHCR, the `smoking-pi` command as the
  installer the roadmap wants, a signed apt repository, an uninstall that
  never deletes a year of measurements. About ten days in total; the first
  two pay for themselves without any packaging. The prototype CLI and unit
  ship under `packaging/` and work from a clone today; the builder took the
  working tree on its first run and shipped the reference Pi's target list,
  which is why it now takes only what is committed.

### Removed

- The `EchoPingDNS` and `EchoPingHttp` entries in the probes template:
  `echoping` is unmaintained and not packaged, so they never ran.

## [2.9.0] — 2026-09-19

The hop every measurement crosses is measured too, and the project says
what it is in the languages it uses.

The reference Pi, it turns out, has never had a cable in `eth0`: every
latency and loss figure in a year of history reached the internet over
`wlan0`, and nothing recorded the state of that link. So a microcut that
coincided with a signal dip could only ever be blamed on "your line". This
release records the Wi-Fi uplink every ten seconds, charts it under the
microcuts it explains, gives the assistant a tool for it, and lets the
verdict say *the router or the air, not the ISP* — while stating plainly
which numbers a Raspberry Pi's own driver does not report, rather than
drawing an empty panel and calling it zero.

Around it, the housekeeping a public project owes its readers: a guide for
running OpenClaw on another machine without opening a port, a citation file
CI keeps honest, a security policy that says what happens after you file,
one spelling (American) written into the rules and applied to the tree, and
a review step that is an independent session rather than a bot whose
review request often went unanswered. Charts learned to scale to the typical shape instead of the worst
spike, and an MCP error can no longer carry an InfluxDB token in its text.

### Added

- **The Wi-Fi hop is explained: the verdict, the digest and an MCP tool now
  say it.** With the hop recorded and charted, the last step is letting the
  words follow. `get_wifi_stats(hours, interface)` answers "how is the
  Wi-Fi?" from the record — current association and signal, the window's
  signal range and weak share, disconnects, roams, failures, peak throughput,
  and the five weakest samples each linked to its moment — and
  `system_status` carries a `wifi` block so an assistant knows every other
  number crossed that link. The verdict gains a `wifi` scope, 📶, taken when
  the first hop is cutting *and* the uplink spent at least a minute's worth
  of samples below −75 dBm (or dropped) in the hour: *"Your Wi-Fi — the
  first hop is cutting out and the signal fell to −78 dBm; the router or the
  air, not the ISP."* When the Wi-Fi was fine every existing line is
  byte-identical, and the context line shows `wi-fi min −54 dBm` so a reader
  sees it was checked; a spare radio that is not the default route is
  reported and never acted on. The daily digest and the AI report get a
  *Local link* line. Two settings, `WIFI_WEAK_DBM` and `WIFI_WEAK_SAMPLES`,
  shared by the alerter and the MCP server. The OpenClaw skill learns the
  tool, the dBm bands, and a Wi-Fi bullet in its report template.

- **The doctor reads negative defaults.** `DEFAULT_WIFI_WEAK_DBM = -75.0`
  is a `UnaryOp` in the AST, not a `Constant`, so the compose-vs-module
  default check silently skipped it and would have reported OK for a value
  it never compared. It now resolves negated numbers; the test reintroduces
  the gap with a compose file pinning −70 against a module −75 and asserts
  the FAIL.

- **The Wi-Fi hop is charted, next to the microcuts it explains.** A *Wi-Fi
  Link* dashboard (six rows: link now, signal, PHY bitrate, throughput,
  quality, roaming) and — the panel that matters — the Pi's signal and TX
  failures drawn under the CPE microcut panels on the same time axis, so a
  cut that lines up with a dip reads as the router or the air rather than
  the ISP. Panels for what a driver may not report (noise, MCS, retries)
  say so in their description instead of looking broken. Current-value
  tiles use `group() |> last()`, because after a roam a plain `last()`
  would happily show the stale access point. `links.wifi_links()` builds
  deep links into the dashboard (the graph pair only — an interface has no
  per-ping detail, peers or edit page), and `system_status`, the digest and
  alerts gain a `grafana_wifi_link` entry point. Every panel query was run
  through Grafana's datasource API on the reference Pi: 0 errors, and the
  empty ones are exactly the fields brcmfmac does not write.

- **The Wi-Fi hop is recorded.** The reference Pi's uplink is `wlan0` —
  `eth0` has never had a carrier — so every latency and loss figure in a
  year of history crossed a Wi-Fi link that nothing measured. A microcut
  coinciding with a −80 dBm dip is a router problem; the verdict could only
  ever call it "your line". A new collector in the smokeping container,
  `wifi_link.py`, samples the wireless uplink every 10 s through nl80211
  (`iw`, now in the image; no capability needed under host networking) and
  writes a `wifi_link` measurement: signal, negotiated PHY bitrate,
  channel/band/width, failures, the interface byte counters that survive
  re-association, `carrier_down_count` for disconnects, and — on drivers
  that report a survey — noise, SNR and channel-busy share. Tags are
  `interface` and, while associated, `ssid`/`bssid`, so a roam is a visible
  series change. On a wired host it logs one line and idles. Field types
  are pinned (counters `int`, levels `float`; a Python bool would have been
  written as a boolean field and poisoned the measurement) and the tests
  assert the line protocol. What the Pi's own `brcmfmac` does *not* report
  — noise, retries, beacon loss, MCS — is documented as absent rather than
  invented. The dashboard, the MCP tool and the verdict follow in their own
  changes; `docs/wifi.md` says what is recorded and how to query it.

- **A guide for OpenClaw on another machine.** The integration docs assumed
  OpenClaw and Smoking Pi share a host, and the security model depends on
  it: both the MCP server and the gateway listen on loopback and nothing
  else can reach them. Someone with OpenClaw on a desktop or a VPS had no
  supported way to connect the two, and the obvious one — forward the port,
  rely on the bearer token — puts a server that can add targets and restart
  SmokePing behind a single secret on the public internet.
  `docs/remote-openclaw.md` gives three ways that keep both loopback binds,
  ranked by exposure: an SSH tunnel carrying both directions in one session
  (after which the co-located guide applies verbatim; a systemd unit in
  `examples/openclaw/` keeps it up), a Tailscale/WireGuard mesh (Serve or a
  direct tailnet bind), and Cloudflare Tunnel with an Access service token
  (with the plain statement that Cloudflare then sees the traffic). It says
  what each one exposes to floods and to third parties, what not to do, and
  records the resolv.conf trap that once cost the reference Pi nine targets
  for ten days when Tailscale logged out. One knob to make the tailnet
  variant possible: `MCP_HOST` is now overridable from `.env` (default
  unchanged, `127.0.0.1`; the template says never `0.0.0.0`). The reference
  deployment stays co-located.

- **`CITATION.cff`, checked by CI.** GitHub's "Cite this repository" button
  now works, giving author, title, version, date and repository URL in a
  form citation managers import. Left out on purpose: a DOI and an ORCID,
  because neither exists for this project yet and a citation file should
  not carry a guess. The file is the kind that rots — the version in it is
  wrong the day after the next release — so a CI job validates it against
  the CFF schema and fails if `version`/`date-released` do not match the
  newest released section of this changelog. The release procedure in
  CONTRIBUTING and AGENTS names the step.

- **SECURITY.md says what to send and what happens next.** The policy had a
  private channel, a scope and a supported-versions line, but a reporter
  had to guess what a useful report contains (edition, version, backend,
  entry point, impact) and had no idea what happens after filing — whether
  a fix goes on a public branch, when the advisory is published, whether
  they get credit, when they may disclose. It now spells out the report
  contents, a four-step coordinated-disclosure process on GitHub's private
  advisory fork, the 14/30-day escalation, and who reads the reports
  (the CODEOWNERS owner).

- **The logo is back.** A steaming raspberry pie, drawn in August 2025 and
  committed only to a branch that never merged; the README on `main` had
  pointed at `img/logo.jpg` for a year without the file existing. Recovered
  while pruning that branch, downscaled from 2048² (700 KB) to 512² (about
  a tenth of that), and placed above the title at 220 px.

### Changed

- **American English is the project's language, and now it says so.** The
  code base had grown up bilingual: `initialize` next to `initialise`,
  `color` in matplotlib calls and `colour` in the comment above them, a
  `centre` variable in the deep-link code, `behaviour` in the agent
  instructions and the OpenClaw skill. Harmless in any single file, but an
  agent that reads AGENTS.md and copies its spelling into a new function
  name produces the next inconsistency, and a reviewer has no rule to point
  at. AGENTS.md, CONTRIBUTING.md and the PR template now state the rule
  (US spelling for names, docs, comments and agent instructions), and the
  sixty-odd existing deviations across docs, comments, the OpenClaw skill,
  two shell messages and one local variable are corrected. No public name
  changes: the one shipped British spelling (`cancelled` in the web-admin AI
  result) stays, and the policy says why.

- **The review step is an independent session, not Copilot.** AGENTS.md,
  CONTRIBUTING.md and the PR template said every PR gets a Copilot review
  and must not merge over it unread. In practice the review-request API
  silently did nothing on a third of recent PRs (#57, #62, #63) and the
  reviews cost more than they found. The rule is now: a reviewer who did
  not write the change — a fresh model session (the maintainer uses a cold
  Sonnet session per PR) or a human — reads the whole diff against
  AGENTS.md's contracts, and its findings and their resolution are recorded
  in a PR comment. The first two such reviews caught a broken link and two
  lock files that had leaked into a docs PR, which is the argument.

- **Charts: the scale follows the typical shape, not the worst spike.**
  A 24 h gateway chart with nine windows peaking at 180 ms on a 9 ms link
  let those nine set the axis, pressing the median and the inner band —
  the part a reader actually judges by — into the bottom fifth of the
  panel. The latency axis now follows the 90th percentile of the outer
  band (with a third of headroom over the inner band, and never cutting
  the median or a peer line); windows above it are clipped and *counted*,
  and the count and true maximum are written on the chart ("▲ 9 windows
  peaked above 81 ms (max 187 ms)"). A clipped chart that says it is
  clipped hides nothing.

  The loss panel draws the alert threshold that applies — `MICROCUT_LOSS_PCT`
  for the gateway, `HIGH_LOSS_PCT` otherwise — as a dashed line with its
  value, so the gateway's permanent 10 % ICMP floor reads as "well under
  the line" rather than as loss. Dashed is reserved for this: the grid is
  solid hairlines. The median gets a halo in the surface color so it stays
  legible where it runs through its own band. All three apply to alert
  charts too, since the renderer is shared; the `mcp-server` service now
  receives both threshold variables so `get_chart` draws the same line the
  alerter would.

### Security

- **MCP tool errors no longer carry exception text.** The roadmap's "make
  sure the MCP server cannot leak credentials" item, reviewed end to end:
  the bearer check is constant-time, tool arguments named like secrets are
  redacted in the log, `/status` carries no secrets, and no token or
  password can reach a tool result. Two internals could: an InfluxDB
  failure returned the client's whole exception — response headers, body
  and the Flux query — and a config-manager failure embedded the API's
  base URL (which in a proxied deployment can carry userinfo) and up to 200
  bytes of whatever body came back. A tool result goes to the model and
  from there into a chat, so it now gets the same discipline as an HTTP
  error body: a message chosen in code plus an `error_id`, detail in the
  mcp-server log under that id. `ConfigAPIError` names the operation, the
  HTTP status and config-manager's own (static) error, nothing else. Tests
  raise an `ApiException`-shaped error carrying a fake token inside every
  measurement tool and assert it never comes back.

## [2.8.1] — 2026-09-14

The repository grows the files a contributor looks for first, and a fresh
install stops shipping two APIs unauthenticated.

Two days after 2.8.0, on reading the code reviews that 2.8.0's PRs were
merged over: `setup.sh` had never generated the config-manager or MCP bearer
tokens, so both services — one of which exposes every mutation the other
has — came up unauthenticated on a new box while the README said otherwise.
They are generated now. The rest is the housekeeping a project is supposed
to have (`CONTRIBUTING.md`, `AGENTS.md`, a code of conduct, owners,
templates), a rule that every PR's Copilot review gets read, and the
smaller findings from the reviews that went unread.

### Security

- **`setup.sh` now generates `CONFIG_API_TOKEN` (Standard, Pro) and
  `MCP_API_TOKEN` (Pro).** Both shipped empty, which both services treat as
  *unauthenticated*, while the README described the APIs as bearer-protected
  — a Copilot review comment on the README, and a fair one. The MCP server
  exposes every mutation the config API has. `show-passwords.sh` prints both
  tokens with the header each expects, and says so in red when one is
  unset. Existing `.env` files are not touched; set the two keys by hand to
  get the same protection (`openssl rand -hex 32`).

- **`safe_path.confine()` also resolves symlinks**, and rejects `.`, `..`
  and the empty name explicitly (on a root base, `..` normalizes to the
  base itself, which the prefix check cannot see). Both from the Copilot
  review of #52; the lexical check remains what CodeQL scores.

### Fixed

- **`manage-containers.sh` works with Compose v2.** It required the legacy
  `docker-compose` binary while the README's requirements only promised the
  `docker compose` plugin. It now uses whichever is present, preferring v2.

- **Tests that were not testing what they said** (Copilot review of #52,
  #53): the config-manager unknown-type test used a path Werkzeug
  normalizes away before routing, so its assertions never ran; the status
  test patched a method that does not exist; the CrUX confinement test
  failed the regex before reaching the code under test; the backslash
  redirect guard had no test at all. Each now exercises the real branch.

- **README claims that were not true**, from the same review: setup does
  not wait for health in every edition; profiles given on the command line
  are not persisted (edit `COMPOSE_PROFILES` in `.env`); `.env` is not
  exported into your shell; deep links appear only once `PUBLIC_BASE_HOST`
  is set; the config file paths and the data-flow description are Pro's,
  now labeled as such; CodeQL runs through GitHub's default setup, not the
  workflow; and every multi-line `cd` example is anchored at the checkout
  root.

### Added

- **The community files a repository is supposed to have.** `CONTRIBUTING.md`
  (setup, per-module tests, the things that look wrong but are load-bearing,
  PR and commit conventions, what a useful bug report contains),
  `AGENTS.md` (the same for AI coding agents: commands, constraints such as
  the error-response contract and path confinement, and the rule that every
  PR gets a read Copilot review), a Contributor Covenant `CODE_OF_CONDUCT.md`
  pointing at the maintainer's GitHub handle and the private advisory link,
  `CODEOWNERS`, a pull request template with the checklist CI cannot run for
  you, and bug/feature issue templates that ask for the edition, the
  `error_id` and the doctor's output. The README's documentation table now
  lists them.

## [2.8.0] — 2026-09-12

A picture you can hand to anyone, and nothing on the wire that should not be.

The one thing every answer this stack gave still lacked was something a
person *without* a login could look at. Public snapshot links were the
obvious route and were rejected in 2.7.0 for what they are — permanent,
world-readable URLs with the measurements inside. This release takes the
opposite trade: a static PNG, drawn on request by the MCP server, delivered
into the chat, forwarded by the user to whoever they choose. It draws what
SmokePing always drew — the median with the spread of the individual pings
around it — because a jittery-but-alive link and a clean one can share a
median, and the band is what tells them apart.

The other half is the sixty-nine open CodeQL alerts, which were five
problems: every route echoed exception text to the browser, request-named
files reached the filesystem on the strength of a regex, a token lived in
`localStorage`, the login redirect echoed its input, and CI's token had more
rights than it used. None was a known exploit; all were the kind of thing
that turns a small bug elsewhere into a disclosure. All closed, each with a
test that reintroduces it, and the count on `main` is zero.

### Added

- **`get_chart` — a picture, on request.** New MCP tool that draws one
  target's latency and packet loss over a window as a PNG: the median line
  with the spread of the individual pings shaded around it (outer min–max,
  inner quartiles — SmokePing's "smoke"), loss underneath on a fixed 0–100
  axis, major and minor gridlines, local-time axis, and a footer naming the
  source and when it was drawn. `with_peers=true` adds the same-category
  peers as faint lines.

  The point is the person who has no login here. Every other answer this
  stack gives ends in a Grafana link that asks the reader to sign in; a PNG
  is something the user can forward to a friend or the ISP. It is the
  opposite trade from the snapshot links switched off in 2.7.0: a static
  file the user chose to send, not a permanent world-readable URL.

  It is on request only. No other tool attaches images, and the server
  instructions and the OpenClaw skill both say so — an ordinary "how is my
  internet?" stays text. `deliver=true` also posts the file into the OpenClaw
  chat through the same Gateway endpoint the alerter uses (the agent can
  *see* an MCP image but cannot forward it), and the result reports
  `delivered` or a `delivery_error` that names the missing setting; the
  image comes back either way.

### Security

Sixty-nine open CodeQL alerts, five causes, three PRs (#51, #52, and the
error-response change below). None was a known exploit; all were the kind of
thing that turns a small bug elsewhere into a disclosure.

- **Error responses no longer echo exception text** (51 ×
  `py/stack-trace-exposure`, config-manager and web-admin). Every route
  answered `{'error': str(e)}`, so whatever an exception carried — a
  database DSN with its password, the config-manager URL and token, a
  filesystem path, a library's internal message — went to the browser and to
  anything else on the port. Routes now return a message chosen in code plus
  an eight-character `error_id`; the detail goes to the log with a traceback
  under that id (`docker compose logs web-admin | grep <id>`). Validation
  reasons are unchanged: the config-manager validators return lists of
  plain strings instead of raising, and `PUT /config/<type>` returns them as
  `problems`. "Not found" is the static `Target not found`. Tests in both
  apps raise an exception containing a fake secret inside every affected
  route and assert it never reaches the body — reintroduce `str(e)` and
  the route's case fails.

- **Request-named files are confined to their directory** (10 ×
  `py/path-injection`). The CrUX and Cloudflare cache files are named after
  a `country` query parameter, the AI reports page takes a `file` name, and
  config-manager formatted `CONFIG_DIR/<type>.yaml` before checking the
  type. Each had a regex allow-list, which bounds the string but not the
  path. web-admin gains `services/safe_path.confine()` (normalize, then
  require the base-directory prefix); config-manager resolves the type
  through a literal table so request text is never formatted into a path.

- **The Cloudflare API token is no longer written to `localStorage`** (1 ×
  `js/clear-text-storage-of-sensitive-data`). It was saved on every
  keystroke, readable by any script on the origin. It now lives in the
  password field for the life of the page and travels only in the POST
  body; values left by earlier versions are removed on load.

- **The post-login redirect is rebuilt, not echoed** (1 ×
  `py/url-redirection`), and rejects backslashes, which browsers read as a
  second slash.

- **CI's `GITHUB_TOKEN` is `contents: read`** (6 ×
  `actions/missing-workflow-permissions`). No job writes to the repository.

### Changed

- **README rewritten** in the shape of the sibling Netflix OCA Locator's:
  features first, one quick start, usage by task, a documentation table
  that only lists files that exist. The broken logo reference
  (`img/logo.jpg` never existed) is gone until there is a logo; the
  security contact points at `SECURITY.md`'s private reporting instead of
  a placeholder address.

- **The chart renderer moved to `shared/modules/common/charts.py`** so the
  alerter and the MCP server draw the same picture. The alerter's
  `charts.py` is an alias, as `flux.py` already was. The OpenClaw
  `/tools/invoke` payload builder moved with it (`common/openclaw.py`); the
  alerter's `openclaw_invoke_payload` delegates to it.

- **Alert charts gained the dispersion band and minor gridlines** as a
  consequence of sharing the renderer. A jittery-but-alive link and a clean
  one can share a median; the band is what tells them apart.

- **The `mcp-server` service runs on the host network**, as the alerter
  does and for the same reason: the OpenClaw gateway listens on the host's
  loopback only — which is the right setting — and no bridge network can
  reach it. Exposure is unchanged: `MCP_HOST=127.0.0.1` pins the listener
  exactly where the old `127.0.0.1:8090:8090` port mapping put it.
  `CONFIG_API_URL`/`INFLUX_URL` for this service now default to the
  loopback addresses both backends already publish on (override with
  `MCP_CONFIG_API_URL` / `MCP_INFLUX_URL`).

### Fixed

- **Microcut alert charts drew the gateway's latency ×1000.** `cpe_latency`
  stores milliseconds while `latency`/`dns_latency` store seconds, and the
  renderer scaled every measurement as seconds — so a 7 ms gateway plotted as
  7000 ms. Visibly wrong, but only on a chart nobody had compared to the
  dashboard. Now scaled per measurement, with a test that reintroduces the
  bug.

- **web-admin no longer logs `Control server error: Permission denied:
  '/home/smokeping'` at startup.** Its user was created with `useradd -r`,
  which makes no home directory, and gunicorn 26 opens a control socket
  under `$HOME`. The user now gets a home. Harmless before, but a
  permanent error line on a clean boot is one you learn to skip, and then
  you skip the one that matters.

- `docs/alerting.md` now lists `ALERT_CHARTS` and the `CHART_*` knobs in the
  environment reference; they were documented only in `.env.template`.

## [2.7.0] — 2026-09-01

Alerts that reach you, say what they mean, and can be told to be quiet.

The alerting engine had been evaluating rules correctly for a week and telling
nobody: `NOTIFY_MODE=openclaw` POSTed to a path no OpenClaw build serves, and
the 404 got generalised into "the gateway has no HTTP ingress at all". It has
one. Everything else here follows from delivery actually working — a verdict
line so an alert leads with what it means, the chart in the message, a daily
digest so silence stops being ambiguous, and mute control by conversation.

The other half is about not trusting green lights. The doctor learned to check
the *running* stack, not just the repo, after a masked build failure let a
three-week-old image look healthy. That check then caught a stale container and
a Dependabot PR that would have initialized an empty PostgreSQL over the config
source of truth — with CI passing, because CI never builds that image.


### Fixed

- **Grafana snapshots switched off.** A snapshot is a permanent,
  *unauthenticated* URL with the measurements embedded in it — `GET
  /api/snapshots/<key>` answers 200 with no credentials, confirmed on 13.0.2.
  Everything else on this host is behind a login (anonymous auth off, the API
  401s), so this was the one way to publish home network data by accident, and
  it sits one click away in Grafana's share dialog. `external_enabled` is worse
  and defaulted **on**: it publishes to the third-party `snapshots.raintank.io`.
  Both are now off, and creating a snapshot returns `403 Dashboard Snapshots
  are disabled` even as admin.

  This also closes out the planned "public snapshot links" feature. A spike
  showed it was technically workable on 13.0.2, but the design it implies —
  handing out world-readable URLs — is the wrong trade for this deployment.
  Alert links point at Grafana and the reader signs in.

- **`container-dns-fresh` no longer warns about a resolver that was pinned on
  purpose.** Two changes from the same batch disagreed: one pins the smokeping
  container's resolvers in Compose (`dns:`) so it cannot inherit a resolver
  that later evaporates, and the other warns whenever a container's resolver
  is not the host's. The warning only appeared once smokeping was next
  recreated and the pin actually took effect — so it would have shown up on a
  later unrelated deploy, looking like a new fault.

  Left alone it would have warned on every run forever, and a check that
  always warns is one you stop reading. That is expensive here specifically:
  this check exists because a frozen resolver silently killed half the
  monitoring for ten days. It now reads the Compose `dns:` blocks and treats a
  pinned resolver as expected, resolving container to service by Compose label
  rather than by name (`container_name:` overrides make name-parsing wrong).
  A resolver that is neither pinned nor the host's is still reported, and a
  pin on one service does not excuse another — both pinned by tests.

### Changed

- **Python base images 3.11 → 3.14**, and CI now tests on the version it
  ships. CI ran `setup-python@3.11` while every container ran a different
  interpreter, so a 3.14-only break could not have been caught — the same
  class of drift as a compose default silently overriding a module constant.
  Verified before bumping: all seven modules' suites pass on 3.14, and both
  the heaviest image (alerter — matplotlib, numpy, pillow) and the one with
  Rust-compiled deps (web-admin — pydantic) build on **arm64** with real
  wheels rather than falling back to source builds on a Pi.

- **Dependabot no longer groups stateful major bumps with routine ones.**
  `postgres` and `influxdb` majors are ignored outright; other docker majors
  are split from minor/patch, matching what the Python ecosystem already did.

  This is a fix for a near miss. One grouped PR carried the harmless Python
  bump *and* `postgres:15→18`, and CI passed it — the Docker build matrix
  never builds the postgres image, and no CI job has an existing volume, so
  there was nothing for a green check to mean. Two failure modes were
  confirmed against the live volume: PG18 refuses to start on a PG15 directory
  (loud, safe), and — worse — PG18 relocated its default `PGDATA` to
  `/var/lib/postgresql/18/docker`, so with our mount it reports
  "uninitialized" and would initialize an **empty** cluster while the real
  data sat orphaned. Postgres is this project's config source of truth, so
  that mode presents as every target vanishing from a container reporting
  healthy. New `docs/upgrades.md` carries the dump-first procedure for both
  stateful images.

### Added

- **Alert mutes, controlled by conversation rather than buttons.** Four MCP
  tools — `mute_alerts`, `unmute_alerts`, `ack_incident`, `list_alert_state` —
  so "mute amazon for two hours, I'm reflashing the router" works. Telegram
  buttons cannot call back without an OpenClaw channel plugin; natural language
  is the better interface anyway, because it carries a scope, a duration and a
  reason that no button could.

  Two containers now share state, and the race is removed by construction
  rather than managed: each file has exactly one writer (the alerter owns
  `state.json`, the mcp-server owns `mutes.json`) and the other side mounts it
  `:ro`, so single-writer is OS-enforced instead of conventional. Neither
  process ever read-modify-writes the other's file, so there is no lock to
  acquire and none to leak. Cross-container reads are safe because both writers
  already used temp-file-plus-`os.replace`; that was introduced as crash
  safety and is now also the concurrency contract.

  Muting is the one feature here that can *cause* a missed outage, so the
  mitigations are structural rather than left to discipline: a 24-hour cap on
  any mute, every digest listing what is muted and how many alerts each one
  swallowed, recoveries never suppressed for an incident that was already
  announced, no recovery at all for one muted from first sight (nobody heard it
  start), and `unmute_alerts(all=True)`. A missing or corrupt mutes file means
  **everything alerts** — delivery must never depend on that file being
  readable, so its failure mode is noise rather than silence.

  The check sits after the cooldown and after the rate limiter, and never calls
  `_record_notification()`. After the limiter so an alert the ceiling already
  blocked is not also counted as one the mute suppressed; before recording so
  the hourly budget counts only what was really sent; and inside the incident
  loop so `last_seen` and the severity refresh still run — skipping them would
  leave the incident looking brand new when the mute lifts, sending it down the
  first-seen path and reintroducing the flapping the grace period fixed.

- **The doctor can now check the running stack (`--live`).** Two checks, both
  for failures that already happened here, and both sharing one shape: the
  broken thing keeps looking healthy, so nothing goes red and nobody looks.

  `deployed-code-current` compares the sha256 of every deployed `.py` — the
  module's own source and the shared `common` package — against the
  repository. This is `dde5e36` ("the flap fix never reached the deployed
  container"), and it recurred while building this batch: an image failed to
  build, the failure was masked by a shell pipeline's exit code, `docker
  compose up -d` recreated the container from a three-week-old image, and
  every surface reported success. A container it cannot read is reported as
  "cannot verify", never as drift — claiming a difference it did not measure
  would be the same class of bug.

  `container-dns-fresh` compares each container's resolvers against the
  host's. Docker writes `/etc/resolv.conf` once, at container creation, so a
  container created while a VPN was up keeps that resolver after the VPN is
  gone — which cost nine of eighteen targets for ten days, with hostname
  targets at 100% loss and raw-IP targets perfectly healthy. Loopback
  resolvers are excluded: `127.0.0.11` is Docker's own embedded DNS and is
  fresh by construction. Without that exclusion the first real run flagged
  all six healthy containers, which is how a check gets ignored.

  Both skip cleanly without Docker, so CI and the static path are unchanged.

- **A daily digest, so silence stops being ambiguous.** Alerts only fire when
  something breaks, which means a quiet channel means either "nothing
  happened" or "the monitoring stopped" — and those are the two states you
  most need to tell apart. `DIGEST_ENABLED` opts into a wall-clock summary of
  the last 24 hours, in the same shape as everything else this bot sends:
  traffic lights, bold sections, worst targets first.

  **Firing exactly once is the hard part**, because the loop wakes every 60
  seconds and has no idea what time it is between ticks. Each tick resolves
  the most recent scheduled instant at or before now — the *slot* — and state
  records which slot last fired, making delivery idempotent: ten ticks in a
  minute resolve one slot and send one message.

  The slot is persisted, **not the send time**. That distinction is the whole
  mechanism: anything recorded before the slot instant re-fires the same day,
  which is what a send-time-like value becomes after an NTP step backwards or
  on a container whose RTC starts behind. A reintroduction test pins it.
  Both DST transitions are covered — a nonexistent wall time resolves to one
  stable instant, an ambiguous one picks the same instant every tick, and a
  full day of five-minute ticks across a transition delivers once.

  Past `DIGEST_MAX_LATENESS` (4h) a slot is recorded as fired **without**
  sending. A Pi that was off for two days must not deliver 08:30's digest at
  19:00, and must not deliver two.

  **It never claims health it did not verify.** If the aggregate query raises
  or returns zero targets, nothing is sent and the slot is retired with an
  error. "All clear" when the truth is "InfluxDB did not answer" would turn a
  broken monitor into a reassuring message — the exact failure the digest
  exists to catch, and the first thing its tests assert.

  Counts come from a new `state["history"]`, appended on each alert or
  recovery the alerter decides to send — whether or not delivery then
  succeeded — and pruned to 200 entries and 48 hours. Necessary because
  `reconcile()` pops a record on recovery, so by 08:30 an incident that fired
  and cleared at 03:00 has left no other trace.

### Fixed

- **`show-tunnel-urls.sh` reported the first hostname a tunnel ever had.** A
  quick tunnel gets a brand new `*.trycloudflare.com` name every time
  cloudflared reconnects, and every one stays in the container log; the script
  took `head -1`, so it printed the oldest — dead, and entirely plausible
  looking. Found with 12 distinct URLs in one log while it reported #1. It now
  takes the last, and warns when a tunnel has rotated, since anything saved
  earlier into `.env`, a bookmark or a chat is already a dead link.

- **A text-only message could not be delivered silently.** `silent` was set
  only on the image path and only from `ALERT_SILENT`, so a text-only digest
  would ring a phone at 08:30 regardless. It is now a per-message property
  that wins over the env default — quiet is a fact about *this* message, not
  about the deployment — and it survives the text-only retry after an image
  send fails, which previously dropped it.

- **Every link is offered twice: at home, and from anywhere.** Deep links were
  built from a single base URL, which forced a choice nobody can make
  correctly — the same person reads an answer from the couch and from a train.
  A LAN address is the better link at home (one hop, and up even when
  Cloudflare isn't) and a dead link on cellular.

  `TUNNEL_BASE_HOST` (plus `GRAFANA_TUNNEL_URL` / `WEB_ADMIN_TUNNEL_URL`)
  configures the from-anywhere address separately, and every entry in a `links`
  object gains a `_tunnel` twin: same panel, same window, different host.
  `system_status` twins its three entry points the same way.

  Three behaviors keep a twin from lying about where it goes: a tunnel with no
  LAN address configured *becomes* the primary link rather than leaving a
  tunnel-only Pi with no links at all; two tiers resolving to the same base are
  not twinned, because two labels on one URL invite a reader to try "the other
  one"; and the ClickHouse gate covers both tiers, since a twinned 404 is still
  a 404.

  In alerts only the *graph* gets a twin. A Grafana deep link is ~120
  characters and a caption budget is 1024, so mirroring all four would spend a
  quarter of it saying the same thing twice.

- **The monitoring skill now specifies the shape of a report, not just its
  content.** `examples/openclaw/smokeping-monitoring/SKILL.md` gained a report
  template — traffic lights per section (🟢 nothing to do, 🟡 real but minor,
  🔴 acted on), bold headings, one fact per bullet with its number and window,
  a verdict as the headline rather than a summary, and a graphs section
  offering both links.

  Three rules exist because the first real reports broke them. **Headings are
  the channel's bold, never `###`** — Telegram does not render Markdown
  headings, so `### DNS` arrives as literal hashes or flat text, which is what
  made an otherwise correct report read as one wall. **Every section is
  bullets, one fact and one line each** — a semicolon or a second measurement
  means it is two bullets, with a worked wrong/right pair in the file.
  **A bullet that names a moment carries that moment's link**, taken from the
  `worst_windows` entries `get_microcut_stats` already zooms to ±15 minutes;
  the week-long overview URL drops the reader into 168 hours to hunt for a
  spike the agent had already found.

  **The template is a structure, not a script.** It is written in English
  because the tools and metric names are; the skill directs the agent to answer
  in whatever language the question arrived in, and marks what never gets
  translated — target names (they are database keys: a translated
  `CloudflareDNS` cannot be looked up, muted, or found on a dashboard), numbers
  with their units, the traffic lights, and the links.

  It also names the four values an operator must tune before installing — the
  host's names, the CPE loss floor, the timezone, the example target names —
  rather than saying "replace the placeholders" and marking none of them. A
  wrong loss floor makes the agent confidently wrong rather than obviously
  broken, which is the harder failure to notice.

- **`shared/scripts/install-openclaw-skill.sh`** installs the skill, backs up
  an existing one, and optionally reloads the gateway. `--check` exits
  non-zero when the installed copy is stale — the failure this guards against
  is invisible, since an agent running a stale skill keeps answering, just in
  the old shape, and the install looks like it silently did nothing. The
  README and `docs/openclaw-integration.md` now point at it.

- **Alerts arrive with the graph.** Each one carries a rendered PNG: median
  latency over packet loss for the target, its same-category peers as gray
  context, and a marked line at the moment the incident started — so the
  question a Grafana trip is usually made to answer (*since when, and is it
  just this one?*) is answered in the notification.

  Rendered with matplotlib in-process, **not** `grafana-image-renderer`: that
  is a headless Chromium at ~400 MB resident and seconds of CPU per render, on
  a Pi that has already hit its soft thermal limit. Bytes are posted as base64
  in the invoke body, so there is no shared filesystem between the container
  and the gateway, no path translation and no retention sweep.

  Design decisions that are load-bearing rather than cosmetic:

  - **Two stacked panels, never twin y-axes.** Latency and loss have different
    scales; a dual axis invents a correlation that is not in the data.
  - **The loss axis is pinned 0–100.** Autoscaled, 4% loss looks catastrophic,
    and loss is the axis a reader interprets absolutely.
  - **Emphasis, not eight hues.** The story is one target, so it wears a status
    color and the peers recede.
  - **Digest bars are one hue.** Coloring each bar darker-where-bigger
    double-encodes bar length on nominal categories; over-threshold rows are
    marked with a glyph and a status-colored value instead, so color never
    carries meaning alone — and it keeps discriminating when everything is over
    the line.

  A chart never costs an alert: rendering is wrapped, matplotlib is imported
  lazily, and a failed image send retries **exactly once** as text rather than
  doubling the retry budget.

- **Alerts now answer the question they raise.** Every notification carried a
  measurement (`amazon: mean loss 22.4% over 15m`) and left the reader to work
  out whether it was their line, their ISP, or that one site. Each alert now
  leads with a verdict: `🌐 Not you — 12 of 16 destinations affected but your
  local link is clean, so this is upstream.`

  It is deterministic and costs **no additional queries** — the breadth,
  microcut and liveness rows were already fetched by the rules and thrown
  away, which matters on a Pi that has hit its thermal limit.
  `evaluate_with_context()` returns them; `evaluate()` is unchanged.

  Two properties are load-bearing:

  - `exporter_stale` outranks every other scope unconditionally. Announcing a
    network fault from an *absence* of measurements is the worst thing this
    could do, and when the exporter stalls every target reads as 100% lost
    precisely because nothing is arriving.
  - Hosts that never answer ICMP are excluded from breadth entirely. A target
    pointed at a host that does not respond charts a permanent flat 100%;
    counting those turns one slow site into an ISP outage. Anything at 100%
    whose incident predates `VERDICT_STALE_DOWN_HOURS` is dropped from both
    numerator and denominator. This is not hypothetical — see the DNS note
    below, where nine such targets would have made every verdict wrong.

  Every verdict logs its own inputs, so one you disagree with is diagnosable
  from `docker compose logs alerter` without reproducing the moment.

### Changed


- **Code that two images need now lives in `shared/modules/common/`**, copied
  into each image at build time (`context: ../../shared`). Containers cannot
  import across each other, so the Flux helpers were already triplicated
  between the alerter, ai-insights and the mcp-server, and the deep-link UID
  maps existed twice. Extracted rather than copied a fourth time: `tsdb.py`
  (queries, the loss-unit clamp), `aggregates.py` (the per-target and CPE
  rollups) and `links.py` (Grafana/web-admin URLs).

  The old module paths remain as re-export shims, so no call site changed.
  Two tests did, and the reason is worth writing down: a shim forwards public
  names, but **`monkeypatch.setattr(shim, "query_influx", ...)` does not
  intercept the real function**, because the moved code resolves that name in
  its own globals. The ai-insights collector tests were silently querying the
  live InfluxDB instead of their stub. They now patch the module that owns the
  code. The alerter's lazy-client test moved for the same reason — asserting
  `_influx_client is None` through a shim would hold forever whether or not a
  client had been built.

### Fixed

- **The smokeping container's resolver is pinned instead of inherited.** Docker
  writes `/etc/resolv.conf` once, at container creation, and never again — so a
  container inherits whatever the host's resolver happened to be at that
  instant and holds it forever.

  This deployment captured a Tailscale MagicDNS address. Tailscale later logged
  out, and from then on **nine of eighteen targets reported 100% loss for ten
  days** — every hostname target, while every raw-IP target stayed green.
  Nothing surfaced it, because "100% loss" and "that host is down" are
  indistinguishable; the alerter dutifully tracked the incidents to
  `notified_count=253` with delivery switched off.

  `dns:` is honoured even under `network_mode: host`. Public resolvers by
  default (`SMOKEPING_DNS`, `SMOKEPING_DNS_FALLBACK` to override) so this does
  not depend on any particular LAN addressing. Only the smokeping service is
  pinned: the bridge-network services must keep Docker's embedded resolver for
  service-name lookups.

  The real lesson is the missing signal, not the missing config — a *live*
  doctor check for "a target that has never produced a non-100% point" would
  have caught this in an hour instead of ten days. `docs/doctor.md` lists the
  live checks as still unbuilt.

- **Chart timestamps matched the data, not the label.** matplotlib formats
  dates with `rcParams["timezone"]` (UTC) regardless of each datetime's own
  tzinfo, so the x-axis rendered UTC beneath a footer naming the local zone —
  an hour's silent offset, which is exactly what makes someone mis-correlate an
  incident with whatever they were doing at the time. Caught by rendering the
  chart and looking at it.

- **Messages are composed to a budget instead of truncated.** Telegram allows
  4096 characters for a message but only **1024 for a caption**, so an alert
  carrying a chart has a quarter of the room. Sections now carry a priority
  and the least valuable are dropped until the message fits — mute hint, then
  the breadth recap (the verdict line already states that number), then the
  links, which survive longest because a static image caption cannot be
  explored and a link is the way out of it. The headline, verdict and numbers
  are never dropped.

  Only if those alone overflow is text trimmed, and then on a line boundary.
  `reports_watcher` previously did a hard `content[:max_chars]` slice, which
  the moment messages became HTML could cut a tag in half — Telegram rejects
  that with a 400, which `/tools/invoke` reports as HTTP 200 with
  `{"ok": false}`, so the notifier would burn three retries and log a
  permanent delivery failure for what is really a formatting bug. That cap
  also now bounds the **whole** message including its header; it previously
  delivered 3530 characters at a documented limit of 3500.

  Every interpolated value is escaped: target names are user-editable and
  `a<b&c` is a legal one today.

- **Several alerter tests depended on the developer's own shell.** The env
  scrub list in `conftest.py` was missing `STALE_WINDOW`, `ALERT_RESOLVE_AFTER`,
  `ALERT_MAX_PER_HOUR` and `MICROCUT_LOSS_PCT`, so an exported value silently
  changed what they asserted.

- **Deep links no longer point at dashboards that do not exist.** Every
  dashboard UID in `links.py` comes from the InfluxDB provisioning tree, but
  the ClickHouse tree is a parallel set with different UIDs and no CPE
  dashboard at all. Under `TSDB_TYPE=clickhouse` every link the MCP tools
  emitted resolved to a Grafana 404 — while looking entirely valid in the
  answer. Links are now gated on the active backend, the same doctrine the
  module already applied to an unset base URL: no link beats a broken one.
  `system_status()` distinguishes the two reasons, since telling someone to
  set `PUBLIC_BASE_HOST` when it is already set is its own dead end.

- **The flap fix below did not reach the deployed container.** Raising
  `DEFAULT_DOWN_WINDOW` from 900 to 1200 fixed nothing in practice, because
  `docker-compose.yml` pinned `DOWN_WINDOW=${DOWN_WINDOW:-900}` and a Compose
  default silently wins over a module default. Both files read as correct on
  their own; only the pair was wrong. `STALE_WINDOW`, added in the same batch,
  was declared in no compose file, no `.env.template` and no doc at all.

  Fixed, and — more usefully — made impossible to repeat. The instrumentation
  doctor gained **`alerter-env-defaults-match`**, which extracts every env var
  the alerter reads out of its own source with `ast` (including the
  `os.environ.get(...) or DEFAULT_X` idiom) and asserts that each Compose
  `${VAR:-x}` default equals the `DEFAULT_*` constant behind it. Reverting the
  compose line to 900 now fails CI with the specific pair that disagrees.
  A companion **`alerter-env-declared`** warns when a knob exists only in
  Python — discoverable in neither compose, `.env.template`, nor the
  `docs/alerting.md` table.

  `docs/alerting.md` also contradicted itself on this value (900 in the rules
  table, 1200 in the flap-damping section), and `exporter_stale`'s alert text
  still claimed a hardcoded "10m" after the window became configurable and
  moved to 1200 s. The message now reports the window it actually queried.

- **`COMPOSE_PROFILES` is now persisted, not passed once.** Starting an
  optional service with `COMPOSE_PROFILES=mcp docker compose up -d mcp-server`
  leaves it running but unmanaged: the next `docker compose down` removes it
  and the following `up -d` does not bring it back, with nothing to say so.
  That is how this deployment ended up running an MCP server that Compose no
  longer knew about — and when it goes, OpenClaw silently falls back to
  answering from the shell instead of the monitoring data. `setup.sh` now
  writes `COMPOSE_PROFILES` into the generated `.env` alongside `TSDB_TYPE`,
  and `.env.template` documents the full profile list.

- **A flapping incident sent unbounded notifications.** Found the hard way: a
  `target_down` incident on a test target alternated alert/recovery on a
  five-minute cycle and delivered ~48 messages every two hours to a real
  phone, for four hours.

  Three defects at three layers, all now fixed:

  1. `DOWN_WINDOW` was 900 s with `DOWN_MIN_POINTS=3` on a 300 s probe step —
     *exactly* three points, so ordinary window jitter yielded two, the rule
     stopped matching, and the incident looked resolved. Now 1200 s (four
     points where three are required).
  2. Recovery deleted the incident record outright, so the next appearance
     took the first-seen path and alerted immediately. `ALERT_COOLDOWN` only
     ever suppressed *continuously* active incidents and did nothing in the
     one case where it matters. An incident must now be absent for
     `ALERT_RESOLVE_AFTER` (default 900 s) before it counts as recovered;
     reappearing inside that window is silent.
  3. New `ALERT_MAX_PER_HOUR` (default 6): a hard ceiling per incident key,
     independent of the lifecycle logic, so a future lifecycle bug cannot
     reach a phone at that volume. `0` disables it.

  Verified by replaying the observed flap pattern against both versions: 48
  notifications per two hours before, at most 3 after.

- **Alert delivery to OpenClaw now works.** `NOTIFY_MODE=openclaw` was
  unusable: it POSTed to `{OPENCLAW_URL}/hooks/agent`, a path that exists on no
  OpenClaw build. The resulting 404 was read as *"the gateway is WebSocket-only
  and has no HTTP ingress at all"*, and the mode was documented as needing an
  HTTP-RPC plugin or a bridge that was never written — so the alerting engine
  ran for a week evaluating rules correctly and telling nobody.

  The gateway does serve HTTP, multiplexed onto the same port: `POST
  /tools/invoke` is always enabled. Alerts are now delivered by invoking
  OpenClaw's `message` tool through it, authenticated with the Gateway token
  (`OPENCLAW_GATEWAY_TOKEN`; `OPENCLAW_HOOK_TOKEN` still accepted). Verified
  against OpenClaw 2026.7.1-2. The generalisation from one missing route to a
  whole missing protocol is called out in both docs, since the shape of that
  mistake is more useful than the fix.

- **A refused send counted as a delivered alert.** `/tools/invoke` answers
  **HTTP 200 with `{"ok": false}`** when the tool itself fails — a blocked
  tool, a bad channel, an unknown recipient. The notifier checked only the
  status code, so every one of those would have been recorded as success. It
  now inspects the body and retries, for this endpoint only (a generic webhook
  keeps status-code semantics).

- **The preflight could not tell a blocked tool from a missing endpoint.** Both
  answer 404. Reporting the wrong one is exactly the inference that caused the
  bug above, so the body is now read to separate "your tool policy blocks
  `message`" from "that route does not exist", alongside 401 for a rejected
  Gateway token and a connection error for a gateway that is down.

- `NOTIFY_MODE=openclaw` now refuses to run with `OPENCLAW_TO` unset instead of
  posting a message with no recipient and reporting success.


## [2.6.0] — 2026-08-09

Answers that come with the graph, and a tool that checks the monitoring is
actually monitoring.

Two additions that point in the same direction. Deep links close the gap
between "median 8 ms, 0% loss" and the panel that shows what the number is
hiding. The instrumentation doctor closes a wider one: nothing in CI could
tell whether a dashboard, a datasource, or an exporter still agreed with its
neighbours, which is how v2.5.0 shipped a Grafana that would not start.

Upgrade notes:

- **Deep links are off until you configure them.** Set `PUBLIC_BASE_HOST` on
  the mcp-server service to the address a reader can actually open. Unset is a
  supported state, not a broken one — tool responses simply carry no links.
- **The `Grafana dashboards & provisioning` CI job now runs the doctor.** It
  checks strictly more than the validator it replaces, so a repo that passed
  before may now fail — that is the point. Run it locally with
  `PYTHONPATH=shared/modules/doctor python -m doctor`.

### Added

- **An instrumentation doctor** (`shared/modules/doctor/`, `docs/doctor.md`),
  which verifies the monitoring wiring rather than the network: the case where
  a measurement or panel looks fine and silently charts nothing. Nine static
  checks compare each stage of the pipeline against the next — dashboard
  queries against the vocabulary the exporters actually write, datasource
  references against what provisioning declares, dashboard files against the
  paths Grafana scans.

  It replaces the inline JSON/YAML validator in the `Grafana dashboards &
  provisioning` CI job, so every PR is now gated on it. Notably it catches the
  two-datasources-claim-default configuration that made v2.5.0 unable to start
  Grafana, which nothing in CI would have caught before.

  The vocabulary is read out of the exporter source with `ast` rather than
  copied into a list, since a copied list drifts silently — which is the bug
  class the tool exists for. Every check is proved by a test that reintroduces
  the bug and asserts the doctor fails; a doctor that only ever passes is
  indistinguishable from one that does nothing.

  The live checks (target present in the DB but missing from the RRDs or the
  TSDB, queries that error or return empty, loss outside its declared range)
  are not built yet — `docs/doctor.md` lists them.

- **Deep links in MCP tool responses.** Each target now carries a `links`
  object — the Grafana panel scoped to that target and time window, the
  per-ping detail, the side-by-side against its peers, and the web-admin page
  for editing it — so an assistant can hand over the graph instead of only the
  median. `get_microcut_stats` zooms each of its worst-5 windows to a
  ±15-minute range around when it happened.

  Off until configured, deliberately: this host answers on a LAN IP, a
  Tailscale name and possibly a tunnel hostname, and a link to the wrong one
  looks right in the transcript and fails silently on the reader's phone.
  Set `PUBLIC_BASE_HOST` (standard ports appended) or `GRAFANA_PUBLIC_URL` /
  `WEB_ADMIN_PUBLIC_URL` where a proxy hides the ports. Unset means no links
  at all rather than a guess; `system_status()` is the one place that says so.

### Changed

- `get_loss_events` returns a `by_target` rollup (event count, worst loss, the
  span covered, links) alongside the raw events. It was returning up to 500
  individual points with no summary, and a hundred loss points on one target
  is one story rather than a hundred.

### Fixed

- The web-admin login redirect dropped the query string — it redirected to
  `next=request.path`, so opening `/targets/?q=Amazon` while logged out landed
  on the unfiltered list of every target after logging in. Uses `full_path`
  now. Absolute and protocol-relative `next` values are still rejected.
- `/targets/` now honours a `?q=` parameter by pre-filtering the list, so the
  links above open on the target being discussed.

## [2.5.1] — 2026-08-07

A hotfix release. **v2.5.0 does not start Grafana in the default InfluxDB
mode** — a fresh install comes up with no Grafana at all — so 2.5.0 should not
be deployed. Alongside that, two nightly jobs turned out to have been failing
silently (the IPv6 gate was being undone every night; the OCA refresh had not
completed since 2026-08-03), the MCP integration was found not to be wired
despite reading as healthy, and the Pi was found thermally throttled by its
own steady-state logging and probe rates.

The common thread is instrumentation that reports success without doing the
work — none of these four had a failure signal a person would notice. Each fix
adds one.

### Fixed

- **v2.5.0 will not start Grafana in the default InfluxDB mode** (PR #29). The
  ClickHouse work set `isDefault: true` on the ClickHouse datasource, on the
  reasoning that ClickHouse mode provisions that file alone. InfluxDB mode,
  however, bind-mounts the whole datasources directory read-only, so *both*
  files are provisioned, two datasources claim default, and Grafana refuses to
  start. The `isDefault: false` that was replaced existed for exactly this
  reason. It stayed latent through the release because Grafana had not been
  restarted since the merge. The committed file is `false` again and the
  ClickHouse entrypoint flips it while building its own writable tree.
  **A fresh v2.5.0 install in the default mode comes up with no Grafana; use
  2.5.1.**
- **The IPv6 gate was undone every night** (PR #29). `ipv6_check` held its
  verdict in a module-level variable, but config is generated in more than one
  process: the API regenerates in-process, while the nightly OCA refresh runs
  `oca_fetcher.py` as a *subprocess* that imports `ConfigGenerator` and
  regenerates there too. That subprocess started with an empty cache, hit the
  deliberate unknown-means-allowed rule, and wrote IPv6 targets back in — with
  the generator's log going to captured subprocess output, so nothing appeared
  to have run. The verdict is now persisted to a JSON file with an atomic
  replace and `get_status` falls back to it. Fail-open is kept but scoped
  correctly: unknown now means *nobody has ever checked*, not *this process
  has not checked*.
- **The nightly OCA refresh had been failing** with `UniqueViolation` on
  `targets_name_key` since at least 2026-08-03 (PR #29). The replace deletes
  then re-adds in one transaction, but SQLAlchemy's unit of work emits saves
  before deletes within a flush, so new rows collided with old rows not yet
  deleted — guaranteed whenever the OCA list came back unchanged, which is the
  normal case. An explicit flush after the deletes orders them correctly while
  keeping the single transaction that stops a mid-way failure from emptying
  the category.
- **An agent with shell access answered network questions by running `ping`
  instead of using the MCP server** (PR #29), even with the server registered
  and healthy. Three causes: the OpenClaw skill was never installed (now a
  documented required step, with the exact symptom of skipping it); FastMCP
  was constructed with no `instructions`, so a client saw nine getters with no
  hint that this host holds months of continuous measurement; and the tool
  docstrings read like one-shot getters — `get_latency_stats` even offered
  "how is my connection to 8.8.8.8?" as an example, precisely the question
  being answered with `ping`. Docstrings now state the measurement cadence,
  say to prefer recorded history over a live probe, and carry the two
  looks-broken-but-isn't cases (ICMP-dark hosts, the CPE rate-limit floor).
- **The MCP integration could not be verified, only guessed at** (PR #29). The
  access log showed just `POST /mcp 200`, which cannot distinguish an agent
  invoking a tool from an agent merely connecting — so a plausible-sounding
  answer produced from the shell read as proof the tools were wired. Every
  tool now logs name, compacted args, result shape and duration (args named
  token/password/secret/key redacted, long values truncated), and the
  verification step in `docs/openclaw-integration.md` requires a `tool=` line
  in the server log. The real root cause is documented too: a gateway already
  running when the server is registered never picks it up — the Codex runtime
  fingerprints the server set per thread, so `openclaw mcp reload`, a gateway
  restart and a fresh session are required, while a separate config-reading
  poller keeps `mcp probe`/`mcp doctor` green.

### Changed

- **Steady-state load on the Pi cut substantially** (PR #29), after the host
  was found to have hit its soft temperature limit and frequency-capped
  (`throttled=0xe0000`). Measured 65.9 °C → 60.9 °C.
  - Grafana ran at `debug`, writing ~1,100 log lines every three minutes to an
    SD card. Now `info`, env-overridable. Measured 1151 → 178 lines/3 min.
  - Container logs were unbounded (json-file default, no daemon config). All
    Pro services now rotate at 10 MB × 3 via a compose anchor.
  - Dashboard provisioning re-walked the directory and rebuilt the search
    index every 30 s; now 300 s.
  - The microcut detector never idled — it slept `PROBE_WINDOW - elapsed`,
    which is ~0, so it probed back-to-back forever at 5 pps (~432k
    packets/day) and pressured the gateway's ICMP rate limiter, making part of
    the "constant loss floor" self-inflicted. `CPE_PROBE_IDLE` (default 20 s)
    gives a 1-in-3 duty cycle; measured cadence 10 s → 30 s. Set it to `0` to
    restore the old behavior. `MICROCUT_BURST_N` rescaled 6 → 2 to match,
    since it counts *observed* windows.
  - `rrd2influx` re-fetched all 40 RRDs every 60 s although SmokePing writes
    on a 300 s step, so most cycles spawned 40 `rrdtool` processes for
    nothing. It now skips RRDs whose mtime has not advanced past their last
    export, failing open on a `stat` error so a target can never silently stop
    exporting.

## [2.5.0] — 2026-08-03

Alerting, IPv6 gating, MCP hardening, Grafana 12, and a working ClickHouse
backend (Sprints 10–13).

The alerting engine is the headline, but building it surfaced a set of
measurement bugs that had been quietly corrupting the data it was meant to
watch. Loss was understated 2–4× in InfluxDB and overstated 10× in ClickHouse,
both because the exporters guessed at how many pings a probe sends instead of
reading it from the RRD. Two of the five new alert rules could never have
fired against that data, and a third fired permanently. Everything below was
verified against the live stack or a throwaway copy of it, not just unit
tests.

Upgrade notes:

- **Loss values change scale.** InfluxDB `latency`/`dns_latency` loss written
  before this release is understated (half the true value for ping targets, a
  quarter for DNS); points after it are correct. Grafana panels will show a
  step at the cutover. Nothing rewrites history.
- **IPv6 targets may disappear.** On a host with no global IPv6 they are
  omitted from the generated config rather than charting 100% loss. Database
  rows are untouched and return automatically. `IPV6_MODE=force` keeps the old
  behavior.
- **Grafana jumps three majors** (10.4.2 → 12.4.3). The database migrates in
  place; dashboards need no changes.
- New opt-in profiles: `alerts` (alerting engine) and the existing `mcp`.
  `MCP_API_TOKEN` is optional — unset preserves the current unauthenticated
  transport.


- Fixed: ClickHouse mode never had a schema. `shared/modules/clickhouse/init/`
  is mounted at `/docker-entrypoint-initdb.d`, but Docker seeds a fresh named
  volume from the image and the official ClickHouse image ships a populated
  `/var/lib/clickhouse`, so its entrypoint reports "directory appears to
  contain a database" and skips the init hook entirely — silently. The exporter
  now applies `CREATE DATABASE / TABLE IF NOT EXISTS` on connect, which is
  idempotent and independent of whether the hook fires.
- Fixed: `rrd2clickhouse.py` computed `packet_loss = loss * 100`, but the RRD
  `loss` source is a COUNT of lost pings — a fully lost 10-ping cycle was
  recorded as 1000%. It now divides by the RRD's own `ping1..pingN` count, the
  same denominator `rrd2influx.py` uses. `packets_sent`/`packets_received` were
  derived from that broken percentage and are now taken directly.
- Fixed: the ClickHouse exporter never set `measurement_type` (so DNS probes
  were indistinguishable from ping targets) and took `category` from the
  immediate parent directory rather than the top-level section, using raw
  directory names where the InfluxDB exporter uses a mapped vocabulary. Both
  now share `DNS_DIRS`/`CATEGORY_MAP`, and a test asserts the two exporters
  agree.
- Fixed: the ClickHouse Grafana datasource was configured with a top-level
  `url`, which the official plugin ignores in favour of `host`/`port` in
  `jsonData` — its health check failed with "invalid server host". The
  unavailable `vertamedia-clickhouse-datasource` entry is gone.
- Changed: the ClickHouse datasource plugin is baked into the Grafana image at
  build time instead of downloaded on boot, and staged into the data volume on
  first start, so a Pi that boots before its network is up still gets a working
  datasource.
- Fixed: the ClickHouse dashboards were never provisioned — their provider
  config sits in a directory Grafana does not scan, and the read-only
  `provisioning/` bind mount makes it impossible to add one in place (Docker
  cannot create a mountpoint for a file inside a read-only mount). In
  ClickHouse mode the entrypoint now builds a writable provisioning tree
  containing only the ClickHouse datasource and dashboards.
- Fixed: all eight ClickHouse dashboards targeted the unavailable vertamedia
  plugin, filtered on raw directory names (`category = 'DNS_Resolvers'`) that
  the exporter does not write, and asked for `measurement_type = 'latency'` on
  DNS panels. They now use the official plugin's `rawSql` form and the
  exporter's vocabulary. Fourteen panels also used `target = ${target:sqlstring}`,
  which is a syntax error whenever the variable expands to more than one value
  — reachable via the "All" option — and now use `IN (...)`.
- Changed: ClickHouse is no longer marked experimental/unmaintained in the
  README. All 58 dashboard queries were executed against a real ClickHouse
  instance in a throwaway Compose project; docs in `docs/clickhouse.md`.

- Changed: Grafana 10.4.2 -> 12.4.3. All nine dashboards use only `timeseries`,
  `stat`, and `row` panels, so nothing needed a schema rewrite. Verified by
  running 12.4.3 against a copy of the live database in a scratch Compose
  project before touching the real one: migrations clean, 9/9 dashboards
  provisioned, InfluxDB and PostgreSQL datasources healthy, and an identical
  15-frame result from the same query on both versions. 13.0.2 was tested the
  same way and also worked, but logs a plugin-install error on every boot that
  12.4.3 does not, so the mature line won.
- Fixed: the Grafana entrypoint reset the admin password with `grafana-cli`,
  which Grafana 11 deprecated and 13 removed. On 13 the step failed and was
  swallowed as a passing warning, which would have silently pinned the admin
  password to whatever the database already held — changing
  `GF_SECURITY_ADMIN_PASSWORD` in `.env` would have stopped working with no
  clear signal. It now prefers `grafana cli`, falls back to the old binary for
  older base images, and reports a real error when both fail.

- Added: optional bearer auth on the MCP server's HTTP transport. Setting
  `MCP_API_TOKEN` requires `Authorization: Bearer <token>` on every request;
  unset leaves the transport open exactly as before, and stdio is never gated.
  The tool surface includes mutations and the port is loopback-bound, so this
  gives it a credential of its own, separate from `CONFIG_API_TOKEN` and from
  OpenClaw's gateway token. `/health` stays open for liveness probes.
- Fixed: the alerter's `openclaw` delivery mode targeted `/hooks/agent`, which
  does not exist. A stock OpenClaw gateway (verified on 2026.7.1-2) is
  WebSocket-only and returns 404 for every HTTP path — `openclaw hooks`
  manages internal agent lifecycle hooks, not inbound HTTP. The path is now
  configurable via `OPENCLAW_HOOK_PATH` so it can point at an HTTP-RPC plugin
  or a bridge, and the alerter probes the endpoint at startup and logs an
  explicit error on 404 instead of failing silently on the first incident.
  `webhook` mode remains the portable option.
- Added: `docs/openclaw-integration.md` (MCP registration via `openclaw mcp
  set`, tool filtering, token separation, and what alert delivery actually
  requires) and `examples/openclaw/smokeping-monitoring/SKILL.md`, a
  placeholder-only skill giving an agent the loss conventions and the
  looks-broken-but-is-not cases.

- Added: IPv6 gating. config-manager checks global IPv6 reachability and omits
  `FPing6` targets from the generated `Targets` file when the host cannot reach
  the IPv6 internet, instead of charting a flat 100% loss that reads as an
  outage. The check requires a global-unicast address (ULA and link-local do
  not count — a router's `fd00::/8` prefix or a Tailscale address makes
  `scope global` non-empty on a host with no IPv6 at all), a usable default
  route, and an actual reply from a probe host. It runs inside the SmokePing
  container, whose `network_mode: host` namespace is what its probes see;
  config-manager's own bridge network has no IPv6 and would report a false
  negative. Rechecked every `IPV6_RECHECK_INTERVAL` (900 s) with config
  regenerated only when the verdict flips, so targets return on their own when
  IPv6 comes back. `IPV6_MODE=force|off` overrides; database rows are never
  modified. New `GET /ipv6-status` and `POST /ipv6-status/refresh` endpoints.
  Docs: `docs/ipv6-gating.md`.

- Fixed: the RRD → InfluxDB exporter divided the loss count by a fixed 20
  pings, but SmokePing's probes send 10 (FPing/FPing6) and 5 (DNS), so every
  loss ratio written since 2026-07 was understated — 2× for ping targets, 4×
  for DNS. A fully unreachable target recorded 0.5 instead of 1.0, which made
  the alerter's `target_down` and `ipv6_down` rules (threshold 0.999)
  impossible to trigger and halved the loss shown on every Grafana panel. The
  denominator is now read per RRD from its `ping1..pingN` data sources;
  `SMOKEPING_PINGS` remains only as a fallback. Points written before this fix
  keep their old scale.
- Fixed: alerter `microcut_burst` counted any `cpe_latency` window with loss
  above zero. CPE gateways rate-limit ICMP, so the 5 pps probe sees a constant
  loss floor (measured on the live link: p50 10%, p99 30%, never a fully lost
  window in 24 h) — every window qualified, and the rule fired permanently
  without ever recovering. A window now counts only above `MICROCUT_LOSS_PCT`
  (default 50%), which is what an actual brief cut looks like.
- Changed: alerter `DOWN_WINDOW` default 300 s → 900 s. SmokePing probes on a
  300 s step, so the old window could only ever contain one point and never
  met `target_down`'s 3-point minimum.
- Sprint 10: Alerting engine + OpenClaw delivery.
  - New `shared/modules/alerter/` service: deterministic (no-LLM) rules
    evaluated against InfluxDB every `ALERT_INTERVAL` (60 s) —
    `target_down`, `high_loss`, `microcut_burst`, `exporter_stale`, and an
    aggregate `ipv6_down` — with incident dedup/cooldown/recovery state
    persisted atomically to a named volume.
  - Delivery via `NOTIFY_MODE`: `off` (log-only default), `openclaw`
    (POST `/hooks/agent` on a local OpenClaw gateway), or `webhook`
    (generic JSON POST with optional bearer token); 3 retries with
    exponential backoff. Also forwards ai-insights `report-*.md` files
    (at most one per `REPORT_DELIVERY_INTERVAL`, truncated
    Telegram-friendly).
  - Pro compose: `alerter` service behind the opt-in `alerts` profile
    (`network_mode: host`, `alerter-state` volume, read-only `reports`
    mount); `.env.template` gains an all-empty alerting block; CI
    validates the `alerts` profile. Docs: `docs/alerting.md`.

## [2.4.0] — 2026-07-31

AI insights & in-UI assistant (Sprint 9) — final sprint of the 2026-07
modernization plan.

- Sprint 9: AI insights & in-UI assistant.
  - New `shared/modules/ai-insights/` service: pulls latency/loss/microcut
    aggregates from InfluxDB and writes plain-language Markdown health
    reports via Claude (Haiku by default; `AI_MODEL` overridable). Guardrails:
    input-size cap, `AI_REPORTS_PER_DAY` cap, clean no-op when
    `ANTHROPIC_API_KEY` is unset. Compose snippet documented in
    `docs/ai-insights.md` (compose/editions files owned by an in-flight PR).
  - web-admin: new AI section — `/ai/reports` renders the generated reports
    (tiny built-in safe Markdown renderer, empty-state help when disabled)
    and `/ai/chat` is an assistant that reuses the MCP tool surface
    (stats read tools run inline; add/remove/toggle target require an
    explicit confirmation card before executing). No `apply_config` tool —
    regeneration is automatic.
- Pro compose: `ai-insights` service behind the opt-in `ai` profile with
  a shared `reports` volume (PR #21); `.env.template` gains
  ANTHROPIC_API_KEY and AI tuning knobs (PR #21). Enable with
  COMPOSE_PROFILES=influxdb,ai.

## [2.3.0] — 2026-07-31

Editions repair, AI-operability, and three production fixes found while
deploying (Sprints 7–8).

### Fixed (post-deploy)
- **OCA refresh data loss** (PR #19): the daily Netflix OCA refresh could
  replace the `netflix_oca` targets with an empty set when the fetch
  failed (this happened in production while the locator install was
  broken). Empty fetches are now refused and the DB replace is a single
  transaction. Golden tests moved to immutable fixtures so live runtime
  state can never fail CI (PR #19).
- **Slow container boot** (PR #18): the exporter cont-init script blocked
  s6 service startup (~5 min to web UI after recreate); loops are now
  detached and the sleeps removed — boot-to-web measured at 9 s.

- Sprint 8 (PR #13): MCP server module — manage targets and query latency
  data conversationally from Claude Code / Claude Desktop.
- Sprint 7: editions repair & compose unification.
  - Pro: generated `Targets`/`Probes` now reach SmokePing via a read-only
    directory mount (`/config/generated`) plus a `05-link-generated-config.sh`
    cont-init symlink script, replacing single-file bind mounts that went
    stale after config-manager's atomic (inode-swapping) writes.
  - Standard: fixed broken data path (config-manager's output volume never
    reached SmokePing) using the same shared-volume + symlink pattern; added
    a persistent `/app/config` bind mount, `COMPOSE_PROJECT_NAME` /
    `CONFIG_API_TOKEN` / `CONFIG_DIR` / `OUTPUT_DIR` env wiring, and
    `CONFIG_API_TOKEN` pass-through to web-admin.
  - Exporter dependencies (python3, rrdtool, traceroute, influxdb-client,
    PyYAML) baked into the smokeping image at build time; cont-init/
    entrypoint installs now only run as a fallback (no more apk/pip on
    every boot).
  - config-manager image: real multi-stage build — production stage copies
    the venv and oca-locator checkout from the builder instead of
    re-installing git/docker.io and re-cloning (~300 MB smaller);
    `netflix_oca_locator` is now installed into the venv the app actually
    runs from. Docker packages dropped entirely (only the Python docker
    SDK is used).
  - Pinned floating images: `linuxserver/smokeping:version-2.9.0-r0`,
    `cloudflare/cloudflared:2026.5.0`; removed obsolete compose `version:`
    key; `PUID`/`PGID` are now `${PUID:-1000}`-style overridable in all
    editions; InfluxDB gained a compose healthcheck.
  - Pro: `mcp-server` service wired in behind the `mcp` profile
    (127.0.0.1:8090); CI now validates `COMPOSE_PROFILES=mcp`.
  - `setup.sh` scripts use `docker compose` (v2) instead of `docker-compose`.

## [2.2.0] — 2026-07-30

Web-admin hardening and UX overhaul (Sprints 4–5).

### Security (PR #14)
- Password-hash auth (`WEB_ADMIN_PASSWORD_HASH`) with constant-time
  plaintext fallback; no hardcoded default credentials; `SECRET_KEY`
  required; CSRF protection on every state-changing request; per-IP
  login lockout; open-redirect fix; Cloudflare token out of URL query.
- Single authenticated data path: web-admin only talks to the
  config-manager API (Bearer token); YAML fallback dual-writes and the
  silently-broken dashboard delete removed; Docker socket removed from
  web-admin entirely; scheduler runs exactly once.
- API token auth active end-to-end on the Pro deployment.

### UX (PR #16)
- Bootstrap + icons vendored locally — the UI works during the exact
  internet outages it exists to diagnose.
- Toast/confirm/fetch helpers replace all alert()/confirm() dialogs.
- Targets: search, category/status filters, inactive targets visible
  with activate toggle, edit dialog, bulk delete, CSV export.
- Add-target: identical client/server name validation, inline errors,
  IPv6-aware hostname checks, auto-regeneration feedback.
- Dashboard: full target lists with per-target SmokePing and Grafana
  links; shows Database/YAML mode.
- Top Sites: loading-modal deadlock fixed, honest selection counters,
  review-before-update, real CrUX per-country lists; broken countries
  page removed.

## [2.1.0] — 2026-07-29

Modernization phase 1 (Sprints 1–3, 6 + CI). PostgreSQL becomes the real
source of truth and the Grafana/exporter pipeline is made trustworthy.

### Added
- **CPE microcut detection** (PR #7): hourly traceroute discovery of the
  ISP CPE (IPv4+IPv6), 5 pps windowed probing, `cpe_latency` measurement
  in InfluxDB, and a dedicated Grafana dashboard.
- **CI pipeline** (PRs #8, #9): ruff critical rules, per-edition
  `docker compose config` validation (incl. ClickHouse overlay), shell
  syntax checks, Grafana dashboard/provisioning validation with
  duplicate-uid detection, auto-discovered per-module pytest suites, and
  Docker image build checks.
- **API auth** (PR #10): optional shared-token auth on the config-manager
  API (`CONFIG_API_TOKEN`, Bearer / X-API-Token).
- First real test suites: config-manager (32), smokeping-exporters (30).

### Changed
- **PostgreSQL activated as the configuration source of truth** (PR #10):
  idempotent YAML→DB migration runs at startup; YAML becomes
  import/export. Config generation is atomic, file-locked, in-process
  (the subprocess fork-chain "zombie generator" is gone), and fully
  data-driven (hardcoded big-name template blocks removed). Gunicorn
  replaces the Flask dev server. FPing6 seeded (fixes IPv6 target 500s).
- **Grafana overhaul** (PR #11): single dashboard provider
  (`foldersFromFilesStructure`), dashboards organized into folders,
  entrypoint rewritten with proper signal handling (`exec`), Dockerfile
  HEALTHCHECK, dead `$status` variable and user-specific exclusions
  removed, CPE microcut dashboard finished (target variable, thresholds,
  proper panel options).
- **Exporter rewrite** (PR #11): `rrd2influx.py` now fetches incrementally
  since the last exported timestamp with downtime backfill (≤24h) and
  writes **loss as a ratio 0–1** (was a raw lost-ping count that rendered
  1 lost ping as 100%). Historical points keep the old scale.

### Fixed
- **6-week outage root cause** (PR #7): no `restart:` policy on any core
  Pro service — a reboot left the whole stack down. All services now
  `restart: unless-stopped`.
- **All 8 InfluxDB dashboards rendered empty** (PR #7): queries targeted
  bucket `latency`; the deployment writes to `smokeping` (67 refs fixed).
- Atomic writes preserved root ownership on host bind mounts, breaking
  git and host tooling (PR #12); DB reads had no `order_by`, making
  generated config nondeterministic (PR #12).
- Env template drift (PR #8): `TIMEZONE`→`TZ` (timezone config was a
  silent no-op in basic/standard), wrong `POSTGRES_DB` in standard.

### Removed
- ~2,300 lines of committed runtime artifacts (PR #8): YAML backups,
  `cookies.txt`, OCA dumps, orphaned modules (`api_docs.py`, stub
  compose file, broken test scripts). Backup retention added so they
  cannot accumulate again.

### Deprecated
- ClickHouse support marked experimental/unmaintained (parked; known
  datasource-plugin and schema mismatches documented in README).

## [2.0.0] — historical

Pre-modernization state: three Docker editions (Basic/Standard/Pro),
config-manager + web-admin + Grafana/InfluxDB stack as of PR #6.
