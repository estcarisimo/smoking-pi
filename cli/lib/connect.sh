# shellcheck shell=bash
# smoking-pi connect: `connect` / `disconnect`, one way in for every assistant.
# Sourced by cli/smoking-pi, never run on its own.

# Every assistant is an MCP client. One running on this machine (OpenClaw)
# uses the local token; any other signs in through the remote connector
# (mcp-server/connector.py) with a pairing code. The name is a label the
# owner picks -- there is no per-assistant code here.

connector_ready() {
    need_edition
    if [ "$SMOKING_PI_EDITION" != pro ]; then
        echo "Assistants connect through the MCP server, a Pro service; the $SMOKING_PI_EDITION edition does not ship it." >&2
        return 1
    fi
    if [ -z "$(compose ps -q --status running mcp-server 2>/dev/null)" ]; then
        echo "The MCP server is not running: add 'mcp' to the profiles ($(cli_name) config set COMPOSE_PROFILES ...), then $(cli_name) up" >&2
        return 1
    fi
}

# --- connect --tailscale: the tunnel, done for the owner -------------------
# Tailscale Funnel publishes this Pi's MCP server (127.0.0.1:8090) at
# https://<machine>.<tailnet>.ts.net, with nothing to open on the router.
# By hand it was five commands and three traps: Tailscale's DNS takes over
# the Pi's resolver (and when it logs out, every container keeps a dead one:
# half the monitoring went silent for ten days once); the address has a
# tailnet part nobody knows by heart, typed as a placeholder twice on the
# reference Pi; and a second device with the same name gets "-1". This
# reads the real name from Tailscale itself.

ts() { tailscale "$@"; }

# Funnel's one listener here is :443; --off turns that listener off.
ts_off() {
    if ! command -v tailscale >/dev/null; then
        echo "Tailscale is not installed: nothing is published through it."
    elif ts funnel --https=443 off >/dev/null 2>&1; then
        echo "Funnel off: this Pi publishes nothing on its ts.net address (port 443)."
    else
        echo "Tailscale did not turn Funnel off; 'tailscale funnel status' says what is still on." >&2
        return 1
    fi
}

ts_field() {
    # One field of `tailscale status --json`: BackendState, AuthURL, or
    # Self.DNSName (without its trailing dot). Empty when unknown.
    ts status --json 2>/dev/null | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
if sys.argv[1] == "DNSName":
    print(((d.get("Self") or {}).get("DNSName") or "").rstrip("."))
else:
    print(d.get(sys.argv[1]) or "")' "$1" || true
}

connect_tailscale() {
    local yes=0 off=0 a i
    for a in "$@"; do
        case "$a" in
            --yes) yes=1 ;; --off) off=1 ;;
            *) echo "unknown option $a (connect --tailscale [--off] [--yes])" >&2; return 2 ;;
        esac
    done
    if [ "$(id -u)" != 0 ]; then
        echo "Tailscale is set up as root: run it with sudo." >&2
        return 1
    fi
    # --off before the MCP server check: a stopped server is exactly when
    # someone wants it unpublished.
    if [ "$off" = 1 ]; then
        need_edition
        ts_off || return 1
        case "$(env_get MCP_PUBLIC_URL)" in
            "") ;;
            https://*.ts.net|https://*.ts.net/) cmd_config unset MCP_PUBLIC_URL || return 1 ;;
            *) echo "MCP_PUBLIC_URL is $(env_get MCP_PUBLIC_URL), another tunnel's: left as it is." ;;
        esac
        return 0
    fi
    connector_ready || return 1
    if ! command -v tailscale >/dev/null; then
        echo "Tailscale is not installed. Its own installer adds its apt repository and the package:"
        echo "  curl -fsSL https://tailscale.com/install.sh | sh"
        if [ "$yes" = 0 ]; then
            local answer=""
            [ -t 0 ] && read -r -p "Run it now? [y/N] " answer
            case "$answer" in y|Y|yes) ;; *) echo "Not installed; run it, or add --yes." >&2; return 1 ;; esac
        fi
        curl -fsSL https://tailscale.com/install.sh | sh || { echo "The Tailscale installer failed." >&2; return 1; }
    fi
    # Before anything else: Tailscale must never own this Pi's DNS.
    if ! ts set --accept-dns=false 2>/dev/null; then
        echo "Warning: Tailscale did not accept --accept-dns=false; check 'tailscale debug prefs' (CorpDNS)." >&2
    fi
    if [ "$(ts_field BackendState)" != Running ]; then
        # `login`, not `up`: with saved settings (a hostname, say) `up`
        # refuses unless every one is repeated, even bare -- the staging
        # Pi's first two runs stopped there -- and the link it showed was a
        # stale one. `login` asks for a fresh link, but it resets every
        # setting it is not given: the `set` above was undone and the
        # staging Pi's DNS went to Tailscale. So the flag goes here too, and
        # it is checked again once signed in.
        # It runs aside and the link is read from the daemon, so this works
        # the same over SSH, in a script, or at the console.
        local log url="" shown="" i up_pid
        log="$(mktemp)"
        setsid tailscale login --accept-dns=false > "$log" 2>&1 < /dev/null &
        up_pid=$!
        # setsid keeps Ctrl-C and a dropped SSH session from reaching it.
        # shellcheck disable=SC2064  # expand now: these are this run's
        trap "kill $up_pid 2>/dev/null; rm -f '$log'" EXIT INT TERM
        sleep 2
        local state noted=""
        for i in $(seq 1 600); do
            state="$(ts_field BackendState)"
            [ "$state" = Running ] && break
            if [ "$state" = NeedsMachineAuth ] && [ -z "$noted" ]; then
                echo "Signed in; your tailnet must approve this device in Tailscale's admin console."
                noted=1
            fi
            url="$(ts_field AuthURL)"
            if [ -n "$url" ] && [ "$url" != "$shown" ]; then
                echo "Sign this Pi in to your Tailscale account (any browser, any device):"
                echo "  $url"
                echo "Waiting for the sign-in..."
                shown="$url"
            fi
            if ! kill -0 "$up_pid" 2>/dev/null && ! wait "$up_pid"; then
                cat "$log" >&2; rm -f "$log"; trap - EXIT INT TERM
                return 1
            fi
            sleep 1
        done
        kill "$up_pid" 2>/dev/null || true
        rm -f "$log"
        trap - EXIT INT TERM
        if [ "$(ts_field BackendState)" != Running ]; then
            echo "Tailscale is not signed in yet ($(ts_field BackendState)); run this again after signing in." >&2
            return 1
        fi
        echo "Signed in to Tailscale."
    fi
    # Checked, not assumed, before anything is recreated: a container takes
    # the host's resolver when it is created and keeps it.
    ts set --accept-dns=false 2>/dev/null || true
    local corp
    corp="$(ts debug prefs 2>/dev/null | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("CorpDNS"))
