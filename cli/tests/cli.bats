#!/usr/bin/env bats
# The smoking-pi command against a stubbed docker: what it runs, in what
# order, with which files -- never the real daemon. The stub records every
# invocation in $DOCKER_LOG and answers the few queries the command makes
# (`compose config --format json`, `--services`, `ps`). Run from the repo:
#   bats cli/tests/cli.bats
# CI runs it; the real-daemon proof is the deploy on the reference Pi.
#
# "This never happens" is written `if cmd; then false; fi`, never `! cmd`:
# errexit ignores a pipeline negated with `!`, so `! grep -q x log` fails a
# test only when it is the test's last line, and anywhere else proves
# nothing. The log exists from setup on, so a grep for what must be absent
# answers "not there" (1), never "no such file" (2).

setup() {
    REPO="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
    CLI="$REPO/cli/smoking-pi"
    export DOCKER_LOG="$BATS_TEST_TMPDIR/docker.log"
    : > "$DOCKER_LOG"
    export STUB_HOME="$BATS_TEST_TMPDIR/home"
    # A minimal tree: the command only needs the edition directories, the
    # scripts it calls, and CITATION.cff for `version`.
    mkdir -p "$STUB_HOME/editions/pro" "$STUB_HOME/editions/basic" "$STUB_HOME/shared/modules/doctor"
    cp "$REPO/editions/pro/docker-compose.yml" "$REPO/editions/pro/docker-compose.clickhouse.yml" \
       "$REPO/editions/pro/docker-compose.packaged.yml" "$STUB_HOME/editions/pro/"
    printf '#!/bin/sh\necho SETUP "$@" >> "%s"\necho SETUP_INSTALL=$SMOKING_PI_INSTALL >> "%s"\nprintf "COMPOSE_PROFILES=%%s\\n" "${2:-influxdb}" > "$SMOKING_PI_ENV_FILE"\n' "$DOCKER_LOG" "$DOCKER_LOG" > "$STUB_HOME/editions/pro/setup.sh"
    mkdir -p "$STUB_HOME/shared/scripts"
    printf '#!/bin/sh\necho PASSWORDS "$@" >> "%s"\necho PASSWORDS_CWD "$(pwd)" >> "%s"\n' "$DOCKER_LOG" "$DOCKER_LOG" > "$STUB_HOME/shared/scripts/show-passwords.sh"
    cp "$STUB_HOME/editions/pro/setup.sh" "$STUB_HOME/editions/basic/"
    chmod +x "$STUB_HOME/editions/"*/*.sh "$STUB_HOME/shared/scripts/show-passwords.sh"
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
    # `compose up` fails its first STUB_UP_FAILS times (unset = never).
    *" up -d"*)
        if [ -n "${STUB_UP_FAILS:-}" ]; then
            n=$(cat "$DOCKER_LOG.up" 2>/dev/null || echo 0)
            echo $((n + 1)) > "$DOCKER_LOG.up"
            [ "$n" -ge "$STUB_UP_FAILS" ] || exit 1
        fi ;;
    # The project's containers as "name state" (STUB_STATES, \n-separated).
    *"--format {{.Names}} {{.State}}"*) printf '%b' "${STUB_STATES:-}" ;;
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
    # The mdns service's status (STUB_MDNS; unset = not running).
    *"exec -T mdns python status.py --json"*) [ -n "${STUB_MDNS:-}" ] || exit 1; printf '%b' "$STUB_MDNS" ;;
    *"exec -T postgres pg_dumpall"*) echo "-- dump" ;;
    # Quick tunnels: labeled ones as "page name" (STUB_TUNNELS), every
    # container name (STUB_NAMES), and a log with two hostnames, the
    # current one last.
    *"ps -a --filter label=io.smoking-pi.quick-tunnel "*) printf '%b' "${STUB_TUNNELS:-}" ;;
    "ps -a --format {{.Names}} {{.Image}}") printf '%b' "${STUB_NAMES:-}" ;;
    # cloudflared logs its own API endpoint too (on a failed request).
    "logs "*tunnel-*) printf 'INF https://old-one.trycloudflare.com\nINF https://%s-now.trycloudflare.com\nERR POST https://api.trycloudflare.com/tunnel\n' "${2##*-}" ;;
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
    # The package's layout (packaging/deb/build.sh): cli/ as
    # /usr/lib/smoking-pi, /usr/bin/smoking-pi a relative link to its entry.
    mkdir -p "$BATS_TEST_TMPDIR/usr/bin" "$BATS_TEST_TMPDIR/usr/lib/smoking-pi"
    cp -r "$REPO/cli/smoking-pi" "$REPO/cli/lib" "$BATS_TEST_TMPDIR/usr/lib/smoking-pi/"
    ln -s ../lib/smoking-pi/smoking-pi "$BATS_TEST_TMPDIR/usr/bin/smoking-pi"
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
    if grep -q SETUP "$DOCKER_LOG"; then false; fi
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
    [ ! -s "$DOCKER_LOG" ]
    run "$CLI" install --yes --edition basic --profiles mcp
    [ "$status" -eq 0 ]
    [[ "$output" == *"--profiles applies to the pro edition only"* ]]
    if grep -q ' up -d' "$DOCKER_LOG"; then false; fi
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
    # --yes asks nothing: what the alerter still needs is named, by command.
    [[ "$output" == *"Still to do:"* ]]
    [[ "$output" == *"alerts: smoking-pi alerts"* ]]
    run compose_calls
    [[ "$output" == *" up -d"* ]]
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
    if grep -q 'clickhouse' "$DOCKER_LOG"; then false; fi
    [ "$(stat -c %a "$BATS_TEST_TMPDIR/bk")" = 700 ]
}

@test "backup --online never stops the stack and says so in the manifest" {
    run "$CLI" backup "$BATS_TEST_TMPDIR/bk" --online
    [ "$status" -eq 0 ]
    if grep -q ' down$' "$DOCKER_LOG"; then false; fi
    if grep -q ' up -d$' "$DOCKER_LOG"; then false; fi
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
    if grep -q ' down$' "$DOCKER_LOG"; then false; fi
    if grep -q 'volume create' "$DOCKER_LOG"; then false; fi
    run bash -c "echo pro | '$CLI' restore '$BATS_TEST_TMPDIR/bk'"
    [ "$status" -eq 0 ]
    [[ "$output" == *"keeping the existing"* ]]
    if grep -q FROM=backup "$SMOKING_PI_ENV_FILE"; then false; fi
    grep -q 'volume create --label com.docker.compose.project=pro --label com.docker.compose.volume=postgres-data pro_postgres-data' "$DOCKER_LOG"
    grep -q 'volume create --label com.docker.compose.project=pro --label com.docker.compose.volume=smokeping-config smokeping-pro-config' "$DOCKER_LOG"
    if grep -q 'clickhouse' "$DOCKER_LOG"; then false; fi
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
    if grep -q ' up -d$' "$DOCKER_LOG"; then false; fi
}

@test "restore reports a volume that failed to extract, goes on with the rest, leaves the stack stopped, exits 1" {
    make_backup_dir
    fail_docker_on 'smokeping-config.tgz:/from.tgz:ro'
    run "$CLI" restore "$BATS_TEST_TMPDIR/bk" --yes
    [ "$status" -eq 1 ]
    [[ "$output" == *"smokeping-pro-config: emptied but the tarball did not extract"* ]]
    [[ "$output" == *"volume pro_postgres-data restored"* ]]
    [[ "$output" == *"restore INCOMPLETE; the stack is stopped. Failed: smokeping-pro-config"* ]]
    if grep -q ' up -d$' "$DOCKER_LOG"; then false; fi
}

@test "a packaged restore that failed enables no unit" {
    make_backup_dir
    export SMOKING_PI_PACKAGED=1
    printf '#!/bin/sh\necho "systemctl $*" >> "%s"\n' "$DOCKER_LOG" > "$BATS_TEST_TMPDIR/bin/systemctl"
    fail_docker_on 'smokeping-config.tgz:/from.tgz:ro'
    run "$CLI" restore "$BATS_TEST_TMPDIR/bk" --yes --force
    [ "$status" -eq 1 ]
    if grep -q '^systemctl' "$DOCKER_LOG"; then false; fi
}

@test "purge asks for the project name and aborts on anything else" {
    run bash -c "echo nope | '$CLI' purge"
    [ "$status" -eq 1 ]
    [[ "$output" == *"aborted."* ]]
    if grep -q 'volume rm' "$DOCKER_LOG"; then false; fi
    [ -f "$SMOKING_PI_ENV_FILE" ]
}

@test "purge with the project name typed removes the active volumes (fixed names too) and keeps the env file" {
    run bash -c "echo pro | '$CLI' purge"
    [ "$status" -eq 0 ]
    grep -q 'volume rm pro_postgres-data pro_grafana-data smokeping-pro-config' "$DOCKER_LOG"
    if grep -q 'clickhouse' "$DOCKER_LOG"; then false; fi
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
    [ -d "$SMOKING_PI_CONFIG_DIR" ]
    [ ! -e "$SMOKING_PI_CONFIG_DIR/targets.yaml" ]
    [ -d "$SMOKING_PI_OUTPUT_DIR" ]
}

@test "packaged purge --config stops and disables the unit first, and removes the edition record" {
    export SMOKING_PI_DEFAULTS="$BATS_TEST_TMPDIR/defaults"
    printf 'SMOKING_PI_PACKAGED=1\n' > "$SMOKING_PI_DEFAULTS"
    printf 'pro\n' > "$BATS_TEST_TMPDIR/edition"
    printf '#!/bin/sh\necho "systemctl $*" >> "%s"\n' "$DOCKER_LOG" > "$BATS_TEST_TMPDIR/bin/systemctl"
    run "$CLI" purge --config --yes
    [ "$status" -eq 0 ]
    [ ! -e "$BATS_TEST_TMPDIR/edition" ]
    # Before the env file goes: the unit's ExecStop (`down`) reads it.
    first_down=$(grep -n 'compose .* down' "$DOCKER_LOG" | head -1 | cut -d: -f1)
    disabled=$(grep -n '^systemctl disable --now smoking-pi$' "$DOCKER_LOG" | cut -d: -f1)
    [ -n "$disabled" ] && [ -n "$first_down" ] && [ "$disabled" -lt "$first_down" ]
}

@test "purge without --config leaves the unit and the edition record alone" {
    export SMOKING_PI_DEFAULTS="$BATS_TEST_TMPDIR/defaults"
    printf 'SMOKING_PI_PACKAGED=1\n' > "$SMOKING_PI_DEFAULTS"
    printf 'pro\n' > "$BATS_TEST_TMPDIR/edition"
    printf '#!/bin/sh\necho "systemctl $*" >> "%s"\n' "$DOCKER_LOG" > "$BATS_TEST_TMPDIR/bin/systemctl"
    run "$CLI" purge --yes
    [ "$status" -eq 0 ]
    [ -e "$BATS_TEST_TMPDIR/edition" ]
    if grep -q '^systemctl disable' "$DOCKER_LOG"; then false; fi
}

@test "doctor runs without writing bytecode (under sudo it would be root's, inside /opt)" {
    printf '#!/bin/sh\necho "PY dontwrite=$PYTHONDONTWRITEBYTECODE $*" >> "%s"\n' "$DOCKER_LOG" > "$BATS_TEST_TMPDIR/bin/python3"
    chmod +x "$BATS_TEST_TMPDIR/bin/python3"
    run "$CLI" doctor
    [ "$status" -eq 0 ]
    grep -q '^PY dontwrite=1 -m doctor' "$DOCKER_LOG"
}

@test "passwords forwards its flags, so --show-secrets reaches the script" {
    run "$CLI" passwords --show-secrets --force
    [ "$status" -eq 0 ]
    grep -qx 'PASSWORDS --show-secrets --force' "$DOCKER_LOG"
    # From the edition's directory: the script reads docker-compose.yml there.
    grep -qx "PASSWORDS_CWD $STUB_HOME/editions/pro" "$DOCKER_LOG"
}

# An install transcript is pasted into issues and photographed. It ends on
# the hidden view, and says in one line where the values are.
@test "install ends short: no passwords banner, and points at --show-secrets without passing it" {
    rm -f "$SMOKING_PI_ENV_FILE"
    run "$CLI" install --edition pro --yes
    [ "$status" -eq 0 ]
    # The banner is `passwords`' job; at the end of an install it buried
    # the address under a hundred lines.
    if grep -q PASSWORDS "$DOCKER_LOG"; then false; fi
    [[ "$output" == *"smoking-pi passwords --show-secrets"* ]]
    [[ "$output" == *"doctor --live"* ]]
    [[ "$output" != *"Still to do"* ]]
    # setup.sh is told it runs under install, and skips its own ending.
    grep -q 'SETUP_INSTALL=1' "$DOCKER_LOG"
}

# whiptail as a person would answer it: the menus by their text, the
# answer on stderr (install reads it through 3>&1 1>&2 2>&3).
stub_whiptail() {
    cat > "$BATS_TEST_TMPDIR/bin/whiptail" <<'STUB'
#!/bin/sh
echo "whiptail $*" >> "$DOCKER_LOG"
case "$*" in
    *"Which edition?"*) echo pro >&2 ;;
    *"Time-series backend?"*) echo influxdb >&2 ;;
    *"Optional services"*) printf '%s' "${STUB_PICKS:-}" >&2 ;;
    *"chat assistant"*) echo later >&2 ;;
esac
STUB
    chmod +x "$BATS_TEST_TMPDIR/bin/whiptail"
}

@test "install menus: Pro is preselected, and the services chosen are set up or listed, never 'edit the env file'" {
    rm -f "$SMOKING_PI_ENV_FILE"
    stub_whiptail
    STUB_PICKS='"alerts" "ai"' run "$CLI" install
    [ "$status" -eq 0 ]
    grep -q 'whiptail .*--default-item pro --menu Which edition?' "$DOCKER_LOG"
    grep -qx 'COMPOSE_PROFILES=influxdb,alerts,ai' "$SMOKING_PI_ENV_FILE"
    # No terminal here (bats pipes stdin), so nothing is asked: listed.
    [[ "$output" == *"alerts: smoking-pi alerts"* ]]
    [[ "$output" == *"AI reports: smoking-pi config set ANTHROPIC_API_KEY"* ]]
    [[ "$output" != *"in $SMOKING_PI_ENV_FILE"* ]]
    [[ "$output" != *"NOTIFY_MODE"* ]]
}

# The follow-ups ask only on a terminal, which bats never gives: script(1)
# runs install on a pty, fed the answers a person would type.
install_on_a_tty() {
    command -v script >/dev/null || skip "no script(1) for a pty"
    cp "$REPO/editions/pro/.env.template" "$STUB_HOME/editions/pro/"
    # A prompt nobody answers waits forever on a pty (script(1) passes end
    # of input once): a regression then fails here instead of hanging CI.
    printf '%b' "$1" | SHELL=/bin/bash timeout 60 script -qec "$CLI install" /dev/null | tr -d '\r'
}

@test "install on a terminal asks each follow-up: alerts nowhere, AI key declined" {
    rm -f "$SMOKING_PI_ENV_FILE"
    stub_whiptail
    export STUB_PICKS='"alerts" "ai"'
    run install_on_a_tty '4\nn\n'
    [[ "$output" == *"Where should alerts go?"* ]]
    [[ "$output" == *"Enter it now?"* ]]
    # Answered, so not left to do; declined, so listed.
    [[ "$output" != *"alerts: smoking-pi alerts"* ]]
    [[ "$output" == *"AI reports: smoking-pi config set ANTHROPIC_API_KEY"* ]]
    grep -qx 'NOTIFY_MODE=off' "$SMOKING_PI_ENV_FILE"
    # One question, not a heading and then the question.
    [ "$(printf '%s' "$output" | grep -c 'alerts go')" -eq 1 ]
}

@test "install on a terminal: end of input at the AI key prompt is a no, not the default yes" {
    rm -f "$SMOKING_PI_ENV_FILE"
    stub_whiptail
    export STUB_PICKS='"ai"'
    run install_on_a_tty ''
    [[ "$output" == *"AI reports: smoking-pi config set ANTHROPIC_API_KEY"* ]]
    [[ "$output" != *"ANTHROPIC_API_KEY:"* ]]
}

@test "install with the dns profile starts the observer and says what to set on the router" {
    dns_setup
    rm -f "$SMOKING_PI_ENV_FILE"
    STUB_DNS_RUNNING=1 run "$CLI" install --yes --database influxdb --profiles dns
    [ "$status" -eq 0 ]
    [[ "$output" == *"primary:   192.0.2.10"* ]]
    [[ "$output" == *"DNS observer: set the router's DNS as shown above, then smoking-pi dns test"* ]]
}

@test "packaged install enables the unit and starts it without waiting (it would wait for our lock)" {
    rm -f "$SMOKING_PI_ENV_FILE"
    export SMOKING_PI_DEFAULTS="$BATS_TEST_TMPDIR/defaults"
    printf 'SMOKING_PI_PACKAGED=1\n' > "$SMOKING_PI_DEFAULTS"
    printf '#!/bin/sh\necho "systemctl $*" >> "%s"\n' "$DOCKER_LOG" > "$BATS_TEST_TMPDIR/bin/systemctl"
    run "$CLI" install --yes --edition pro
    [ "$status" -eq 0 ]
    grep -qx 'systemctl enable smoking-pi' "$DOCKER_LOG"
    grep -qx 'systemctl start --no-block smoking-pi' "$DOCKER_LOG"
    if grep -q 'systemctl enable --now' "$DOCKER_LOG"; then false; fi
    [[ "$output" == *"Starts at boot"* ]]
    # A package install's env file is root's: the advice says sudo.
    [[ "$output" == *"sudo smoking-pi passwords --show-secrets"* ]]
    [[ "$output" == *"sudo smoking-pi doctor --live"* ]]
}

@test "restore on a packaged install enables the unit; --no-start enables it without starting it" {
    make_backup_dir
    export SMOKING_PI_PACKAGED=1
    printf '#!/bin/sh\necho "systemctl $*" >> "%s"\n' "$DOCKER_LOG" > "$BATS_TEST_TMPDIR/bin/systemctl"
    run "$CLI" restore "$BATS_TEST_TMPDIR/bk" --yes --force --no-start
    [ "$status" -eq 0 ]
    grep -qx 'systemctl enable smoking-pi' "$DOCKER_LOG"
    if grep -q 'systemctl start' "$DOCKER_LOG"; then false; fi
    [[ "$output" == *"Starts at boot"* ]]
    : > "$DOCKER_LOG"
    run "$CLI" restore "$BATS_TEST_TMPDIR/bk" --yes --force
    [ "$status" -eq 0 ]
    grep -qx 'systemctl enable smoking-pi' "$DOCKER_LOG"
    grep -qx 'systemctl start --no-block smoking-pi' "$DOCKER_LOG"
}

@test "restore from a clone touches no unit" {
    unset SMOKING_PI_PACKAGED
    make_backup_dir
    printf '#!/bin/sh\necho "systemctl $*" >> "%s"\n' "$DOCKER_LOG" > "$BATS_TEST_TMPDIR/bin/systemctl"
    run "$CLI" restore "$BATS_TEST_TMPDIR/bk" --yes --force --no-start
    [ "$status" -eq 0 ]
    if grep -q 'systemctl enable' "$DOCKER_LOG"; then false; fi
}

@test "install from a clone touches no unit" {
    rm -f "$SMOKING_PI_ENV_FILE"
    printf '#!/bin/sh\necho "systemctl $*" >> "%s"\n' "$DOCKER_LOG" > "$BATS_TEST_TMPDIR/bin/systemctl"
    run "$CLI" install --yes --edition pro
    [ "$status" -eq 0 ]
    if grep -q 'systemctl enable' "$DOCKER_LOG"; then false; fi
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

@test "url names the .local name the mdns service holds, not the hostname's" {
    STUB_MDNS='{\n "name": "smoking-pi-2.local",\n "state": "announced"\n}\n' run "$CLI" url
    [ "$status" -eq 0 ]
    [[ "$output" == *"Also, from most computers on this network: http://smoking-pi-2.local:8080/"* ]]
}

@test "url prints no .local name while mdns is still probing or not running" {
    STUB_MDNS='{\n "name": "smoking-pi.local",\n "state": "probing"\n}\n' run "$CLI" url
    [ "$status" -eq 0 ]
    [[ "$output" != *"smoking-pi.local"* ]]
    run "$CLI" url
    [ "$status" -eq 0 ]
    [[ "$output" != *".local"* ]]
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
    # Same for HOME: the command also looks under ~/.nvm and ~/.local/bin.
    rm -f "$BATS_TEST_TMPDIR/bin/openclaw"
    export PATH="$BATS_TEST_TMPDIR/bin:/usr/bin:/bin"
    export HOME="$BATS_TEST_TMPDIR/nobody"
    run "$CLI" openclaw
    # Not an error: the stack measures without an assistant.
    [ "$status" -eq 0 ]
    [[ "$output" == *"ANOTHER machine"* ]]
    [[ "$output" == *"remote-openclaw.md"* ]]
    [[ "$output" == *"nothing is broken"* ]]
    # No token, no profile change: it did not half-configure anything.
    if grep -q 'MCP_API_TOKEN' "$SMOKING_PI_ENV_FILE"; then false; fi
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
    # Timeouts under the keys OpenClaw accepts: 2026.8 refuses the whole
    # registration over the retired connectTimeout/timeout.
    grep -q '"connectionTimeoutMs": 5000' "$DOCKER_LOG"
    grep -q '"requestTimeoutMs": 30000' "$DOCKER_LOG"
    if grep -qE '"(connectTimeout|timeout)"' "$DOCKER_LOG"; then false; fi
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

# nvm puts openclaw under ~/.nvm, off any PATH a script or sudo sees.
# An nvm openclaw that logs which version ran, for the two tests below.
stub_nvm_openclaw() {
    local d="$1/.nvm/versions/node/$2/bin"
    mkdir -p "$d"
    printf '#!/bin/sh\necho "nvm-%s openclaw $*" >> "%s"\n' "$2" "$DOCKER_LOG" > "$d/openclaw"
    chmod +x "$d/openclaw"
}

@test "openclaw finds nvm's default version when PATH has none" {
    stub_openclaw
    rm -f "$BATS_TEST_TMPDIR/bin/openclaw"
    export PATH="$BATS_TEST_TMPDIR/bin:/usr/bin:/bin"
    export HOME="$BATS_TEST_TMPDIR/user"
    stub_nvm_openclaw "$HOME" v22.22.0
    stub_nvm_openclaw "$HOME" v24.2.0
    stub_nvm_openclaw "$HOME" v24.18.0
    stub_nvm_openclaw "$HOME" v25.9.0
    mkdir -p "$HOME/.nvm/alias"
    printf '24\n' > "$HOME/.nvm/alias/default"
    run "$CLI" openclaw
    [ "$status" -eq 0 ]
    # The newest 24, not the newest overall: 24 is what nvm runs.
    grep -q 'nvm-v24.18.0 openclaw mcp set smokeping' "$DOCKER_LOG"
    if grep -q 'nvm-v25' "$DOCKER_LOG"; then false; fi
}

@test "openclaw reads nvm's default alias as a version, not a prefix" {
    stub_openclaw
    rm -f "$BATS_TEST_TMPDIR/bin/openclaw"
    export PATH="$BATS_TEST_TMPDIR/bin:/usr/bin:/bin"
    export HOME="$BATS_TEST_TMPDIR/user"
    stub_nvm_openclaw "$HOME" v2.0.0
    stub_nvm_openclaw "$HOME" v24.18.0
    mkdir -p "$HOME/.nvm/alias"
    printf '2\n' > "$HOME/.nvm/alias/default"
    run "$CLI" openclaw
    [ "$status" -eq 0 ]
    # `2` is v2.*, which v24 is not.
    grep -q 'nvm-v2.0.0 openclaw mcp set smokeping' "$DOCKER_LOG"
    printf 'v24.18.0\n' > "$HOME/.nvm/alias/default"
    : > "$DOCKER_LOG"
    run "$CLI" openclaw
    grep -q 'nvm-v24.18.0 openclaw mcp set smokeping' "$DOCKER_LOG"
}

# A packaged install needs sudo to read /etc/smoking-pi/env. OpenClaw is
# still the user's: run as root, `mcp set` writes /root/.openclaw, which
# no gateway reads, and sudo's secure_path hides nvm's openclaw anyway.
@test "openclaw under sudo registers as the user who ran sudo" {
    stub_openclaw
    rm -f "$BATS_TEST_TMPDIR/bin/openclaw"
    export PATH="$BATS_TEST_TMPDIR/bin:/usr/bin:/bin"
    local home="$BATS_TEST_TMPDIR/alice"
    stub_nvm_openclaw "$home" v24.18.0
    export SUDO_USER=alice HOME="$BATS_TEST_TMPDIR/root"
    # root, and alice is uid 1000 with that home.
    printf '#!/bin/sh\n[ "$1" = -u ] && [ -n "$2" ] && { echo 1000; exit 0; }\n[ "$1" = -u ] && { echo 0; exit 0; }\necho root\n' \
        > "$BATS_TEST_TMPDIR/bin/id"
    printf '#!/bin/sh\necho "alice:x:1000:1000::%s:/bin/bash"\n' "$home" > "$BATS_TEST_TMPDIR/bin/getent"
    # sudo logs its whole command line, as the real one does to the
    # journal, then runs the command. chown has no alice to give files to.
    printf '#!/bin/sh\necho "SUDO $*" >> "%s"\nshift 3\nexec "$@"\n' "$DOCKER_LOG" > "$BATS_TEST_TMPDIR/bin/sudo"
    printf '#!/bin/sh\necho "CHOWN $*" >> "%s"\n' "$DOCKER_LOG" > "$BATS_TEST_TMPDIR/bin/chown"
    chmod +x "$BATS_TEST_TMPDIR/bin/id" "$BATS_TEST_TMPDIR/bin/getent" \
             "$BATS_TEST_TMPDIR/bin/sudo" "$BATS_TEST_TMPDIR/bin/chown"
    printf 'COMPOSE_PROFILES=influxdb,mcp\nMCP_API_TOKEN=s3cr3ttoken\n' > "$SMOKING_PI_ENV_FILE"
    run "$CLI" openclaw
    [ "$status" -eq 0 ]
    [[ "$output" == *"as alice"* ]]
    # alice's nvm bin first on PATH, and her session bus for systemctl --user.
    grep -q "SUDO -u alice -H env PATH=$home/.nvm/versions/node/v24.18.0/bin:" "$DOCKER_LOG"
    grep -q 'XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus' "$DOCKER_LOG"
    # The token reached openclaw, but never sudo's (logged) command line;
    # the file that carried it was alice's and is gone.
    # The JSON spans lines, so count: openclaw's own log line holds it once;
    # through sudo's argv it would show up twice.
    grep -q 'nvm-v24.18.0 openclaw mcp set smokeping' "$DOCKER_LOG"
    [ "$(grep -c '"Authorization": "Bearer s3cr3ttoken"' "$DOCKER_LOG")" -eq 1 ]
    grep -q '^CHOWN alice ' "$DOCKER_LOG"
    [ ! -e "$(sed -n 's/^CHOWN alice //p' "$DOCKER_LOG")" ]
    grep -q 'nvm-v24.18.0 openclaw mcp set smokeping' "$DOCKER_LOG"
    # The skill goes into alice's ~/.openclaw too, not root's.
    [ "$(grep -c 'SUDO -u alice -H' "$DOCKER_LOG")" -eq 2 ]
    grep -q 'SKILL --reload' "$DOCKER_LOG"
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

@test "openclaw --check, agent failed: says what OpenClaw said, not 'reload the tools'" {
    stub_openclaw
    # The staging Pi's case: the gateway was not running.
    printf '#!/bin/sh\necho "openclaw $*" >> "%s"\necho "gateway agent requires credentials before opening a websocket" >&2\nexit 1\n' \
        "$DOCKER_LOG" > "$BATS_TEST_TMPDIR/bin/openclaw"
    run "$CLI" openclaw --check
    [ "$status" -eq 1 ]
    [[ "$output" == *"did not answer (openclaw exited 1)"* ]]
    [[ "$output" == *"| gateway agent requires credentials"* ]]
    [[ "$output" == *"openclaw gateway status"* ]]
    [[ "$output" != *"cached tool set"* ]]
}

@test "openclaw --check: a tool call in the server's log is Connected, even if the agent then failed" {
    stub_openclaw 1
    cat > "$BATS_TEST_TMPDIR/bin/docker" <<'STUB'
#!/bin/sh
echo "docker $*" >> "$DOCKER_LOG"
case "$*" in
    *"logs mcp-server"*) echo "tool=system_status args=- -> ok" ;;
esac
exit 0
STUB
    chmod +x "$BATS_TEST_TMPDIR/bin/docker"
    run "$CLI" openclaw --check
    [ "$status" -eq 0 ]
    [[ "$output" == *"Connected"* ]]
    [[ "$output" != *"did not answer"* ]]
}

@test "openclaw --check, agent answered from its shell: the advice names the skill check by its full path" {
    stub_openclaw
    run "$CLI" openclaw --check
    [ "$status" -eq 1 ]
    [[ "$output" == *"$STUB_HOME/shared/scripts/install-openclaw-skill.sh --check"* ]]
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
    if grep -q '^CURL .*tok123deadbeef' "$DOCKER_LOG"; then false; fi
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
    if grep -q 'openclaw mcp set' "$DOCKER_LOG"; then false; fi
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

@test "config list groups keys by the template's sections, and a word narrows it" {
    config_setup
    run "$CLI" config list
    [ "$status" -eq 0 ]
    [[ "$output" == *$'Alerts: daily digest\n  DIGEST_ENABLED'* ]]
    run "$CLI" config list digest
    [ "$status" -eq 0 ]
    [[ "$output" == *"DIGEST_AT"* ]]
    [[ "$output" != *"POSTGRES_PASSWORD"* ]]
    run "$CLI" config list nosuchsection
    [ "$status" -eq 2 ]
    [[ "$output" == *"Alerts: delivery"* ]]
}

@test "config describe: section, description, type and bounds, default, what reads it; a secret stays hidden" {
    config_setup
    run "$CLI" config describe CPE_PROBE_RATE
    [ "$status" -eq 0 ]
    [[ "$output" == *"CPE_PROBE_RATE  (CPE and microcuts)"* ]]
    [[ "$output" == *"int, at least 1, at most 1000"* ]]
    [[ "$output" == *"Default:   5"* ]]
    [[ "$output" == *"Read by:   smokeping"* ]]
    run "$CLI" config describe POSTGRES_PASSWORD
    [[ "$output" == *"(set, hidden"* ]]
    [[ "$output" != *"pg-secret"* ]]
    [[ "$output" == *"Fixed at install"* ]]
    run "$CLI" config describe NOT_A_KEY
    [ "$status" -eq 2 ]
}

@test "config set refuses a value of the wrong type before writing anything" {
    config_setup
    cp "$SMOKING_PI_ENV_FILE" "$BATS_TEST_TMPDIR/env.before"
    local bad
    for bad in "CPE_PROBE_RATE 0" "CPE_PROBE_RATE fast" "DIGEST_AT 25:00" "NOTIFY_MODE email" \
               "ALERT_CHARTS maybe" "DNS_WIZARD_COVERAGE 1.5" "OPENCLAW_URL localhost:18789" \
               "INFERENCE_SINCE 2026-13-01" "SMOKEPING_PORT 70000" \
               "DNS_WIZARD_COVERAGE 0" "DNS_WIZARD_INTERVAL 30" "TZ zone.tab" \
               "MCP_PUBLIC_URL http://192.168.1.10:8000/mcp" "MCP_PUBLIC_URL https://pi.example.ts.net/mcp"; do
        # shellcheck disable=SC2086  # KEY VALUE
        run "$CLI" config set $bad
        [ "$status" -eq 2 ] || { echo "accepted: $bad"; return 1; }
    done
    cmp "$SMOKING_PI_ENV_FILE" "$BATS_TEST_TMPDIR/env.before"
    if compose_calls | grep -q ' up '; then false; fi
}

@test "config set takes a value its type allows, and anything for an untyped key" {
    config_setup
    run "$CLI" config set DIGEST_AT 7:05 --no-apply
    [ "$status" -eq 0 ]
    run "$CLI" config set DNS_EXPORT_NAMES 1 --no-apply
    [ "$status" -eq 0 ]
    run "$CLI" config set WIFI_WEAK_DBM -70.5 --no-apply
    [ "$status" -eq 0 ]
    run "$CLI" config set WIFI_INTERFACE 'wlan0 or whatever' --no-apply
    [ "$status" -eq 0 ]
    run "$CLI" config set DNS_WIZARD_INTERVAL 0 --no-apply
    [ "$status" -eq 0 ]
    run "$CLI" config set DNS_WIZARD_COVERAGE .8 --no-apply
    [ "$status" -eq 0 ]
    run "$CLI" config set MCP_PUBLIC_URL https://pi.example.ts.net --no-apply
    [ "$status" -eq 0 ]
    # A choice or a true/false is taken in any case and written lowercase.
    run "$CLI" config set NOTIFY_MODE Telegram --no-apply
    [ "$status" -eq 0 ]
    grep -qx 'NOTIFY_MODE=telegram' "$SMOKING_PI_ENV_FILE"
    # An empty value is not how to clear one.
    run "$CLI" config set CPE_PROBE_RATE ""
    [ "$status" -eq 2 ]
    [[ "$output" == *"config unset CPE_PROBE_RATE"* ]]
    grep -qx 'DIGEST_AT=7:05' "$SMOKING_PI_ENV_FILE"
    grep -qx 'WIFI_WEAK_DBM=-70.5' "$SMOKING_PI_ENV_FILE"
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
    if grep -q ' up -d' "$DOCKER_LOG"; then false; fi
}

@test "config set refuses a secret on the command line and writes nothing" {
    config_setup
    run "$CLI" config set ANTHROPIC_API_KEY sk-on-argv
    [ "$status" -eq 2 ]
    [[ "$output" == *"not taken from the command line"* ]]
    if grep -q 'sk-on-argv' "$SMOKING_PI_ENV_FILE"; then false; fi
}

@test "config set reads a secret from stdin, stores it, and never prints or passes it" {
    config_setup
    run bash -c "printf %s 'sk-from-stdin' | '$CLI' config set ANTHROPIC_API_KEY --no-apply"
    [ "$status" -eq 0 ]
    grep -qx 'ANTHROPIC_API_KEY=sk-from-stdin' "$SMOKING_PI_ENV_FILE"
    [[ "$output" == *"set (hidden)"* ]]
    [[ "$output" != *"sk-from-stdin"* ]]
    if grep -q 'sk-from-stdin' "$DOCKER_LOG"; then false; fi
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
    if grep -q 'NOTIFY_MOD=' "$SMOKING_PI_ENV_FILE"; then false; fi
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
    if grep -q ' up -d' "$DOCKER_LOG"; then false; fi
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

@test "links --lan mdns stores the name the mdns service holds, not MDNS_NAME" {
    links_setup
    STUB_MDNS='{\n "name": "smoking-pi-2.local",\n "state": "announced"\n}\n' run "$CLI" links --lan mdns
    [ "$status" -eq 0 ]
    grep -qx 'PUBLIC_BASE_HOST=smoking-pi-2.local' "$SMOKING_PI_ENV_FILE"
    [[ "$output" == *"http://smoking-pi-2.local:3000/ (Grafana)"* ]]
    [[ "$output" == *"Only a device that resolves .local names"* ]]
}

@test "links --lan mdns refuses while mdns holds no name, and changes nothing" {
    links_setup
    STUB_MDNS='{\n "name": "smoking-pi.local",\n "state": "probing"\n}\n' run "$CLI" links --lan mdns
    [ "$status" -eq 1 ]
    [[ "$output" == *"holds no name"* ]]
    grep -qx 'PUBLIC_BASE_HOST=' "$SMOKING_PI_ENV_FILE"
}

@test "links --lan mdns refuses while mdns is not running; links still shows, without a warning" {
    links_setup
    run "$CLI" links --lan mdns
    [ "$status" -eq 1 ]
    [[ "$output" == *"holds no name"* ]]
    sed -i 's/^PUBLIC_BASE_HOST=.*/PUBLIC_BASE_HOST=smoking-pi.local/' "$SMOKING_PI_ENV_FILE"
    run "$CLI" links
    [ "$status" -eq 0 ]
    [[ "$output" == *"at home:       http://smoking-pi.local:3000/"* ]]
    [[ "$output" != *"Warning"* ]]
}

