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
