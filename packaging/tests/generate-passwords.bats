#!/usr/bin/env bats
# generate-passwords.sh, which every edition's setup.sh (and so every
# `smoking-pi install`) runs first: it writes the secrets and must never
# print one. Until 2.15.8 it printed every password and token of the new
# install to the terminal, a few lines above install's own "not printed".
#   bats packaging/tests/generate-passwords.bats

setup() {
    REPO="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
    SCRIPT="$REPO/shared/scripts/generate-passwords.sh"
    # No timedatectl here: the timezone falls back to UTC, not the host's.
    mkdir -p "$BATS_TEST_TMPDIR/bin"
    printf '#!/bin/sh\nexit 1\n' > "$BATS_TEST_TMPDIR/bin/timedatectl"
    chmod +x "$BATS_TEST_TMPDIR/bin/timedatectl"
    export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
}

# Every non-empty value the script put where its edition's template had an
# empty one or a default: the generated secrets, whatever their names.
generated_values() {
    local template="$1" env="$2" key value
    while IFS='=' read -r key value; do
        case "$key" in ""|\#*) continue ;; esac
        [ -n "$value" ] || continue
        grep -qxF "$key=$value" "$template" || printf '%s\n' "$value"
    done < "$env"
}

check_edition() {
    local edition="$1" env="$BATS_TEST_TMPDIR/$1.env"
    run bash "$SCRIPT" --edition "$edition" --target-dir "$REPO/editions/$edition" --env-file "$env"
    [ "$status" -eq 0 ]
    [ -f "$env" ]
    # 0600 from the start is not observable here; 0600 at the end is.
    [ "$(stat -c %a "$env" 2>/dev/null || stat -f %Lp "$env")" = 600 ]
    local value found=0
    while IFS= read -r value; do
        # Short values (UTC, a port) are not secrets and could match text.
        [ "${#value}" -ge 16 ] || continue
        found=$((found + 1))
        if [[ "$output" == *"$value"* ]]; then
            echo "a generated value is in the output: ${value:0:4}..." >&2
            return 1
        fi
    done < <(generated_values "$REPO/editions/$edition/.env.template" "$env")
    echo "$found"
}

@test "pro: no generated secret is printed" {
    run check_edition pro
    [ "$status" -eq 0 ]
    # Nine secrets on InfluxDB: a count of zero would mean the check
    # compared nothing, and passed for that reason.
    [ "$output" -ge 9 ]
}

@test "pro: the output names the command that shows the secrets" {
    run bash "$SCRIPT" --edition pro --target-dir "$REPO/editions/pro" --env-file "$BATS_TEST_TMPDIR/e"
    [ "$status" -eq 0 ]
    [[ "$output" == *"smoking-pi passwords --show-secrets"* ]]
    # The old ending told a package install to run `docker compose up -d`.
    [[ "$output" != *"docker compose up"* ]]
}

@test "standard: no generated secret is printed" {
    run check_edition standard
    [ "$status" -eq 0 ]
    [ "$output" -ge 4 ]
}

@test "basic: runs and prints no secret (it generates none)" {
    run check_edition basic
    [ "$status" -eq 0 ]
}
