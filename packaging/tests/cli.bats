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
    printf '#!/bin/sh\necho PASSWORDS "$@" >> "%s"\n' "$DOCKER_LOG" > "$STUB_HOME/editions/pro/show-passwords.sh"
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
    # The project's containers, "service name" per line (STUB_CONTAINERS,
    # \n-separated; unset = none).
    *"ps -a --filter label=com.docker.compose.project=pro "*) printf '%b' "${STUB_CONTAINERS:-}" ;;
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
    # `url` (and the end of `install`) asks the routing table where this
    # host is and whether HTTP answers there. Stubbed so no test depends on
    # the machine's network, and `install`'s --wait never really sleeps.
    # STUB_V4: the default route's source ("" = no v4 route); STUB_HTTP:
    # the status curl reports (000 = nothing listening); STUB_WHO: `who -m`.
    export STUB_V4=192.0.2.10 STUB_HTTP=302 STUB_WHO=""
    unset SSH_CONNECTION SSH_CLIENT
    cat > "$BATS_TEST_TMPDIR/bin/ip" <<'STUB'
#!/bin/sh
case "$*" in
    "-4 route get 1.1.1.1") [ -z "$STUB_V4" ] || echo "1.1.1.1 via 192.0.2.1 dev wlan0 src $STUB_V4 uid 1000" ;;
    "route get "*[a-z]*) echo "Error: any valid prefix is expected rather than \"$3\"." >&2; exit 1 ;;
    "route get "*) echo "$3 dev eth0 src 198.51.100.7 uid 0" ;;
esac
STUB
    printf '#!/bin/sh
echo "curl $*" >> "$DOCKER_LOG"
printf %%s "$STUB_HTTP"
' > "$BATS_TEST_TMPDIR/bin/curl"
    printf '#!/bin/sh
[ -z "$STUB_WHO" ] || echo "pi       pts/0        2026-09-22 10:00 ($STUB_WHO)"
' > "$BATS_TEST_TMPDIR/bin/who"
    printf '#!/bin/sh
exit 1
' > "$BATS_TEST_TMPDIR/bin/systemctl"
    printf '#!/bin/sh
echo "sleep $*" >> "$DOCKER_LOG"
' > "$BATS_TEST_TMPDIR/bin/sleep"
    chmod +x "$BATS_TEST_TMPDIR/bin/"*
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
    [ "$status" -eq 0 ]
    [[ "$output" == *"home:     /opt/smoking-pi"* ]]
    run "$CLI" paths
    [ "$status" -eq 0 ]
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

@test "packaged mode without a pin runs the installed tree's version, so a new package pulls its own release" {
    SMOKING_PI_PACKAGED=1 run "$CLI" paths
    [[ "$output" == *"/<service>:9.9.9"* ]]
    SMOKING_PI_PACKAGED=1 run "$CLI" upgrade --skip-doctor
    [ "$status" -eq 0 ]
    run compose_calls
    [[ "$output" == *" pull"* ]]
    [[ "$output" != *" build"* ]]
    # A clone (not packaged) still builds: dev is never published.
    run "$CLI" paths
    [[ "$output" == *"/<service>:dev"* ]]
}

@test "version comes from CITATION.cff" {
    run "$CLI" version
    [ "$output" = "9.9.9" ]
}