@test "links warns when the stored .local name is not the one mdns holds" {
    links_setup
    sed -i 's/^PUBLIC_BASE_HOST=.*/PUBLIC_BASE_HOST=smoking-pi.local/' "$SMOKING_PI_ENV_FILE"
    STUB_MDNS='{\n "name": "smoking-pi-2.local",\n "state": "announced"\n}\n' run "$CLI" links
    [ "$status" -eq 0 ]
    [[ "$output" == *"now holds smoking-pi-2.local"* ]]
    STUB_MDNS='{\n "name": "smoking-pi.local",\n "state": "announced"\n}\n' run "$CLI" links
    [[ "$output" != *"Warning"* ]]
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
    if grep -q 'main.py --test' "$DOCKER_LOG"; then false; fi
    # The token is never printed.
    [[ "$output" != *"gw-secret"* ]]
}

@test "alerts --telegram reads the token from stdin, never prints it, and sets the chat" {
    alerts_setup
    export STUB_PREFLIGHT="Delivery preflight: Telegram bot @pi_bot can write to chat 4242"
    tok="123456:AAAbbbCCCdddEEEfffGGGhhhIIIjjjKKKlll"
    run sh -c "printf '%s\n' '$tok' | '$CLI' alerts --telegram --to 4242 --yes"
    [ "$status" -eq 0 ]
    grep -qx "TELEGRAM_BOT_TOKEN=$tok" "$SMOKING_PI_ENV_FILE"
    grep -qx 'TELEGRAM_CHAT_ID=4242' "$SMOKING_PI_ENV_FILE"
    grep -qx 'NOTIFY_MODE=telegram' "$SMOKING_PI_ENV_FILE"
    grep -qx 'COMPOSE_PROFILES=influxdb,mcp,alerts' "$SMOKING_PI_ENV_FILE"
    [[ "$output" == *"@pi_bot can write to chat 4242"* ]]
    [[ "$output" != *"$tok"* ]]
    if grep -q "$tok" "$DOCKER_LOG"; then false; fi
    # Set once, the token is not asked for again.
    run "$CLI" alerts --telegram --to -100123 --yes </dev/null
    [ "$status" -eq 0 ]
    grep -qx 'TELEGRAM_CHAT_ID=-100123' "$SMOKING_PI_ENV_FILE"
}

