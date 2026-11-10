#!/usr/bin/env bats
# inject-shared-loss.sh: the staging injection test refuses to run anywhere
# but a staging Pi, against its own Docker daemon. It writes loss into the
# inference cache and InfluxDB, so the guard is tested here, against stubbed
# hostname and docker commands; nothing reaches a daemon.
#   bats packaging/tests/inject-shared-loss.bats

setup() {
    REPO="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
    SCRIPT="$REPO/shared/scripts/inject-shared-loss.sh"
    STUB="$BATS_TEST_TMPDIR/bin"
    mkdir -p "$STUB"
    LOG="$BATS_TEST_TMPDIR/docker.log"
    export LOG
    host stagingpi
    daemon stagingpi
    export PATH="$STUB:$PATH"
}

host() {
    printf '#!/bin/sh\necho %s\n' "$1" > "$STUB/hostname"
    chmod +x "$STUB/hostname"
}

# A docker that says which daemon it is, and records every other call.
# No container is found, so a run that gets past the guard stops there.
daemon() {
    cat > "$STUB/docker" <<EOF
#!/bin/sh
echo "\$*" >> "\$LOG"
case "\$1" in
    info) [ -n "$1" ] && echo "$1" ;;
    ps) ;;
esac
exit 0
EOF
    chmod +x "$STUB/docker"
}

touched() {
    # Any docker call other than the daemon's name.
    [ -f "$LOG" ] && grep -qv '^info ' "$LOG"
}

@test "the reference Pi is refused before any docker call" {
    host smokingpi
    run "$SCRIPT" --host smokingpi
    [ "$status" -eq 2 ]
    [[ "$output" == *"staging Pi only"* ]]
    [ ! -f "$LOG" ]
}

@test "--host must repeat the host's name" {
    run "$SCRIPT" --host stagingpi2
    [ "$status" -eq 2 ]
    [[ "$output" == *"--host must repeat this host's name exactly ('stagingpi')"* ]]
    [ ! -f "$LOG" ]
}

@test "no --host is no confirmation" {
    run "$SCRIPT"
    [ "$status" -eq 2 ]
    [ ! -f "$LOG" ]
}

@test "a Docker daemon that is not this host's is refused" {
    daemon smokingpi
    run "$SCRIPT" --host stagingpi
    [ "$status" -eq 2 ]
    [[ "$output" == *"the Docker daemon in use is 'smokingpi'"* ]]
    ! touched
}

@test "an unreachable daemon is refused too" {
    daemon ""
    run "$SCRIPT" --host stagingpi
    [ "$status" -eq 2 ]
    [[ "$output" == *"'unreachable'"* ]]
}

@test "past the guard it looks for the inference service, and stops without it" {
    run "$SCRIPT" --host stagingpi
    [ "$status" -eq 2 ]
    [[ "$output" == *"inference service is not running"* ]]
    grep -q 'com.docker.compose.service=inference' "$LOG"
}

@test "--help reads the header and calls nothing" {
    run "$SCRIPT" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"STAGING Pi"* ]]
    [ ! -f "$LOG" ]
}
