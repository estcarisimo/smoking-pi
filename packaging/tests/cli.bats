#!/usr/bin/env bats
# The smoking-pi command against a stubbed docker: what it runs, in what
# order, with which files -- never the real daemon. The stub records every
# invocation in $DOCKER_LOG and answers the few queries the command makes
# (`compose config --format json`, `--services`, `ps`). Run from the repo:
#   bats packaging/tests/cli.bats
# CI runs it; the real-daemon proof is the deploy on the reference Pi.

setup() {
    REPO="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
    CLI="$REPO/packaging/smoking-pi"
    export DOCKER_LOG="$BATS_TEST_TMPDIR/docker.log"
    export STUB_HOME="$BATS_TEST_TMPDIR/home"
    # A minimal tree: the command only needs the edition directories, the
    # scripts it calls, and CITATION.cff for `version`.
    mkdir -p "$STUB_HOME/editions/pro" "$STUB_HOME/editions/basic" "$STUB_HOME/shared/modules/doctor"
    cp "$REPO/editions/pro/docker-compose.yml" "$REPO/editions/pro/docker-compose.clickhouse.yml" \
       "$REPO/editions/pro/docker-compose.packaged.yml" "$STUB_HOME/editions/pro/"
    printf '#!/bin/sh\necho SETUP "$@" >> "%s"\nprintf "COMPOSE_PROFILES=%%s\\n" "${2:-influxdb}" > "$SMOKING_PI_ENV_FILE"\n' "$DOCKER_LOG" > "$STUB_HOME/editions/pro/setup.sh"
    printf '#!/bin/sh\necho PASSWORDS >> "%s"\n' "$DOCKER_LOG" > "$STUB_HOME/editions/pro/show-passwords.sh"
    chmod +x "$STUB_HOME/editions/pro/"*.sh
    printf 'version: 9.9.9\n' > "$STUB_HOME/CITATION.cff"
    export SMOKING_PI_HOME="$STUB_HOME"
    export SMOKING_PI_ENV_FILE="$BATS_TEST_TMPDIR/env"
    printf 'COMPOSE_PROFILES=influxdb,mcp\nPOSTGRES_USER=smokeping\n' > "$SMOKING_PI_ENV_FILE"
    unset SMOKING_PI_PACKAGED SMOKING_PI_VERSION SMOKING_PI_CONFIG_DIR SMOKING_PI_OUTPUT_DIR SMOKING_PI_EDITION

    # The stub. Anything not answered here is only logged.
    mkdir -p "$BATS_TEST_TMPDIR/bin"
    cat > "$BATS_TEST_TMPDIR/bin/docker" <<'STUB'
#!/bin/sh
echo "docker $*" >> "$DOCKER_LOG"
case "$*" in
    *"config --format json"*)
        echo '{"name":"pro","services":{"postgres":{"volumes":[{"type":"volume","source":"postgres-data"}]},"grafana":{"volumes":[{"type":"volume","source":"grafana-data"},{"type":"bind","source":"/etc/localtime"}]},"clickhouse":{"volumes":[]}},"volumes":{"postgres-data":{},"grafana-data":{},"clickhouse-data":{"name":"smokeping-pro-clickhouse-data"}}}' ;;
    *"config --services"*) printf 'postgres\ngrafana\nsmokeping\n' ;;
    *"ps --status running --services"*) printf 'postgres\n' ;;
    *"exec -T postgres pg_dumpall"*) echo "-- dump" ;;
    *"system df -v"*) printf 'VOLUME NAME LINKS SIZE\npro_postgres-data 1 48MB\npro_grafana-data 1 240MB\n' ;;
    *"run --rm -v "*)
        # backup's tar into /to: create the file the command reports on.
        for a in "$@"; do case "$a" in /to/*.tgz) ;; esac; done
        out=$(echo "$*" | sed -n 's/.*tar czf \(\/to\/[^ ]*\).*/\1/p')
        [ -n "$out" ] && touch "$BACKUP_VOLUMES_DIR/$(basename "$out")" ;;
esac
exit 0
STUB
    chmod +x "$BATS_TEST_TMPDIR/bin/docker"
    export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
    # No whiptail: install must take the flag path.
    export -n WHIPTAIL 2>/dev/null || true
    mkdir -p "$BATS_TEST_TMPDIR/nowhiptail"
    export PATH="$BATS_TEST_TMPDIR/nowhiptail:$PATH"
}

compose_calls() { grep '^docker compose' "$DOCKER_LOG"; }

@test "help prints usage, exits 0, and no stray command runs (an unquoted heredoc once ran 'dev')" {
    run "$CLI" help
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage: smoking-pi <command>"* ]]
    [[ "$output" != *"not found"* ]]
}

@test "unknown command exits 2" {
    run "$CLI" frobnicate
    [ "$status" -eq 2 ]
}