@test "a candidate's VERSION file wins over CITATION.cff, so it pulls the candidate's images" {
    # build.sh writes it for X.Y.Z~rc.N; CITATION.cff already says X.Y.Z,
    # whose images do not exist until the release.
    printf '9.9.9-rc.2\n' > "$STUB_HOME/VERSION"
    run "$CLI" version
    [ "$output" = "9.9.9-rc.2" ]
    SMOKING_PI_PACKAGED=1 run "$CLI" paths
    [[ "$output" == *"/<service>:9.9.9-rc.2"* ]]
    rm "$STUB_HOME/VERSION"
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

@test "packaged install records the edition beside the env file; the conffile is never edited" {
    rm -f "$SMOKING_PI_ENV_FILE"
    export SMOKING_PI_DEFAULTS="$BATS_TEST_TMPDIR/defaults"
    printf '#SMOKING_PI_EDITION=pro\nSMOKING_PI_PACKAGED=1\n' > "$SMOKING_PI_DEFAULTS"
    run "$CLI" install --yes --edition basic
    [ "$status" -eq 0 ]
    [ "$(cat "$BATS_TEST_TMPDIR/edition")" = basic ]
    [ "$(cat "$SMOKING_PI_DEFAULTS")" = $'#SMOKING_PI_EDITION=pro\nSMOKING_PI_PACKAGED=1' ]
    # The next command, with nothing in the environment, is Basic's.
    unset SMOKING_PI_EDITION
    run "$CLI" paths
    [[ "$output" == *"edition:  basic"* ]]
    # The environment (or the conffile) still overrides the record.
    SMOKING_PI_EDITION=pro run "$CLI" paths
    [[ "$output" == *"edition:  pro"* ]]
    # A clone records nothing: its env file lives beside its edition.
    rm -f "$SMOKING_PI_ENV_FILE" "$BATS_TEST_TMPDIR/edition"
    printf '\n' > "$SMOKING_PI_DEFAULTS"
    run "$CLI" install --yes --edition basic
    [ "$status" -eq 0 ]
    [ ! -e "$BATS_TEST_TMPDIR/edition" ]
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

@test "restore onto a fresh packaged card takes the backup's edition and records it; an installed one still refuses" {
    mkdir -p "$BATS_TEST_TMPDIR/bk/volumes"
    printf 'edition=basic\nproject=basic\nonline=0\n' > "$BATS_TEST_TMPDIR/bk/manifest"
    printf 'COMPOSE_PROFILES=\nFROM=backup\n' > "$BATS_TEST_TMPDIR/bk/env"
    touch "$BATS_TEST_TMPDIR/bk/volumes/smokeping-config.tgz"
    export SMOKING_PI_PACKAGED=1
    # Installed Pro (an env file, no recorded edition): a Basic backup is refused.
    run "$CLI" restore "$BATS_TEST_TMPDIR/bk" --yes --no-start
    [ "$status" -eq 1 ]
    [[ "$output" == *"backup is of the basic edition, this is pro"* ]]
    [ ! -f "$BATS_TEST_TMPDIR/edition" ]
    # A fresh card: no env file, no edition recorded.
    rm "$SMOKING_PI_ENV_FILE"
    run "$CLI" restore "$BATS_TEST_TMPDIR/bk" --yes --no-start
    [ "$status" -eq 0 ]
    [[ "$output" == *"restoring as the backup's, basic"* ]]
    [ "$(cat "$BATS_TEST_TMPDIR/edition")" = basic ]
    grep -q FROM=backup "$SMOKING_PI_ENV_FILE"
    # A manifest without an edition adopts nothing and writes nothing.
    rm "$SMOKING_PI_ENV_FILE" "$BATS_TEST_TMPDIR/edition"
    sed -i '/^edition=/d' "$BATS_TEST_TMPDIR/bk/manifest"
    run "$CLI" restore "$BATS_TEST_TMPDIR/bk" --yes --no-start
    [ "$status" -eq 1 ]
    [ ! -f "$BATS_TEST_TMPDIR/edition" ]
    printf 'edition=basic\n' >> "$BATS_TEST_TMPDIR/bk/manifest"
    # A recorded edition is a choice: it is never overwritten.
    rm -f "$SMOKING_PI_ENV_FILE"; echo pro > "$BATS_TEST_TMPDIR/edition"
    run "$CLI" restore "$BATS_TEST_TMPDIR/bk" --yes --no-start
    [ "$status" -eq 1 ]
    [ "$(cat "$BATS_TEST_TMPDIR/edition")" = pro ]
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

@test "passwords forwards its flags, so --show-secrets reaches the script" {
    run "$CLI" passwords --show-secrets --force
    [ "$status" -eq 0 ]
    grep -qx 'PASSWORDS --show-secrets --force' "$DOCKER_LOG"
}

# An install transcript is pasted into issues and photographed. It ends on
# the hidden view, and says in one line where the values are.
@test "install ends on the masked view and points at --show-secrets without passing it" {
    rm -f "$SMOKING_PI_ENV_FILE"
    run "$CLI" install --edition pro --yes
    [ "$status" -eq 0 ]
    grep -qx 'PASSWORDS' "$DOCKER_LOG"
    ! grep -q 'PASSWORDS .*--show-secrets' "$DOCKER_LOG"
    [[ "$output" == *"smoking-pi passwords --show-secrets"* ]]
}

# The URL an install ends on. It used to be http://localhost:8080 -- which,
# to someone who installed over SSH from a laptop, is the laptop.
@test "url, at the machine: the default route's address, both Pro URLs, the usernames" {
    run "$CLI" url
    [ "$status" -eq 0 ]
    [[ "$output" == *"Web admin  http://192.0.2.10:8080/   (user admin)"* ]]
    [[ "$output" == *"Grafana    http://192.0.2.10:3000/   (user admin)"* ]]
    [[ "$output" == *"any computer on the same network"* ]]
    [[ "$output" != *localhost* ]]
    # Asked the address it prints, not loopback: proves the bind is reachable.
    grep -q '^curl .*http://192.0.2.10:8080/' "$DOCKER_LOG"
}

@test "url over SSH: the address the client connected to, named as theirs, with a tunnel fallback" {
    SSH_CONNECTION="203.0.113.5 50000 192.0.2.44 22" run "$CLI" url
    [ "$status" -eq 0 ]
    [[ "$output" == *"http://192.0.2.44:8080/"* ]]
    [[ "$output" == *"connected from (203.0.113.5)"* ]]
    [[ "$output" == *"ssh -L 8080:localhost:8080 "*"@192.0.2.44"* ]]
}

@test "url under sudo, where SSH_CONNECTION is gone: the source address toward the who -m client" {
    STUB_WHO=203.0.113.5 run "$CLI" url
    [ "$status" -eq 0 ]
    [[ "$output" == *"http://198.51.100.7:8080/"* ]]
    [[ "$output" == *"connected from (203.0.113.5)"* ]]
    # A resolved hostname in utmp (UseDNS): not routable by name, so the
    # default route's address -- never localhost.
    STUB_WHO=laptop.lan run "$CLI" url
    [[ "$output" == *"http://192.0.2.10:8080/"* ]]
    # A desktop session reports its display, not a host: not an SSH client.
    STUB_WHO=:0 run "$CLI" url
    [[ "$output" == *"http://192.0.2.10:8080/"* ]]
    [[ "$output" != *"connected from"* ]]
}

# Pro publishes the web admin as 0.0.0.0:8080, v4 only: a v6 URL there got
# no answer on the reference Pi.
@test "url over SSH on IPv6 prints the v4 address; bracketed v6 only when there is no v4 route" {
    SSH_CONNECTION="2001:db8::5 50000 2001:db8::44 22" run "$CLI" url
    [[ "$output" == *"http://192.0.2.10:8080/"* ]]
    STUB_V4="" SSH_CONNECTION="2001:db8::5 50000 2001:db8::44 22" run "$CLI" url
    [[ "$output" == *"http://[2001:db8::44]:8080/"* ]]
    # ssh wants the bare address after the @.
    [[ "$output" == *"@2001:db8::44 "* ]]
}

@test "url exits 1 and says where to look when nothing answers; --wait retries first" {
    STUB_HTTP=000 run "$CLI" url --wait 9
    [ "$status" -eq 1 ]
    [[ "$output" == *"Nothing answers at http://192.0.2.10:8080/"* ]]
    [[ "$output" == *"smoking-pi logs"* ]]
    [ "$(grep -c '^sleep 3' "$DOCKER_LOG")" -eq 3 ]
    run "$CLI" url --wait
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: smoking-pi url"* ]]
    run "$CLI" url --wait soon
    [ "$status" -eq 2 ]
}

@test "url on Basic: SmokePing on the env file's port, :80 left out, no login and no passwords line" {
    printf 'SMOKEPING_PORT=80\n' > "$SMOKING_PI_ENV_FILE"
    SMOKING_PI_EDITION=basic run "$CLI" url
    [ "$status" -eq 0 ]
    [[ "$output" == *"SmokePing  http://192.0.2.10/   (no login)"* ]]
    [[ "$output" != *Grafana* ]]
    [[ "$output" != *"passwords"* ]]
}

@test "install ends on the URL, and a page still starting is not a failed install" {
    rm -f "$SMOKING_PI_ENV_FILE"
    STUB_HTTP=000 run "$CLI" install --edition pro --yes
    [ "$status" -eq 0 ]
    [[ "$output" == *"Open Smoking Pi:"* ]]
    [[ "${lines[-1]}" == *"smoking-pi logs"* ]]
    # It waited before giving up: 120 s in steps of 3.
    [ "$(grep -c '^sleep 3' "$DOCKER_LOG")" -eq 40 ]
}

# --- openclaw ------------------------------------------------------------
# The connector must never leave the stack worse than it found it, and must
# never claim a connection it has not seen evidence of.

# A stubbed gateway plus a stubbed skill installer: the real one writes into
# ~/.openclaw, which a test must not touch.
stub_openclaw() {
    printf '#!/bin/sh\necho "openclaw $*" >> "%s"\nexit %s\n' "$DOCKER_LOG" "${1:-0}" \
        > "$BATS_TEST_TMPDIR/bin/openclaw"
    # Records argv AND the config curl reads on stdin, so a test can tell
    # "the credential is not on the command line" from "the credential
    # never arrived" -- which look identical if you only assert an absence.
    # The config curl reads on stdin is multi-line; flatten it to one log
    # line so a test can assert on it.
    printf '#!/bin/sh\necho "CURL $*" >> "%s"\nif [ "$1" = "-K" ]; then cfg=$(cat | tr "\\n" " "); echo "CURLCFG $cfg" >> "%s"; case "$cfg" in *Authorization*) echo 200 ;; *) echo 401 ;; esac; else echo 401; fi\n' \
        "$DOCKER_LOG" "$DOCKER_LOG" > "$BATS_TEST_TMPDIR/bin/curl"
    mkdir -p "$STUB_HOME/shared/scripts"
    printf '#!/bin/sh\necho "SKILL $*" >> "%s"\n' "$DOCKER_LOG" \
        > "$STUB_HOME/shared/scripts/install-openclaw-skill.sh"
    chmod +x "$BATS_TEST_TMPDIR/bin/openclaw" "$BATS_TEST_TMPDIR/bin/curl" \
             "$STUB_HOME/shared/scripts/install-openclaw-skill.sh"
}

@test "openclaw without a gateway explains both cases and changes nothing" {
    printf 'COMPOSE_PROFILES=influxdb\n' > "$SMOKING_PI_ENV_FILE"
    # A PATH with no openclaw on it at all. Deleting the stub is not enough:
    # the developer's own machine may have the real one installed, and then
    # this test would take the opposite branch and pass only on CI.
    rm -f "$BATS_TEST_TMPDIR/bin/openclaw"
    export PATH="$BATS_TEST_TMPDIR/bin:/usr/bin:/bin"
    run "$CLI" openclaw
    # Not an error: the stack measures without an assistant.
    [ "$status" -eq 0 ]
    [[ "$output" == *"ANOTHER machine"* ]]
    [[ "$output" == *"remote-openclaw.md"* ]]
    [[ "$output" == *"nothing is broken"* ]]
    # No token, no profile change: it did not half-configure anything.
    ! grep -q 'MCP_API_TOKEN' "$SMOKING_PI_ENV_FILE"
    grep -qx 'COMPOSE_PROFILES=influxdb' "$SMOKING_PI_ENV_FILE"
}

@test "openclaw generates the token, records the mcp profile and registers" {
    printf 'COMPOSE_PROFILES=influxdb\nPOSTGRES_USER=smokeping\n' > "$SMOKING_PI_ENV_FILE"
    chmod 600 "$SMOKING_PI_ENV_FILE"
    stub_openclaw
    run "$CLI" openclaw
    [ "$status" -eq 0 ]
    grep -q '^MCP_API_TOKEN=[0-9a-f]\{64\}$' "$SMOKING_PI_ENV_FILE"
    grep -qx 'COMPOSE_PROFILES=influxdb,mcp' "$SMOKING_PI_ENV_FILE"
    # Everything else in the file survived the rewrite, and so did the mode.
    grep -qx 'POSTGRES_USER=smokeping' "$SMOKING_PI_ENV_FILE"
    [ "$(stat -c '%a' "$SMOKING_PI_ENV_FILE")" = 600 ]
    grep -q 'openclaw mcp set smokeping' "$DOCKER_LOG"
    grep -q 'SKILL --reload' "$DOCKER_LOG"
}

@test "openclaw keeps an existing token and does not duplicate the mcp profile" {
    printf 'COMPOSE_PROFILES=influxdb,mcp\nMCP_API_TOKEN=keepme\n' > "$SMOKING_PI_ENV_FILE"
    stub_openclaw
    run "$CLI" openclaw
    [ "$status" -eq 0 ]
    grep -qx 'MCP_API_TOKEN=keepme' "$SMOKING_PI_ENV_FILE"
    grep -qx 'COMPOSE_PROFILES=influxdb,mcp' "$SMOKING_PI_ENV_FILE"
    [[ "$output" == *"already set"* ]]
}

# The trap the whole verification exists for: the agent answers fluently
# from its own shell while the MCP server is never called.
@test "openclaw --check fails when the server logged no tool call" {
    stub_openclaw
    run "$CLI" openclaw --check
    [ "$status" -eq 1 ]
    [[ "$output" == *"NOT connected"* ]]
    [[ "$output" == *"its own shell"* ]]
}

@test "openclaw --check passes only on a tool= line from the server" {
    stub_openclaw
    cat > "$BATS_TEST_TMPDIR/bin/docker" <<'STUB'
#!/bin/sh
echo "docker $*" >> "$DOCKER_LOG"
case "$*" in
    *"logs mcp-server"*) echo "tool=get_latency_stats args=hours=6 -> 19 stats in 40ms" ;;
esac
exit 0
STUB
    chmod +x "$BATS_TEST_TMPDIR/bin/docker"
    run "$CLI" openclaw --check
    [ "$status" -eq 0 ]
    [[ "$output" == *"Connected"* ]]
    [[ "$output" == *"tool=get_latency_stats"* ]]
}

@test "openclaw refuses on an edition that has no MCP server" {
    export SMOKING_PI_EDITION=basic
    run "$CLI" openclaw
    [ "$status" -eq 1 ]
    [[ "$output" == *"Pro service"* ]]
    [[ "$output" == *"without OpenClaw"* ]]
}

# --yes is the scripted path: it must not prompt, and must still say the
# assistant exists.
@test "install --yes names the openclaw command instead of prompting" {
    rm -f "$SMOKING_PI_ENV_FILE"
    run "$CLI" install --edition pro --yes
    [ "$status" -eq 0 ]
    [[ "$output" == *"smoking-pi openclaw"* ]]
}

# PR #96's lesson, restated: a credential on a command line is readable by
# every account on the host, and a test that only checks it is ABSENT
# passes just as happily when the credential never arrived at all.
@test "openclaw keeps the MCP token off curl's command line and still sends it" {
    printf 'COMPOSE_PROFILES=mcp\nMCP_API_TOKEN=tok123deadbeef\n' > "$SMOKING_PI_ENV_FILE"
    stub_openclaw
    run "$CLI" openclaw
    [ "$status" -eq 0 ]
    # Not in argv...
    ! grep -q '^CURL .*tok123deadbeef' "$DOCKER_LOG"
    # ...and curl was driven from stdin, with the header actually present.
    grep -q '^CURL -K -' "$DOCKER_LOG"
    grep -q 'CURLCFG .*Authorization: Bearer tok123deadbeef' "$DOCKER_LOG"
}

# The token is interpolated into the JSON handed to `openclaw mcp set`. A
# generated one is hex; a hand-written one is whatever someone typed.
@test "openclaw refuses a token that would break the registration JSON" {
    printf 'COMPOSE_PROFILES=mcp\nMCP_API_TOKEN=has"quote\n' > "$SMOKING_PI_ENV_FILE"
    stub_openclaw
    run "$CLI" openclaw
    [ "$status" -eq 1 ]
    [[ "$output" == *"cannot be"* ]]
    # It refused before touching the gateway.
    ! grep -q 'openclaw mcp set' "$DOCKER_LOG"
}

# --- config: env-file settings by name ---------------------------------------

config_setup() {
    cp "$REPO/editions/pro/.env.template" "$STUB_HOME/editions/pro/"
    printf 'COMPOSE_PROFILES=influxdb,mcp\nPOSTGRES_PASSWORD=pg-secret\nOPENCLAW_GATEWAY_TOKEN=gw-secret\nWIFI_INTERFACE=\n' > "$SMOKING_PI_ENV_FILE"
}

@test "config list shows declared keys, hides secrets, marks unset ones" {
    config_setup
    run "$CLI" config list
    [ "$status" -eq 0 ]
    [[ "$output" == *"OPENCLAW_GATEWAY_TOKEN"*"(set, hidden)"* ]]
    [[ "$output" == *"WIFI_INTERFACE"*"(unset: the default applies)"* ]]
    [[ "$output" != *"gw-secret"* ]]
    [[ "$output" != *"pg-secret"* ]]
}

@test "config set writes the key and recreates only the enabled services that read it" {
    config_setup
    run "$CLI" config set WIFI_INTERFACE wlan0
    [ "$status" -eq 0 ]
    grep -qx 'WIFI_INTERFACE=wlan0' "$SMOKING_PI_ENV_FILE"
    grep -q 'up -d smokeping$' "$DOCKER_LOG"
    # The other secrets are untouched.
    grep -qx 'POSTGRES_PASSWORD=pg-secret' "$SMOKING_PI_ENV_FILE"
}

@test "config set never starts a service whose profile is off" {
    config_setup
    run "$CLI" config set NOTIFY_MODE openclaw
    [ "$status" -eq 0 ]
    grep -qx 'NOTIFY_MODE=openclaw' "$SMOKING_PI_ENV_FILE"
    [[ "$output" == *"alerter"*"which no enabled profile runs"* ]]
    ! grep -q ' up -d' "$DOCKER_LOG"
}

@test "config set refuses a secret on the command line and writes nothing" {
    config_setup
    run "$CLI" config set ANTHROPIC_API_KEY sk-on-argv
    [ "$status" -eq 2 ]
    [[ "$output" == *"not taken from the command line"* ]]
    ! grep -q 'sk-on-argv' "$SMOKING_PI_ENV_FILE"
}

@test "config set reads a secret from stdin, stores it, and never prints or passes it" {
    config_setup
    run bash -c "printf %s 'sk-from-stdin' | '$CLI' config set ANTHROPIC_API_KEY --no-apply"
    [ "$status" -eq 0 ]
    grep -qx 'ANTHROPIC_API_KEY=sk-from-stdin' "$SMOKING_PI_ENV_FILE"
    [[ "$output" == *"set (hidden)"* ]]
    [[ "$output" != *"sk-from-stdin"* ]]
    ! grep -q 'sk-from-stdin' "$DOCKER_LOG" 2>/dev/null
}

@test "config set refuses what install generated: the data volumes hold it" {
    config_setup
    run bash -c "printf %s new | '$CLI' config set POSTGRES_PASSWORD"
    [ "$status" -eq 2 ]
    [[ "$output" == *"generated or fixed at install"* ]]
    grep -qx 'POSTGRES_PASSWORD=pg-secret' "$SMOKING_PI_ENV_FILE"
    run bash -c "printf %s new | '$CLI' config set MCP_API_TOKEN"
    [ "$status" -eq 2 ]
    [[ "$output" == *"smoking-pi openclaw"* ]]
}

@test "config refuses a key the template does not declare, and suggests the near one" {
    config_setup
    run "$CLI" config set NOTIFY_MOD openclaw
    [ "$status" -eq 2 ]
    [[ "$output" == *"Did you mean NOTIFY_MODE?"* ]]
    ! grep -q 'NOTIFY_MOD=' "$SMOKING_PI_ENV_FILE"
}

@test "config set COMPOSE_PROFILES applies to the whole stack" {
    config_setup
    run "$CLI" config set COMPOSE_PROFILES influxdb,mcp,alerts
    [ "$status" -eq 0 ]
    grep -q 'up -d --remove-orphans' "$DOCKER_LOG"
}

@test "config get hides a secret unless --show-secrets; unset clears; the same value is a no-op" {
    config_setup
    run "$CLI" config get OPENCLAW_GATEWAY_TOKEN
    [[ "$output" == *"(set, hidden"* ]]
    [[ "$output" != *"gw-secret"* ]]
    run "$CLI" config get OPENCLAW_GATEWAY_TOKEN --show-secrets
    [ "$output" = "gw-secret" ]
    run "$CLI" config get WIFI_INTERFACE
    [ "$output" = "(unset: the default applies)" ]
    run "$CLI" config unset OPENCLAW_GATEWAY_TOKEN --no-apply
    [ "$status" -eq 0 ]
    grep -qx 'OPENCLAW_GATEWAY_TOKEN=' "$SMOKING_PI_ENV_FILE"
    : > "$DOCKER_LOG"
    run "$CLI" config set WIFI_INTERFACE ""
    [[ "$output" == *"already that; nothing changed"* ]]
    [ ! -s "$DOCKER_LOG" ]
}

@test "config keeps a value with \$ literal: single-quoted in the file, raw when read back" {
    config_setup
    run bash -c "printf %s 'pbkdf2:sha256:260000\$salt\$hash' | '$CLI' config set WEB_ADMIN_PASSWORD_HASH --no-apply"
    [ "$status" -eq 0 ]
    grep -qx "WEB_ADMIN_PASSWORD_HASH='pbkdf2:sha256:260000\$salt\$hash'" "$SMOKING_PI_ENV_FILE"
    run "$CLI" config get WEB_ADMIN_PASSWORD_HASH --show-secrets
    [ "$output" = 'pbkdf2:sha256:260000$salt$hash' ]
    # Plain values are written as they always were.
    run "$CLI" config set WIFI_INTERFACE wlan0 --no-apply
    grep -qx 'WIFI_INTERFACE=wlan0' "$SMOKING_PI_ENV_FILE"
}

@test "config refuses a value with a single quote, two values, and an empty secret on stdin" {
    config_setup
    run "$CLI" config set DIGEST_AT "it's" --no-apply
    [ "$status" -eq 2 ]
    run "$CLI" config set WIFI_INTERFACE wlan0 wlan1
    [ "$status" -eq 2 ]
    run bash -c "printf '' | '$CLI' config set ANTHROPIC_API_KEY"
    [ "$status" -eq 2 ]
    [[ "$output" == *"nothing read"* ]]
}

@test "config treats a webhook URL as a secret and a port as an ordinary setting" {
    config_setup
    run "$CLI" config set ALERT_WEBHOOK_URL https://hooks.example/T0/abc --no-apply
    [ "$status" -eq 2 ]
    [[ "$output" == *"not taken from the command line"* ]]
    run "$CLI" config set CLICKHOUSE_HTTP_PORT 8124 --no-apply
    [ "$status" -eq 0 ]
}

# --- links: where alert and assistant links point --------------------------------

links_setup() {
    cp "$REPO/editions/pro/.env.template" "$STUB_HOME/editions/pro/"
    printf 'COMPOSE_PROFILES=influxdb,mcp\nPUBLIC_BASE_HOST=\nTUNNEL_BASE_HOST=\n' > "$SMOKING_PI_ENV_FILE"
}

@test "links with no option shows where links point, and changes nothing" {
    links_setup
    run "$CLI" links
    [ "$status" -eq 0 ]
    [[ "$output" == *"at home:       none (smoking-pi links --lan auto)"* ]]
    [[ "$output" == *"from anywhere: none"* ]]
    ! grep -q ' up -d' "$DOCKER_LOG" 2>/dev/null
}

@test "links --lan auto takes the default route's source address, not the SSH one" {
    links_setup
    export SSH_CONNECTION="100.64.0.9 50000 100.101.102.103 22"
    run "$CLI" links --lan auto
    [ "$status" -eq 0 ]
    grep -qx 'PUBLIC_BASE_HOST=192.0.2.10' "$SMOKING_PI_ENV_FILE"
    [[ "$output" == *"http://192.0.2.10:3000/ (Grafana)"* ]]
    # alerter and mcp-server read it; the stub's enabled services have neither.
    [[ "$output" == *"which no enabled profile runs"* ]]
}

@test "links --tunnel refuses a bare host: it would become a dead http link" {
    links_setup
    run "$CLI" links --tunnel smokingpi.example.com
    [ "$status" -eq 2 ]
    [[ "$output" == *"Include the scheme"* ]]
    grep -qx 'TUNNEL_BASE_HOST=' "$SMOKING_PI_ENV_FILE"
    run "$CLI" links --tunnel https://smokingpi.example.com/
    [ "$status" -eq 0 ]
    grep -qx 'TUNNEL_BASE_HOST=https://smokingpi.example.com' "$SMOKING_PI_ENV_FILE"
}

@test "links --lan refuses loopback and URLs; --off clears both" {
    links_setup
    run "$CLI" links --lan 127.0.0.1
    [ "$status" -eq 2 ]
    run "$CLI" links --lan ftp://x
    [ "$status" -eq 2 ]
    printf 'PUBLIC_BASE_HOST=10.0.0.2\nTUNNEL_BASE_HOST=https://t.example\n' > "$SMOKING_PI_ENV_FILE"
    run "$CLI" links --off
    [ "$status" -eq 0 ]
    grep -qx 'PUBLIC_BASE_HOST=' "$SMOKING_PI_ENV_FILE"
    grep -qx 'TUNNEL_BASE_HOST=' "$SMOKING_PI_ENV_FILE"
}

@test "links on an edition without the MCP server says so" {
    links_setup
    export SMOKING_PI_EDITION=basic
    cp "$REPO/editions/basic/docker-compose.yml" "$STUB_HOME/editions/basic/"
    run "$CLI" links --lan auto
    [ "$status" -eq 1 ]
    [[ "$output" == *"Pro services"* ]]
}


@test "links refuses a forgotten value, a link-local IPv6 literal and a path; takes a LAN proxy's https://host" {
    links_setup
    run "$CLI" links --lan --off
    [ "$status" -eq 2 ]
    grep -qx 'PUBLIC_BASE_HOST=' "$SMOKING_PI_ENV_FILE"
    run "$CLI" links --tunnel --off
    [ "$status" -eq 2 ]
    run "$CLI" links --lan fe80::1
    [ "$status" -eq 2 ]
    [[ "$output" == *"link-local"* ]]
    grep -qx 'PUBLIC_BASE_HOST=' "$SMOKING_PI_ENV_FILE"
    run "$CLI" links --lan https://pi.lan/grafana
    [ "$status" -eq 2 ]
    run "$CLI" links --lan https://pi.lan
    [ "$status" -eq 0 ]
    grep -qx 'PUBLIC_BASE_HOST=https://pi.lan' "$SMOKING_PI_ENV_FILE"
    [[ "$output" == *"at home:       https://pi.lan"* ]]
    run "$CLI" links --lan http://localhost
    [ "$status" -eq 2 ]
}

# --- alerts: where they go ------------------------------------------------------

alerts_setup() {
    cp "$REPO/editions/pro/.env.template" "$STUB_HOME/editions/pro/"
    printf 'COMPOSE_PROFILES=influxdb,mcp\nNOTIFY_MODE=off\nOPENCLAW_GATEWAY_TOKEN=gw-secret\n' > "$SMOKING_PI_ENV_FILE"
    # In front of the suite's stub: the alerter's preflight line and the
    # test send. Everything else falls through to it.
    mkdir -p "$BATS_TEST_TMPDIR/bin2"
    cat > "$BATS_TEST_TMPDIR/bin2/docker" <<STUB
#!/bin/sh
case "\$*" in
    *"logs alerter"*) echo "docker \$*" >> "\$DOCKER_LOG"; echo "alerter-1 | 2026-09-24 12:00:00 \${STUB_LEVEL:-INFO} alerter.notifier: \${STUB_PREFLIGHT}"; exit 0 ;;
    *"exec -T alerter python main.py --test"*) echo "docker \$*" >> "\$DOCKER_LOG"; exit \${STUB_TEST_EXIT:-0} ;;
esac
exec "$BATS_TEST_TMPDIR/bin/docker" "\$@"
STUB
    chmod +x "$BATS_TEST_TMPDIR/bin2/docker"
    export PATH="$BATS_TEST_TMPDIR/bin2:$PATH"
    export STUB_PREFLIGHT="Delivery preflight: http://127.0.0.1:18789/tools/invoke reachable, 'message' tool permitted (HTTP 200)"
}

