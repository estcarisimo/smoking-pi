#!/usr/bin/env bats
# release-version.sh: which git tags release.yml accepts, and what each one
# publishes. A tag is irreversible once pushed -- the images are public the
# moment the run starts -- so the shapes are tested here, not on a tag.
#   bats packaging/tests/release-version.bats

setup() {
    REPO="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
    SCRIPT="$REPO/packaging/release-version.sh"
    CFF="$BATS_TEST_TMPDIR/CITATION.cff"
    printf 'cff-version: 1.2.0\nversion: "2.13.0"\ndate-released: 2026-09-24\n' > "$CFF"
}

@test "a release tag publishes its version, and the package carries it" {
    run "$SCRIPT" v2.13.0 "$CFF"
    [ "$status" -eq 0 ]
    [ "$output" = "$(printf 'kind=release\nversion=2.13.0\ndeb_version=2.13.0')" ]
}

@test "the script never publishes latest -- that waits for the install tests" {
    run "$SCRIPT" v2.13.0 "$CFF"
    [[ "$output" != *latest* ]]
}

@test "a candidate is the release's number with -rc.N, and its package sorts below the release" {
    run "$SCRIPT" v2.13.0-rc.2 "$CFF"
    [ "$status" -eq 0 ]
    [ "$output" = "$(printf 'kind=candidate\nversion=2.13.0-rc.2\ndeb_version=2.13.0~rc.2')" ]
    dpkg --compare-versions 2.13.0~rc.2 lt 2.13.0
    dpkg --compare-versions 2.13.0~rc.2 gt 2.12.0
    dpkg --compare-versions 2.13.0~rc.10 gt 2.13.0~rc.9
}

@test "a candidate must already agree with CITATION.cff" {
    run "$SCRIPT" v2.14.0-rc.1 "$CFF"
    [ "$status" -ne 0 ]
    [[ "$output" == *"CITATION.cff says 2.13.0"* ]]
}

@test "a release that disagrees with CITATION.cff is refused" {
    run "$SCRIPT" v2.12.1 "$CFF"
    [ "$status" -ne 0 ]
    [[ "$output" == *"tag v2.12.1 but"* ]]
}

@test "other v-shapes are refused rather than published as something" {
    for tag in v2.13 v2.13.0.1 v2.13.0-beta.1 v2.13.0-rc v2.13.0-rc.0 v2.13.0-rc.01 v2.13.0rc1 vX.Y.Z; do
        run "$SCRIPT" "$tag" "$CFF"
        [ "$status" -ne 0 ] || { echo "accepted $tag"; return 1; }
        [[ "$output" == *"refusing '$tag'"* ]]
    done
}

@test "a throwaway tag skips the version check and gets a package dpkg accepts" {
    run "$SCRIPT" test-1a2b3c4 "$CFF"
    [ "$status" -eq 0 ]
    [ "$output" = "$(printf 'kind=test\nversion=test-1a2b3c4\ndeb_version=0.0.0~test-1a2b3c4')" ]
}

@test "a throwaway tag that is not a valid image tag is refused" {
    run "$SCRIPT" 'test-a/b' "$CFF"
    [ "$status" -ne 0 ]
    [[ "$output" == *"not a valid image tag"* ]]
}