@test "alerts --telegram refuses what is not a token or a chat, before writing the mode" {
    alerts_setup
    run sh -c "printf 'not-a-token\n' | '$CLI' alerts --telegram --to 4242 --yes"
    [ "$status" -eq 2 ]
    if grep -q 'TELEGRAM_BOT_TOKEN=not' "$SMOKING_PI_ENV_FILE"; then false; fi
    grep -qx 'NOTIFY_MODE=off' "$SMOKING_PI_ENV_FILE"
    printf 'TELEGRAM_BOT_TOKEN=123456:AAAbbbCCCdddEEEfffGGGhhhIIIjjjKKKlll\n' >> "$SMOKING_PI_ENV_FILE"
    run "$CLI" alerts --telegram --to 'x;y' --yes
    [ "$status" -eq 2 ]
    grep -qx 'NOTIFY_MODE=off' "$SMOKING_PI_ENV_FILE"
    # No chat and no terminal: it says how, and does not wait for a message.
    run "$CLI" alerts --telegram --yes </dev/null
    [ "$status" -eq 2 ]
    [[ "$output" == *"--to CHAT_ID"* ]]
    if grep -q 'find-chat' "$DOCKER_LOG"; then false; fi
}

@test "alerts --telegram on a terminal finds the chat in the alerter image and asks before using it" {
    command -v script >/dev/null || skip "no script(1) for a pty"
    alerts_setup
    export STUB_PREFLIGHT="Delivery preflight: Telegram bot @pi_bot can write to chat 4242"
    printf 'TELEGRAM_BOT_TOKEN=123456:AAAbbbCCCdddEEEfffGGGhhhIIIjjjKKKlll\n' >> "$SMOKING_PI_ENV_FILE"
    mkdir -p "$BATS_TEST_TMPDIR/bin3"
    cat > "$BATS_TEST_TMPDIR/bin3/docker" <<STUB
#!/bin/sh
case "\$*" in
    *"run --rm --no-deps -T alerter python telegram.py find-chat"*)
        echo "docker \$*" >> "\$DOCKER_LOG"
        echo " alerter Pulling 12345"; echo "The message came from: Ana (chat 4242)." >&2; echo 4242; exit 0 ;;
