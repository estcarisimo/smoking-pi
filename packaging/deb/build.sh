#!/usr/bin/env bash
# Build a Debian package of the tracked tree: code under /opt/smoking-pi,
# the CLI in /usr/bin, a systemd unit. No Docker needed to build; Docker is
# a runtime dependency of what it installs. Output: dist/smoking-pi_<ver>_all.deb
#
# This is the end-to-end trial from docs/packaging.md, not a supported
# install path yet: read that document for what still has to move out of
# the tree before a package can upgrade in place.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${1:-$(sed -n 's/^version: *//p' "$ROOT/CITATION.cff")}"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
PKG="$STAGE/smoking-pi"
mkdir -p "$PKG/DEBIAN" "$PKG/opt/smoking-pi" "$PKG/usr/bin" "$PKG/lib/systemd/system" "$PKG/etc/default"

# The committed tree at HEAD -- never the working tree. The state the stack
# rewrites (editions/*/config-manager/{config,output}, .env) is untracked
# since the relocatable-state change, so the archive cannot carry a
# deployment's target list; a fresh install is seeded by config-manager.
( cd "$ROOT" && git archive --format=tar HEAD -- editions shared docs examples README.md LICENSE CHANGELOG.md CITATION.cff SECURITY.md ) \
    | tar -C "$PKG/opt/smoking-pi" -xf -
install -m 0755 "$ROOT/packaging/smoking-pi" "$PKG/usr/bin/smoking-pi"
install -m 0644 "$ROOT/packaging/systemd/smoking-pi.service" "$PKG/lib/systemd/system/smoking-pi.service"
cat > "$PKG/etc/default/smoking-pi" <<'ENV'
# Settings for the smoking-pi command and the systemd unit (a conffile:
# dpkg keeps your edits across upgrades).
SMOKING_PI_HOME=/opt/smoking-pi
#SMOKING_PI_EDITION=pro
# The packaged layout: nothing the stack rewrites lives under /opt, so a
# package upgrade replaces code only (docs/packaging.md, "Relocatable state").
SMOKING_PI_ENV_FILE=/etc/smoking-pi/env
SMOKING_PI_CONFIG_DIR=/etc/smoking-pi/config
SMOKING_PI_OUTPUT_DIR=/var/lib/smoking-pi/output
# No source bind-mounts: the containers run the code baked into their
# images, so replacing /opt/smoking-pi cannot reach a running stack
# (docker-compose.packaged.yml).
SMOKING_PI_PACKAGED=1
# The images this version was released with: the compose files pull
# ghcr.io/estcarisimo/smoking-pi/<service>:<version> instead of building
# (docs/packaging.md, "Published images"). Leave it matching the package.
SMOKING_PI_VERSION=__VERSION__
ENV
sed -i "s/__VERSION__/$VERSION/" "$PKG/etc/default/smoking-pi"
SIZE=$(du -sk "$PKG" | cut -f1)
cat > "$PKG/DEBIAN/control" <<CTL
Package: smoking-pi
Version: $VERSION
Section: net
Priority: optional
Architecture: all
Depends: docker.io | docker-ce, docker-compose-plugin | docker-compose-v2, openssl, python3, python3-yaml, git
Recommends: whiptail
Installed-Size: $SIZE
Maintainer: Esteban Carisimo <noreply@smoking-pi.dev>
Homepage: https://github.com/estcarisimo/smoking-pi
Description: SmokePing network monitoring for a Raspberry Pi, with Grafana, alerts and an MCP server
 A Docker Compose stack around SmokePing in three editions (Basic, Standard,
 Pro). This package installs the stack definition, the smoking-pi command
 and a systemd unit; images are built or pulled on first start.
CTL
cat > "$PKG/DEBIAN/postinst" <<'POST'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
    # The state directories, empty: config-manager seeds config on first
    # start and regenerates output. Secrets (env, mode 600) are written by
    # `smoking-pi install`.
    mkdir -p /etc/smoking-pi/config /var/lib/smoking-pi/output
    chmod 0750 /etc/smoking-pi
    systemctl daemon-reload >/dev/null 2>&1 || true
    echo "smoking-pi installed. Next: sudo smoking-pi install   (then: systemctl enable --now smoking-pi)"
fi
POST
cat > "$PKG/DEBIAN/prerm" <<'PRE'
#!/bin/sh
set -e
if [ "$1" = remove ]; then
    systemctl disable --now smoking-pi >/dev/null 2>&1 || true
fi
# Data stays: docker volumes, /etc/smoking-pi and /var/lib/smoking-pi are not
# removed (docs/packaging.md backlog #6 is the purge policy).
PRE
chmod 0755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/prerm"
echo "/etc/default/smoking-pi" > "$PKG/DEBIAN/conffiles"

mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/smoking-pi_${VERSION}_all.deb"
dpkg-deb --build --root-owner-group "$PKG" "$OUT" >/dev/null
echo "$OUT"
