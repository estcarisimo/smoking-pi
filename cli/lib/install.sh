# shellcheck shell=bash
# smoking-pi install: `install`, and `link` (a clone's command on the PATH).
# Sourced by cli/smoking-pi, never run on its own.

cli_name() {
    if [ "${SMOKING_PI_PACKAGED:-0}" = 1 ]; then echo "sudo smoking-pi"; else echo smoking-pi; fi
}

# Each optional Pro service needs one more thing from the person installing:
# where alerts go, an API key, a router setting. Interactive, it is asked
# now, through the command that owns it and its checks; with --yes (or no
# terminal), the command is named instead. What is left goes to stdout,
# one line each, for the caller's "Still to do"; everything else, prompts
# included, goes to stderr, so the caller's $( ) captures only that list.
# (An install run with 2>file therefore asks in that file: run it plainly.)
install_followups() {
    local profiles="$1" yes="$2" interactive=0 cli
    cli="$(cli_name)"
    [ "$yes" = 0 ] && [ -t 0 ] && interactive=1
    if [[ ",$profiles," == *,alerts,* ]]; then
        if [ "$interactive" = 1 ]; then
            echo >&2
            cmd_alerts >&2 || echo "  alerts: $cli alerts   (until then they are evaluated and only logged)"
        else
            echo "  alerts: $cli alerts   (until then they are evaluated and only logged)"
        fi
    fi
    if [[ ",$profiles," == *,ai,* ]] && [ -z "$(env_get ANTHROPIC_API_KEY)" ]; then
        local a=n
        if [ "$interactive" = 1 ]; then
            echo >&2
            echo "AI reports need an Anthropic API key (console.anthropic.com, API keys)." >&2
            # End of input is a no, not the default yes.
            read -r -p "Enter it now? It is typed, not shown. [Y/n] " a && a="${a:-y}" || a=n
        fi
        case "$a" in
            y|Y|yes) cmd_config set ANTHROPIC_API_KEY >&2 \
                         || echo "  AI reports: $cli config set ANTHROPIC_API_KEY" ;;
            *) echo "  AI reports: $cli config set ANTHROPIC_API_KEY   (until then no report is written)" ;;
        esac
    fi
    if [[ ",$profiles," == *,dns,* ]]; then
        # No question to ask: enable waits until the observer answers and
        # prints the router setting, which only the person can change.
        echo >&2
        if cmd_dns enable >&2; then
            echo "  DNS observer: set the router's DNS as shown above, then $cli dns test"
        else
            echo "  DNS observer: $cli dns status   (it did not answer yet)"
        fi
    fi
}

# A package install is started at boot by its unit. Enabled here, not left
# as a step of the guide: nobody wants an install that is gone after a
# reboot. Started without waiting (--no-block): its ExecStart is
# `smoking-pi up`, which takes the stack lock this install holds until it
# exits -- `enable --now` would wait for a unit that waits for us.
# --enable-only: at boot, not now (a restore told --no-start).
enable_unit() {
    [ "${SMOKING_PI_PACKAGED:-0}" = 1 ] || return 0
    command -v systemctl >/dev/null 2>&1 || return 0
    systemctl cat smoking-pi >/dev/null 2>&1 || return 0
    if ! systemctl enable smoking-pi >/dev/null 2>&1; then
        echo "Could not enable the smoking-pi service. To start it at boot:" >&2
        echo "  sudo systemctl enable --now smoking-pi" >&2
    elif [ "${1:-}" = --enable-only ]; then
        echo "Starts at boot: the smoking-pi service is enabled."
    elif ! systemctl start --no-block smoking-pi >/dev/null 2>&1; then
        echo "Starts at boot: the smoking-pi service is enabled (systemd did not start it now;"
        echo "the stack is running without it)."
    else
        echo "Starts at boot: the smoking-pi service is enabled."
    fi
}