esac
exec "$BATS_TEST_TMPDIR/bin2/docker" "\$@"
STUB
    chmod +x "$BATS_TEST_TMPDIR/bin3/docker"
    export PATH="$BATS_TEST_TMPDIR/bin3:$PATH"
    # A bot something else already reads: no reading, it says how to give the chat.
    run sh -c "printf 'Yes\n' | SHELL=/bin/bash timeout 60 script -qec '$CLI alerts --telegram' /dev/null"
    [ "$status" -eq 2 ]
    [[ "$output" == *"--to CHAT_ID"* ]]
    # `! grep` mid-test cannot fail a bats test (errexit ignores `!`).
    if grep -q 'find-chat' "$DOCKER_LOG"; then false; fi
    grep -qx 'NOTIFY_MODE=off' "$SMOKING_PI_ENV_FILE"
    # Declined: nothing changes.
    run sh -c "printf 'n\nn\n' | SHELL=/bin/bash timeout 60 script -qec '$CLI alerts --telegram' /dev/null"
    grep -q 'find-chat 120' "$DOCKER_LOG"
    grep -qx 'NOTIFY_MODE=off' "$SMOKING_PI_ENV_FILE"
    if grep -q 'TELEGRAM_CHAT_ID=4242' "$SMOKING_PI_ENV_FILE"; then false; fi
    # Accepted: the number, not Compose's line before it.
    run sh -c "printf 'n\ny\nn\n' | SHELL=/bin/bash timeout 60 script -qec '$CLI alerts --telegram' /dev/null"
    [[ "$output" == *"The message came from: Ana"* ]]
    grep -qx 'TELEGRAM_CHAT_ID=4242' "$SMOKING_PI_ENV_FILE"
    grep -qx 'NOTIFY_MODE=telegram' "$SMOKING_PI_ENV_FILE"
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
    if grep -q 'main.py --test' "$DOCKER_LOG"; then false; fi
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
    if grep -q 's3cret' "$DOCKER_LOG"; then false; fi
    : > "$DOCKER_LOG"
    run "$CLI" alerts --off --yes
    [ "$status" -eq 0 ]
    grep -qx 'NOTIFY_MODE=off' "$SMOKING_PI_ENV_FILE"
    if grep -q 'logs alerter' "$DOCKER_LOG"; then false; fi
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
    if grep -q ' up -d' "$DOCKER_LOG"; then false; fi
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
    if grep -q '^DIGEST_' "$SMOKING_PI_ENV_FILE"; then false; fi
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
    if grep -q 'rm -v\|volume rm\|pro-postgres-1\|pro-grafana-1' "$DOCKER_LOG"; then false; fi
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
    if grep -q '^docker stop\|^docker rm' "$DOCKER_LOG"; then false; fi
}

@test "when Compose cannot list the enabled services, nothing is removed: an empty list is not 'all disabled'" {
    export STUB_CONTAINERS='postgres pro-postgres-1\n'
    fail_docker_on "config --services"
    run "$CLI" upgrade --skip-doctor
    [ "$status" -eq 0 ]
    [[ "$output" == *"containers of disabled profiles were not checked"* ]]
    if grep -q '^docker stop\|^docker rm' "$DOCKER_LOG"; then false; fi
}

@test "when docker cannot list the project's containers, nothing is removed and it says so" {
    export STUB_CONTAINERS='ai-insights pro-ai-insights-1\n'
    fail_docker_on "ps -a --filter"
    run "$CLI" upgrade --skip-doctor
    [ "$status" -eq 0 ]
    [[ "$output" == *"docker could not list the pro containers"* ]]
    if grep -q '^docker stop\|^docker rm' "$DOCKER_LOG"; then false; fi
}

@test "up also removes a disabled profile's container" {
    export STUB_CONTAINERS='smokeping pro-smokeping-1\nai-insights pro-ai-insights-1\n'
    run "$CLI" up
    [ "$status" -eq 0 ]
    grep -qx 'docker rm pro-ai-insights-1' "$DOCKER_LOG"
    if grep -q 'pro-smokeping-1$' <(grep '^docker \(stop\|rm\)' "$DOCKER_LOG"); then false; fi
}

@test "config set COMPOSE_PROFILES turning a profile off removes its container" {
    config_setup
    export STUB_CONTAINERS='postgres pro-postgres-1\nalerter pro-alerter-1\n'
    run "$CLI" config set COMPOSE_PROFILES influxdb
    [ "$status" -eq 0 ]
    grep -qx 'docker rm pro-alerter-1' "$DOCKER_LOG"
}

# A clone on a release tag runs that release's images. The stub tree made
# into a git repository, tagged as the test says.
stub_git() {
    git -C "$STUB_HOME" init -q
    git -C "$STUB_HOME" -c user.name=t -c user.email=t@example.invalid add -A
    git -C "$STUB_HOME" -c user.name=t -c user.email=t@example.invalid commit -qm stub
    local t; for t in "$@"; do git -C "$STUB_HOME" tag "$t"; done
}

@test "a clone on a release tag runs its images: the final release beats its candidates" {
    stub_git v9.9.9-rc.3 v9.9.9 not-a-release
    run "$CLI" paths
    [[ "$output" == *"<service>:9.9.9 (the checkout is on v9.9.9)"* ]]
    run "$CLI" upgrade --skip-doctor
    [ "$status" -eq 0 ]
    [[ "$output" == *"Pulling the 9.9.9 images (the checkout is on v9.9.9)"* ]]
    run compose_calls
    [[ "${lines[0]}" == *" pull" ]]
    [[ "$output" != *"build"* ]]
}

@test "a clone on a candidate tag only runs the candidate's images" {
    stub_git v9.9.9-rc.2 v9.9.9-rc.10
    run "$CLI" paths
    [[ "$output" == *"<service>:9.9.9-rc.10 "* ]]
}

@test "a clone off any tag builds :dev, as before" {
    stub_git
    run "$CLI" paths
    [[ "$output" == *"<service>:dev (dev: built from home"* ]]
    run "$CLI" upgrade --skip-doctor
    run compose_calls
    [[ "${lines[0]}" == *" build --pull" ]]
}

@test "SMOKING_PI_VERSION=dev builds even on a tag; any other value is a pin that wins" {
    stub_git v9.9.9
    SMOKING_PI_VERSION=dev run "$CLI" upgrade --skip-doctor
    run compose_calls
    [[ "${lines[0]}" == *" build --pull" ]]
    SMOKING_PI_VERSION=1.0.0 run "$CLI" paths
    [[ "$output" == *"<service>:1.0.0"* ]]
    [[ "$output" != *"checkout is on"* ]]
}

@test "a tag with no published images: upgrade stops and names the way to build" {
    stub_git v9.9.9
    fail_docker_on " pull"
    run "$CLI" upgrade --skip-doctor
    [ "$status" -eq 1 ]
    [[ "$output" == *"SMOKING_PI_VERSION=dev smoking-pi upgrade"* ]]
    run compose_calls
    [[ "$output" != *"up -d"* ]]
}

@test "packaged mode ignores git: the installed tree's version, as before" {
    stub_git v1.2.3
    SMOKING_PI_PACKAGED=1 run "$CLI" paths
    [[ "$output" == *"<service>:9.9.9"* ]]
}

# `smoking-pi` alone, and `link`: the command by name from any directory.
# A clone of the stub tree: the command copied into it and run from there,
# so its home is the tree it sits in, as in a real checkout.
stub_clone() {
    mkdir -p "$STUB_HOME/.git" "$STUB_HOME/cli" "$STUB_HOME/packaging"
    cp -r "$REPO/cli/smoking-pi" "$REPO/cli/lib" "$STUB_HOME/cli/"
    # The forwarding file the old links point at.
    cp "$REPO/packaging/smoking-pi" "$STUB_HOME/packaging/smoking-pi"
    unset SMOKING_PI_HOME
    # No sudo that could reach a real /usr/local/bin.
    printf '#!/bin/sh\nexit 1\n' > "$BATS_TEST_TMPDIR/bin/sudo"; chmod +x "$BATS_TEST_TMPDIR/bin/sudo"
    export SMOKING_PI_BIN_DIRS="$BATS_TEST_TMPDIR/sysbin $BATS_TEST_TMPDIR/userbin"
    mkdir -p "$BATS_TEST_TMPDIR/sysbin"
}

@test "no command: version, edition, how many services run, where to open it, the common commands" {
    run "$CLI"
    [ "$status" -eq 0 ]
    [[ "$output" == *"smoking-pi 9.9.9"* ]]
    [[ "$output" == *"Edition:  pro, 1 of 3 services running"* ]]
    [[ "$output" == *"Open:     http://192.0.2.10:8080/"* ]]
    [[ "$output" == *"smoking-pi passwords"* ]]
    [[ "$output" == *"smoking-pi --help"* ]]
    # Short: not the reference.
    [[ "$output" != *"Usage: smoking-pi <command>"* ]]
}

@test "no command, nothing installed: says so and names install" {
    rm "$SMOKING_PI_ENV_FILE"
    run "$CLI"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Not installed on this machine yet"* ]]
    [[ "$output" == *"smoking-pi install"* ]]
}

@test "no command, Docker not answering: says so rather than 0 of N running" {
    fail_docker_on "ps --status running"
    run "$CLI"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Docker did not answer"* ]]
    [[ "$output" != *"services running"* ]]
}

@test "--help is still the full reference" {
    run "$CLI" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage: smoking-pi <command>"* ]]
    [[ "$output" == *"  link "* ]]
}

@test "link from a clone: the first writable directory gets a symlink to the checkout's command" {
    stub_clone
    run "$STUB_HOME/cli/smoking-pi" link
    [ "$status" -eq 0 ]
    [[ "$output" == *"smoking-pi is now a command: $BATS_TEST_TMPDIR/sysbin/smoking-pi"* ]]
    [ "$(readlink "$BATS_TEST_TMPDIR/sysbin/smoking-pi")" = "$STUB_HOME/cli/smoking-pi" ]
    # Not on this shell's PATH: it says what to do.
    [[ "$output" == *"open a new terminal"* ]]
    # And the link works from anywhere, finding its home through it.
    cd "$BATS_TEST_TMPDIR"
    run "$BATS_TEST_TMPDIR/sysbin/smoking-pi" paths
    [[ "$output" == *"home:     $STUB_HOME"* ]]
}

@test "link without root and without a passwordless sudo falls back to the user's directory" {
    stub_clone
    chmod 555 "$BATS_TEST_TMPDIR/sysbin"
    [ -w "$BATS_TEST_TMPDIR/sysbin" ] && skip "running as root: every directory is writable"
    run "$STUB_HOME/cli/smoking-pi" link
    [ "$status" -eq 0 ]
    [ ! -e "$BATS_TEST_TMPDIR/sysbin/smoking-pi" ]
    [ "$(readlink "$BATS_TEST_TMPDIR/userbin/smoking-pi")" = "$STUB_HOME/cli/smoking-pi" ]
}

@test "link when already on the PATH: nothing to do; --quiet says nothing" {
    stub_clone
    ln -s "$STUB_HOME/cli/smoking-pi" "$BATS_TEST_TMPDIR/sysbin/smoking-pi"
    export PATH="$BATS_TEST_TMPDIR/sysbin:$PATH"
    run "$STUB_HOME/cli/smoking-pi" link
    [[ "$output" == *"already"* ]]
    run "$STUB_HOME/cli/smoking-pi" link --quiet
    [ -z "$output" ]
}

@test "link never shadows a package's smoking-pi, and never replaces a real file" {
    stub_clone
    mkdir -p "$BATS_TEST_TMPDIR/usr/bin"
    printf '#!/bin/sh\n' > "$BATS_TEST_TMPDIR/usr/bin/smoking-pi"; chmod +x "$BATS_TEST_TMPDIR/usr/bin/smoking-pi"
    export PATH="$BATS_TEST_TMPDIR/usr/bin:$PATH"
    run "$STUB_HOME/cli/smoking-pi" link
    [ "$status" -eq 0 ]
    [[ "$output" == *"Not linking"* ]]
    [ ! -e "$BATS_TEST_TMPDIR/sysbin/smoking-pi" ]
}

@test "link repoints a link another checkout left, and says so" {
    stub_clone
    ln -s /elsewhere/packaging/smoking-pi "$BATS_TEST_TMPDIR/sysbin/smoking-pi"
    export PATH="$BATS_TEST_TMPDIR/sysbin:$PATH"
    run "$STUB_HOME/cli/smoking-pi" link
    [ "$status" -eq 0 ]
    [ "$(readlink "$BATS_TEST_TMPDIR/sysbin/smoking-pi")" = "$STUB_HOME/cli/smoking-pi" ]
}

