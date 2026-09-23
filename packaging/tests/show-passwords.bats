#!/usr/bin/env bats
# show-passwords.sh against a stubbed docker and curl: what it prints, what
# it withholds, and what it refuses. The roadmap's rule is that secrets are
# shown "solo a peticion explicita" -- these tests are that rule.
#   bats packaging/tests/show-passwords.bats

setup() {
    REPO="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
    SCRIPT="$REPO/shared/scripts/show-passwords.sh"
    EDITION_DIR="$BATS_TEST_TMPDIR/pro"
    mkdir -p "$EDITION_DIR"

    # detect_edition() reads this file's contents, not the daemon.
    cat > "$EDITION_DIR/docker-compose.yml" <<'EOF'
services:
  smokeping: {image: busybox}
  postgres: {image: busybox}
  config-manager: {image: busybox}
  web-admin: {image: busybox}
  grafana: {image: busybox}
  influxdb: {image: busybox}
EOF

    export SMOKING_PI_ENV_FILE="$BATS_TEST_TMPDIR/env"
    cat > "$SMOKING_PI_ENV_FILE" <<'EOF'
GF_SECURITY_ADMIN_PASSWORD=SENTINEL-grafana
CONFIG_API_TOKEN=SENTINEL-config
MCP_API_TOKEN=SENTINEL-mcp
INFLUX_ORG=org
INFLUX_BUCKET=bucket
DOCKER_INFLUXDB_INIT_PASSWORD=SENTINEL-influxpw
INFLUX_TOKEN=SENTINEL-influxtoken
POSTGRES_PASSWORD=SENTINEL-pg
DATABASE_URL=postgresql://smokeping:SENTINEL-pg@postgres:5432/smokeping_targets
WEB_ADMIN_PASSWORD=SENTINEL-web
SECRET_KEY=SENTINEL-key
CLICKHOUSE_PASSWORD=SENTINEL-ch
TZ=UTC
EOF
    chmod 600 "$SMOKING_PI_ENV_FILE"

    # No daemon, no network: every probe fails, which is fine -- this suite
    # is about the secrets, not the health checks.
    mkdir -p "$BATS_TEST_TMPDIR/bin"
    printf '#!/bin/sh\nexit 1\n' > "$BATS_TEST_TMPDIR/bin/docker"
    printf '#!/bin/sh\nexit 1\n' > "$BATS_TEST_TMPDIR/bin/curl"
    chmod +x "$BATS_TEST_TMPDIR/bin/"*
    export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
}

# `run` inside a subshell would strand $status and $output there.
run_sp() { cd "$EDITION_DIR" || return 1; run bash "$SCRIPT" "$@"; }

# bats captures stdout through a pipe, so every `run` here is already the
# not-a-terminal case -- which is the case that matters most.
@test "by default no secret value is printed, but each is reported as set" {
    run_sp
    [ "$status" -eq 0 ]
    refute_sentinels
    plain=$(printf '%s' "$output" | sed 's/\x1b\[[0-9;]*m//g')
    # One per secret the Pro/InfluxDB path reaches, by its own label.
    for label in "Password:     set (hidden)" \
                 "config-manager: set (hidden)" \
                 "MCP server:     set (hidden)" \
                 "Admin Pass:   set (hidden)" \
                 "API Token:    set (hidden)" \
                 "Secret Key:   set (hidden)" \
                 "URL:          set (hidden)"; do
        [[ "$plain" == *"$label"* ]] || { echo "not masked: $label"; echo "$plain"; return 1; }
    done
    # Grafana, PostgreSQL and web-admin each print "Password:" -- three of them.
    [ "$(grep -c 'Password:     set (hidden)' <<<"$plain")" -eq 3 ]
}

@test "the clickhouse backend hides its password too, and its auth check is gated" {
    printf 'TSDB_TYPE=clickhouse\n' >> "$SMOKING_PI_ENV_FILE"
    run_sp
    [ "$status" -eq 0 ]
    refute_sentinels
    plain=$(printf '%s' "$output" | sed 's/\x1b\[[0-9;]*m//g')
    [[ "$plain" == *"ClickHouse Database"* ]]
    [[ "$plain" == *"Password:     set (hidden)"* ]]
    run_sp --show-secrets --force
    [[ "$output" == *"SENTINEL-ch"* ]]
}

# Taking the credential off argv is only half of it: the first attempt at
# this used curl's UNQUOTED config syntax, where `header = A: B` parses as
# a key/value line and the header is dropped silently -- the check then
# reports an auth failure forever. So assert the credential actually
# ARRIVES, not merely that it is absent from the command line.
@test "the credential reaches curl on stdin, correctly quoted, and is honored" {
    cat > "$BATS_TEST_TMPDIR/bin/curl" <<'STUB'
#!/bin/sh
# Answer only if the config on stdin really carries our token.
for a in "$@"; do [ "$a" = "-K" ] && want=1; done
[ -n "$want" ] || exit 0
cfg=$(cat)
printf '%s\n' "$cfg" >> "$CURL_STDIN_LOG"
case "$cfg" in
    *'header = "Authorization: Token SENTINEL-influxtoken"'*) exit 0 ;;
    *) exit 22 ;;