cmd_install() {
    local edition="$SMOKING_PI_EDITION" database=influxdb profiles="" yes=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --edition) edition="$2"; shift 2 ;;
            --database) database="$2"; shift 2 ;;
            --profiles) profiles="$2"; shift 2 ;;
            --yes|-y) yes=1; shift ;;
            *) echo "unknown option $1" >&2; exit 2 ;;
        esac
    done
    case "$edition" in basic|standard|pro) ;; *) echo "unknown edition: $edition" >&2; exit 2 ;; esac
    case "$database" in influxdb|clickhouse) ;; *) echo "unknown database: $database" >&2; exit 2 ;; esac
    local pr
    for pr in ${profiles//,/ }; do
        case "$pr" in mcp|alerts|ai|dns) ;; *) echo "unknown profile: $pr (mcp, alerts, ai, dns)" >&2; exit 2 ;; esac
    done
    if [ "$yes" = 0 ] && command -v whiptail >/dev/null; then
        # Pro preselected: the guide recommends it, and Enter should agree.
        edition=$(whiptail --title "Smoking Pi" --default-item pro --menu "Which edition?" 15 70 3 \
            basic "SmokePing only" standard "+ web admin and PostgreSQL" \
            pro "+ Grafana, InfluxDB, alerts, MCP (recommended)" 3>&1 1>&2 2>&3) || exit 1
        if [ "$edition" = pro ]; then
            database=$(whiptail --title "Smoking Pi" --menu "Time-series backend?" 12 60 2 \
                influxdb "InfluxDB 2 (recommended)" clickhouse "ClickHouse (experimental)" 3>&1 1>&2 2>&3) || exit 1
            # Optional services are Compose profiles; the choice is written
            # into the env file so a plain `up` keeps it (setup.sh explains).
            # Each one chosen is set up after the stack starts, by the
            # command that owns it (install_followups): no env file to edit.
            profiles=$(whiptail --title "Smoking Pi" --checklist \
                "Optional services. Space toggles; each one you pick is set up after the stack starts." 16 78 4 \
                mcp "Assistant: a chat assistant can read your history" OFF \
                alerts "Alerts: outages, microcuts, Wi-Fi (asks where to send)" ON \
                ai "AI reports: written summaries (asks for an API key)" OFF \
                dns "DNS observer: what this house uses (router change)" OFF 3>&1 1>&2 2>&3) || exit 1
            profiles=$(echo "$profiles" | tr -d '"' | tr ' ' ',')
        fi
    fi
    if [ "$edition" != pro ] && [ -n "$profiles" ]; then
        echo "note: --profiles applies to the pro edition only; ignored for $edition" >&2; profiles=""
    fi
    SMOKING_PI_EDITION="$edition"; EDITION_DIR="$SMOKING_PI_HOME/editions/$edition"
    # An explicit SMOKING_PI_ENV_FILE wins; otherwise the env file follows
    # the edition chosen here, not the default edition computed at the top.
    [ -n "${SMOKING_PI_ENV_FILE_SET:-}" ] || ENV_FILE="$EDITION_DIR/.env"
    export SMOKING_PI_ENV_FILE="$ENV_FILE"
    need_edition
    if [ -f "$ENV_FILE" ]; then
        # setup.sh regenerates every secret. Against volumes that already
        # hold the old PostgreSQL/InfluxDB credentials that is a broken
        # stack, not a reinstall -- so an existing install is never re-run
        # by accident. (Backlog #4 in docs/packaging.md gives upgrade its
        # own command.)
        echo "$ENV_FILE exists: this edition is already installed." >&2
        echo "Use 'smoking-pi up' to start it. To start over, stop the stack, remove its" >&2
        echo "volumes (docker volume ls | grep ${edition}_), delete the env file, then run install again." >&2
        exit 1
    fi
    echo "Installing the $edition edition from $SMOKING_PI_HOME (env file: $ENV_FILE)"
    # Packaged: the unit and every later command take the edition from the
    # state file beside the env file (see EDITION_FILE at the top). Without
    # it a packaged Basic install was started as Pro by `systemctl start
    # smoking-pi`, against Basic's env file. A clone needs none: its env
    # file lives beside the edition it belongs to.
    if [ "${SMOKING_PI_PACKAGED:-}" = 1 ] && [ -n "$EDITION_FILE" ]; then
        mkdir -p "$(dirname "$EDITION_FILE")"
        printf '%s\n' "$edition" > "$EDITION_FILE"
    fi
    if [ "$edition" = pro ]; then
        ( cd "$EDITION_DIR" && SMOKING_PI_INSTALL=1 ./setup.sh --database "$database" --env-file "$ENV_FILE" )
        if [ -n "$profiles" ]; then
            # setup.sh records the backend profile; append the optional ones
            # and start them. `ai` and `alerts` need keys the env file does
            # not have yet. Neither crash-loops without one -- ai-insights
            # logs and exits 0, the alerter's NOTIFY_MODE defaults to `off`
            # -- which is worse to leave unsaid, not better: the container
            # stays up and healthy while nothing is ever delivered. So
            # install_followups asks for them below, or lists them.
            sed -i "s/^COMPOSE_PROFILES=.*/COMPOSE_PROFILES=$database,$profiles/" "$ENV_FILE"
            compose up -d
        fi
    else
        ( cd "$EDITION_DIR" && SMOKING_PI_INSTALL=1 ./setup.sh )
    fi
    # The assistant is optional and stays optional: the stack is already
    # measuring by this point, so every branch here ends with a working
    # install. Three answers, because "not installed" and "installed on my
    # laptop" need opposite advice and the old yes/no could not tell them
    # apart -- it printed a doc path either way.
    if [ "$edition" = pro ] && [ "$yes" = 0 ] && command -v whiptail >/dev/null; then
        local answer
        answer=$(whiptail --title "Smoking Pi" --menu \
            "Connect a chat assistant? (optional)\n\nOpenClaw can answer \"how is my internet?\" from this Pi's recorded history instead of a fresh speed test. Smoking Pi is fully working without it." 16 74 3 \
            here   "OpenClaw runs on this Pi - set it up now" \
            remote "OpenClaw runs on another machine" \
            later  "Not now" 3>&1 1>&2 2>&3) || answer=later
        echo
        case "$answer" in
            here)   cmd_openclaw || true ;;
            remote) echo "OpenClaw on another machine needs a tunnel between the two loopbacks"
                    echo "before anything else works:"
                    echo "  $SMOKING_PI_HOME/docs/remote-openclaw.md"
                    echo "Then run 'smoking-pi openclaw' on the machine the gateway is on." ;;
            *)      echo "Skipping the assistant. When you want it: '$(cli_name) openclaw'." ;;
        esac
    elif [ "$edition" = pro ] && [ "$yes" = 1 ]; then
        echo; echo "To connect a chat assistant later: '$(cli_name) openclaw'."
    fi
    local todo=""
    [ "$edition" != pro ] || todo="$(install_followups "$profiles" "$yes")"
    enable_unit
    # Short on purpose: the full banner (`passwords`) is a reference, and
    # at the end of an install it buried the one line that matters, the
    # address, under a hundred lines of troubleshooting.
    echo
    echo "Installed: the $edition edition. The first measurements arrive after one"
    echo "300-second step; then '$(cli_name) doctor --live' checks that they do."
    if [ -n "$todo" ]; then
        echo "Still to do:"
        printf '%s\n' "$todo"
    fi
    # From a clone, the command those lines name must exist by name
    # (setup.sh links it too; this one is for whoever skipped it).
    link_cli --quiet
    publish_avahi
    echo
    # Last, so it is what is on screen when the install finishes. An install
    # whose web page is still starting is not a failed install.
    cmd_url --wait 120 || true
}