@test "link repoints this checkout's link from before the move to cli/, and the old one still runs meanwhile" {
    stub_clone
    ln -s "$STUB_HOME/packaging/smoking-pi" "$BATS_TEST_TMPDIR/sysbin/smoking-pi"
    export PATH="$BATS_TEST_TMPDIR/sysbin:$PATH"
    # Through the old link: the forwarding file runs the command in cli/.
    cd "$BATS_TEST_TMPDIR"
    run smoking-pi paths
    [ "$status" -eq 0 ]
    [[ "$output" == *"home:     $STUB_HOME"* ]]
    run smoking-pi link
    [ "$status" -eq 0 ]
    [ "$(readlink "$BATS_TEST_TMPDIR/sysbin/smoking-pi")" = "$STUB_HOME/cli/smoking-pi" ]
}

@test "link leaves the package's /usr/bin link alone (it points into /usr/lib, not a checkout)" {
    stub_clone
    mkdir -p "$BATS_TEST_TMPDIR/usr/bin" "$BATS_TEST_TMPDIR/usr/lib/smoking-pi"
    cp -r "$REPO/cli/smoking-pi" "$REPO/cli/lib" "$BATS_TEST_TMPDIR/usr/lib/smoking-pi/"
    ln -s ../lib/smoking-pi/smoking-pi "$BATS_TEST_TMPDIR/usr/bin/smoking-pi"
    export PATH="$BATS_TEST_TMPDIR/usr/bin:$PATH"
    run "$STUB_HOME/cli/smoking-pi" link
    [ "$status" -eq 0 ]
    [[ "$output" == *"Not linking"* ]]
    [ ! -e "$BATS_TEST_TMPDIR/sysbin/smoking-pi" ]
}

@test "every module in cli/lib is loaded, and the entry loads nothing that is not there" {
    local listed on_disk
    listed="$(sed -n 's/^for _module in \(.*\); do$/\1/p' "$CLI" | tr ' ' '\n' | sort)"
    on_disk="$(cd "$REPO/cli/lib" && ls -- *.sh | sed 's/\.sh$//' | sort)"
    [ -n "$listed" ]
    [ "$listed" = "$on_disk" ] || { echo "loaded: $listed"; echo "on disk: $on_disk"; return 1; }
}

@test "link outside a clone (the package's tree) does nothing" {
    mkdir -p "$STUB_HOME/cli" && cp -r "$REPO/cli/smoking-pi" "$REPO/cli/lib" "$STUB_HOME/cli/"
    export SMOKING_PI_BIN_DIRS="$BATS_TEST_TMPDIR/sysbin"; mkdir -p "$BATS_TEST_TMPDIR/sysbin"
    run "$CLI" link
    [ "$status" -eq 0 ]
    [[ "$output" == *"not a clone"* ]]
    [ ! -e "$BATS_TEST_TMPDIR/sysbin/smoking-pi" ]
}

@test "no command from a clone that is not on the PATH: the tip names link" {
    stub_clone
    run "$STUB_HOME/cli/smoking-pi"
    [[ "$output" == *"cli/smoking-pi link"* ]]
}

@test "install from a clone links the command, so the names it prints work in every directory" {
    stub_clone
    rm "$SMOKING_PI_ENV_FILE"
    run "$STUB_HOME/cli/smoking-pi" install --edition pro --database influxdb --yes
    [ "$status" -eq 0 ]
    [ "$(readlink "$BATS_TEST_TMPDIR/sysbin/smoking-pi")" = "$STUB_HOME/cli/smoking-pi" ]
    [[ "$output" == *"smoking-pi is now a command"* ]]
}

@test "every edition's setup.sh links the command (the path the README gives a clone)" {
    for ed in basic standard pro; do
        grep -q 'cli/smoking-pi" link --quiet' "$REPO/editions/$ed/setup.sh" \
            || { echo "editions/$ed/setup.sh does not run smoking-pi link"; return 1; }
    done
}

@test "restart of the whole Pro stack checks the InfluxDB token after; of one service, does not" {
    printf '#!/bin/sh\necho SYNC "$SMOKING_PI_ENV_FILE" >> "%s"\n' "$DOCKER_LOG" > "$STUB_HOME/editions/pro/sync-influx-token.sh"
    chmod +x "$STUB_HOME/editions/pro/sync-influx-token.sh"
    run "$CLI" restart grafana
    [ "$status" -eq 0 ]
    if grep -q '^SYNC' "$DOCKER_LOG"; then false; fi
    run "$CLI" restart
    [ "$status" -eq 0 ]
    [ "$(grep -n 'compose.* restart$' "$DOCKER_LOG" | cut -d: -f1)" -lt "$(grep -n '^SYNC' "$DOCKER_LOG" | cut -d: -f1)" ]
    grep -qx "SYNC $SMOKING_PI_ENV_FILE" "$DOCKER_LOG"
    # Waited for influxd to answer first: asked too early, the script
    # finds no usable token and cries wolf.
    grep -q 'exec -T influxdb influx ping' "$DOCKER_LOG"
    # On ClickHouse there is no InfluxDB token to check.
    : > "$DOCKER_LOG"
    printf 'COMPOSE_PROFILES=clickhouse\n' > "$SMOKING_PI_ENV_FILE"
    run "$CLI" restart
    if grep -q '^SYNC' "$DOCKER_LOG"; then false; fi
}

@test "restart: a failing token check or an InfluxDB that never answers warns, and the restart still succeeds" {
    printf '#!/bin/sh\necho SYNC >> "%s"\nexit 1\n' "$DOCKER_LOG" > "$STUB_HOME/editions/pro/sync-influx-token.sh"
    chmod +x "$STUB_HOME/editions/pro/sync-influx-token.sh"
    run "$CLI" restart
    [ "$status" -eq 0 ]
    [[ "$output" == *"token check failed"* ]]
    : > "$DOCKER_LOG"
    fail_docker_on "influx ping"
    run "$CLI" restart
    [ "$status" -eq 0 ]
    [[ "$output" == *"did not answer within 90 s"* ]]
    if grep -q '^SYNC' "$DOCKER_LOG"; then false; fi
}

# --- enable / disable: the optional services --------------------------------------

@test "enable alone lists the edition's optional services, on or off" {
    run "$CLI" enable
    [ "$status" -eq 0 ]
    [[ "$output" == *"mcp        on "* ]]
    [[ "$output" == *"alerts     off "* ]]
    [[ "$output" == *"inference  off "* ]]
    if compose_calls | grep -q ' up '; then false; fi
}

@test "enable records the profile once and applies the whole stack" {
    run "$CLI" enable alerts ai
    [ "$status" -eq 0 ]
    grep -qx 'COMPOSE_PROFILES=influxdb,mcp,alerts,ai' "$SMOKING_PI_ENV_FILE"
    compose_calls | grep -q 'up -d --remove-orphans'
    # Where alerts go is its own command; the profile alone sends nothing.
    [[ "$output" == *"smoking-pi alerts --telegram"* ]]
    [[ "$output" == *"config set ANTHROPIC_API_KEY"* ]]
}

@test "disable removes only that profile; nothing to do changes nothing" {
    run "$CLI" disable mcp
    [ "$status" -eq 0 ]
    grep -qx 'COMPOSE_PROFILES=influxdb' "$SMOKING_PI_ENV_FILE"
    : > "$DOCKER_LOG"
    run "$CLI" disable mcp
    [ "$status" -eq 0 ]
    [[ "$output" == *"Nothing to change"* ]]
    if compose_calls | grep -q ' up '; then false; fi
}

@test "enable refuses the backend, an unknown name, and a service the edition lacks, changing nothing" {
    run "$CLI" enable clickhouse
    [ "$status" -eq 2 ]
    [[ "$output" == *"fixed at install"* ]]
    run "$CLI" enable alerts nonsense
    [ "$status" -eq 2 ]
    [[ "$output" == *"unknown service: nonsense"* ]]
    grep -qx 'COMPOSE_PROFILES=influxdb,mcp' "$SMOKING_PI_ENV_FILE"
    mkdir -p "$STUB_HOME/editions/standard"
    cp "$REPO/editions/standard/docker-compose.yml" "$STUB_HOME/editions/standard/"
    SMOKING_PI_EDITION=standard run "$CLI" enable mcp
    [ "$status" -eq 2 ]
    [[ "$output" == *"not part of the standard edition"* ]]
    if compose_calls | grep -q ' up '; then false; fi
}

@test "enable refuses a profile list with no backend: applying it would stop the database" {
    printf 'COMPOSE_PROFILES=mcp\n' > "$SMOKING_PI_ENV_FILE"
    run "$CLI" enable alerts
    [ "$status" -eq 1 ]
    [[ "$output" == *"config set COMPOSE_PROFILES influxdb,mcp"* ]]
    grep -qx 'COMPOSE_PROFILES=mcp' "$SMOKING_PI_ENV_FILE"
    if compose_calls | grep -q ' up '; then false; fi
}

@test "enable dns hands over to dns enable (the port check and the router steps)" {
    run "$CLI" enable dns alerts
    [ "$status" -eq 2 ]
    [[ "$output" == *"on its own"* ]]
}

# --- tunnel: Cloudflare quick tunnels --------------------------------------------

@test "tunnel start publishes nothing without a confirmation" {
    run "$CLI" tunnel start </dev/null
    [ "$status" -eq 2 ]
    [[ "$output" == *"--yes"* ]]
    if grep -q '^docker run' "$DOCKER_LOG"; then false; fi
}

@test "tunnel start --yes: one labeled, pinned cloudflared per page on the right network, current URLs" {
    run "$CLI" tunnel start --yes
    [ "$status" -eq 0 ]
    [ "$(grep -c '^docker run' "$DOCKER_LOG")" -eq 3 ]
    grep -q '^docker run -d --name pro-tunnel-grafana --label io.smoking-pi.quick-tunnel=grafana --network pro_default .*cloudflare/cloudflared:2026\.5\.0 tunnel --no-autoupdate --url http://grafana:3000' "$DOCKER_LOG"
    grep -q '^docker run -d --name pro-tunnel-smokeping .*--network bridge --add-host=host.docker.internal:host-gateway ' "$DOCKER_LOG"
    if grep -q 'cloudflared:latest' "$DOCKER_LOG"; then false; fi
    [[ "$output" == *"grafana    https://grafana-now.trycloudflare.com"* ]]
    [[ "$output" != *"old-one"* ]]
    [[ "$output" != *"api.trycloudflare"* ]]
}

@test "tunnel start after a partial failure starts only the missing pages" {
    export STUB_TUNNELS='grafana pro-tunnel-grafana\nwebadmin pro-tunnel-webadmin\n'
    run "$CLI" tunnel start --yes
    [ "$status" -eq 0 ]
    [ "$(grep -c '^docker run' "$DOCKER_LOG")" -eq 1 ]
    grep -q '^docker run -d --name pro-tunnel-smokeping ' "$DOCKER_LOG"
    [[ "$output" == *"grafana    https://grafana-now.trycloudflare.com (already running)"* ]]
}

@test "tunnel start with tunnels already up only shows them" {
    export STUB_TUNNELS='grafana pro-tunnel-grafana\nwebadmin pro-tunnel-webadmin\nsmokeping pro-tunnel-smokeping\n'
    run "$CLI" tunnel start --yes
    [ "$status" -eq 0 ]
    [[ "$output" == *"already running"* ]]
    if grep -q '^docker run' "$DOCKER_LOG"; then false; fi
}

@test "tunnel stop removes labeled tunnels and the old script's tunnel-* ones, nothing else" {
    export STUB_TUNNELS='grafana pro-tunnel-grafana\n'
    # The old script's (cloudflared, named tunnel-<page>) go too; a container
    # that only shares such a name, or only the word, stays.
    export STUB_NAMES='pro-grafana-1 grafana/grafana:13\ntunnel-webadmin cloudflare/cloudflared:latest\ntunnel-grafana nginx:1\nmy-tunnel-thing cloudflare/cloudflared:latest\n'
    run "$CLI" tunnel stop
    [ "$status" -eq 0 ]
    grep -qx 'docker rm -f pro-tunnel-grafana tunnel-webadmin' "$DOCKER_LOG"
}

@test "tunnel with none running says how to start them" {
    run "$CLI" tunnel
    [ "$status" -eq 0 ]
    [[ "$output" == *"No quick tunnels"* ]]
}

# --- dns: the DNS observer --------------------------------------------------------

dns_setup() {
    cp "$REPO/editions/pro/.env.template" "$STUB_HOME/editions/pro/"
    printf 'COMPOSE_PROFILES=influxdb,mcp\n' > "$SMOKING_PI_ENV_FILE"
    chmod 600 "$SMOKING_PI_ENV_FILE"
    # In front of the suite's stub: whether the observer runs (STUB_DNS_RUNNING)
    # and its status script. Everything else falls through to it.
    mkdir -p "$BATS_TEST_TMPDIR/bin3"
    cat > "$BATS_TEST_TMPDIR/bin3/docker" <<STUB
#!/bin/sh
case "\$*" in
    *"ps -q --status running dns-observer"*) echo "docker \$*" >> "\$DOCKER_LOG"; [ -z "\${STUB_DNS_RUNNING:-}" ] || echo abc123; exit 0 ;;
    *"exec -T dns-observer python status.py"*) echo "docker \$*" >> "\$DOCKER_LOG"; echo '{"server": {"answering": true}}'; echo "state:          observing"; exit 0 ;;
    *"ps -q --status running config-manager"*) echo "docker \$*" >> "\$DOCKER_LOG"; [ -z "\${STUB_CM_RUNNING:-}" ] || echo cm123; exit 0 ;;
    *"exec -T config-manager python wizard_adopt.py"*) echo "docker \$*" >> "\$DOCKER_LOG"; echo "Would add 230 targets for 46 services"; exit 0 ;;
    *"exec -T dns-observer python connection_test.py"*) echo "docker \$*" >> "\$DOCKER_LOG"; echo "FAIL  router forwards to the Pi: 0/10"; exit "\${STUB_DNS_TEST_RC:-0}" ;;
