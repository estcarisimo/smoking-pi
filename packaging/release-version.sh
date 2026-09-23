#!/bin/sh
# release-version.sh TAG [CITATION.cff] -- what a git tag means to release.yml.
#
# Prints key=value lines for $GITHUB_OUTPUT:
#   kind         release | candidate | test
#   version      the image tag this run publishes (never `latest`)
#   deb_version  the Debian version of the package it builds
#
# Three shapes are accepted, and nothing else that starts with `v`:
#
#   vX.Y.Z       a release. CITATION.cff must say X.Y.Z: the package and
#                `smoking-pi version` report it, and the images must carry
#                the same number or a packaged install would pull nothing.
#                `latest` is moved to it only after every install test has
#                passed (release.yml, `promote`), never by this script.
#   vX.Y.Z-rc.N  a release candidate: the same checks and the same pipeline
#                on exactly the artifacts the release will ship, published
#                as a GitHub pre-release, so the Raspberry Pi acceptance
#                (docs/release-acceptance.md) runs on them before vX.Y.Z is
#                tagged on the same commit. CITATION.cff says X.Y.Z already.
#                The package is X.Y.Z~rc.N, which dpkg sorts below X.Y.Z.
#   test-*       a throwaway run of the pipeline, on any branch: no version
#                check, no release asset; the package is 0.0.0~<tag>.
set -eu

tag="${1:?usage: release-version.sh TAG [CITATION.cff]}"
cff_file="${2:-CITATION.cff}"

case "$tag" in
    test-*)
        case "$tag" in
            *[!A-Za-z0-9._-]*) echo "refusing '$tag': not a valid image tag" >&2; exit 1 ;;
        esac
        echo "kind=test"
        echo "version=$tag"
        echo "deb_version=0.0.0~$tag"
        exit 0 ;;
esac

base=$(printf '%s\n' "$tag" | sed -nE 's/^v([0-9]+\.[0-9]+\.[0-9]+)(-rc\.[1-9][0-9]*)?$/\1/p')
[ -n "$base" ] || {
    echo "refusing '$tag': a release is vX.Y.Z, a candidate vX.Y.Z-rc.N (N from 1), a throwaway test-*" >&2
    exit 1
}
cff=$(sed -nE 's/^version: *"?([0-9.]+)"?.*/\1/p' "$cff_file")
[ "$cff" = "$base" ] || { echo "tag $tag but $cff_file says $cff" >&2; exit 1; }

case "$tag" in
    *-rc.*)
        rc="${tag##*-rc.}"
        echo "kind=candidate"
        echo "version=$base-rc.$rc"
        echo "deb_version=$base~rc.$rc" ;;
    *)
        echo "kind=release"
        echo "version=$base"
        echo "deb_version=$base" ;;
esac
