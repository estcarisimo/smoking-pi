#!/usr/bin/env bats
# dig-usec against a fake dig (DIG_BIN): no network, no real dig.
#   bats packaging/tests/dig-usec.bats

bats_require_minimum_version 1.5.0

setup() {
    WRAPPER="$BATS_TEST_DIRNAME/../../shared/modules/smokeping/dig-usec"
    FAKE="$BATS_TEST_TMPDIR/dig"
    export DIG_BIN="$FAKE"
    # The probe's own regexes, from Smokeping::probes::DNS.
    PROBE_RE='query time:\s+([0-9.]+)\smsec.*'
}

fake() { printf '#!/bin/sh\n%s\n' "$1" > "$FAKE"; chmod +x "$FAKE"; }

@test "asks dig for -u and passes the arguments through" {
    fake 'echo "args: $*"'
    run "$WRAPPER" @127.0.0.1 -p 5353 localhost
    [ "$status" -eq 0 ]
    [ "$output" = "args: -u @127.0.0.1 -p 5353 localhost" ]
}

@test "rewrites the usec line to msec with three decimals" {
    fake 'printf ";; ANSWER SECTION:\n;; Query time: 1234 usec\n;; SERVER: x\n"'
    run "$WRAPPER" localhost
    [ "$status" -eq 0 ]
    [ "${lines[0]}" = ";; ANSWER SECTION:" ]
    [ "${lines[1]}" = ";; Query time: 1.234 msec" ]
    [ "${lines[2]}" = ";; SERVER: x" ]
}

@test "sub-millisecond and long times format correctly" {
    fake 'printf ";; Query time: 87 usec\n"'
    run "$WRAPPER" localhost
    [ "$output" = ";; Query time: 0.087 msec" ]
    fake 'printf ";; Query time: 1500000 usec\n"'
    run "$WRAPPER" localhost
    [ "$output" = ";; Query time: 1500.000 msec" ]
}

@test "the rewritten line matches the probe's regex" {
    fake 'printf ";; Query time: 2345 usec\n"'
    run "$WRAPPER" localhost
    run perl -e '$_ = $ARGV[0]; exit(!(/'"$PROBE_RE"'/i && $1 == 2.345))' "$output"
    [ "$status" -eq 0 ]
}

@test "other lines, including similar ones, are untouched" {
    fake 'printf "; <<>> DiG <<>> localhost\n;; query time in the text\n;; Query time: 5 msec\n"'
    run "$WRAPPER" localhost
    [ "${lines[0]}" = "; <<>> DiG <<>> localhost" ]
    [ "${lines[1]}" = ";; query time in the text" ]
    [ "${lines[2]}" = ";; Query time: 5 msec" ]
}

@test "the exit code of dig is the exit code of the wrapper" {
    fake 'printf ";; no servers could be reached\n"; exit 9'
    run "$WRAPPER" localhost
    [ "$status" -eq 9 ]
    [ "$output" = ";; no servers could be reached" ]
}

@test "stderr stays on stderr" {
    fake 'echo oops >&2; printf ";; Query time: 1000 usec\n"'
    run --separate-stderr "$WRAPPER" localhost
    [ "$output" = ";; Query time: 1.000 msec" ]
    [ "$stderr" = "oops" ]
}