esac
exec "$BATS_TEST_TMPDIR/bin/docker" "\$@"
STUB
    chmod +x "$BATS_TEST_TMPDIR/bin3/docker"
    # Nothing on port 53 unless STUB_PORT53 says what.
    printf '#!/bin/sh\n[ -z "$STUB_PORT53" ] || echo "$STUB_PORT53"\n' > "$BATS_TEST_TMPDIR/bin3/ss"
    chmod +x "$BATS_TEST_TMPDIR/bin3/ss"
    export PATH="$BATS_TEST_TMPDIR/bin3:$PATH" STUB_PORT53=""
}

@test "dns enable generates the password, adds the profile, starts it, says what to set on the router" {
    dns_setup
    run "$CLI" dns enable
    [ "$status" -eq 0 ]
    grep -Eq '^DNS_ADMIN_PASSWORD=[0-9a-f]{48}$' "$SMOKING_PI_ENV_FILE"
    grep -qx 'COMPOSE_PROFILES=influxdb,mcp,dns' "$SMOKING_PI_ENV_FILE"
    grep -q 'up -d dns-observer' "$DOCKER_LOG"
    [[ "$output" == *"primary:   192.0.2.10"* ]]
    [[ "$output" == *"secondary: 1.1.1.1"* ]]
    # The password is never printed, and the file keeps its mode.
    pw="$(sed -n 's/^DNS_ADMIN_PASSWORD=//p' "$SMOKING_PI_ENV_FILE")"
    [[ "$output" != *"$pw"* ]]
    [ "$(stat -c %a "$SMOKING_PI_ENV_FILE")" = 600 ]
}

@test "dns enable keeps an existing password and adds the profile once" {
    dns_setup
    printf 'COMPOSE_PROFILES=influxdb,dns\nDNS_ADMIN_PASSWORD=keepme\n' > "$SMOKING_PI_ENV_FILE"
    run "$CLI" dns enable
    [ "$status" -eq 0 ]
    grep -qx 'DNS_ADMIN_PASSWORD=keepme' "$SMOKING_PI_ENV_FILE"
    grep -qx 'COMPOSE_PROFILES=influxdb,dns' "$SMOKING_PI_ENV_FILE"
}

@test "dns enable refuses when something else holds port 53, and changes nothing" {
    dns_setup
    export STUB_PORT53='UNCONN 0 0 0.0.0.0:53 0.0.0.0:* users:(("dnsmasq",pid=1,fd=4))'
    run "$CLI" dns enable
    [ "$status" -eq 1 ]
    [[ "$output" == *"dnsmasq"* ]]
    grep -qx 'COMPOSE_PROFILES=influxdb,mcp' "$SMOKING_PI_ENV_FILE"
    if grep -q 'up -d dns-observer' "$DOCKER_LOG"; then false; fi
}

@test "dns disable asks first (the router may still point here), --yes removes the profile" {
    dns_setup
    printf 'COMPOSE_PROFILES=influxdb,dns,mcp\n' > "$SMOKING_PI_ENV_FILE"
    run "$CLI" dns disable < /dev/null
    [ "$status" -eq 2 ]
    grep -qx 'COMPOSE_PROFILES=influxdb,dns,mcp' "$SMOKING_PI_ENV_FILE"
    run "$CLI" dns disable --yes
    [ "$status" -eq 0 ]
    [[ "$output" == *"point it back"* ]]
    grep -qx 'COMPOSE_PROFILES=influxdb,mcp' "$SMOKING_PI_ENV_FILE"
}

@test "dns status: the observer's own verdict when running, 'down' and why when not" {
    dns_setup
    run "$CLI" dns status
    [ "$status" -eq 1 ]
    [[ "$output" == *"down"* ]]
    [[ "$output" == *"smoking-pi dns enable"* ]]
    printf 'COMPOSE_PROFILES=influxdb,dns\n' > "$SMOKING_PI_ENV_FILE"
    run "$CLI" dns status
    [[ "$output" == *"smoking-pi up"* ]]
    export STUB_DNS_RUNNING=1
    run "$CLI" dns status
    [ "$status" -eq 0 ]
    [[ "$output" == *"observing"* ]]
}

@test "dns test: runs the path check in the observer, passes its exit code and options, notes a DHCP lease" {
    dns_setup
    # The Pi's address, and whether it is a lease: the host's `ip`, stubbed.
    cat > "$BATS_TEST_TMPDIR/bin3/ip" <<'STUB'
#!/bin/sh
case "$*" in
    *"route get"*) echo "1.1.1.1 via 192.168.1.1 dev wlan0 src 192.168.1.10 uid 1000" ;;
    *"addr show"*) echo "3: wlan0    inet 192.168.1.10/24 brd 192.168.1.255 scope global dynamic noprefixroute wlan0" ;;
esac
STUB
    chmod +x "$BATS_TEST_TMPDIR/bin3/ip"
    run "$CLI" dns test
    [ "$status" -eq 1 ]
    [[ "$output" == *"not running"* ]]
    [[ "$output" == *"smoking-pi dns enable"* ]]
    if grep -q 'connection_test.py' "$DOCKER_LOG"; then false; fi
    export STUB_DNS_RUNNING=1
    run "$CLI" dns test --via 192.168.1.1
    [ "$status" -eq 0 ]
    grep -q 'exec -T dns-observer python connection_test.py --via 192.168.1.1' "$DOCKER_LOG"
    [[ "$output" == *"note: 192.168.1.10 comes from DHCP"* ]]
    export STUB_DNS_TEST_RC=1
    run "$CLI" dns test --json
    [ "$status" -eq 1 ]
    [[ "$output" != *"note:"* ]]
}

@test "dns adopt: runs in config-manager, passes --dry-run, refuses other options and a stopped API" {
    dns_setup
    run "$CLI" dns adopt
    [ "$status" -eq 1 ]
    [[ "$output" == *"config-manager is not running"* ]]
    export STUB_CM_RUNNING=1
    run "$CLI" dns adopt --dry-run
    [ "$status" -eq 0 ]
    grep -q 'exec -T config-manager python wizard_adopt.py --dry-run' "$DOCKER_LOG"
    [[ "$output" == *"46 services"* ]]
    run "$CLI" dns adopt --retire-only --dry-run
    [ "$status" -eq 0 ]
    grep -q 'exec -T config-manager python wizard_adopt.py --retire-only --dry-run' "$DOCKER_LOG"
    run "$CLI" dns adopt --force
    [ "$status" -eq 0 ]
    grep -q 'exec -T config-manager python wizard_adopt.py --force' "$DOCKER_LOG"
    run "$CLI" dns adopt --yes
    [ "$status" -eq 2 ]
    [[ "$output" == *"unknown option --yes"* ]]
}

@test "install accepts the dns profile" {
    rm -f "$SMOKING_PI_ENV_FILE"
    run "$CLI" install --yes --database influxdb --profiles dns
    [[ "$output" != *"unknown profile"* ]]
}

@test "dns enable says so, and prints no router advice, when the observer never answers" {
    dns_setup
    cat > "$BATS_TEST_TMPDIR/bin3/sleep" <<'STUB'
#!/bin/sh
exit 0
STUB
    chmod +x "$BATS_TEST_TMPDIR/bin3/sleep"
    sed -i 's/echo .{"server": {"answering": true}}.;/echo "{}";/' "$BATS_TEST_TMPDIR/bin3/docker"
    run "$CLI" dns enable
    [ "$status" -eq 1 ]
    [[ "$output" == *"did not start answering"* ]]
    [[ "$output" != *"primary:"* ]]
}

@test "packaged, a failed pull names the network, not a build from /opt" {
    fail_docker_on " pull"
    SMOKING_PI_VERSION=2.12.0 run "$CLI" upgrade --skip-doctor
    [ "$status" -eq 1 ]
    [[ "$output" == *"check the network"* ]]
    [[ "$output" != *"SMOKING_PI_VERSION=dev"* ]]
}

# Two Compose runs on one project race each other's recreates: the v2.13.2
# and v2.13.7 upgrades on the reference Pi failed with "Conflict. The
# container name "/<id>_pro-influxdb-1" is already in use" while a second
# session's upgrade / config set was running. A reproduction with two
# concurrent `up`s on Compose v2.38.2 left both replaced services running
# under their temporary names.

hold_lock() {
    # Another command holding the stack lock: flock -o keeps it in flock's
    # own process, not the sleep's.
    flock -o "$STUB_HOME/editions/pro" /bin/sleep 60 3>&- &
    LOCK_PID=$!
    until ! flock -n "$STUB_HOME/editions/pro" true; do :; done 2>/dev/null
}

release_lock() {
    # The sleep ending ends flock, which releases the lock.
    pkill -P "$LOCK_PID" sleep
    wait "$LOCK_PID" || true
}

@test "a command that changes containers waits for another one to finish" {
    hold_lock
    "$CLI" up > "$BATS_TEST_TMPDIR/out" 2>&1 3>&- &
    pid=$!
    for _ in $(seq 100); do
        grep -q "waiting for it to finish" "$BATS_TEST_TMPDIR/out" && break
        /bin/sleep 0.1
    done
    grep -q "Another smoking-pi command is changing this stack" "$BATS_TEST_TMPDIR/out"
    if grep -q ' up -d' "$DOCKER_LOG"; then false; fi
    release_lock
    wait "$pid"
    grep -q ' up -d' "$DOCKER_LOG"
}

@test "a command that only reads does not wait for the lock" {
    hold_lock
    run timeout 10 "$CLI" status
    release_lock
    [ "$status" -eq 0 ]
    [[ "$output" != *"waiting"* ]]
    grep -q ' ps$' "$DOCKER_LOG"
}

@test "the lock is not passed on to docker compose" {
    # A child holding fd 9 open past the command would hold the lock too.
    printf '#!/bin/sh\necho "fd9 $([ -e /proc/self/fd/9 ] && echo open || echo closed)" >> "$DOCKER_LOG"\nexit 0\n' > "$BATS_TEST_TMPDIR/bin/docker"
    run "$CLI" up
    grep -q 'fd9 closed' "$DOCKER_LOG"
    if grep -q 'fd9 open' "$DOCKER_LOG"; then false; fi
}

@test "after up, a container left under Compose's temporary name gets its name back" {
    export STUB_STATES='b2a14da7a0ee_pro-influxdb-1 running\npro-postgres-1 running\n'
    run "$CLI" up
    [ "$status" -eq 0 ]
    grep -qx 'docker rename b2a14da7a0ee_pro-influxdb-1 pro-influxdb-1' "$DOCKER_LOG"
    [[ "$output" == *"Renamed b2a14da7a0ee_pro-influxdb-1 to pro-influxdb-1"* ]]
    if grep -q '^docker rm\|pro-postgres-1' <(grep '^docker \(rm\|rename\)' "$DOCKER_LOG"); then false; fi
}

@test "a temporary copy that never started, beside the named container, is removed" {
    export STUB_STATES='53642260c024_pro-dns-observer-1 created\npro-dns-observer-1 running\n'
    run "$CLI" upgrade --skip-doctor
    [ "$status" -eq 0 ]
    grep -qx 'docker rm 53642260c024_pro-dns-observer-1' "$DOCKER_LOG"
    if grep -q '^docker rename\|^docker rm pro-dns-observer-1' "$DOCKER_LOG"; then false; fi
}

@test "two running copies are left for a person, with a warning" {
    export STUB_STATES='53642260c024_pro-dns-observer-1 running\npro-dns-observer-1 running\n'
    run "$CLI" up
    [ "$status" -eq 0 ]
    [[ "$output" == *"both exist"* ]]
    if grep -q '^docker \(rm\|rename\)' "$DOCKER_LOG"; then false; fi
}

@test "upgrade: up failing mid-recreate is retried once, after the leftovers are repaired" {
    export STUB_UP_FAILS=1
    export STUB_STATES='b2a14da7a0ee_pro-influxdb-1 created\npro-influxdb-1 running\n'
    run "$CLI" upgrade --skip-doctor
    [ "$status" -eq 0 ]
    [[ "$output" == *"another Compose run was changing this stack"* ]]
    [ "$(grep -c ' up -d --remove-orphans' "$DOCKER_LOG")" -eq 2 ]
    # The repair comes between the two attempts.
    first_rm=$(grep -n '^docker rm b2a14da7a0ee_pro-influxdb-1' "$DOCKER_LOG" | head -1 | cut -d: -f1)
    second_up=$(grep -n ' up -d --remove-orphans' "$DOCKER_LOG" | sed -n 2p | cut -d: -f1)
    [ "$first_rm" -lt "$second_up" ]
}

@test "up failing with no container mid-recreate is not retried, and upgrade fails" {
    export STUB_UP_FAILS=1
    export STUB_STATES='pro-influxdb-1 running\n'
    run "$CLI" upgrade --skip-doctor
    [ "$status" -ne 0 ]
    [ "$(grep -c ' up -d --remove-orphans' "$DOCKER_LOG")" -eq 1 ]
    [[ "$output" != *"trying once more"* ]]
}

