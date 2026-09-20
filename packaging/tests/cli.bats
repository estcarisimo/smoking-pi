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
    cp "$STUB_HOME/editions/pro/setup.sh" "$STUB_HOME/editions/pro/show-passwords.sh" "$STUB_HOME/editions/basic/"
    chmod +x "$STUB_HOME/editions/"*/*.sh
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
        # Three shapes: a default-named volume (pro_postgres-data), one
        # declared with a fixed name and mounted by an active service
        # (smokeping-config -> smokeping-pro-config, the Basic/Standard
        # pattern), and one with a fixed name mounted by NO active
        # service (clickhouse-data), which must never be touched.
        echo '{"name":"pro","services":{"postgres":{"volumes":[{"type":"volume","source":"postgres-data"}]},"grafana":{"volumes":[{"type":"volume","source":"grafana-data"},{"type":"bind","source":"/etc/localtime"}]},"smokeping":{"volumes":[{"type":"volume","source":"smokeping-config"}]},"web-admin":{"volumes":null}},"volumes":{"postgres-data":{},"grafana-data":{},"smokeping-config":{"name":"smokeping-pro-config"},"clickhouse-data":{"name":"smokeping-pro-clickhouse-data"}}}' ;;
    *"config --services"*) printf 'postgres\ngrafana\nsmokeping\n' ;;
    *"ps --status running --services"*) printf 'postgres\n' ;;
    *"exec -T postgres pg_dumpall"*) echo "-- dump" ;;
    *"system df -v"*) printf 'VOLUME NAME LINKS SIZE\npro_postgres-data 1 48MB\npro_grafana-data 1 240MB\n' ;;
    *"tar czf /to/"*)
        # backup's tar: create the file where the host bind of /to says,
        # so the command's own choice of directory is what gets checked.
        host=$(echo "$*" | sed -n 's/.* -v \([^ ]*\):\/to .*/\1/p')
        out=$(echo "$*" | sed -n 's/.*tar czf \/to\/\([^ ]*\).*/\1/p')
        touch "$host/$out" ;;
esac
exit 0
STUB
    chmod +x "$BATS_TEST_TMPDIR/bin/docker"
    export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
}

compose_calls() { grep '^docker compose' "$DOCKER_LOG"; }

fail_docker_on() {
    # Make the stub exit 1 for any invocation whose arguments contain $1.
    sed -i "s|^case \"\$\*\" in|case \"\$*\" in\n    *\"$1\"*) exit 1 ;;|" "$BATS_TEST_TMPDIR/bin/docker"
}

@test "help prints usage, exits 0, and no stray command runs (an unquoted heredoc once ran 'dev')" {
    run "$CLI" help
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage: smoking-pi <command>"* ]]
    [[ "$output" != *"not found"* ]]
}

