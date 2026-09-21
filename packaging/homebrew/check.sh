#!/usr/bin/env bash
# CI: the formula parses, and its sha256 is the checksum of the tarball its
# url names -- the two things a bump can get wrong without a Mac to test on.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FORMULA="$ROOT/Formula/smoking-pi.rb"
ruby -c "$FORMULA" >/dev/null
url=$(sed -n 's/^  url "\(.*\)"/\1/p' "$FORMULA")
want=$(sed -n 's/^  sha256 "\(.*\)"/\1/p' "$FORMULA")
have=$(curl -fsSL "$url" | sha256sum | cut -d' ' -f1)
[ "$have" = "$want" ] || { echo "Formula sha256 $want is not the checksum of $url ($have)"; exit 1; }
tag=$(basename "$url" .tar.gz)
echo "Formula/smoking-pi.rb: syntax OK; sha256 matches $tag"