@test "a retry that fails again fails the upgrade" {
    export STUB_UP_FAILS=2
    export STUB_STATES='b2a14da7a0ee_pro-influxdb-1 created\npro-influxdb-1 running\n'
    run "$CLI" upgrade --skip-doctor
    [ "$status" -ne 0 ]
    [ "$(grep -c ' up -d --remove-orphans' "$DOCKER_LOG")" -eq 2 ]
}

# --- LAN discovery (DNS-SD) ----------------------------------------------
# Avahi announces whatever sits in its services directory; the record must
# say where the page is and nothing a stranger on the network should read.

@test "up announces the stack to Avahi: edition, version, page port, no secrets" {
    mkdir -p "$BATS_TEST_TMPDIR/avahi"
    export SMOKING_PI_AVAHI_FILE="$BATS_TEST_TMPDIR/avahi/smoking-pi.service"
    printf 'COMPOSE_PROFILES=influxdb\nPOSTGRES_PASSWORD=s3cr3t\nMCP_API_TOKEN=t0k3n\n' > "$SMOKING_PI_ENV_FILE"
    run "$CLI" up
    [ "$status" -eq 0 ]
    f="$SMOKING_PI_AVAHI_FILE"
    grep -q '<type>_smoking-pi._tcp</type>' "$f"
    grep -q '<type>_http._tcp</type>' "$f"
    grep -q '<port>8080</port>' "$f"
    grep -q '<txt-record>edition=pro</txt-record>' "$f"
    grep -q '<txt-record>version=9.9.9</txt-record>' "$f"
    grep -q '<txt-record>grafana=3000</txt-record>' "$f"
    if grep -q -e s3cr3t -e t0k3n "$f"; then false; fi
    [ "$(stat -c '%a' "$f")" = 644 ]
    # Unchanged content is not rewritten (Avahi reloads on every write).
    touch -d '2001-01-01' "$f"
    run "$CLI" up
    [ "$(stat -c '%Y' "$f")" = "$(date -d '2001-01-01' +%s)" ]
    # down withdraws it: nothing answers there any more.
    run "$CLI" down
    [ ! -e "$f" ]
}

@test "without Avahi's directory, up announces nothing and still succeeds" {
    export SMOKING_PI_AVAHI_FILE="$BATS_TEST_TMPDIR/no-avahi/smoking-pi.service"
    run "$CLI" up
    [ "$status" -eq 0 ]
    [ ! -e "$BATS_TEST_TMPDIR/no-avahi" ]
}

@test "Basic announces SmokePing's own port and no Grafana" {
    mkdir -p "$BATS_TEST_TMPDIR/avahi"
    export SMOKING_PI_AVAHI_FILE="$BATS_TEST_TMPDIR/avahi/smoking-pi.service" SMOKING_PI_EDITION=basic
    printf 'SMOKEPING_PORT=80\n' > "$SMOKING_PI_ENV_FILE"
    run "$CLI" up
    grep -q '<port>80</port>' "$SMOKING_PI_AVAHI_FILE"
    grep -q 'edition=basic' "$SMOKING_PI_AVAHI_FILE"
    if grep -q grafana "$SMOKING_PI_AVAHI_FILE"; then false; fi
}

@test "discover lists each node once, its IPv4 answer, name unescaped" {
    cat > "$BATS_TEST_TMPDIR/bin/avahi-browse" <<'STUB'
#!/bin/sh
cat <<'OUT'
+;wlan0;IPv6;Smoking\032Pi\032on\032smokingpi;_smoking-pi._tcp;local
=;wlan0;IPv6;Smoking\032Pi\032on\032smokingpi;_smoking-pi._tcp;local;smokingpi.local;fd00::27;8080;"grafana=3000" "path=/" "version=2.14.1" "edition=pro"
=;wlan0;IPv4;Smoking\032Pi\032on\032smokingpi;_smoking-pi._tcp;local;smokingpi.local;192.0.2.27;8080;"grafana=3000" "path=/" "version=2.14.1" "edition=pro"
=;eth0;IPv4;Smoking\032Pi\032on\032lab;_smoking-pi._tcp;local;lab.local;192.0.2.56;80;"path=/" "version=2.13.8" "edition=basic"
=;eth0;IPv4;Smoking\032Pi\032on\032smokingpi;_smoking-pi._tcp;local;smokingpi.local;192.0.2.99;8080;"grafana=3000" "path=/" "version=2.14.1" "edition=pro"
OUT
STUB
    chmod +x "$BATS_TEST_TMPDIR/bin/avahi-browse"
    run "$CLI" discover
    [ "$status" -eq 0 ]
    [ "$(printf '%s\n' "$output" | grep -c '^Smoking Pi on smokingpi$')" -eq 1 ]
    [[ "$output" == *"http://smokingpi.local:8080/   (192.0.2.27, pro 2.14.1)"* ]]
    [[ "$output" != *"fd00::27"* ]]
    # The same host again on another interface: the first IPv4 answer stays.
    [[ "$output" != *"192.0.2.99"* ]]
    # Port 80 needs no port in the URL.
    [[ "$output" == *"http://lab.local/   (192.0.2.56, basic 2.13.8)"* ]]
}

@test "discover says why when nothing answers, and what to install without avahi-browse" {
    printf '#!/bin/sh\nexit 0\n' > "$BATS_TEST_TMPDIR/bin/avahi-browse"
    chmod +x "$BATS_TEST_TMPDIR/bin/avahi-browse"
    run "$CLI" discover
    [ "$status" -eq 1 ]
    [[ "$output" == *"No Smoking Pi announces itself"* ]]
    rm "$BATS_TEST_TMPDIR/bin/avahi-browse"
    export PATH="$BATS_TEST_TMPDIR/bin:/usr/bin:/bin"
    command -v avahi-browse >/dev/null && skip "avahi-browse is installed on this machine"
    run "$CLI" discover
    [ "$status" -eq 2 ]
    [[ "$output" == *"apt install avahi-utils"* ]]
}

@test "discover says so when avahi-browse cannot reach the daemon, instead of exiting silently" {
    printf '#!/bin/sh\necho "Failed to create client object: Daemon not running" >&2\nexit 1\n' \
        > "$BATS_TEST_TMPDIR/bin/avahi-browse"
    chmod +x "$BATS_TEST_TMPDIR/bin/avahi-browse"
    run "$CLI" discover
    [ "$status" -eq 1 ]
    [[ "$output" == *"is avahi-daemon running here?"* ]]
}

@test "SMOKING_PI_ANNOUNCE=0 keeps the stack off the network and withdraws a record already there" {
    mkdir -p "$BATS_TEST_TMPDIR/avahi"
    export SMOKING_PI_AVAHI_FILE="$BATS_TEST_TMPDIR/avahi/smoking-pi.service"
    run "$CLI" up
    [ -e "$SMOKING_PI_AVAHI_FILE" ]
    SMOKING_PI_ANNOUNCE=0 run "$CLI" up
    [ "$status" -eq 0 ]
    [ ! -e "$SMOKING_PI_AVAHI_FILE" ]
}

# A packaged install keeps the env file in /etc/smoking-pi, root's and 0750.
# Without sudo the command could not read it and answered as if nothing
# were installed: "Not installed ... smoking-pi install", every key "unset",
# the DNS observer "not running". Both shapes of locked: a file this user
# cannot read, and a directory it cannot enter (where -e is false too).
lock_env() {
    [ "$(id -u)" != 0 ] || skip "root reads everything"
    case "$1" in
        file) chmod 000 "$SMOKING_PI_ENV_FILE" ;;
        dir)
            mkdir -p "$BATS_TEST_TMPDIR/etc"
            mv "$SMOKING_PI_ENV_FILE" "$BATS_TEST_TMPDIR/etc/env"
            export SMOKING_PI_ENV_FILE="$BATS_TEST_TMPDIR/etc/env"
            chmod 000 "$BATS_TEST_TMPDIR/etc" ;;
    esac
}

unlock_env() { chmod 700 "$BATS_TEST_TMPDIR/etc" 2>/dev/null || true; }

# A failed assertion before unlock_env would leave a 000 directory that
# bats cannot remove.
teardown() { chmod -R u+rwx "$BATS_TEST_TMPDIR" 2>/dev/null || true; }

@test "an env file this user cannot read: config, passwords, dns and status say sudo, never 'unset'" {
    for shape in file dir; do
        : > "$DOCKER_LOG"
        lock_env "$shape"
        for cmd in "config list" "config get POSTGRES_USER" passwords "dns status" links status up; do
            run $CLI $cmd
            [ "$status" -eq 1 ]
            [[ "$output" == *"only root can"* ]]
            [[ "$output" == *"sudo smoking-pi $cmd"* ]]
            [[ "$output" != *"unset"* ]]
        done
        # Nothing reached docker: no command ran on a guess.
        if grep -q '^docker ' "$DOCKER_LOG"; then false; fi
        unlock_env
        chmod 600 "$SMOKING_PI_ENV_FILE"
    done
}

@test "an env file this user cannot read: the bare command says installed, not 'install it'" {
    for shape in file dir; do
        lock_env "$shape"
        run "$CLI"
        unlock_env
        [ "$status" -eq 0 ]
        [[ "$output" == *"The package is installed"* ]]
        [[ "$output" == *"sudo smoking-pi"* ]]
        # It cannot tell whether install ran, so it names install too.
        [[ "$output" == *"Not set up yet? sudo smoking-pi install"* ]]
        [[ "$output" != *"Not installed"* ]]
    done
}

@test "an env file this user cannot read: version and help still answer" {
    lock_env dir
    run "$CLI" version
    [ "$status" -eq 0 ]
    [ "$output" = 9.9.9 ]
    run "$CLI" --help
    unlock_env
    [ "$status" -eq 0 ]
}

# --- budget: what the configured measurements cost -----------------------------

budget_setup() {
    mkdir -p "$BATS_TEST_TMPDIR/bin4"
    cat > "$BATS_TEST_TMPDIR/bin4/docker" <<STUB
#!/bin/sh
case "\$*" in
    *"ps -q --status running config-manager"*) echo "docker \$*" >> "\$DOCKER_LOG"; [ -z "\${STUB_CM_RUNNING:-}" ] || echo cm123; exit 0 ;;
    *"exec -T config-manager python budget.py"*) echo "docker \$*" >> "\$DOCKER_LOG"; echo "21 targets: 1620 samples/h of 20000 (8.1%)"; exit 0 ;;
esac
exec "$BATS_TEST_TMPDIR/bin/docker" "\$@"
STUB
    chmod +x "$BATS_TEST_TMPDIR/bin4/docker"
    export PATH="$BATS_TEST_TMPDIR/bin4:$PATH"
}

@test "budget: runs in config-manager, passes --json, refuses other options and a stopped API" {
    budget_setup
    run "$CLI" budget
    [ "$status" -eq 1 ]
    [[ "$output" == *"config-manager is not running"* ]]
    # Not run: `! grep` alone would not fail the test here, so assert it.
    run grep -q 'budget.py' "$DOCKER_LOG"
    [ "$status" -ne 0 ]
    export STUB_CM_RUNNING=1
    run "$CLI" budget
    [ "$status" -eq 0 ]
    grep -q 'exec -T config-manager python budget.py$' "$DOCKER_LOG"
    [[ "$output" == *"1620 samples/h"* ]]
    run "$CLI" budget --json
    [ "$status" -eq 0 ]
    grep -q 'exec -T config-manager python budget.py --json' "$DOCKER_LOG"
    run "$CLI" budget --all
    [ "$status" -eq 2 ]
    [[ "$output" == *"unknown option --all"* ]]
}

@test "budget: Basic has no config-manager, and says so instead of trying" {
    budget_setup
    cp "$REPO/editions/basic/docker-compose.yml" "$STUB_HOME/editions/basic/"
    export STUB_CM_RUNNING=1
    SMOKING_PI_EDITION=basic run "$CLI" budget
    [ "$status" -eq 1 ]
    [[ "$output" == *"the basic edition does not ship it"* ]]
    run grep -q 'budget.py' "$DOCKER_LOG"
    [ "$status" -ne 0 ]
}

# --- traffic: what the Pi sent and received --------------------------------------

traffic_setup() {
    mkdir -p "$BATS_TEST_TMPDIR/bin4"
    cat > "$BATS_TEST_TMPDIR/bin4/docker" <<STUB
#!/bin/sh
case "\$*" in
    *"ps -q --status running config-manager"*) echo "docker \$*" >> "\$DOCKER_LOG"; [ -z "\${STUB_CM_RUNNING:-}" ] || echo cm123; exit 0 ;;
    *"exec -T config-manager python traffic.py"*) echo "docker \$*" >> "\$DOCKER_LOG"; echo "this month     1.35 GB"; exit 0 ;;
esac
exec "$BATS_TEST_TMPDIR/bin/docker" "\$@"
STUB
    chmod +x "$BATS_TEST_TMPDIR/bin4/docker"
    export PATH="$BATS_TEST_TMPDIR/bin4:$PATH"
}

@test "traffic: runs in config-manager, passes --json, refuses other options and a stopped API" {
    traffic_setup
    run "$CLI" traffic
    [ "$status" -eq 1 ]
    [[ "$output" == *"config-manager is not running"* ]]
    run grep -q 'traffic.py' "$DOCKER_LOG"
    [ "$status" -ne 0 ]
    export STUB_CM_RUNNING=1
    run "$CLI" traffic
    [ "$status" -eq 0 ]
    grep -q 'exec -T config-manager python traffic.py$' "$DOCKER_LOG"
    [[ "$output" == *"this month"* ]]
    run "$CLI" traffic --json
    [ "$status" -eq 0 ]
    grep -q 'exec -T config-manager python traffic.py --json' "$DOCKER_LOG"
    run "$CLI" traffic --month
    [ "$status" -eq 2 ]
    [[ "$output" == *"unknown option --month"* ]]
}

