# shellcheck shell=bash
# smoking-pi config: `config`, env-file settings by name.
# Sourced by cli/smoking-pi, never run on its own.

template_file() { echo "$EDITION_DIR/.env.template"; }

# Keys the edition's template declares, in its order. Only these can be set:
# a typo would otherwise be written, read by nothing, and look applied.
template_keys() { sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' "$(template_file)" | awk '!seen[$0]++'; }

# The template is also the settings' schema. `## Name` starts a section;
# a `#: ...` line describes the key right below it: its type (int, float,
# bool, enum:a|b, time, date, tz, url, origin), bounds (min=N, gt=N: above
# N, max=N, allow=V: V although out of bounds), and flags
# (secret: never printed unless asked; install: fixed at install). The plain
# comment lines above a key, up to a blank line, are its description.
# One awk pass, KEY SECTION META TEMPLATE-VALUE DESCRIPTION per declared key,
# separated by \037 (ASCII unit separator): not a tab, because bash's read
# collapses consecutive whitespace separators and an empty META would shift
# every field after it. The doctor's settings-schema check parses the same.
settings_table() {
    awk '
        /^## /  { section = substr($0, 4); desc = ""; meta = ""; incomment = 0; next }
        /^#: /  { meta = substr($0, 4); next }
        /^#/    { if (!incomment) desc = ""
                  meta = ""  # a #: line describes only the line right below it
                  line = $0; sub(/^# ?/, "", line)
                  desc = desc (desc == "" ? "" : " ") line; incomment = 1; next }
        /^[A-Z][A-Z0-9_]*=/ {
                  key = $0; sub(/=.*/, "", key); value = $0; sub(/^[^=]*=/, "", value)
                  if (!(key in seen)) print key "\037" section "\037" meta "\037" value "\037" desc
                  seen[key] = 1; meta = ""; incomment = 0; next }
        /^[ \t]*$/ { desc = ""; meta = ""; incomment = 0 }
    ' "$(template_file)"
}

setting_field() {
    # $1 KEY, $2 the column: 2 section, 3 meta, 4 template value, 5 description.
    # No early exit: awk leaving before settings_table is done writing
    # makes the pipeline fail with SIGPIPE under pipefail.
    settings_table | awk -F'\037' -v k="$1" -v f="$2" '$1 == k && !done { print $f; done = 1 }'
}

setting_flag() {
    # Whether KEY's `#:` line carries the word $2 (secret, install).
    local meta; meta=" $(setting_field "$1" 3) "
    [[ "$meta" == *" $2 "* ]]
}

setting_type() {
    # The type word of KEY's `#:` line; "string" when it declares none.
    local word
    for word in $(setting_field "$1" 3); do
        case "$word" in secret|install|min=*|max=*|gt=*|allow=*) ;; *) echo "$word"; return ;; esac
    done
    echo string
}

setting_bound() {
    # The value of min=, gt=, max= or allow= ($2) on KEY's `#:` line, or nothing.
    local word
    for word in $(setting_field "$1" 3); do
        case "$word" in "$2"=*) echo "${word#*=}"; return ;; esac
    done
}