@test "alerts --openclaw sets the mode, recipient and channel, turns on the profile, prints the preflight" {
    alerts_setup
    run "$CLI" alerts --openclaw --to telegram:123 --yes
    [ "$status" -eq 0 ]
    grep -qx 'NOTIFY_MODE=openclaw' "$SMOKING_PI_ENV_FILE"
    grep -qx 'OPENCLAW_TO=telegram:123' "$SMOKING_PI_ENV_FILE"
    grep -qx 'OPENCLAW_CHANNEL=telegram' "$SMOKING_PI_ENV_FILE"
    grep -qx 'COMPOSE_PROFILES=influxdb,mcp,alerts' "$SMOKING_PI_ENV_FILE"
    grep -q 'up -d alerter' "$DOCKER_LOG"
    [[ "$output" == *"'message' tool permitted"* ]]
    # Only what the new container logged: --since the moment before the recreate.
    grep -Eq 'logs alerter --since 20[0-9-]+T[0-9:]+Z' "$DOCKER_LOG"
    # No test message unless asked.
    ! grep -q 'main.py --test' "$DOCKER_LOG"
    # The token is never printed.
    [[ "$output" != *"gw-secret"* ]]
}

@test "alerts refuses a bare chat id: OpenClaw does not deliver to one" {
    alerts_setup
    run "$CLI" alerts --openclaw --to 123456 --yes
    [ "$status" -eq 2 ]
    grep -qx 'NOTIFY_MODE=off' "$SMOKING_PI_ENV_FILE"
}

