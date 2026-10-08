# shellcheck shell=bash
# smoking-pi openclaw: `openclaw`, the OpenClaw on this machine.
# Sourced by cli/smoking-pi, never run on its own.

# docs/openclaw-integration.md is six steps, each with a trap that makes a
# broken integration look like a working one. This runs the mechanical
# ones and ends on the only check that tells the two apart.

# OpenClaw belongs to a user, not to root: its config is ~/.openclaw, its
# gateway is usually a `systemctl --user` unit, and nvm installs the
# command under ~/.nvm, which sudo's secure_path drops. A packaged install
# needs sudo to read the env file, so under sudo every openclaw step runs
# as the user who ran sudo. Run as root, `openclaw mcp set` would have
# registered the server in /root/.openclaw, which no gateway reads.
openclaw_user() {
    [ "$(id -u)" = 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ] \
        && echo "$SUDO_USER" || true
}

# The openclaw command that user runs: PATH first, then nvm's default
# version, then any nvm version (newest first), then the usual per-user
# bin directories. Empty if there is none.
openclaw_bin() {
    local user home alias f
    command -v openclaw 2>/dev/null && return 0
    user="$(openclaw_user)"
    home="$HOME"
    [ -n "$user" ] && home="$(getent passwd "$user" 2>/dev/null | cut -d: -f6)"
    [ -n "$home" ] || return 0
    alias="$(cat "$home/.nvm/alias/default" 2>/dev/null || true)"
    # `24` means v24.*, never v240; `24.18.0` means exactly that one.
    case "$alias" in
        v[0-9]*|[0-9]*)
            f="$(ls -d "$home/.nvm/versions/node/v${alias#v}"{,.*}/bin/openclaw 2>/dev/null | sort -V | tail -1 || true)"
            [ -n "$f" ] && { echo "$f"; return 0; } ;;
    esac
    f="$(ls -d "$home"/.nvm/versions/node/*/bin/openclaw 2>/dev/null | sort -V | tail -1 || true)"
    [ -n "$f" ] && { echo "$f"; return 0; }
    for f in "$home/.local/bin/openclaw" "$home/.npm-global/bin/openclaw" \
             "$home/.volta/bin/openclaw" "$home/.bun/bin/openclaw"; do
        [ -x "$f" ] && { echo "$f"; return 0; }
    done
    return 0
}

# Run "$@" as the OpenClaw user, with the directory of $OPENCLAW_BIN first
# on PATH (nvm keeps `node` beside it) and that user's session bus, so
# `systemctl --user restart openclaw-gateway` reaches their gateway.
as_openclaw_user() {
    local user uid path
    user="$(openclaw_user)"
    path="$(dirname "$OPENCLAW_BIN"):$PATH"
    if [ -z "$user" ]; then
        PATH="$path" "$@"
        return
    fi
    uid="$(id -u "$user")"
    sudo -u "$user" -H env PATH="$path" XDG_RUNTIME_DIR="/run/user/$uid" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" "$@"
}

