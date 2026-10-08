# shellcheck shell=bash
# smoking-pi env: reading and writing the env file. The only code that edits it.
# Sourced by cli/smoking-pi, never run on its own.

env_get() {
    # A value env_set had to quote comes back without its single quotes.
    [ -f "$ENV_FILE" ] && sed -n "s/^$1=//p" "$ENV_FILE" | tail -1 | sed "s/^'\\(.*\\)'\$/\\1/" || true
}

# How a value is written into the env file. Two readers interpolate `$` in
# it -- Compose (.env files, since 2.24) and bash (show-passwords.sh and
# sync-influx-token.sh source the file) -- and both leave a single-quoted value
# alone: an unquoted `pbkdf2:sha256:260000$salt$hash` reached the container
# as `pbkdf2:sha256:260000` (seen with Compose 2.38). Plain values are
# written as they always were. A value holding `'` cannot be quoted for
# either reader and is refused by the caller.
env_quote() {
    case "$1" in
        *[!A-Za-z0-9_.,:/@%+=~-]*) printf "'%s'" "$1" ;;
        *) printf '%s' "$1" ;;
    esac
}

# Escape a value for curl's config-file syntax, which is what -K - reads.
# Inside quotes curl honors \\ and \", so both have to be escaped; the same
# helper lives in shared/scripts/show-passwords.sh for the same reason.
curl_cfg_quote() { printf '%s' "$1" | sed 's/[\\"]/\\&/g'; }

env_set() {
    # Add or replace VAR=value, keeping the rest of the file and its mode:
    # this file holds every secret, so a widened mode here undoes what
    # `passwords` checks for.
    local k="$1" v tmp
    v="$(env_quote "$2")"
    tmp="$(mktemp "$(dirname "$ENV_FILE")/.env.XXXXXX")"
    if grep -q "^$k=" "$ENV_FILE" 2>/dev/null; then
        # awk, not sed: the value is substituted literally, so a `&` or a
        # `/` in it cannot turn into a regex replacement.
        awk -v k="$k" -v v="$v" \
            'index($0, k "=") == 1 && !done { print k "=" v; done = 1; next } { print }' \
            "$ENV_FILE" > "$tmp"
    elif [ -f "$ENV_FILE" ]; then
        # Not `|| true`: swallowing a read error here would write a file
        # holding only this one line and then rename it over every secret
        # in the deployment.
        cat "$ENV_FILE" > "$tmp"
        printf '%s=%s\n' "$k" "$v" >> "$tmp"
    else
        printf '%s=%s\n' "$k" "$v" > "$tmp"
    fi
    chmod --reference="$ENV_FILE" "$tmp" 2>/dev/null || chmod 600 "$tmp"
    mv "$tmp" "$ENV_FILE"
}