@test "alerts takes the gateway token from the user's openclaw.json" {
    alerts_setup
    printf 'COMPOSE_PROFILES=influxdb\n' > "$SMOKING_PI_ENV_FILE"
    mkdir -p "$BATS_TEST_TMPDIR/u/.openclaw"
    printf '{"gateway": {"auth": {"token": "from-json"}}}' > "$BATS_TEST_TMPDIR/u/.openclaw/openclaw.json"
    run env -u SUDO_USER USER=nobody-here HOME="$BATS_TEST_TMPDIR/u" "$CLI" alerts --openclaw --to telegram:1 --yes
    [ "$status" -eq 0 ]
    grep -qx 'OPENCLAW_GATEWAY_TOKEN=from-json' "$SMOKING_PI_ENV_FILE"
    [[ "$output" != *"from-json"* ]]
}

@test "alerts says delivery is not working when the preflight is not green" {
    alerts_setup
    export STUB_PREFLIGHT="Delivery preflight: http://127.0.0.1:18789/tools/invoke rejected the Gateway token (401)."
    export STUB_LEVEL=ERROR
    run "$CLI" alerts --openclaw --to telegram:1 --test --yes
    [ "$status" -eq 1 ]
    [[ "$output" == *"rejected the Gateway token"* ]]
    # "reachable, but the tool is not permitted" is red too, despite the word.
    export STUB_PREFLIGHT="Delivery preflight: http://127.0.0.1:18789/tools/invoke is reachable, but the 'message' tool is not permitted"
    run "$CLI" alerts --openclaw --to telegram:1 --yes
    [ "$status" -eq 1 ]
    [[ "$output" == *"not working yet"* ]]
    ! grep -q 'main.py --test' "$DOCKER_LOG"
}