@test "up passes the env file, the recorded profiles, and no overlays by default" {
    run "$CLI" up
    [ "$status" -eq 0 ]
    run compose_calls
    [[ "$output" == *"--env-file $SMOKING_PI_ENV_FILE -f docker-compose.yml up -d"* ]]
    [[ "$output" != *"clickhouse"* ]]
    [[ "$output" != *"packaged"* ]]
}

@test "the clickhouse profile adds its overlay; packaged mode adds its override last" {
    printf 'COMPOSE_PROFILES=clickhouse\n' > "$SMOKING_PI_ENV_FILE"
    SMOKING_PI_PACKAGED=1 run "$CLI" up
    [ "$status" -eq 0 ]
    run compose_calls
    [[ "$output" == *"-f docker-compose.yml -f docker-compose.clickhouse.yml -f docker-compose.packaged.yml up -d"* ]]
}

@test "paths reports development mode and the never-published dev image tag" {
    run "$CLI" paths
    [ "$status" -eq 0 ]
    [[ "$output" == *"mode:     development"* ]]
    [[ "$output" == *"<service>:dev (dev: built from home, never pulled)"* ]]
}

@test "paths reports packaged mode and the pinned version" {
    SMOKING_PI_PACKAGED=1 SMOKING_PI_VERSION=2.12.0 run "$CLI" paths
    [[ "$output" == *"mode:     packaged"* ]]
    [[ "$output" == *"ghcr.io/estcarisimo/smoking-pi/<service>:2.12.0"* ]]
}

@test "version comes from CITATION.cff" {
    run "$CLI" version
    [ "$output" = "9.9.9" ]
}

@test "install refuses to run over an existing env file (setup.sh would rotate the secrets)" {
    run "$CLI" install --yes
    [ "$status" -eq 1 ]
    [[ "$output" == *"already installed"* ]]
    ! grep -q SETUP "$DOCKER_LOG"
}

@test "install validates edition, database and profiles before doing anything" {
    rm -f "$SMOKING_PI_ENV_FILE"
    run "$CLI" install --yes --edition nope
    [ "$status" -eq 2 ]
    run "$CLI" install --yes --database mysql
    [ "$status" -eq 2 ]
    run "$CLI" install --yes --profiles mcp,bogus
    [ "$status" -eq 2 ]
    [[ "$output" == *"unknown profile: bogus"* ]]
    [ ! -f "$DOCKER_LOG" ]
}

@test "install --profiles appends the optional profiles to what setup.sh recorded and starts them" {
    rm -f "$SMOKING_PI_ENV_FILE"
    run "$CLI" install --yes --database influxdb --profiles mcp,alerts
    [ "$status" -eq 0 ]
    grep -q 'SETUP --database influxdb --env-file' "$DOCKER_LOG"
    grep -qx 'COMPOSE_PROFILES=influxdb,mcp,alerts' "$SMOKING_PI_ENV_FILE"
    [[ "$output" == *"alerts profile needs NOTIFY_MODE"* ]]
    run compose_calls
    [[ "$output" == *" up -d"* ]]
    grep -q PASSWORDS "$DOCKER_LOG"
}

@test "upgrade from a clone rebuilds with fresh base images, then recreates what changed" {
    run "$CLI" upgrade --skip-doctor
    [ "$status" -eq 0 ]
    run compose_calls
    [[ "${lines[0]}" == *" build --pull" ]]
    [[ "${lines[1]}" == *" up -d --remove-orphans" ]]
}

@test "upgrade with a pinned version pulls the release instead of building" {
    SMOKING_PI_VERSION=2.12.0 run "$CLI" upgrade --skip-doctor
    [ "$status" -eq 0 ]
    run compose_calls
    [[ "${lines[0]}" == *" pull" ]]
    [[ "$output" != *"build"* ]]
}

@test "upgrade refuses without an env file" {
    rm -f "$SMOKING_PI_ENV_FILE"
    run "$CLI" upgrade
    [ "$status" -eq 1 ]
    [[ "$output" == *"nothing installed to upgrade"* ]]
}