@test "installed outside a checkout, home defaults to /opt/smoking-pi; inside one, to the checkout" {
    mkdir -p "$BATS_TEST_TMPDIR/usr/bin"
    cp "$CLI" "$BATS_TEST_TMPDIR/usr/bin/smoking-pi"
    unset SMOKING_PI_HOME
    run "$BATS_TEST_TMPDIR/usr/bin/smoking-pi" paths
    [[ "$output" == *"home:     /opt/smoking-pi"* ]]
    run "$CLI" paths
    [[ "$output" == *"home:     $REPO"* ]]
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
    [ "$status" -eq 0 ]
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
    run "$CLI" install --yes --edition basic --profiles mcp
    [ "$status" -eq 0 ]
    [[ "$output" == *"--profiles applies to the pro edition only"* ]]
    ! grep -q ' up -d' "$DOCKER_LOG"
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

@test "backup dumps postgres, stops the stack, tars the volumes the active services mount by key, restarts" {
    run "$CLI" backup "$BATS_TEST_TMPDIR/bk"
    [ "$status" -eq 0 ]
    [ -f "$BATS_TEST_TMPDIR/bk/postgres.sql" ]
    [ -f "$BATS_TEST_TMPDIR/bk/env" ]
    grep -qx 'edition=pro' "$BATS_TEST_TMPDIR/bk/manifest"
    # Tarballs are named by the Compose key; the manifest records the
    # Docker name each came from -- including a fixed `name:`.
    [ -f "$BATS_TEST_TMPDIR/bk/volumes/postgres-data.tgz" ]
    [ -f "$BATS_TEST_TMPDIR/bk/volumes/smokeping-config.tgz" ]
    grep -qx 'volume=postgres-data=pro_postgres-data' "$BATS_TEST_TMPDIR/bk/manifest"
    grep -qx 'volume=smokeping-config=smokeping-pro-config' "$BATS_TEST_TMPDIR/bk/manifest"
    # Order: dump (running), down, tars, up.
    run grep -n -E 'pg_dumpall| down$|tar czf| up -d$' "$DOCKER_LOG"
    [[ "${lines[0]}" == *pg_dumpall* ]]
    [[ "${lines[1]}" == *" down" ]]
    [[ "${lines[2]}" == *"pro_postgres-data:/from:ro"* ]]
    [[ "${lines[3]}" == *"pro_grafana-data:/from:ro"* ]]
    [[ "${lines[4]}" == *"smokeping-pro-config:/from:ro"* ]]
    [[ "${lines[5]}" == *" up -d" ]]
    # The ClickHouse volume is declared in the config but mounted by no
    # active service: not copied (it cost ten minutes of downtime once).
    ! grep -q 'clickhouse' "$DOCKER_LOG"
    [ "$(stat -c %a "$BATS_TEST_TMPDIR/bk")" = 700 ]
}

@test "backup --online never stops the stack and says so in the manifest" {
    run "$CLI" backup "$BATS_TEST_TMPDIR/bk" --online
    [ "$status" -eq 0 ]
    ! grep -q ' down$' "$DOCKER_LOG"
    ! grep -q ' up -d$' "$DOCKER_LOG"
    grep -qx 'online=1' "$BATS_TEST_TMPDIR/bk/manifest"
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

make_backup_dir() {
    # A backup taken under another project name, with a fixed-name volume
    # and a key this stack does not mount (clickhouse-data).
    mkdir -p "$BATS_TEST_TMPDIR/bk/volumes"
    printf 'edition=pro\nproject=other\nonline=%s\nvolume=postgres-data=other_postgres-data\nvolume=smokeping-config=smokeping-other-config\n' "${1:-0}" > "$BATS_TEST_TMPDIR/bk/manifest"
    printf 'COMPOSE_PROFILES=influxdb\nFROM=backup\n' > "$BATS_TEST_TMPDIR/bk/env"
    touch "$BATS_TEST_TMPDIR/bk/volumes/postgres-data.tgz" "$BATS_TEST_TMPDIR/bk/volumes/smokeping-config.tgz" "$BATS_TEST_TMPDIR/bk/volumes/clickhouse-data.tgz"
}

@test "restore resolves each key to the name THIS stack mounts (fixed name too), skips keys it does not mount, and asks first" {
    make_backup_dir
    run bash -c "echo nope | '$CLI' restore '$BATS_TEST_TMPDIR/bk'"
    [ "$status" -eq 1 ]
    [[ "$output" == *"pro_postgres-data  <- volumes/postgres-data.tgz"* ]]
    [[ "$output" == *"smokeping-pro-config  <- volumes/smokeping-config.tgz"* ]]
    [[ "$output" == *"Skipped (no active service mounts them here): clickhouse-data"* ]]
    [[ "$output" == *"aborted."* ]]
    ! grep -q ' down$' "$DOCKER_LOG"
    ! grep -q 'volume create' "$DOCKER_LOG"
    run bash -c "echo pro | '$CLI' restore '$BATS_TEST_TMPDIR/bk'"
    [ "$status" -eq 0 ]
    [[ "$output" == *"keeping the existing"* ]]
    ! grep -q FROM=backup "$SMOKING_PI_ENV_FILE"
    grep -q 'volume create --label com.docker.compose.project=pro --label com.docker.compose.volume=postgres-data pro_postgres-data' "$DOCKER_LOG"
    grep -q 'volume create --label com.docker.compose.project=pro --label com.docker.compose.volume=smokeping-config smokeping-pro-config' "$DOCKER_LOG"
    ! grep -q 'clickhouse' "$DOCKER_LOG"
    # Empty first, extract second, each its own run; the tarball is the
    # key's, the target the resolved name's.
    grep -q -- '-v smokeping-pro-config:/to alpine:3.20 sh -c find /to -mindepth 1 -delete' "$DOCKER_LOG"
    grep -q -- "-v smokeping-pro-config:/to -v $BATS_TEST_TMPDIR/bk/volumes/smokeping-config.tgz:/from.tgz:ro alpine:3.20 tar xzf /from.tgz -C /to" "$DOCKER_LOG"
    run grep -n -E ' down$| up -d$' "$DOCKER_LOG"
    [[ "${lines[0]}" == *" down" ]]
    [[ "${lines[1]}" == *" up -d" ]]
}

@test "restore --force --no-start --yes overwrites the env file, warns about an --online backup, leaves the stack stopped" {
    make_backup_dir 1
    run "$CLI" restore "$BATS_TEST_TMPDIR/bk" --force --no-start --yes
    [ "$status" -eq 0 ]
    grep -q FROM=backup "$SMOKING_PI_ENV_FILE"
    [[ "$output" == *"WARNING: this backup was taken --online"* ]]
    [[ "$output" == *"the stack is stopped"* ]]
    ! grep -q ' up -d$' "$DOCKER_LOG"
}

@test "restore reports a volume that failed to extract, goes on with the rest, leaves the stack stopped, exits 1" {
    make_backup_dir
    fail_docker_on 'smokeping-config.tgz:/from.tgz:ro'
    run "$CLI" restore "$BATS_TEST_TMPDIR/bk" --yes
    [ "$status" -eq 1 ]
    [[ "$output" == *"smokeping-pro-config: emptied but the tarball did not extract"* ]]
    [[ "$output" == *"volume pro_postgres-data restored"* ]]
    [[ "$output" == *"restore INCOMPLETE; the stack is stopped. Failed: smokeping-pro-config"* ]]
    ! grep -q ' up -d$' "$DOCKER_LOG"
}

@test "purge asks for the project name and aborts on anything else" {
    run bash -c "echo nope | '$CLI' purge"
    [ "$status" -eq 1 ]
    [[ "$output" == *"aborted."* ]]
    ! grep -q 'volume rm' "$DOCKER_LOG"
    [ -f "$SMOKING_PI_ENV_FILE" ]
}

@test "purge with the project name typed removes the active volumes (fixed names too) and keeps the env file" {
    run bash -c "echo pro | '$CLI' purge"
    [ "$status" -eq 0 ]
    grep -q 'volume rm pro_postgres-data pro_grafana-data smokeping-pro-config' "$DOCKER_LOG"
    ! grep -q 'clickhouse' "$DOCKER_LOG"
    [ -f "$SMOKING_PI_ENV_FILE" ]
}

@test "purge --config --yes also removes the env file and recreates empty config/output dirs, even if a volume rm fails" {
    export SMOKING_PI_CONFIG_DIR="$BATS_TEST_TMPDIR/cfg" SMOKING_PI_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
    mkdir -p "$SMOKING_PI_CONFIG_DIR" && touch "$SMOKING_PI_CONFIG_DIR/targets.yaml"
    fail_docker_on 'volume rm'
    run "$CLI" purge --config --yes
    [ "$status" -eq 0 ]
    [[ "$output" == *"some volumes could not be removed"* ]]
    [ ! -f "$SMOKING_PI_ENV_FILE" ]
    [ -d "$SMOKING_PI_CONFIG_DIR" ] && [ ! -e "$SMOKING_PI_CONFIG_DIR/targets.yaml" ]
    [ -d "$SMOKING_PI_OUTPUT_DIR" ]
}
