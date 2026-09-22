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
    [[ "$output" == *"set"* && "$output" == *"hidden"* ]]
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