esac
STUB
    chmod +x "$BATS_TEST_TMPDIR/bin/curl"
    export CURL_STDIN_LOG="$BATS_TEST_TMPDIR/curl-stdin.log"
    run_sp
    [[ "$output" == *"InfluxDB token is valid"* ]] || {
        echo "the check did not authenticate; curl saw:"; cat "$CURL_STDIN_LOG"; return 1; }
}

# A token whose characters mean something to curl's config parser must
# survive it: an unescaped quote would truncate the value.
@test "a quote or a backslash in a secret survives the config parser" {
    # Single-quoted in the env file: bash cannot source an unbalanced
    # double quote, which is a limit of the file format, not of curl.
    sed -i "s|^INFLUX_TOKEN=.*|INFLUX_TOKEN='SENTINEL-aw\"kward\\\\token'|" "$SMOKING_PI_ENV_FILE"
    cat > "$BATS_TEST_TMPDIR/bin/curl" <<'STUB'
#!/bin/sh
for a in "$@"; do [ "$a" = "-K" ] && want=1; done
[ -n "$want" ] || exit 0
cat > "$CURL_STDIN_LOG"
exit 0
STUB
    chmod +x "$BATS_TEST_TMPDIR/bin/curl"
    export CURL_STDIN_LOG="$BATS_TEST_TMPDIR/curl-stdin.log"
    run_sp
    # Escaped on the wire: \" and \\ are what curl decodes back to " and \.
    grep -qF 'header = "Authorization: Token SENTINEL-aw\"kward\\token"' "$CURL_STDIN_LOG" || {
        echo "badly quoted:"; cat "$CURL_STDIN_LOG"; return 1; }
}

# A credential passed as a command-line argument is readable by every
# account on the host (ps, /proc/*/cmdline) no matter what stdout does.
# The two health checks take theirs on stdin instead.
@test "no secret is passed to a health check on the command line" {
    cat > "$BATS_TEST_TMPDIR/bin/curl" <<'STUB'
#!/bin/sh
echo "curl $*" >> "$CURL_LOG"
exit 1
STUB
    chmod +x "$BATS_TEST_TMPDIR/bin/curl"
    export CURL_LOG="$BATS_TEST_TMPDIR/curl.log"
    printf 'TSDB_TYPE=clickhouse\n' >> "$SMOKING_PI_ENV_FILE"
    run_sp --show-secrets --force
    ! grep -q 'SENTINEL-' "$CURL_LOG" || { echo "a secret reached argv:"; cat "$CURL_LOG"; return 1; }
    sed -i 's/TSDB_TYPE=clickhouse//' "$SMOKING_PI_ENV_FILE"
    : > "$CURL_LOG"
    run_sp --show-secrets --force
    ! grep -q 'SENTINEL-' "$CURL_LOG" || { echo "a secret reached argv:"; cat "$CURL_LOG"; return 1; }
}

@test "--show-secrets alone refuses when stdout is not a terminal, and prints nothing" {
    run_sp --show-secrets
    [ "$status" -eq 3 ]
    refute_sentinels
    [[ "$output" == *"not a terminal"* ]]
    [[ "$output" == *"--force"* ]]
}

@test "--show-secrets --force prints every secret" {
    run_sp --show-secrets --force
    [ "$status" -eq 0 ]
    for s in grafana config mcp influxpw influxtoken pg web key; do
        [[ "$output" == *"SENTINEL-$s"* ]] || { echo "missing SENTINEL-$s"; return 1; }
    done
}

@test "-s is --show-secrets and is refused the same way" {
    run_sp -s
    [ "$status" -eq 3 ]
    refute_sentinels
}

@test "an unset secret is still reported: withholding it would hide an open endpoint" {
    printf 'TZ=UTC\n' > "$SMOKING_PI_ENV_FILE"
    run_sp
    [ "$status" -eq 0 ]
    [[ "$output" == *"the API is unauthenticated"* ]]
    [[ "$output" == *"the MCP endpoint is unauthenticated"* ]]
}

@test "an env file readable beyond its owner is called out; a 600 one is not" {
    chmod 644 "$SMOKING_PI_ENV_FILE"
    run_sp
    [[ "$output" == *"readable beyond its owner"* ]]
    chmod 600 "$SMOKING_PI_ENV_FILE"
    run_sp
    [[ "$output" != *"readable beyond its owner"* ]]
}

@test "--help exits 0 and an unknown flag exits 2, neither reading the env file" {
    run_sp --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"--show-secrets"* ]]
    refute_sentinels
    run_sp --nope
    [ "$status" -eq 2 ]
    refute_sentinels
}

# The names this script printed for years belonged to a Compose project that
# stopped existing when the editions split: `grafana-influx_*` volumes and
# `grafana-influx_postgres_1`. The advice built on them was worse than
# useless -- it told people to delete volumes that were not theirs.
@test "no output names the long-dead grafana-influx project, or a script that is not there" {
    run_sp --show-secrets --force
    [[ "$output" != *"grafana-influx"* ]]
    [[ "$output" != *"verify-influxdb.sh"* ]]
}

refute_sentinels() {
    [[ "$output" != *"SENTINEL-"* ]] || { echo "a secret leaked:"; echo "$output"; return 1; }
}