except ValueError: pass' 2>/dev/null || true)"
    if [ "$corp" != False ]; then
        echo "Tailscale still owns this Pi's DNS (CorpDNS=${corp:-unknown}); stopping before anything is published." >&2
        echo "Fix: sudo tailscale set --accept-dns=false, then run this again." >&2
        return 1
    fi
    echo "Tailscale keeps out of this Pi's DNS."
    local name
    name="$(ts_field DNSName)"
    if [ -z "$name" ]; then
        echo "Tailscale is up but reports no machine name; check 'tailscale status'." >&2
        return 1
    fi
    # --yes: no prompt over SSH. When Funnel is not yet allowed on the
    # tailnet, Tailscale prints the page that allows it and waits for it.
    if ! ts funnel --bg --yes 127.0.0.1:8090; then
        echo "Tailscale did not turn Funnel on; 'tailscale funnel status' says why." >&2
        return 1
    fi
    local public="https://$name"
    echo "Published at $public"
    if [ "$(env_get MCP_PUBLIC_URL)" != "$public" ]; then
        cmd_config set MCP_PUBLIC_URL "$public" || return 1
    else
        echo "MCP_PUBLIC_URL is already $public."
    fi
    # From the internet's side: the first request also gets Funnel its
    # certificate, which can take a few seconds.
    local issuer=""
    for i in $(seq 1 "${SMOKING_PI_TS_CHECK_TRIES:-12}"); do
        issuer="$(curl -s -m 10 "$public/.well-known/oauth-authorization-server" 2>/dev/null \
            | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("issuer",""))
except ValueError: pass' 2>/dev/null || true)"
        [ "$issuer" = "$public" ] && break
        [ "$i" -lt "${SMOKING_PI_TS_CHECK_TRIES:-12}" ] && sleep 5
    done
    if [ "$issuer" != "$public" ]; then
        echo "Published, but $public does not answer as this server yet. A new name can take" >&2
        echo "up to 10 minutes to appear in public DNS; run this again then, or check:" >&2
        echo "  curl -s $public/.well-known/oauth-authorization-server" >&2
        return 1
    fi
    echo "Checked from outside: $public answers as this Pi's MCP server."
    echo "Next: $(cli_name) connect NAME   (grok, claude, chatgpt...)"
}

