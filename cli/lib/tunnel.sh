# shellcheck shell=bash
# smoking-pi tunnel: Cloudflare quick tunnels, public URLs for the web pages.
# Sourced by cli/smoking-pi, never run on its own.

# A quick tunnel (TryCloudflare) needs no account: cloudflared asks for a
# random https://<words>.trycloudflare.com and forwards it to one service.
# Anyone with the URL reaches the page, protected only by that page's own
# login, and the URL changes on every restart. For assistants use
# `connect --tailscale`; for links in alerts, a named tunnel
# (shared/cloudflare-tunnel). The pin follows that compose file's.
TUNNEL_IMAGE="${SMOKING_PI_TUNNEL_IMAGE:-cloudflare/cloudflared:2026.5.0}"
# The label that marks a quick tunnel's container, so stop and status find
# them by identity rather than by a name some other container may have.
TUNNEL_LABEL=io.smoking-pi.quick-tunnel

tunnel_targets() {
    # One line per page this edition serves: name, URL as seen from the
    # tunnel's network, the network, extra `docker run` arguments.
    local proj="$1"
    case "$SMOKING_PI_EDITION" in
        basic)
            echo "smokeping http://smokeping:80 ${proj}_smokeping-net" ;;
        standard)
            echo "smokeping http://smokeping:80 ${proj}_smokeping-net"
            echo "webadmin http://web-admin:8080 ${proj}_smokeping-net" ;;
        pro)
            # SmokePing runs on the host network in Pro: reached through the
            # host's gateway address from the default bridge.
            echo "smokeping http://host.docker.internal:80 bridge --add-host=host.docker.internal:host-gateway"
            echo "webadmin http://web-admin:8080 ${proj}_default"
            echo "grafana http://grafana:3000 ${proj}_default" ;;
    esac
}

tunnel_url() {
    # The tunnel's CURRENT hostname: the last one in its log, not the first.
    # A restarted cloudflared asks for a new one and the log keeps the old.
    # api.trycloudflare.com is cloudflared's own endpoint, logged when the
    # request for a tunnel fails: never a page's address.
    { docker logs "$1" 2>&1 || true; } \
        | { grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' || true; } \
        | { grep -v '^https://api\.' || true; } | tail -1
}

tunnel_containers() {
    # "page container" per running or stopped quick tunnel of this host.
    docker ps -a --filter "label=$TUNNEL_LABEL" --format "{{.Label \"$TUNNEL_LABEL\"}} {{.Names}}" 2>/dev/null || true
    # And the ones shared/scripts/create-tunnel.sh started before this
    # command replaced it (v2.28.0): no label, named tunnel-<page>, and
    # running cloudflared -- a container of yours that only shares the
    # name is not one.
    docker ps -a --format '{{.Names}} {{.Image}}' 2>/dev/null \
        | awk '$1 ~ /^tunnel-(smokeping|webadmin|grafana)$/ && $2 ~ /^cloudflare\/cloudflared(:|$)/ { p = $1; sub(/^tunnel-/, "", p); print p, $1 }'
}

tunnel_status() {
    local page name url any=0
    while read -r page name; do
        [ -n "$name" ] || continue
        any=1
        url="$(tunnel_url "$name")"
        printf '  %-10s %s\n' "$page" "${url:-(no URL yet: docker logs $name)}"
    done < <(tunnel_containers)
    if [ "$any" = 0 ]; then
        echo "No quick tunnels. Start them: $(cli_name) tunnel start"
        return 0
    fi
    echo
    echo "Public: anyone with a URL reaches that page and its login. Stop them: $(cli_name) tunnel stop"
}

tunnel_start() {
    local yes=0
    while [ $# -gt 0 ]; do
        case "$1" in --yes|-y) yes=1; shift ;; *) echo "unknown option $1" >&2; return 2 ;; esac
    done
    need_edition
    local proj existing
    proj="$(project)" || proj=""
    [ -n "$proj" ] || { echo "Compose could not name the project: is the stack installed? ($(cli_name) status)" >&2; return 1; }
    existing="$(tunnel_containers)"
    # Every page already has its tunnel: nothing to ask. A page whose
    # tunnel failed last time is started now, the others are left alone.
    if [ -n "$existing" ] && ! tunnel_targets "$proj" | awk '{ print $1 }' | grep -vxF -f <(awk '{ print $1 }' <<<"$existing") >/dev/null; then
        echo "Quick tunnels are already running:"
        tunnel_status
        return 0
    fi
    echo "This publishes the $SMOKING_PI_EDITION edition's web pages on the Internet, at random"
    echo "trycloudflare.com addresses: whoever has a URL reaches the page and its login."
    if [ "$yes" = 0 ]; then
        [ -t 0 ] || { echo "Confirm with --yes." >&2; return 2; }
        local a
        read -r -p "Start them? [y/N] " a
        case "$a" in y|Y|yes) ;; *) echo "Nothing started."; return 1 ;; esac
    fi
    local page target network extra name i url failed=0
    while read -r page target network extra; do
        name="${proj}-tunnel-$page"
        if awk -v p="$page" '$1 == p { found = 1 } END { exit !found }' <<<"$existing"; then
            printf '  %-10s %s\n' "$page" "$(tunnel_url "$(awk -v p="$page" '$1 == p { print $2; exit }' <<<"$existing")") (already running)"
            continue
        fi
        # shellcheck disable=SC2086  # extra: zero or one word
        if ! docker run -d --name "$name" --label "$TUNNEL_LABEL=$page" \
                --network "$network" $extra --restart unless-stopped \
                "$TUNNEL_IMAGE" tunnel --no-autoupdate --url "$target" >/dev/null; then
            echo "Could not start the $page tunnel (is the stack up? $(cli_name) status)" >&2
            failed=1
            continue
        fi
        url=""
        for i in $(seq 1 30); do
            url="$(tunnel_url "$name")"
            [ -n "$url" ] && break
            sleep 1
        done
        printf '  %-10s %s\n' "$page" "${url:-(no URL within 30 s: docker logs $name)}"
    done < <(tunnel_targets "$proj")
    echo
    echo "These change on every restart, and keep running when the stack is down."
    echo "Stop them: $(cli_name) tunnel stop"
    return "$failed"
}

tunnel_stop() {
    local names
    names="$(tunnel_containers | awk '{print $2}')"
    if [ -z "$names" ]; then
        echo "No quick tunnels to stop."
        return 0
    fi
    # shellcheck disable=SC2086  # one word per container
    docker rm -f $names >/dev/null
    echo "Stopped: $(echo $names)"
}

cmd_tunnel() {
    local action="${1:-status}"; [ $# -gt 0 ] && shift
    case "$action" in
        status) tunnel_status ;;
        start) tunnel_start "$@" ;;
        stop) tunnel_stop ;;
        -h|--help) usage ;;
        *) echo "unknown tunnel action: $action (status, start, stop)" >&2; return 2 ;;
    esac
}
