#!/usr/bin/env bash
# Builds the apt repository that GitHub Pages serves under /apt: a flat
# repository (`deb [signed-by=...] <url> ./`) holding every release's .deb,
# with a signed Release (docs/packaging.md, "The apt repository").
#
#   packaging/apt/build-repo.sh OUT_DIR PACKAGE.deb...
#
# Signing: APT_SIGNING_KEY holds the ASCII-armored private key (an Actions
# secret; no passphrase). Unset, the repository is built unsigned and the
# script says so -- apt refuses an unsigned repository by default, so an
# unsigned build is for the CI check of this script, never for publishing.
# The public key is exported next to the index as smoking-pi.gpg (binary,
# for signed-by=) and smoking-pi.asc (readable).
set -euo pipefail
OUT="${1:-}"; shift || true
[ -n "$OUT" ] && [ $# -gt 0 ] || { echo "usage: $0 OUT_DIR PACKAGE.deb..." >&2; exit 2; }
for tool in dpkg-scanpackages apt-ftparchive gpg; do
    command -v "$tool" >/dev/null || { echo "$tool missing (dpkg-dev, apt-utils, gnupg)" >&2; exit 2; }
done

mkdir -p "$OUT/pool"
for deb in "$@"; do
    [ -f "$deb" ] || { echo "not a file: $deb" >&2; exit 2; }
    dpkg-deb --info "$deb" >/dev/null || { echo "not a Debian package: $deb" >&2; exit 2; }
    cp -f "$deb" "$OUT/pool/"
done

cd "$OUT"
# Paths in Packages are relative to the repository root, which is what a
# flat repository's `./` distribution resolves against.
dpkg-scanpackages --multiversion pool /dev/null > Packages
gzip -9 -k -f Packages
apt-ftparchive \
    -o APT::FTPArchive::Release::Origin=smoking-pi \
    -o APT::FTPArchive::Release::Label=smoking-pi \
    -o APT::FTPArchive::Release::Suite=stable \
    -o APT::FTPArchive::Release::Codename=stable \
    -o APT::FTPArchive::Release::Architectures=all \
    -o APT::FTPArchive::Release::Description="Smoking Pi packages, built by the release workflow from the tagged tree" \
    release . > Release

if [ -n "${APT_SIGNING_KEY:-}" ]; then
    # A private keyring for this run only, never the user's.
    export GNUPGHOME; GNUPGHOME="$(mktemp -d)"; trap 'rm -rf "$GNUPGHOME"' EXIT
    printf '%s\n' "$APT_SIGNING_KEY" | gpg --batch --quiet --import
    keyid=$(gpg --batch --list-secret-keys --with-colons | awk -F: '$1 == "sec" { print $5; exit }')
    [ -n "$keyid" ] || { echo "APT_SIGNING_KEY holds no secret key" >&2; exit 1; }
    gpg --batch --yes --default-key "$keyid" --clearsign -o InRelease Release
    gpg --batch --yes --default-key "$keyid" --armor --detach-sign -o Release.gpg Release
    gpg --batch --export "$keyid" > smoking-pi.gpg
    gpg --batch --armor --export "$keyid" > smoking-pi.asc
    echo "signed with $keyid: $(gpg --batch --list-keys --with-colons "$keyid" | awk -F: '$1 == "uid" { print $10; exit }')"
else
    rm -f InRelease Release.gpg smoking-pi.gpg smoking-pi.asc
    echo "::warning::APT_SIGNING_KEY unset: the repository is unsigned (apt will refuse it); fine for a check, not for publishing" >&2
fi
echo "repository at $OUT: $(grep -c '^Package:' Packages) package version(s)"
grep -E '^(Package|Version|Filename):' Packages | paste - - - | sed 's/^/   /'