@test "alerts --test sends one through the alerter and reports a failed send" {
    alerts_setup
    run "$CLI" alerts --openclaw --to telegram:1 --test --yes
    [ "$status" -eq 0 ]
    grep -q 'exec -T alerter python main.py --test' "$DOCKER_LOG"
    [[ "$output" == *"Test message sent"* ]]
    export STUB_TEST_EXIT=1
    run "$CLI" alerts --openclaw --to telegram:1 --test --yes
    [ "$status" -eq 1 ]
    [[ "$output" == *"NOT delivered"* ]]
}

@test "alerts --webhook reads the URL from stdin, never argv, never prints it; --off waits for nothing" {
    alerts_setup
    run bash -c "printf %s ftp://x | '$CLI' alerts --webhook --yes"
    [ "$status" -eq 2 ]
    run bash -c "printf %s 'https://hooks.example/T0/s3cret' | '$CLI' alerts --webhook --yes"
    [ "$status" -eq 0 ]
    grep -qx 'ALERT_WEBHOOK_URL=https://hooks.example/T0/s3cret' "$SMOKING_PI_ENV_FILE"
    grep -qx 'NOTIFY_MODE=webhook' "$SMOKING_PI_ENV_FILE"
    [[ "$output" != *"s3cret"* ]]
    ! grep -q 's3cret' "$DOCKER_LOG"
    : > "$DOCKER_LOG"
    run "$CLI" alerts --off --yes
    [ "$status" -eq 0 ]
    grep -qx 'NOTIFY_MODE=off' "$SMOKING_PI_ENV_FILE"
    ! grep -q 'logs alerter' "$DOCKER_LOG"
}

