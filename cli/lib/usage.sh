# shellcheck shell=bash
# smoking-pi usage: --help (the reference) and `smoking-pi` alone (the brief).
# Sourced by cli/smoking-pi, never run on its own.

usage() {
    cat <<'USAGE'
smoking-pi — SmokePing on a Raspberry Pi, with the trimmings

Usage: smoking-pi <command> [args]   (no command: what is installed and running)

  install [--edition basic|standard|pro] [--database influxdb|clickhouse]
          [--profiles mcp,alerts,ai,dns] [--yes]
                     First install only: generate secrets, choose the edition,
                     backend and optional profiles, start the stack, print what
                     to open. Interactive unless --yes. Refuses to run over an
                     existing env file (it would rotate the secrets the data
                     volumes already hold).
  up | down | restart | status | logs [service]
                     The Compose stack, with the profiles recorded in the env file.
  upgrade [--skip-doctor]
                     After a new package or checkout: pull the release's images
                     (or rebuild, from a clone), recreate what changed, run the
                     doctor. Read docs/upgrades.md first for a PostgreSQL or
                     InfluxDB major; 'backup' before one.
  backup [DIR] [--online]
                     pg_dumpall, then every volume the stack mounts as a
                     tarball with the stack stopped (--online: keep it running;
                     the tarballs may be inconsistent), plus the env file and
                     config directory. Default DIR: ./smoking-pi-backup-<timestamp>.
  restore DIR [--force] [--no-start] [--yes]
                     Put back the env file and config (--force: overwrite ones
                     that exist); then, after you type the project name, stop
                     the stack, replace each volume's contents from DIR's
                     tarballs and start it (--no-start: leave it stopped to
                     inspect first).
  purge [--config] [--yes]
                     Stop the stack and delete its Docker volumes -- a year of
                     measurements -- after you type the project name.
                     --config also deletes the env file, config and output
                     directories (what 'install' needs gone to start over).
  passwords [--show-secrets [--force]]
                     Show URLs, usernames and health checks. Secret VALUES
                     are hidden unless --show-secrets; that refuses a pipe
                     or a file unless --force, so they stay out of logs.
  url [--wait SECONDS]
                     The address to open in a browser -- over SSH, the one your
                     computer reaches -- the username, and whether it answers
                     (--wait: keep trying that long first). Exits 1 if not.
  config list | get KEY [--show-secrets] | set KEY [VALUE] [--no-apply] | unset KEY
                     Settings in the env file, by name: only keys the edition's
                     .env.template declares. 'set' recreates just the services
                     whose compose entry reads the key (--no-apply: only write).
                     A secret (TOKEN, PASSWORD, SECRET, *_KEY) is never taken
                     from the command line: it is asked for, or read from stdin.
                     Credentials install generated are refused: the data
                     volumes hold them.
  links [--lan auto|mdns|ADDRESS] [--tunnel https://HOST] [--off]
                     Where the links in alerts and assistant answers point
                     (Pro). No option: show it. --lan auto: this machine's
                     address on the local network. --lan mdns: the .local
                     name the mdns service holds. --tunnel: the address that
                     works from anywhere (a Cloudflare tunnel), scheme included.
  alerts [--telegram [--to CHAT_ID] | --openclaw [--to RECIPIENT] | --webhook
         | --off] [--test] [--yes] [--digest HH:MM|off [--digest-tz ZONE]]
                     Where alerts go (Pro): set NOTIFY_MODE and its keys, turn
                     on the alerts profile, recreate the alerter and print its
                     own delivery preflight. --telegram: a bot of your own,
                     no OpenClaw; asks for its token (hidden, or on stdin)
                     and finds the chat id from a message you send the bot.
                     --test (or answering yes) sends one labeled test
                     message: the only proof one arrives.
                     --digest: a daily summary at HH:MM (in ZONE, else TZ),
                     alone or with the rest.
  connect --tailscale [--off] [--yes]
                     Publish the MCP server for remote assistants through
                     Tailscale Funnel (Pro): installs Tailscale if asked, signs
                     in (printing the link), keeps Tailscale off this Pi's DNS,
                     turns Funnel on, sets MCP_PUBLIC_URL to the real ts.net
                     name and checks it from outside. --off undoes it.
  connect [NAME [--as KIND] [--check] | --list]
                     Connect an assistant (Pro). With no NAME: what is
                     connected and the URL to give one. 'connect openclaw':
                     an OpenClaw gateway on this machine (as 'openclaw'
                     below). Any other NAME: a remote assistant that can add
                     a remote MCP server; prints the URL, a one-time pairing
                     code, and the steps for that assistant (claude,
                     claude-code, chatgpt, cursor, grok, or a name that
                     starts with one, like claude-work; --as KIND for any
                     other name; --list shows them). It gets read-only
                     tools. Needs MCP_PUBLIC_URL and a tunnel: without one,
                     it offers 'connect --tailscale' (docs/remote-connector.md).
                     --check: whether NAME has called a tool since it signed
                     in, the only proof it uses your measurements.
  disconnect NAME    Sign a remote assistant out; the others keep working.
  openclaw [--check]
                     Connect this stack to an OpenClaw gateway on this machine:
                     the MCP token, the mcp profile, registration and the skill.
                     --check asks the agent a question and reads the MCP
                     server's log, which is the only thing that proves the
                     agent is using your history instead of its own shell.
                     Pro only; OpenClaw elsewhere: docs/remote-openclaw.md.
  budget [--json]
                     What the configured measurements cost (Standard, Pro):
                     samples per hour and approximate MB per day per probe,
                     most expensive first, against MEASUREMENT_BUDGET_MB_PER_DAY
                     and MEASUREMENT_BUDGET_SAMPLES_PER_HOUR. Accounting only:
                     nothing is throttled. See docs/measurement-budget.md.
  traffic [--json]
                     What the Pi sent and received (Pro): today, yesterday,
                     this week, this month, last month and the last 30 days,
                     on the uplink interface and to the Internet only, with
                     how much of each period was measured and this month's
                     traffic by service. See docs/measurement-budget.md.
  dns [status [--json] | test [--via ROUTER] [--json] | adopt [--dry-run] [--force] | enable [--yes] | disable [--yes]]
                     The DNS observer (Pro): AdGuard Home on port 53, for the
                     router to forward the house's DNS to. 'enable' turns on
                     the dns profile, starts it and says what to set on the
                     router; 'status' says whether it is receiving queries and,
                     if not, why (router reverted, never configured, down).
                     'test' checks the whole path in seconds, right after
                     changing the router: the Pi answers on the LAN, resolves
                     upstream, and the router forwards unique test names here.
                     'adopt' measures what the DNS wizard selected (the
                     services behind 80% of the house's activity, plus one
                     per network and CDN): ICMP, TCP 443, HTTP/1.1, /2, /3,
                     in the dns_wizard category. Adds services; tries each
                     new layer first and skips one the host does not
                     answer, and deactivates layers silent for a day.
                     --dry-run lists, and says whether it fits the
                     measurement budget; over it, adopting is refused unless
                     --force. --retire-only only deactivates.
                     'disable' asks you to point the router back first.
                     docs/dns-observer.md.
  discover           Every Smoking Pi on this network and where to open it, from
                     their DNS-SD announcements (needs avahi-browse). install,
                     up and upgrade announce this one when Avahi runs here;
                     down withdraws it.
  doctor [--live]    Instrumentation doctor (needs the doctor package).
  link               From a clone: make 'smoking-pi' a command in every directory
                     (a link in /usr/local/bin, else ~/.local/bin). setup.sh and
                     'upgrade' do it already; the package and Homebrew install
                     their own.
  paths              Where things are (home, edition, env file, config, output, images, data).
  version            Installed version.

Environment: SMOKING_PI_HOME (default: this checkout or /opt/smoking-pi),
             SMOKING_PI_EDITION (default: pro),
             SMOKING_PI_PACKAGED=1 adds docker-compose.packaged.yml (no source
             bind-mounts; the package sets it),
             SMOKING_PI_ENV_FILE, SMOKING_PI_CONFIG_DIR, SMOKING_PI_OUTPUT_DIR
             (default: beside the edition's compose file; the package sets
             /etc/smoking-pi/env, /etc/smoking-pi/config, /var/lib/smoking-pi/output).
             SMOKING_PI_VERSION pulls the published images of that release
             instead of building (packaged: the installed version unless
             set; a clone: the release tag its checkout is on, else `dev`,
             never published, so it builds; `dev` forces a build),
             SMOKING_PI_REGISTRY to pull a fork's images.
USAGE
}

brief() {
    # `smoking-pi` alone: what is here and the handful of commands a person
    # actually needs, not the full reference (that is --help).
    local v running total=0
    v="$(installed_version)"
    echo "smoking-pi ${v:-unknown} -- SmokePing network monitoring"
    echo
    if [ ! -d "$EDITION_DIR" ]; then
        echo "No $SMOKING_PI_EDITION edition at $EDITION_DIR."
    elif env_locked; then
        # Packaged: /etc/smoking-pi is root's, secrets included, and the
        # edition file with it, so the edition is not named here.
        # Nor can this user tell a set-up install from a package that was
        # never set up: the edition file is in there too. Name both.
        echo "The package is installed. Its settings are in $(dirname "$ENV_FILE"), which only root can read."
        echo "Run it with sudo to see its state: sudo smoking-pi"
        echo "Not set up yet? sudo smoking-pi install"
    elif [ ! -f "$ENV_FILE" ]; then
        echo "Not installed on this machine yet. Start with:"
        echo "  smoking-pi install        choose the edition, then start it"
    else
        # `config` needs no daemon, `ps` does: only a ps that answered counts.
        if running="$(compose ps --status running --services 2>/dev/null)"; then
            running="$(printf '%s' "$running" | grep -c . || true)"
            total="$(compose config --services 2>/dev/null | grep -c . || true)"
            echo "Edition:  $SMOKING_PI_EDITION, $running of $total services running"
        else
            echo "Edition:  $SMOKING_PI_EDITION (Docker did not answer: is it running, and may you use it?)"
        fi
        case "$SMOKING_PI_EDITION" in
            basic) echo "Open:     http://$(host_address):$(env_value SMOKEPING_PORT | grep . || echo 8080)/" ;;
            standard) echo "Open:     http://$(host_address):$(env_value WEB_ADMIN_PORT | grep . || echo 8080)/" ;;
            *) echo "Open:     http://$(host_address):8080/   (Grafana on :3000)" ;;
        esac
    fi
    cat <<'COMMON'

Common commands:
  smoking-pi status                   the containers and their state
  smoking-pi url                      what to open in a browser, and whether it answers
  smoking-pi passwords                usernames and health checks (--show-secrets: the values)
  smoking-pi doctor --live            is it measuring?
  smoking-pi logs [service]           follow the logs
  smoking-pi upgrade                  after a new release
  smoking-pi backup                   everything, to a directory

Every command and option: smoking-pi --help
COMMON
    # Run by its path from a clone that is not on the PATH yet.
    if [ -e "$SMOKING_PI_HOME/.git" ] && [ "${SMOKING_PI_PACKAGED:-0}" != 1 ] \
        && [ "$(readlink -f "$(command -v smoking-pi 2>/dev/null || echo /nonexistent)")" != "$(readlink -f "$SMOKING_PI_HOME/cli/smoking-pi")" ]; then
        echo
        echo "Tip: 'smoking-pi' is not a command in every directory yet: $SMOKING_PI_HOME/cli/smoking-pi link"
    fi
}