link_cli() {
    # A clone's command on PATH, so it is `smoking-pi passwords` from any
    # directory rather than ~/smoking-pi/cli/smoking-pi. The .deb
    # installs /usr/bin/smoking-pi and Homebrew its own wrapper; this is
    # only for a checkout. A symlink, not a copy: the command finds its home
    # through readlink -f, and an upgrade of the checkout upgrades it.
    # /usr/local/bin is on every PATH (sudo's secure_path included) but
    # needs root; ~/.local/bin does not, and Debian's ~/.profile adds it at
    # login. --quiet: only say something when it changed or could not.
    local quiet=0 self found dir target
    [ "${1:-}" = --quiet ] && quiet=1
    self="$SMOKING_PI_HOME/cli/smoking-pi"
    if [ ! -e "$SMOKING_PI_HOME/.git" ] || [ ! -x "$self" ] || [ "${SMOKING_PI_PACKAGED:-0}" = 1 ]; then
        [ "$quiet" = 1 ] || echo "$SMOKING_PI_HOME is not a clone: its package put smoking-pi on the PATH."
        return 0
    fi
    found="$(command -v smoking-pi 2>/dev/null || true)"
    if [ -n "$found" ] && [ "$(readlink -f "$found")" = "$(readlink -f "$self")" ]; then
        [ "$quiet" = 1 ] || echo "smoking-pi is a command already: $found -> $self"
        return 0
    fi
    # Another smoking-pi that is not a link to a checkout is the package's
    # (or Homebrew's): shadowing it from /usr/local/bin would run this
    # checkout's code against the package's state. Said even under
    # --quiet: it is a "could not", and `smoking-pi` there is not this one.
    # A link to */packaging/smoking-pi is a checkout's from before the
    # command moved to cli/ (v2.28.0): repointed here, so the forwarding
    # file there can go one day.
    if [ -n "$found" ] && ! { [ -L "$found" ] && [[ "$(readlink "$found")" == */cli/smoking-pi || "$(readlink "$found")" == */packaging/smoking-pi ]]; }; then
        echo "Not linking: 'smoking-pi' is already $found (installed by a package). This checkout runs as $self."
        return 0
    fi
    for dir in ${SMOKING_PI_BIN_DIRS:-/usr/local/bin $HOME/.local/bin}; do
        target="$dir/smoking-pi"
        # Never replace a real file there, only a link a checkout made.
        if [ -e "$target" ] && [ ! -L "$target" ]; then continue; fi
        # Ours to write, else root's through a sudo that asks no password
        # (setup.sh may run unattended); a prompt would be worse than
        # ~/.local/bin.
        if { mkdir -p "$dir" 2>/dev/null && [ -w "$dir" ] && ln -sfn "$self" "$target" 2>/dev/null; } \
            || { [ -d "$dir" ] && sudo -n ln -sfn "$self" "$target" 2>/dev/null; }; then
            echo "smoking-pi is now a command: $target -> $self"
            if [ "$(command -v smoking-pi 2>/dev/null)" != "$target" ]; then
                case ":$PATH:" in
                    *":$dir:"*) ;;
                    *) echo "  $dir is not on this shell's PATH yet: open a new terminal (or log in again)." ;;
                esac
            fi
            return 0
        fi
    done
    echo "Could not link smoking-pi into ${SMOKING_PI_BIN_DIRS:-/usr/local/bin or ~/.local/bin}; run it as $self," >&2
    echo "or: sudo ln -s $self /usr/local/bin/smoking-pi" >&2
    return 0
}
