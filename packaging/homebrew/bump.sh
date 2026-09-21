#!/usr/bin/env bash
# After a release: point Formula/smoking-pi.rb at the new tag's source
# tarball and record its checksum -- a release step the maintainer runs,
# because a tag's tarball checksum cannot be committed before the tag
# exists. `brew tap` reads the formula from main, not from the tag, so
# the bump is a normal PR after the release.
#
#   packaging/homebrew/bump.sh vX.Y.Z
set -euo pipefail
TAG="${1:-}"
case "$TAG" in v[0-9]*) ;; *) echo "usage: $0 vX.Y.Z" >&2; exit 2 ;; esac
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FORMULA="$ROOT/Formula/smoking-pi.rb"
URL="https://github.com/estcarisimo/smoking-pi/archive/refs/tags/$TAG.tar.gz"
sha=$(curl -fsSL "$URL" | sha256sum | cut -d' ' -f1)
[ ${#sha} = 64 ] || { echo "could not fetch $URL" >&2; exit 1; }
sed -i "s|^  url \".*\"|  url \"$URL\"|; s|^  sha256 \".*\"|  sha256 \"$sha\"|" "$FORMULA"
grep -nE '^  (url|sha256) ' "$FORMULA"
echo "Formula/smoking-pi.rb points at $TAG; commit it: git commit -am 'chore: Homebrew formula $TAG'"