cmd_connect() {
    case "${1:-}" in
        -h|--help)
            echo "smoking-pi connect [NAME [--as KIND] [--check] | --list | --tailscale [--off] [--yes]]"
            echo "  No NAME: what is connected. 'openclaw': OpenClaw on this machine."
            echo "  Any other NAME: a remote assistant; prints the URL, a pairing code and"
            echo "  that assistant's steps (claude, claude-code, chatgpt, cursor, grok; a"
            echo "  name like claude-work picks Claude's; --as KIND picks one for any name)."
            echo "  --check: whether NAME has called a tool, the only proof it is used."
            echo "  --list: the assistants with steps of their own."
            echo "  --tailscale: publish the server through Tailscale Funnel and set"
            echo "  MCP_PUBLIC_URL (--off undoes it)."
            return 0 ;;
        openclaw) shift; cmd_openclaw "$@"; return $? ;;
        --tailscale) shift; connect_tailscale "$@"; return $? ;;
    esac
    local name="" kind="" check=0 list=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --check) check=1 ;;
            --list) list=1 ;;
            --as) case "${2:-}" in ""|-*) echo "--as needs a KIND ($(cli_name) connect --list)" >&2; return 2 ;; esac
                  kind="$2"; shift ;;
            -h|--help) cmd_connect --help; return 0 ;;
            -*) echo "unknown option $1" >&2; return 2 ;;
            *) [ -z "$name" ] || { echo "one NAME at a time: $1" >&2; return 2; }
               name="$1" ;;
        esac
        shift
    done
    if [ "$list" = 1 ] && { [ -n "$name" ] || [ -n "$kind" ] || [ "$check" = 1 ]; }; then
        echo "--list stands alone: $(cli_name) connect --list" >&2
        return 2
    fi
    if [ -z "$name" ] && { [ "$check" = 1 ] || [ -n "$kind" ]; }; then
        echo "--check and --as need a NAME: $(cli_name) connect NAME --check" >&2
        return 2
    fi
    if [ "$check" = 1 ] && [ -n "$kind" ]; then
        echo "--check takes a NAME only: $(cli_name) connect $name --check" >&2
        return 2
    fi
    connector_ready || return 1
    if [ "$list" = 1 ]; then
        compose exec -T mcp-server python connector.py assistants
        return $?
    fi
    local url
    url="$(env_get MCP_PUBLIC_URL)"
    if [ -z "$name" ]; then
        if [ -z "$url" ]; then
            echo "Remote assistants: off (MCP_PUBLIC_URL is not set: $(cli_name) connect --tailscale,"
            echo "or any tunnel: docs/remote-connector.md)."
        else
            echo "Remote assistants connect at: ${url%/}/mcp"
            compose exec -T mcp-server python connector.py list
        fi
        echo "OpenClaw on this machine: $(cli_name) connect openclaw --check"
        return 0
    fi
    if ! printf '%s' "$name" | grep -Eq '^[A-Za-z0-9._-]{1,40}$'; then
        echo "A name is up to 40 letters, digits, '.', '_' or '-': $name" >&2
        return 2
    fi
    if [ -n "$kind" ] && ! printf '%s' "$kind" | grep -Eq '^[A-Za-z0-9._-]{1,40}$'; then
        echo "Not an assistant kind: $kind ($(cli_name) connect --list)" >&2
        return 2
    fi
    if [ "$check" = 1 ]; then
        compose exec -T mcp-server python connector.py check "$name"
        return $?
    fi
    if [ -z "$url" ]; then
        echo "Remote assistants need an HTTPS address that reaches this Pi (MCP_PUBLIC_URL)." >&2
        local answer=""
        if [ -t 0 ] && command -v tailscale >/dev/null; then
            read -r -p "Publish it through Tailscale Funnel now ($(cli_name) connect --tailscale)? [y/N] " answer || answer=""
        fi
        case "$answer" in
            y|Y|yes)
                connect_tailscale || return 1
                url="$(env_get MCP_PUBLIC_URL)"
                [ -n "$url" ] || return 1
                connector_ready || return 1 ;;
            *)
                echo "One command does it with Tailscale: sudo $(cli_name) connect --tailscale" >&2
                echo "Any other tunnel: docs/remote-connector.md, then" >&2
                echo "  $(cli_name) config set MCP_PUBLIC_URL https://..." >&2
                return 1 ;;
        esac
    fi
    if [ -n "$kind" ]; then
        compose exec -T mcp-server python connector.py pair "$name" --as "$kind"
    else
        compose exec -T mcp-server python connector.py pair "$name"
    fi
}

cmd_disconnect() {
    case "${1:-}" in
        ""|-h|--help) echo "smoking-pi disconnect NAME" >&2; [ -n "${1:-}" ]; return $? ;;
        openclaw)
            echo "OpenClaw on this machine uses the local MCP token, not a sign-in:" >&2
            echo "remove it from OpenClaw ('openclaw mcp remove smokeping'), or rotate MCP_API_TOKEN." >&2
            return 1 ;;
    esac
    connector_ready || return 1
    compose exec -T mcp-server python connector.py revoke "$1"
}