@test "backup dumps postgres, stops the stack, tars only the volumes the active services mount, restarts" {
    export BACKUP_VOLUMES_DIR="$BATS_TEST_TMPDIR/bk/volumes"
    mkdir -p "$BACKUP_VOLUMES_DIR"
    run "$CLI" backup "$BATS_TEST_TMPDIR/bk"
    [ "$status" -eq 0 ]
    [ -f "$BATS_TEST_TMPDIR/bk/postgres.sql" ]
    [ -f "$BATS_TEST_TMPDIR/bk/env" ]
    grep -qx 'edition=pro' "$BATS_TEST_TMPDIR/bk/manifest"
    # Order: dump (running), down, tars, up.
    run grep -n -E 'pg_dumpall| down$|tar czf| up -d$' "$DOCKER_LOG"
    [[ "${lines[0]}" == *pg_dumpall* ]]
    [[ "${lines[1]}" == *" down" ]]
    [[ "${lines[2]}" == *"pro_postgres-data:/from:ro"* ]]
    [[ "${lines[3]}" == *"pro_grafana-data:/from:ro"* ]]
    [[ "${lines[4]}" == *" up -d" ]]
    # The ClickHouse volume is declared in the config but mounted by no
    # active service: not copied (it cost ten minutes of downtime once).
    ! grep -q 'clickhouse-data' "$DOCKER_LOG"
    [ "$(stat -c %a "$BATS_TEST_TMPDIR/bk")" = 700 ]
}

@test "backup --online never stops the stack" {
    export BACKUP_VOLUMES_DIR="$BATS_TEST_TMPDIR/bk/volumes"
    mkdir -p "$BACKUP_VOLUMES_DIR"
    run "$CLI" backup "$BATS_TEST_TMPDIR/bk" --online
    [ "$status" -eq 0 ]
    ! grep -q ' down$' "$DOCKER_LOG"
    ! grep -q ' up -d$' "$DOCKER_LOG"
}

@test "restore refuses a backup of another edition and a directory without a manifest" {
    mkdir -p "$BATS_TEST_TMPDIR/bk"
    run "$CLI" restore "$BATS_TEST_TMPDIR/bk"
    [ "$status" -eq 2 ]
    printf 'edition=basic\nproject=basic\n' > "$BATS_TEST_TMPDIR/bk/manifest"
    run "$CLI" restore "$BATS_TEST_TMPDIR/bk"
    [ "$status" -eq 1 ]
    [[ "$output" == *"backup is of the basic edition"* ]]
}

@test "restore keeps an existing env file unless --force, refills volumes under this project's name, starts" {
    mkdir -p "$BATS_TEST_TMPDIR/bk/volumes"
    printf 'edition=pro\nproject=other\n' > "$BATS_TEST_TMPDIR/bk/manifest"
    printf 'COMPOSE_PROFILES=influxdb\nFROM=backup\n' > "$BATS_TEST_TMPDIR/bk/env"
    touch "$BATS_TEST_TMPDIR/bk/volumes/other_postgres-data.tgz"
    run "$CLI" restore "$BATS_TEST_TMPDIR/bk"
    [ "$status" -eq 0 ]
    [[ "$output" == *"keeping the existing"* ]]
    ! grep -q FROM=backup "$SMOKING_PI_ENV_FILE"
    grep -q 'volume create --label com.docker.compose.project=pro --label com.docker.compose.volume=postgres-data pro_postgres-data' "$DOCKER_LOG"
    grep -q 'find /to -mindepth 1 -delete && tar xzf /from.tgz -C /to' "$DOCKER_LOG"
    run grep -n -E ' down$| up -d$' "$DOCKER_LOG"
    [[ "${lines[0]}" == *" down" ]]
    [[ "${lines[1]}" == *" up -d" ]]
    run "$CLI" restore "$BATS_TEST_TMPDIR/bk" --force --no-start
    grep -q FROM=backup "$SMOKING_PI_ENV_FILE"
    [[ "$output" == *"the stack is stopped"* ]]
}

@test "purge asks for the project name and aborts on anything else" {
    run bash -c "echo nope | '$CLI' purge"
    [ "$status" -eq 1 ]
    [[ "$output" == *"aborted."* ]]
    ! grep -q 'volume rm' "$DOCKER_LOG"
    [ -f "$SMOKING_PI_ENV_FILE" ]
}

@test "purge with the project name typed removes the active volumes and keeps the env file" {
    run bash -c "echo pro | '$CLI' purge"
    [ "$status" -eq 0 ]
    grep -q 'volume rm pro_postgres-data pro_grafana-data' "$DOCKER_LOG"
    ! grep -q 'clickhouse' "$DOCKER_LOG"
    [ -f "$SMOKING_PI_ENV_FILE" ]
}

@test "purge --config --yes also removes the env file and recreates empty config/output dirs" {
    export SMOKING_PI_CONFIG_DIR="$BATS_TEST_TMPDIR/cfg" SMOKING_PI_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
    mkdir -p "$SMOKING_PI_CONFIG_DIR" && touch "$SMOKING_PI_CONFIG_DIR/targets.yaml"
    run "$CLI" purge --config --yes
    [ "$status" -eq 0 ]
    [ ! -f "$SMOKING_PI_ENV_FILE" ]
    [ -d "$SMOKING_PI_CONFIG_DIR" ] && [ ! -e "$SMOKING_PI_CONFIG_DIR/targets.yaml" ]
    [ -d "$SMOKING_PI_OUTPUT_DIR" ]
}