@test "alerts on an edition without the alerter says so and changes nothing" {
    alerts_setup
    export SMOKING_PI_EDITION=basic
    cp "$REPO/editions/basic/docker-compose.yml" "$STUB_HOME/editions/basic/"
    run "$CLI" alerts --off --yes
    [ "$status" -eq 1 ]
    [[ "$output" == *"Pro service"* ]]
    grep -qx 'NOTIFY_MODE=off' "$SMOKING_PI_ENV_FILE"
}

@test "links --lan takes a global IPv6 literal, stores it bracketed, and says the web admin is IPv4-only" {
    links_setup
    cp "$REPO/editions/pro/docker-compose.yml" "$STUB_HOME/editions/pro/"
    run "$CLI" links --lan 2001:DB8::5
    [ "$status" -eq 0 ]
    # Brackets are outside env_quote's safe set, so the value is quoted;
    # Compose and env_get both strip the quotes.
    grep -qxF "PUBLIC_BASE_HOST='[2001:db8::5]'" "$SMOKING_PI_ENV_FILE"
    [[ "$output" == *"http://[2001:db8::5]:3000/ (Grafana)"* ]]
    [[ "$output" == *"web admin listens on IPv4 only"* ]]
    run "$CLI" links --lan '[2001:db8::5]:9999'
    [ "$status" -eq 0 ]
    grep -qxF "PUBLIC_BASE_HOST='[2001:db8::5]:9999'" "$SMOKING_PI_ENV_FILE"
    [[ "$output" == *"at home:       http://[2001:db8::5]:9999/"* ]]
    for bad in '::' '::1' '[fe80::1]' 'fd00::1%eth0' '[2001:db8::5' '[2001:db8::5]:x' '2001:zz::1' '2001:::1'; do
        run "$CLI" links --lan "$bad"
        [ "$status" -eq 2 ]
    done
    grep -qxF "PUBLIC_BASE_HOST='[2001:db8::5]:9999'" "$SMOKING_PI_ENV_FILE"
}