validate_setting() {
    # Whether VALUE is one KEY's consumer accepts: refused here, not by a
    # container that crash-loops on it after the write. An untyped key
    # takes anything, as before the template carried types.
    local key="$1" value="$2" type lo hi
    type="$(setting_type "$key")"
    case "$type" in
        string) return 0 ;;
        int)
            [[ "$value" =~ ^[-+]?[0-9]+$ ]] || { echo "$key takes a whole number, not '$value'." >&2; return 2; } ;;
        float)
            [[ "$value" =~ ^[-+]?([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]] || { echo "$key takes a number, not '$value'." >&2; return 2; } ;;
        bool)
            # 1 and 0 too: every consumer of a key typed bool reads them
            # the same way (and the DNS observer's docs use them).
            case "$value" in true|false|1|0) ;; *) echo "$key takes true or false, not '$value'." >&2; return 2 ;; esac ;;
        enum:*)
            local allowed="${type#enum:}"
            case "|$allowed|" in *"|$value|"*) ;; *) echo "$key takes one of: ${allowed//|/, } (not '$value')." >&2; return 2 ;; esac ;;
        time)
            [[ "$value" =~ ^([01]?[0-9]|2[0-3]):[0-5][0-9]$ ]] || { echo "$key takes a time as HH:MM (24 h), not '$value'." >&2; return 2; } ;;
        date)
            [[ "$value" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] && date -d "$value" >/dev/null 2>&1 \
                || { echo "$key takes a date as YYYY-MM-DD, not '$value'." >&2; return 2; } ;;
        tz)
            # A zone name (not zone.tab or tzdata.zi, files that sit beside
            # them), checked against the zone database where there is one.
            if ! [[ "$value" =~ ^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*$ ]] \
                || { [ -d /usr/share/zoneinfo ] && [ ! -f "/usr/share/zoneinfo/$value" ]; }; then
                echo "$key takes a time zone name like Europe/London or America/Chicago, not '$value'." >&2; return 2
            fi ;;
        url)
            [[ "$value" =~ ^https?://[^[:space:]]+$ ]] || { echo "$key takes a URL with its scheme (https://...), not '$value'." >&2; return 2; } ;;
        origin)
            # https, a host and maybe a port, nothing after: what a remote
            # assistant is given (mcp-server/connector.py refuses the rest).
            [[ "$value" =~ ^https://[^/[:space:]?#]+/?$ ]] || { echo "$key takes https://HOST[:PORT] with no path, not '$value'." >&2; return 2; } ;;
        *)
            # A type this command does not know is the template's mistake
            # (the doctor flags it); refusing every value would be worse.
            return 0 ;;
    esac
    case "$type" in int|float)
        local gt allow
        allow="$(setting_bound "$key" allow)"
        if [ -n "$allow" ] && awk -v v="$value" -v a="$allow" 'BEGIN { exit !(v + 0 == a + 0) }'; then return 0; fi
        lo="$(setting_bound "$key" min)"; gt="$(setting_bound "$key" gt)"; hi="$(setting_bound "$key" max)"
        if [ -n "$gt" ] && awk -v v="$value" -v b="$gt" 'BEGIN { exit !(v <= b) }'; then
            echo "$key must be above $gt (not $value)${allow:+, or exactly $allow}." >&2; return 2
        fi
        if [ -n "$lo" ] && awk -v v="$value" -v b="$lo" 'BEGIN { exit !(v < b) }'; then
            echo "$key must be at least $lo (not $value)${allow:+, or exactly $allow}." >&2; return 2
        fi
        if [ -n "$hi" ] && awk -v v="$value" -v b="$hi" 'BEGIN { exit !(v > b) }'; then
            echo "$key must be at most $hi (not $value)." >&2; return 2
        fi ;;
    esac
}

compose_default() {
    # The default the compose files give KEY (${KEY:-default}), if any.
    # One awk over the files, no `| head`: an early close fails pipefail.
    awk -v k="$1" '!done && match($0, "[$][{]" k ":-[^}]*[}]") {
        s = substr($0, RSTART, RLENGTH); sub("^[$][{]" k ":-", "", s); sub("[}]$", "", s); print s; done = 1
    }' "$EDITION_DIR"/docker-compose*.yml 2>/dev/null || true
}

is_secret_key() {
    # The template's `secret` flag. The name patterns stay as a net, so a
    # credential the template forgot to flag is still never printed (the
    # doctor's settings-schema check fails on that).
    setting_flag "$1" secret && return 0
    case "$1" in
        # A webhook URL is often the credential itself (Slack, Discord, ntfy).
        *TOKEN*|*PASSWORD*|*SECRET*|*_KEY|ALERT_WEBHOOK_URL) return 0 ;;
    esac
    return 1
}

