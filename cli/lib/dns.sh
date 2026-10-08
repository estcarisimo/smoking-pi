# shellcheck shell=bash
# smoking-pi dns: `dns`, the DNS observer.
# Sourced by cli/smoking-pi, never run on its own.

profiles_without() {
    # COMPOSE_PROFILES with $1 removed.
    env_get COMPOSE_PROFILES | tr ',' '\n' | { grep -vx "$1" || true; } | paste -sd, -
}

dns_status() {
    # The observer's own verdict (status.py). When the container is not
    # running there is no verdict to ask for; say so, and that it matters.
    if [ -n "$(compose ps -q --status running dns-observer 2>/dev/null)" ]; then
        compose exec -T dns-observer python status.py "$@"
        return
    fi
    echo "state:          down (the dns-observer container is not running)"
    case ",$(env_get COMPOSE_PROFILES)," in
        *,dns,*) echo "fix:            smoking-pi up; then smoking-pi logs dns-observer" ;;
        *) echo "fix:            not enabled: smoking-pi dns enable" ;;
    esac
    return 1
}

dns_test() {
    # The whole path, now (connection_test.py): what the canary would say
    # in 15 minutes. Exit 1 when a check failed.
    if [ -z "$(compose ps -q --status running dns-observer 2>/dev/null)" ]; then
        echo "FAIL  the dns-observer container is not running" >&2
        case ",$(env_get COMPOSE_PROFILES)," in
            *,dns,*) echo "      fix: smoking-pi up; then smoking-pi logs dns-observer" >&2 ;;
            *) echo "      fix: smoking-pi dns enable" >&2 ;;
        esac
        return 1
    fi
    local rc=0 lan
    compose exec -T dns-observer python connection_test.py "$@" || rc=$?
    # A lease can move the Pi to another address, and the router would
    # forward the house's DNS to nothing. Only the router knows whether
    # the lease is reserved; say it once, as a note, not a failure.
    case " $* " in *" --json "*) return "$rc" ;; esac
    lan="$(lan_address)"
    if [ -n "$lan" ] && ip -4 -o addr show 2>/dev/null | grep -F " $lan/" | grep -q ' dynamic'; then
        echo "note: $lan comes from DHCP; reserve it for this Pi in the router (docs/dns-observer.md, step 1)."
    fi
    return "$rc"
}

dns_adopt() {
    # The wizard's selection becomes targets (config-manager/wizard_adopt.py),
    # asked of the running API from inside its container: the API token
    # stays in the container's environment.
    local a
    for a in "$@"; do
        case "$a" in --dry-run|--retire-only|--force) ;;
            *) echo "unknown option $a (--dry-run, --retire-only, --force)" >&2; return 2 ;; esac
    done
    if [ -z "$(compose ps -q --status running config-manager 2>/dev/null)" ]; then
        echo "config-manager is not running: smoking-pi up" >&2
        return 1
    fi
    compose exec -T config-manager python wizard_adopt.py "$@"
}

port53_holder() {
    # Whatever already listens on port 53 (systemd-resolved's stub listens
    # on 127.0.0.53 only and does not conflict with 0.0.0.0 on Linux, but
    # dnsmasq, a Pi-hole or another AdGuard do).
    command -v ss >/dev/null || return 0
    # `|| true`: under pipefail a grep that matches nothing -- the usual,
    # nothing on port 53 -- would end the whole command.
    ss -Hlnup 'sport = :53' 2>/dev/null | grep -v '127\.0\.0\.5[34]' | head -1 || true
}

cmd_dns() {
    local action="${1:-status}"; [ $# -gt 0 ] && shift
    need_edition
    if ! grep -q '^  dns-observer:' "$EDITION_DIR/docker-compose.yml"; then
        echo "The DNS observer is a Pro service; the $SMOKING_PI_EDITION edition does not ship it." >&2
        return 1
    fi
    local yes=0 a
    case "$action" in
        status) dns_status "$@"; return ;;
        test) dns_test "$@"; return ;;
        adopt) dns_adopt "$@"; return ;;
        enable|disable)
            while [ $# -gt 0 ]; do
                case "$1" in --yes|-y) yes=1; shift ;; *) echo "unknown option $1" >&2; return 2 ;; esac
            done ;;
        -h|--help) usage; return 0 ;;
        *) echo "unknown dns action: $action (status, test, adopt, enable, disable)" >&2; return 2 ;;
    esac

    if [ "$action" = disable ]; then
        echo "If the router forwards DNS to this Pi, point it back at its previous DNS first:"
        echo "with the observer gone, a router with no secondary DNS leaves the house offline."
        if [ "$yes" = 0 ]; then
            [ -t 0 ] || { echo "Confirm with --yes." >&2; return 2; }
            read -r -p "The router no longer points here. Disable? [y/N] " a
            case "$a" in y|Y|yes) ;; *) echo "Nothing changed."; return 1 ;; esac
        fi
        env_set COMPOSE_PROFILES "$(profiles_without dns)"
        compose stop dns-observer >/dev/null 2>&1 || true
        remove_disabled
        echo "DNS observer disabled. Its query log stays in its volumes until 'smoking-pi purge'."
        return 0
    fi

    if [ -z "$(compose ps -q --status running dns-observer 2>/dev/null)" ]; then
        local holder; holder="$(port53_holder)"
        if [ -n "$holder" ]; then
            echo "Something already listens on port 53 here:" >&2
            echo "  $holder" >&2
            echo "Stop it first, or set DNS_BIND to an address it does not use." >&2
            return 1
        fi
    fi
    if [ -z "$(env_get DNS_ADMIN_PASSWORD)" ]; then
        env_set DNS_ADMIN_PASSWORD "$(od -An -tx1 -N24 /dev/urandom | tr -d ' \n')"
        echo "Generated DNS_ADMIN_PASSWORD (smoking-pi passwords --show-secrets shows it)."
    fi
    env_set COMPOSE_PROFILES "$(profiles_with dns)"
    compose up -d dns-observer
    local i up=0
    for i in $(seq 1 30); do
        if compose exec -T dns-observer python status.py --json 2>/dev/null | grep -q '"answering": true'; then
            up=1; break
        fi
        sleep 2
    done
    if [ "$up" = 0 ]; then
        echo "The DNS observer did not start answering within a minute; do not point the router at it yet." >&2
        echo "See: $(cli_name) dns status; $(cli_name) logs dns-observer" >&2
        return 1
    fi
    local lan; lan="$(lan_address)"; lan="${lan:-<the LAN address of this machine>}"
    echo
    echo "The DNS observer answers on $lan, port 53. Nothing reaches it yet."
    echo "On the router, set its DNS server (the upstream it forwards to, often 'custom DNS'):"
    echo "  primary:   $lan"
    echo "  secondary: 1.1.1.1  -- keeps the house online if this Pi is down, at the cost"
    echo "             of the share of queries the router sends there ('partial' in status)."
    echo "Do not change the DNS your devices get by DHCP; the router keeps doing that."
    echo
    echo "Before that:"
    echo "  - reserve $lan for this Pi in the router's DHCP settings, or a new lease"
    echo "    leaves the router forwarding the house's DNS to nothing;"
    echo "  - from a laptop, check it answers: dig @$lan example.com"
    echo "After: $(cli_name) dns test     (checks the whole path in seconds)"
    echo "       $(cli_name) dns status   (the canary keeps checking it every 5 min)"
    echo "Step by step, per router: $SMOKING_PI_HOME/docs/dns-observer.md, \"Setup guide\""
}