openclaw_evidence() {
    # docs/openclaw-integration.md, "Verify with evidence, not with the
    # answer". A well-primed agent answers fluently from its own shell
    # while the MCP server sits untouched, so the reply proves nothing;
    # the server's own tool= lines are the only thing that does.
    OPENCLAW_BIN="$(openclaw_bin)"
    if [ -z "$OPENCLAW_BIN" ]; then
        local who; who="$(openclaw_user)"
        echo "No openclaw command for ${who:-$(id -un)} here, so there is nothing to ask." >&2
        return 2
    fi
    echo "Asking the agent one question, then reading the MCP server's log."
    echo "(The answer is not the test -- the log is.)"
    # Its output is kept, not shown: the answer proves nothing (above), but
    # when the agent could not answer at all, what it said is the reason --
    # its last lines only, which is where an error is, not a whole reply.
    # Bounded: a gateway that accepts and never answers would otherwise
    # hold this check forever (no timeout(1) on macOS: unbounded there).
    local said rc=0 bound=()
    command -v timeout >/dev/null 2>&1 && bound=(timeout 180)
    said="$(as_openclaw_user ${bound[@]+"${bound[@]}"} "$OPENCLAW_BIN" agent --agent main --session-key smoking-pi-check \
        -m "how has my connection been in the last 6 hours?" 2>&1)" || rc=$?
    local seen
    seen="$(compose logs mcp-server --since 3m 2>/dev/null | grep 'tool=' || true)"
    if [ -n "$seen" ]; then
        echo
        echo "Connected. The agent called the server:"
        printf '%s\n' "$seen" | tail -6 | sed 's/^/  /'
        return 0
    fi
    echo >&2
    if [ "$rc" != 0 ]; then
        # Not the trap below: the agent never answered, so the tool set and
        # the skill were never in play. Blaming them sent people the wrong
        # way (a stopped gateway read as "reload the MCP tools").
        if [ "$rc" = 124 ] && [ ${#bound[@]} -gt 0 ]; then
            echo "NOT connected: the agent did not answer within 3 minutes. It said:" >&2
        else
            echo "NOT connected: the agent did not answer (openclaw exited $rc). It said:" >&2
        fi
        printf '%s\n' "${said:-(nothing)}" | tail -6 | sed 's/^/  | /' >&2
        echo "Is the gateway running and set up? 'openclaw gateway status' says;" >&2
        echo "then run this check again." >&2
        return 1
    fi
    echo "NOT connected: the agent answered without calling the MCP server." >&2
    echo "It was answering from its own shell, which sounds right and is not" >&2
    echo "your recorded history. The usual causes, in order:" >&2
    echo "  1. The gateway still has its cached tool set. 'openclaw mcp reload'," >&2
    echo "     restart the gateway, and start a NEW chat session." >&2
    echo "  2. The skill is missing, so nothing redirects the agent:" >&2
    echo "     $SMOKING_PI_HOME/shared/scripts/install-openclaw-skill.sh --check" >&2
    echo "  3. The server is not registered: 'openclaw mcp probe smokeping'." >&2
    echo "$SMOKING_PI_HOME/docs/openclaw-integration.md, 'Verify with evidence', has the detail." >&2
    return 1
}

cmd_openclaw() {
    local check=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --check) check=1; shift ;;
            -h|--help)
                echo "smoking-pi openclaw [--check]"
                echo "  Connect this stack to an OpenClaw gateway on this machine, or say"
                echo "  exactly why it is not connected. --check only runs the verification."
                return 0 ;;
            *) echo "unknown option $1" >&2; return 2 ;;
        esac
    done
    need_edition
    # Every failure below returns; none exits. cmd_install calls this as a
    # subroutine, and a bash `exit` from here would take the whole install
    # down with it -- including the password summary that comes after --
    # which is the opposite of "every branch ends with a working install".
    if [ "$SMOKING_PI_EDITION" != pro ]; then
        echo "The MCP server is a Pro service; the $SMOKING_PI_EDITION edition does not ship it." >&2
        echo "Smoking Pi measures and alerts without OpenClaw -- nothing here is missing." >&2
        return 1
    fi
    [ -f "$ENV_FILE" ] || { echo "no env file at $ENV_FILE: run 'smoking-pi install' first" >&2; return 1; }

    [ "$check" = 1 ] && { openclaw_evidence; return $?; }

    OPENCLAW_BIN="$(openclaw_bin)"
    if [ -z "$OPENCLAW_BIN" ]; then
        # Not an error, and not a reason to stop: the stack is already
        # measuring. Two different situations, and the remote one has its
        # own guide -- saying "install OpenClaw" to someone whose gateway
        # is on their laptop sends them the wrong way.
        echo "No 'openclaw' command on this machine."
        echo
        echo "If OpenClaw runs on ANOTHER machine, that is the supported case and it"
        echo "needs a tunnel between the two loopbacks first:"
        echo "  $SMOKING_PI_HOME/docs/remote-openclaw.md"
        echo "Then run this command there, against the tunnelled port."
        echo
        echo "If you do not have OpenClaw at all, nothing is broken: Smoking Pi"
        echo "measures, alerts and draws its dashboards on its own. Install it when"
        echo "you want to ask questions in chat, then run 'smoking-pi openclaw'."
        return 0
    fi

    echo "Connecting this stack to the OpenClaw gateway on this machine."
    local who; who="$(openclaw_user)"
    echo "Using $OPENCLAW_BIN as ${who:-$(id -un)}."
    echo

    # 1. The credential. The MCP server exposes mutations (add/remove
    #    targets, restart SmokePing); Compose binds it to loopback, but
    #    every process on this host is inside that boundary.
    local token; token="$(env_get MCP_API_TOKEN)"
    if [ -z "$token" ]; then
        token="$(openssl rand -hex 32)"
        env_set MCP_API_TOKEN "$token"
        echo "1/5  Generated MCP_API_TOKEN in $ENV_FILE."
    elif [[ "$token" =~ ^[A-Za-z0-9._~-]+$ ]]; then
        echo "1/5  MCP_API_TOKEN is already set; keeping it."
    else
        # The token is interpolated into the JSON handed to `openclaw mcp
        # set`. A generated one is hex, but this one was written by hand or
        # by another tool, and a `"` or `\` in it would produce malformed
        # JSON -- which surfaces as a registration failure that reads like a
        # connectivity problem. Refuse instead of guessing.
        echo "MCP_API_TOKEN in $ENV_FILE contains characters that cannot be" >&2
        echo "passed through safely (anything outside A-Z a-z 0-9 . _ ~ -)." >&2
        echo "Replace it with 'openssl rand -hex 32', restart the server, and" >&2
        echo "re-run: the value is only a bearer token, nothing derives from it." >&2
        return 1
    fi

    # 2. The profile has to be recorded, or the next 'up' drops the server.
    local p; p="$(profiles)"
    case ",$p," in
        *,mcp,*) echo "2/5  The mcp profile is already recorded." ;;
        *) env_set COMPOSE_PROFILES "${p:+$p,}mcp"
           echo "2/5  Added the mcp profile to $ENV_FILE." ;;
    esac
    compose up -d mcp-server >/dev/null 2>&1 || true
    echo "     mcp-server is up."

    # 3. Prove the port is gated before handing its address to anything.
    local code_open code_auth
    code_open="$(curl -s -o /dev/null -w '%{http_code}' -XPOST http://127.0.0.1:8090/mcp 2>/dev/null || echo 000)"
    # The token goes in on stdin (-K -), never in argv: a command line is
    # readable by every account on this host (ps, /proc/*/cmdline). Same
    # reason, and the same shape, as the health checks in
    # shared/scripts/show-passwords.sh. The value MUST be quoted -- curl
    # reads an unquoted `header = A: B` as a key/value line and drops the
    # header in silence, which looks exactly like an auth failure.
    code_auth="$(printf 'url = "%s"\nrequest = "POST"\nsilent\noutput = "/dev/null"\nwrite-out = "%%{http_code}"\nheader = "Authorization: Bearer %s"\n' \
        "http://127.0.0.1:8090/mcp" "$(curl_cfg_quote "$token")" \
        | curl -K - 2>/dev/null || echo 000)"
    if [ "$code_open" = 000 ]; then
        echo "3/5  WARNING: 127.0.0.1:8090 did not answer. Give it a moment and re-run;" >&2
        echo "     'smoking-pi logs mcp-server' says why if it does not come up." >&2
    elif [ "$code_open" = 401 ] && [ "$code_auth" != 401 ]; then
        echo "3/5  The port refuses an unauthenticated request (401) and accepts the token."
    else
        echo "3/5  WARNING: expected 401 without the token and not-401 with it;" >&2
        echo "     got $code_open and $code_auth. The token may not have reached the" >&2
        echo "     container -- 'smoking-pi restart' does not recreate it, try" >&2
        echo "     'smoking-pi down && smoking-pi up'." >&2
    fi

    # 4. Register, then the skill. Registering alone is the documented
    #    half-install: the agent keeps answering from its shell.
    #    The JSON holding the token goes through a 0600 file, not sudo's
    #    command line: sudo logs the full command it runs to the journal.
    #    Timeouts in milliseconds: OpenClaw 2026.8 rejects the retired
    #    connectTimeout/timeout (seconds) and the whole `mcp set` with them.
    local spec user registered=0
    spec="$(mktemp)"
    printf '{\n  "url": "http://127.0.0.1:8090/mcp",\n  "transport": "streamable-http",\n  "headers": {"Authorization": "Bearer %s"},\n  "connectionTimeoutMs": 5000,\n  "requestTimeoutMs": 30000\n}' \
        "$token" > "$spec"
    user="$(openclaw_user)"
    # A failed chown surfaces below as a failed registration, not here.
    [ -z "$user" ] || chown "$user" "$spec" 2>/dev/null || true
    as_openclaw_user sh -c 'exec "$0" mcp set smokeping "$(cat "$1")"' "$OPENCLAW_BIN" "$spec" \
        >/dev/null && registered=1
    rm -f "$spec"
    if [ "$registered" = 1 ]; then
        echo "4/5  Registered as 'smokeping' with OpenClaw."
    else
        echo "4/5  'openclaw mcp set' failed. Run it by hand from" >&2
        echo "     docs/openclaw-integration.md and re-run this." >&2
        return 1
    fi

    if [ -x "$SMOKING_PI_HOME/shared/scripts/install-openclaw-skill.sh" ]; then
        as_openclaw_user "$SMOKING_PI_HOME/shared/scripts/install-openclaw-skill.sh" --reload >/dev/null 2>&1 \
            && echo "5/5  Installed the smokeping-monitoring skill and reloaded the gateway." \
            || { echo "5/5  WARNING: the skill did not install or the gateway did not reload; run" >&2
                 echo "     shared/scripts/install-openclaw-skill.sh --reload as the gateway's user." >&2
                 echo "     A user unit needs a session or 'loginctl enable-linger' for systemctl --user." >&2; }
    else
        echo "5/5  WARNING: install-openclaw-skill.sh not found; the agent will answer" >&2
        echo "     from its own shell until the skill is installed." >&2
    fi

    echo
    echo "A running gateway keeps its cached tool set, so start a NEW chat session"
    echo "before asking anything. Then:"
    echo "  smoking-pi openclaw --check"
    echo "which asks a question and checks the server's log, not the answer."
}