# Generated by setup.sh at install, or fixed by it: the data volumes hold
# them (the PostgreSQL role's password, InfluxDB's token, Grafana's stored
# admin), so changing the env file alone breaks the service it feeds.
# Same reason `install` refuses to run over an existing env file.
is_install_key() {
    setting_flag "$1" install && return 0
    case "$1" in
        *_PORT) return 1 ;;  # a port mapping, not a credential
        POSTGRES_*|INFLUX_*|DOCKER_INFLUXDB_*|CLICKHOUSE_*|GF_SECURITY_*|\
        WEB_ADMIN_PASSWORD|SECRET_KEY|CONFIG_API_TOKEN|MCP_API_TOKEN|TSDB_TYPE) return 0 ;;
    esac
    return 1
}

# The compose services whose definition mentions ${KEY...}: the ones to
# recreate after it changes. COMPOSE_PROFILES decides which services exist
# at all, so it applies to the whole stack.
services_reading() {
    local key="$1" f
    for f in "$EDITION_DIR"/docker-compose*.yml; do
        [ -f "$f" ] || continue
        awk -v key="$key" '
            /^services:/ { in_services = 1; next }
            /^[^ #]/ { in_services = 0 }
            in_services && /^  [A-Za-z0-9_.-]+:[ ]*$/ { svc = $1; sub(":", "", svc); next }
            in_services && svc != "" && (match($0, "[$][{]" key "[:}?-]") || match($0, "[$]" key "([^A-Za-z0-9_]|$)")) { print svc }
        ' "$f"
    done | sort -u
}

closest_key() {
    # The declared key that shares the longest prefix with a typo.
    local want="$1" best="" best_len=0 k n
    while read -r k; do
        n=0
        while [ "$n" -lt "${#k}" ] && [ "${k:0:$((n + 1))}" = "${want:0:$((n + 1))}" ]; do
            n=$((n + 1))
        done
        if [ "$n" -gt "$best_len" ]; then best="$k"; best_len="$n"; fi
    done < <(template_keys)
    [ "$best_len" -ge 3 ] && echo "$best"
}

check_known_key() {
    local key="$1" hint
    # Not `template_keys | grep -q`: under pipefail, grep closing the pipe
    # early can fail the writer and make a declared key look unknown.
    if ! grep -qx "$key" <(template_keys); then
        echo "$key is not a setting of the $SMOKING_PI_EDITION edition (.env.template does not declare it)." >&2
        hint="$(closest_key "$key")"
        [ -z "$hint" ] || echo "Did you mean $hint?" >&2
        return 2
    fi
}

config_apply() {
    local key="$1" services
    if [ "$key" = COMPOSE_PROFILES ]; then
        echo "Applying: the profiles decide which services run, so the whole stack."
        compose up -d --remove-orphans
        remove_disabled
        return
    fi
    # Only services the recorded profiles enable: naming a service on
    # `compose up` starts it even when its profile is off, and setting an
    # alerter key must not quietly start an alerter nobody enabled.
    local enabled
    if ! enabled="$(compose config --services 2>/dev/null)"; then
        echo "Written, but Compose could not read the stack ('smoking-pi up' shows why); nothing recreated." >&2
        return 1
    fi
    services="$(services_reading "$key" | { grep -Fxf <(printf '%s\n' "$enabled") || true; } | tr '\n' ' ')"
    if [ -z "${services// /}" ]; then
        if [ -n "$(services_reading "$key")" ]; then
            echo "$key is read by $(services_reading "$key" | tr '\n' ' ')which no enabled profile runs; it applies once one does."
        else
            echo "No service of this edition reads $key; nothing to recreate."
        fi
        return
    fi
    echo "Applying: recreating ${services% }"
    # shellcheck disable=SC2086  # one word per service
    compose up -d $services
}

cmd_config() {
    need_edition
    [ -f "$(template_file)" ] || { echo "no $(template_file) here" >&2; return 1; }
    local sub="${1:-list}"; shift || true
    case "$sub" in
        list)
            # Grouped by the template's sections; a word narrows it to the
            # sections whose name contains it (`config list alerts`).
            local filter="${1:-}" k section meta _value _desc v current="" secret shown=0
            while IFS=$'\037' read -r k section meta _value _desc; do
                if [ -n "$filter" ] && [[ "${section,,}" != *"${filter,,}"* ]]; then continue; fi
                if [ "$section" != "$current" ]; then
                    [ "$shown" = 0 ] || echo
                    echo "${section:-Other}"
                    current="$section"
                fi
                shown=1
                v="$(env_get "$k")"
                secret=0
                case " $meta " in *" secret "*) secret=1 ;; esac
                [ "$secret" = 1 ] || ! is_secret_key "$k" || secret=1
                if [ -z "$v" ]; then
                    printf '  %-34s (unset: the default applies)\n' "$k"
                elif [ "$secret" = 1 ]; then
                    printf '  %-34s (set, hidden)\n' "$k"
                else
                    printf '  %-34s %s\n' "$k" "$v"
                fi
            done < <(settings_table)
            if [ "$shown" = 0 ]; then
                echo "No section matches '$filter'. Sections:" >&2
                settings_table | cut -d $'\037' -f2 | awk '!seen[$0]++ { print "  " $0 }' >&2
                return 2
            fi
            echo
            echo "What a key does: $(cli_name) config describe KEY"
            ;;
        describe)
            local key="${1:-}" row section meta tvalue desc type v cdefault readers
            [ -n "$key" ] || { echo "usage: smoking-pi config describe KEY" >&2; return 2; }
            check_known_key "$key" || return 2
            row="$(settings_table | awk -F'\037' -v k="$key" '$1 == k')"
            IFS=$'\037' read -r _ section meta tvalue desc <<<"$row"
            type="$(setting_type "$key")"
            echo "$key  (${section:-Other})"
            [ -z "$desc" ] || echo "$desc" | fold -s -w 76 | sed 's/^/  /'
            echo
            printf '  %-10s %s\n' "Type:" "${type//|/, }$(b="$(setting_bound "$key" min)"; [ -z "$b" ] || printf ', at least %s' "$b")$(b="$(setting_bound "$key" gt)"; [ -z "$b" ] || printf ', above %s' "$b")$(b="$(setting_bound "$key" max)"; [ -z "$b" ] || printf ', at most %s' "$b")$(b="$(setting_bound "$key" allow)"; [ -z "$b" ] || printf ', or exactly %s' "$b")"
            cdefault="$(compose_default "$key")"
            if [ -n "$tvalue" ]; then printf '  %-10s %s\n' "Default:" "$tvalue (written at install)"
            elif [ -n "$cdefault" ]; then printf '  %-10s %s\n' "Default:" "$cdefault (when unset)"
            fi
            v="$(env_get "$key")"
            if [ -z "$v" ]; then printf '  %-10s %s\n' "Now:" "(unset: the default applies)"
            elif is_secret_key "$key"; then printf '  %-10s %s\n' "Now:" "(set, hidden: config get $key --show-secrets)"
            else printf '  %-10s %s\n' "Now:" "$v"
            fi
            readers="$(services_reading "$key" | paste -sd' ' -)"
            [ -z "$readers" ] || printf '  %-10s %s\n' "Read by:" "$readers"
            if is_install_key "$key"; then
                echo "  Fixed at install: the data volumes hold it, so 'config set' refuses it."
            elif is_secret_key "$key"; then
                echo "  Change:    $(cli_name) config set $key   (asked for, never on the command line)"
            else
                echo "  Change:    $(cli_name) config set $key VALUE"
            fi
            ;;
        get)
            local key="${1:-}" show=0
            [ -n "$key" ] || { echo "usage: smoking-pi config get KEY [--show-secrets]" >&2; return 2; }
            [ "${2:-}" = --show-secrets ] && show=1
            check_known_key "$key" || return 2
            if is_secret_key "$key" && [ "$show" = 0 ]; then
                if [ -n "$(env_get "$key")" ]; then echo "(set, hidden: --show-secrets prints it)"; else echo "(unset)"; fi
                return 0
            fi
            local got
            got="$(env_get "$key")"
            if [ -n "$got" ]; then echo "$got"; else echo "(unset: the default applies)"; fi
            ;;
        set|unset)
            local key="${1:-}" value="" apply=1 arg
            [ -n "$key" ] || { echo "usage: smoking-pi config set KEY [VALUE] [--no-apply] | unset KEY [--no-apply]" >&2; return 2; }
            shift
            local have_value=0
            for arg in "$@"; do
                case "$arg" in
                    --no-apply) apply=0 ;;
                    *)
                        [ "$have_value" = 0 ] || { echo "one value only (quote it if it has spaces)" >&2; return 2; }
                        value="$arg"; have_value=1 ;;
                esac
            done
            check_known_key "$key" || return 2
            if is_install_key "$key"; then
                echo "$key was generated or fixed at install, and the data volumes hold it:" >&2
                echo "changing it in the env file alone would lock the service it feeds out." >&2
                case "$key" in
                    MCP_API_TOKEN) echo "'smoking-pi openclaw' manages the MCP token." >&2 ;;
                    TSDB_TYPE) echo "Switching backends is a reinstall; see docs/clickhouse.md." >&2 ;;
                esac
                return 2
            fi
            if [ "$sub" = unset ]; then
                value=""
            elif is_secret_key "$key"; then
                if [ "$have_value" = 1 ]; then
                    echo "$key is a secret: it is not taken from the command line, where" >&2
                    echo "ps and the shell history would keep it. Leave VALUE out and type it," >&2
                    echo "or pipe it: printf %s \"\$VALUE\" | smoking-pi config set $key" >&2
                    return 2
                fi
                if [ -t 0 ]; then
                    read -r -s -p "$key: " value; echo
                else
                    IFS= read -r value || true
                fi
                [ -n "$value" ] || { echo "nothing read for $key; to clear it: smoking-pi config unset $key" >&2; return 2; }
            elif [ "$have_value" = 0 ]; then
                echo "usage: smoking-pi config set $key VALUE" >&2
                return 2
            fi
            if [ "$sub" = set ]; then
                # An empty value is no number, choice or time: for a typed
                # key, say how to clear it instead of "not ''".
                if [ -z "$value" ] && [ "$(setting_type "$key")" != string ]; then
                    echo "No value given. To clear $key (its default applies): $(cli_name) config unset $key" >&2
                    return 2
                fi
                # Every consumer of a choice or a true/false lowercases it
                # (DNS_ALLOW_PRIVATE_UPSTREAM is the exception that does not,
                # which is why it is written lowercase rather than only checked).
                case "$(setting_type "$key")" in bool|enum:*) value="${value,,}" ;; esac
                validate_setting "$key" "$value" || return 2
            fi
            case "$value" in
                *$'\n'*|*$'\r'*) echo "a value cannot span lines" >&2; return 2 ;;
                *"'"*) echo "a value cannot contain ' : neither Compose nor the shell reading the env file could keep it" >&2; return 2 ;;
            esac
            if [ "$(env_get "$key")" = "$value" ]; then
                echo "$key is already that; nothing changed."
                return 0
            fi
            env_set "$key" "$value"
            if [ "$sub" = unset ]; then echo "$key unset: the default applies."
            elif is_secret_key "$key"; then echo "$key set (hidden)."
            else echo "$key=$value"; fi
            if [ "$apply" = 1 ]; then config_apply "$key"; else echo "Not applied (--no-apply): 'smoking-pi up' picks it up."; fi
            ;;
        -h|--help) usage ;;
        *) echo "unknown config command: $sub (list, describe, get, set, unset)" >&2; return 2 ;;
    esac
}