@test "traffic: Basic and Standard have no meters, and say so instead of trying" {
    traffic_setup
    export STUB_CM_RUNNING=1
    for ed in basic standard; do
        mkdir -p "$STUB_HOME/editions/$ed"
        cp "$REPO/editions/$ed/docker-compose.yml" "$STUB_HOME/editions/$ed/"
        SMOKING_PI_EDITION=$ed run "$CLI" traffic
        [ "$status" -eq 1 ]
        [[ "$output" == *"Pro feature; the $ed edition"* ]]
    done
    run grep -q 'traffic.py' "$DOCKER_LOG"
    [ "$status" -ne 0 ]
}

# --- connect / disconnect: one way in for every assistant ---------------------

connect_setup() {
    mkdir -p "$BATS_TEST_TMPDIR/bin5"
    cat > "$BATS_TEST_TMPDIR/bin5/docker" <<STUB
#!/bin/sh
case "\$*" in
    *"ps -q --status running mcp-server"*) echo "docker \$*" >> "\$DOCKER_LOG"; [ -z "\${STUB_MCP_RUNNING:-}" ] || echo mcp123; exit 0 ;;
    *"exec -T mcp-server python connector.py"*) echo "docker \$*" >> "\$DOCKER_LOG"; echo "CONNECTOR OUT"; exit 0 ;;
esac
exec "$BATS_TEST_TMPDIR/bin/docker" "\$@"
STUB
    chmod +x "$BATS_TEST_TMPDIR/bin5/docker"
    export PATH="$BATS_TEST_TMPDIR/bin5:$PATH"
    export SMOKING_PI_EDITION=pro
}

@test "connect: needs the MCP server running" {
    connect_setup
    run "$CLI" connect grok
    [ "$status" -eq 1 ]
    [[ "$output" == *"The MCP server is not running"* ]]
}

@test "connect NAME: off until MCP_PUBLIC_URL is set, then pairs in the container" {
    connect_setup
    export STUB_MCP_RUNNING=1
    run "$CLI" connect grok
    [ "$status" -eq 1 ]
    [[ "$output" == *"need an HTTPS address"* ]]
    # Not a terminal: no question, the one command that does it instead.
    [[ "$output" == *"connect --tailscale"* ]]
    run grep -q 'connector.py' "$DOCKER_LOG"
    [ "$status" -ne 0 ]
    printf 'MCP_PUBLIC_URL=https://mcp.example.com\n' >> "$SMOKING_PI_ENV_FILE"
    run "$CLI" connect grok
    [ "$status" -eq 0 ]
    grep -q 'exec -T mcp-server python connector.py pair grok$' "$DOCKER_LOG"
}

@test "connect: no name lists what is connected and the URL" {
    connect_setup
    export STUB_MCP_RUNNING=1
    printf 'MCP_PUBLIC_URL=https://mcp.example.com/\n' >> "$SMOKING_PI_ENV_FILE"
    run "$CLI" connect
    [ "$status" -eq 0 ]
    [[ "$output" == *"https://mcp.example.com/mcp"* ]]
    grep -q 'connector.py list$' "$DOCKER_LOG"
}

@test "connect: a name is a plain label, never a shell word or an option" {
    connect_setup
    export STUB_MCP_RUNNING=1
    printf 'MCP_PUBLIC_URL=https://mcp.example.com\n' >> "$SMOKING_PI_ENV_FILE"
    run "$CLI" connect 'grok;rm -rf /'
    [ "$status" -eq 2 ]
    run "$CLI" connect --all
    [ "$status" -eq 2 ]
    run grep -q 'connector.py pair' "$DOCKER_LOG"
    [ "$status" -ne 0 ]
}

@test "connect NAME --as KIND picks the assistant's steps; a bad KIND never reaches the container" {
    connect_setup
    export STUB_MCP_RUNNING=1
    printf 'MCP_PUBLIC_URL=https://mcp.example.com\n' >> "$SMOKING_PI_ENV_FILE"
    run "$CLI" connect laptop --as claude-code
    [ "$status" -eq 0 ]
    grep -q 'connector.py pair laptop --as claude-code$' "$DOCKER_LOG"
    run "$CLI" connect laptop --as 'x;y'
    [ "$status" -eq 2 ]
    run "$CLI" connect laptop --as
    [ "$status" -eq 2 ]
    run "$CLI" connect laptop other
    [ "$status" -eq 2 ]
    run "$CLI" connect laptop --as --check
    [ "$status" -eq 2 ]
    run "$CLI" connect --check
    [ "$status" -eq 2 ]
    run "$CLI" connect --list grok
    [ "$status" -eq 2 ]
    run "$CLI" connect grok -h
    [ "$status" -eq 0 ]
    [[ "$output" == *"--check"* ]]
    [ "$(grep -c 'connector.py pair' "$DOCKER_LOG")" -eq 1 ]
}

@test "connect NAME --check and --list ask the container, with or without a public address" {
    connect_setup
    export STUB_MCP_RUNNING=1
    run "$CLI" connect grok --check
    [ "$status" -eq 0 ]
    grep -q 'connector.py check grok$' "$DOCKER_LOG"
    run "$CLI" connect --list
    [ "$status" -eq 0 ]
    grep -q 'connector.py assistants$' "$DOCKER_LOG"
    run grep -q 'connector.py pair' "$DOCKER_LOG"
    [ "$status" -ne 0 ]
}

@test "disconnect: revokes in the container; openclaw is not a sign-in" {
    connect_setup
    export STUB_MCP_RUNNING=1
    run "$CLI" disconnect grok
    [ "$status" -eq 0 ]
    grep -q 'connector.py revoke grok$' "$DOCKER_LOG"
    run "$CLI" disconnect openclaw
    [ "$status" -eq 1 ]
    [[ "$output" == *"local MCP token"* ]]
    run "$CLI" disconnect
    [ "$status" -ne 0 ]
}

@test "connect: Basic has no MCP server, and says so" {
    connect_setup
    SMOKING_PI_EDITION=basic run "$CLI" connect grok
    [ "$status" -eq 1 ]
    [[ "$output" == *"the basic edition does not ship it"* ]]
}

# --- connect --tailscale: the tunnel, done for the owner ----------------------

tailscale_setup() {
    connect_setup
    cp "$REPO/editions/pro/.env.template" "$STUB_HOME/editions/pro/"
    export STUB_MCP_RUNNING=1 TS_LOG="$BATS_TEST_TMPDIR/ts.log" TS_STATE="$BATS_TEST_TMPDIR/ts.state"
    : > "$TS_LOG"
    mkdir -p "$BATS_TEST_TMPDIR/bin6"
    # Root, as under sudo.
    printf '#!/bin/sh\n[ "$1" = -u ] && { echo 0; exit 0; }\nexec /usr/bin/id "$@"\n' > "$BATS_TEST_TMPDIR/bin6/id"
    # Tailscale: NeedsLogin until `up` has been asked twice for status, then Running.
    cat > "$BATS_TEST_TMPDIR/bin6/tailscale" <<'STUB'
#!/bin/sh
echo "tailscale $*" >> "$TS_LOG"
case "$1" in
    status)
        n=$(cat "$TS_STATE" 2>/dev/null || echo 0)
        if [ "$n" = running ] || { grep -q '^tailscale login' "$TS_LOG" && [ "$(grep -c '^tailscale status' "$TS_LOG")" -gt 4 ]; }; then
            echo '{"BackendState":"Running","Self":{"DNSName":"pi-1.tail99.ts.net."}}'
        else
            echo '{"BackendState":"NeedsLogin","AuthURL":"https://login.tailscale.com/a/abc123"}'
        fi ;;
    login) sleep 30 ;;
    debug) [ "$2" = prefs ] && echo "{\"CorpDNS\": ${STUB_CORPDNS:-false}}" ;;
esac
exit 0
STUB
    # The check from outside: the server answers as the published name.
    cat > "$BATS_TEST_TMPDIR/bin6/curl" <<'STUB'
#!/bin/sh
echo "curl $*" >> "$TS_LOG"
echo '{"issuer":"https://pi-1.tail99.ts.net"}'
STUB
    chmod +x "$BATS_TEST_TMPDIR/bin6/"*
    export PATH="$BATS_TEST_TMPDIR/bin6:$PATH"
}

@test "connect --tailscale: needs root" {
    connect_setup
    export STUB_MCP_RUNNING=1
    run "$CLI" connect --tailscale
    [ "$status" -eq 1 ]
    [[ "$output" == *"run it with sudo"* ]]
}

@test "connect --tailscale: signs in with the link, keeps DNS, publishes, sets the real name" {
    tailscale_setup
    run "$CLI" connect --tailscale
    [ "$status" -eq 0 ]
    [[ "$output" == *"https://login.tailscale.com/a/abc123"* ]]
    [[ "$output" == *"Checked from outside: https://pi-1.tail99.ts.net"* ]]
    grep -q '^tailscale set --accept-dns=false$' "$TS_LOG"
    # `login`, never `up`: with saved settings `up` refuses unless each is repeated.
    grep -q '^tailscale login --accept-dns=false$' "$TS_LOG"
    # ...and set again once signed in: `login` resets what it is not given.
    [ "$(grep -c '^tailscale set --accept-dns=false$' "$TS_LOG")" -ge 2 ]
    [[ "$output" == *"Tailscale keeps out of this Pi's DNS"* ]]
    grep -q '^tailscale funnel --bg --yes 127.0.0.1:8090$' "$TS_LOG"
    grep -q "^MCP_PUBLIC_URL='\?https://pi-1.tail99.ts.net'\?$" "$SMOKING_PI_ENV_FILE"
    # DNS is turned off before the sign-in, never after.
    [ "$(grep -n 'accept-dns=false$' "$TS_LOG" | head -1 | cut -d: -f2)" = "tailscale set --accept-dns=false" ]
}

@test "connect --tailscale: already signed in, it does not sign in again" {
    tailscale_setup
    echo running > "$TS_STATE"
    run "$CLI" connect --tailscale
    [ "$status" -eq 0 ]
    run grep -qE '^tailscale (up|login)' "$TS_LOG"
    [ "$status" -ne 0 ]
    grep -q '^tailscale funnel --bg --yes 127.0.0.1:8090$' "$TS_LOG"
}

@test "connect --tailscale: says so when the address does not answer as this server" {
    tailscale_setup
    echo running > "$TS_STATE"
    printf '#!/bin/sh\necho "{\\"issuer\\":\\"https://someone-else.example\\"}"\n' > "$BATS_TEST_TMPDIR/bin6/curl"
    export SMOKING_PI_TS_CHECK_TRIES=1
    run "$CLI" connect --tailscale
    [ "$status" -eq 1 ]
    [[ "$output" == *"does not answer as this server yet"* ]]
}

@test "connect --tailscale: stops before publishing if Tailscale still owns the DNS" {
    tailscale_setup
    echo running > "$TS_STATE"
    export STUB_CORPDNS=true
    run "$CLI" connect --tailscale
    [ "$status" -eq 1 ]
    [[ "$output" == *"still owns this Pi's DNS"* ]]
    run grep -q '^tailscale funnel' "$TS_LOG"
    [ "$status" -ne 0 ]
    run grep -q '^MCP_PUBLIC_URL=' "$SMOKING_PI_ENV_FILE"
    [ "$status" -ne 0 ]
}

@test "connect --tailscale --off: Funnel off and the address cleared" {
    tailscale_setup
    printf 'MCP_PUBLIC_URL=https://pi-1.tail99.ts.net\n' >> "$SMOKING_PI_ENV_FILE"
    run "$CLI" connect --tailscale --off
    [ "$status" -eq 0 ]
    grep -q '^tailscale funnel --https=443 off$' "$TS_LOG"
    run grep -q '^MCP_PUBLIC_URL=.\+' "$SMOKING_PI_ENV_FILE"
    [ "$status" -ne 0 ]
}

@test "connect --tailscale --off: works with the MCP server down, keeps another tunnel's address" {
    tailscale_setup
    unset STUB_MCP_RUNNING
    printf 'MCP_PUBLIC_URL=https://mcp.example.com\n' >> "$SMOKING_PI_ENV_FILE"
    run "$CLI" connect --tailscale --off
    [ "$status" -eq 0 ]
    [[ "$output" == *"another tunnel's: left as it is"* ]]
    grep -q '^MCP_PUBLIC_URL=https://mcp.example.com$' "$SMOKING_PI_ENV_FILE"
}

@test "connect --tailscale: not installed and no terminal, it says how and stops" {
    connect_setup
    export STUB_MCP_RUNNING=1
    mkdir -p "$BATS_TEST_TMPDIR/bin7"
    printf '#!/bin/sh\n[ "$1" = -u ] && { echo 0; exit 0; }\nexec /usr/bin/id "$@"\n' > "$BATS_TEST_TMPDIR/bin7/id"
    chmod +x "$BATS_TEST_TMPDIR/bin7/id"
    PATH="$BATS_TEST_TMPDIR/bin7:$PATH" run "$CLI" connect --tailscale < /dev/null
    [ "$status" -eq 1 ]
    [[ "$output" == *"tailscale.com/install.sh"* ]]
}