@test "links shows a host that names its port once, not with :3000 appended" {
    links_setup
    printf 'PUBLIC_BASE_HOST=pi.local:9999\nTUNNEL_BASE_HOST=\n' > "$SMOKING_PI_ENV_FILE"
    run "$CLI" links
    [ "$status" -eq 0 ]
    [[ "$output" == *"at home:       http://pi.local:9999/"* ]]
    printf 'PUBLIC_BASE_HOST=2001:db8::5\nTUNNEL_BASE_HOST=\n' > "$SMOKING_PI_ENV_FILE"
    run "$CLI" links
    [[ "$output" == *"at home:       http://[2001:db8::5]:3000/ (Grafana)"* ]]
}

# --- alerts --digest ---------------------------------------------------------------

@test "alerts --digest alone sets the time and zone, touches nothing about delivery, and says it is not sent while off" {
    alerts_setup
    run "$CLI" alerts --digest 07:45 --digest-tz Europe/London
    [ "$status" -eq 0 ]
    grep -qx 'DIGEST_ENABLED=true' "$SMOKING_PI_ENV_FILE"
    grep -qx 'DIGEST_AT=07:45' "$SMOKING_PI_ENV_FILE"
    grep -qx 'DIGEST_TZ=Europe/London' "$SMOKING_PI_ENV_FILE"
    grep -qx 'NOTIFY_MODE=off' "$SMOKING_PI_ENV_FILE"
    [[ "$output" == *"07:45 Europe/London"* ]]
    [[ "$output" == *"logged, not sent"* ]]
    # The alerts profile is not in the stub's enabled services: nothing started.
    ! grep -q ' up -d' "$DOCKER_LOG"
}

