# shellcheck shell=bash
# smoking-pi services: `enable` / `disable`, the optional services by profile.
# Sourced by cli/smoking-pi, never run on its own.

# COMPOSE_PROFILES decides which optional services exist at all, and it
# must list every one in use: a service started once with
# `COMPOSE_PROFILES=mcp docker compose up -d` runs unmanaged, and the next
# `down` + `up -d` silently drops it (.env.template says so at length).
# These commands are the only way the docs tell anyone to change it.

# The optional profiles a person turns on and off, with what each runs.
# The backend profiles (influxdb, clickhouse) are not here: they follow
# TSDB_TYPE, which the data volumes fix at install.
optional_profiles() {
    cat <<'PROFILES'
mcp        MCP server: the assistant tools (docs/mcp-server.md)
alerts     alerting engine (docs/alerting.md; 'smoking-pi alerts' chooses where they go)
ai         AI health reports (docs/ai-insights.md; needs ANTHROPIC_API_KEY)
inference  EXPERIMENTAL congestion and degradation detectors (docs/inference.md)
dns        DNS observer on port 53 (docs/dns-observer.md; same as 'smoking-pi dns enable')
PROFILES
}

edition_has_profile() {
    # Whether this edition's compose files define a service under profile $1.
    # Both spellings in use: `profiles: [mcp]` and a `- ai` list item.
    awk -v p="$1" '
        /^ +profiles: *\[/ { s = $0; gsub(/.*\[|\].*/, "", s); n = split(s, a, / *, */)
                              for (i = 1; i <= n; i++) if (a[i] == p) found = 1; next }
        /^ +profiles: *$/   { inlist = 1; next }
        inlist && /^ +- /   { s = $0; sub(/^ +- +/, "", s); if (s == p) found = 1; next }
                            { inlist = 0 }
        END { exit !found }' "$EDITION_DIR"/docker-compose*.yml
}

profile_on() {
    case ",$(env_get COMPOSE_PROFILES)," in *",$1,"*) return 0 ;; esac
    return 1
}

services_list() {
    local name desc state any=0
    while read -r name desc; do
        edition_has_profile "$name" || continue
        any=1
        if profile_on "$name"; then state=on; else state=off; fi
        printf '  %-10s %-4s %s\n' "$name" "$state" "$desc"
    done < <(optional_profiles)
    if [ "$any" = 0 ]; then
        echo "The $SMOKING_PI_EDITION edition has no optional services; Pro does."
    else
        echo
        echo "Turn one on or off: $(cli_name) enable NAME | $(cli_name) disable NAME"
    fi
}

cmd_services() {
    # enable|disable [NAME...] (no NAME: list them).
    local action="$1"; shift
    need_edition
    case "${1:-}" in
        -h|--help) usage; return 0 ;;
        "") services_list; return 0 ;;
    esac
    local name changed=() next
    next="$(env_get COMPOSE_PROFILES)"
    for name in "$@"; do
        case "$name" in
            influxdb|clickhouse)
                echo "$name is the measurement backend, fixed at install (TSDB_TYPE): switching is a reinstall, see docs/clickhouse.md." >&2
                return 2 ;;
        esac
        if ! optional_profiles | grep -q "^$name "; then
            echo "unknown service: $name (one of: $(optional_profiles | awk '{print $1}' | paste -sd' ' -))" >&2
            return 2
        fi
        if ! edition_has_profile "$name"; then
            echo "$name is not part of the $SMOKING_PI_EDITION edition; Pro has it." >&2
            return 2
        fi
        # The DNS observer takes over port 53 and the house's DNS may come
        # to depend on it: its own command checks the port first and says
        # what to change on the router, before and after.
        if [ "$name" = dns ]; then
            if [ $# -gt 1 ]; then
                echo "dns is enabled and disabled on its own: $(cli_name) $action dns" >&2
                return 2
            fi
            cmd_dns "$action"
            return
        fi
        if [ "$action" = enable ]; then
            profile_on "$name" && continue
            next="$(profiles_from "$next" add "$name")"
        else
            profile_on "$name" || continue
            next="$(profiles_from "$next" remove "$name")"
        fi
        changed+=("$name")
    done
    if [ "${#changed[@]}" -eq 0 ]; then
        echo "Nothing to change: $* already ${action}d."
        return 0
    fi
    env_set COMPOSE_PROFILES "$next"
    echo "COMPOSE_PROFILES=$next"
    config_apply COMPOSE_PROFILES
    for name in "${changed[@]}"; do
        [ "$action" = enable ] || continue
        case "$name" in
            alerts) [ -n "$(env_get NOTIFY_MODE)" ] || echo "Alerts are on but go nowhere yet: $(cli_name) alerts --telegram (or --openclaw, --webhook)." ;;
            ai) [ -n "$(env_get ANTHROPIC_API_KEY)" ] || echo "AI reports need a key: $(cli_name) config set ANTHROPIC_API_KEY" ;;
            mcp) echo "Next: $(cli_name) connect (an assistant) or $(cli_name) openclaw (OpenClaw on this machine)." ;;
        esac
    done
    [ "$action" = enable ] || echo "Their data stays in the volumes until '$(cli_name) purge'."
}

profiles_from() {
    # $1 a profile list, $2 add|remove, $3 the profile: the new list, order
    # kept, no duplicates, no empty items.
    local list="$1" op="$2" p="$3"
    if [ "$op" = add ]; then
        case ",$list," in *",$p,"*) echo "$list" ;; *) echo "${list:+$list,}$p" ;; esac
    else
        tr ',' '\n' <<<"$list" | { grep -vx -e "$p" -e '' || true; } | paste -sd, -
    fi
}