@test "alerts --digest refuses a time or zone it cannot read, before writing anything" {
    alerts_setup
    for bad in 24:00 12:60 noon 7:5; do
        run "$CLI" alerts --digest "$bad"
        [ "$status" -eq 2 ]
    done
    run "$CLI" alerts --digest 07:45 --digest-tz Mars/Olympus
    [ "$status" -eq 2 ]
    run "$CLI" alerts --digest 07:45 --digest-tz ../../etc/hostname
    [ "$status" -eq 2 ]
    run "$CLI" alerts --digest-tz Europe/London
    [ "$status" -eq 2 ]
    run "$CLI" alerts --digest --openclaw
    [ "$status" -eq 2 ]
    ! grep -q '^DIGEST_' "$SMOKING_PI_ENV_FILE"
}

@test "alerts --digest off turns it off; with a mode, both are set in one go" {
    alerts_setup
    run "$CLI" alerts --digest off
    [ "$status" -eq 0 ]
    grep -qx 'DIGEST_ENABLED=false' "$SMOKING_PI_ENV_FILE"
    run "$CLI" alerts --openclaw --to telegram:1 --digest 08:30 --yes
    [ "$status" -eq 0 ]
    grep -qx 'NOTIFY_MODE=openclaw' "$SMOKING_PI_ENV_FILE"
    grep -qx 'DIGEST_ENABLED=true' "$SMOKING_PI_ENV_FILE"
    grep -qx 'DIGEST_AT=08:30' "$SMOKING_PI_ENV_FILE"
    [[ "$output" != *"logged, not sent"* ]]
}


@test "alerts --digest takes 7:45 as 07:45; with --off both are said once each" {
    alerts_setup
    run "$CLI" alerts --digest 7:45
    [ "$status" -eq 0 ]
    grep -qx 'DIGEST_AT=07:45' "$SMOKING_PI_ENV_FILE"
    run "$CLI" alerts --off --digest 08:30 --yes
    [ "$status" -eq 0 ]
    grep -qx 'NOTIFY_MODE=off' "$SMOKING_PI_ENV_FILE"
    grep -qx 'DIGEST_AT=08:30' "$SMOKING_PI_ENV_FILE"
    [[ "$output" == *"logged, not delivered"* ]]
    [[ "$output" == *"logged, not sent"* ]]
}

@test "upgrade stops and removes the containers of a service whose profile is off, after the up, and keeps the rest" {
    # The rc.3 acceptance: ai-insights (profile `ai` off) is defined, so not
    # an orphan to --remove-orphans, and kept running on its old image.
    export STUB_CONTAINERS='postgres pro-postgres-1\nai-insights pro-ai-insights-1\ngrafana pro-grafana-1\nalerter pro-alerter-1\n'
    run "$CLI" upgrade --skip-doctor
    [ "$status" -eq 0 ]
    [[ "$output" == *"Removing containers of services no enabled profile runs: pro-ai-insights-1 pro-alerter-1"* ]]
    grep -q 'ps -a --filter label=com.docker.compose.project=pro ' "$DOCKER_LOG"
    grep -qx 'docker stop pro-ai-insights-1 pro-alerter-1' "$DOCKER_LOG"
    grep -qx 'docker rm pro-ai-insights-1 pro-alerter-1' "$DOCKER_LOG"
    # Never a volume, never an enabled service's container.
    ! grep -q 'rm -v\|volume rm\|pro-postgres-1\|pro-grafana-1' "$DOCKER_LOG"
    # The up comes first; stop, then rm.
    up=$(grep -n 'up -d --remove-orphans' "$DOCKER_LOG" | cut -d: -f1)
    stop=$(grep -n '^docker stop' "$DOCKER_LOG" | cut -d: -f1)
    rm=$(grep -n '^docker rm' "$DOCKER_LOG" | cut -d: -f1)
    [ "$up" -lt "$stop" ] && [ "$stop" -lt "$rm" ]
}

@test "upgrade with every container enabled stops nothing and says nothing about it" {
    export STUB_CONTAINERS='postgres pro-postgres-1\nsmokeping pro-smokeping-1\n'
    run "$CLI" upgrade --skip-doctor
    [ "$status" -eq 0 ]
    [[ "$output" != *"Removing containers"* ]]
    ! grep -q '^docker stop\|^docker rm' "$DOCKER_LOG"
}

@test "when Compose cannot list the enabled services, nothing is removed: an empty list is not 'all disabled'" {
    export STUB_CONTAINERS='postgres pro-postgres-1\n'
    fail_docker_on "config --services"
    run "$CLI" upgrade --skip-doctor
    [ "$status" -eq 0 ]
    [[ "$output" == *"containers of disabled profiles were not checked"* ]]
    ! grep -q '^docker stop\|^docker rm' "$DOCKER_LOG"
}

@test "when docker cannot list the project's containers, nothing is removed and it says so" {
    export STUB_CONTAINERS='ai-insights pro-ai-insights-1\n'
    fail_docker_on "ps -a --filter"
    run "$CLI" upgrade --skip-doctor
    [ "$status" -eq 0 ]
    [[ "$output" == *"docker could not list the pro containers"* ]]
    ! grep -q '^docker stop\|^docker rm' "$DOCKER_LOG"
}

@test "up also removes a disabled profile's container" {
    export STUB_CONTAINERS='smokeping pro-smokeping-1\nai-insights pro-ai-insights-1\n'
    run "$CLI" up
    [ "$status" -eq 0 ]
    grep -qx 'docker rm pro-ai-insights-1' "$DOCKER_LOG"
    ! grep -q 'pro-smokeping-1$' <(grep '^docker \(stop\|rm\)' "$DOCKER_LOG")
}

@test "config set COMPOSE_PROFILES turning a profile off removes its container" {
    config_setup
    export STUB_CONTAINERS='postgres pro-postgres-1\nalerter pro-alerter-1\n'
    run "$CLI" config set COMPOSE_PROFILES influxdb
    [ "$status" -eq 0 ]
    grep -qx 'docker rm pro-alerter-1' "$DOCKER_LOG"
}
