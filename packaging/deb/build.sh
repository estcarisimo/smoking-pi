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
# A release candidate (X.Y.Z~rc.N, packaging/release-version.sh) runs the
# :X.Y.Z-rc.N images, but its CITATION.cff already says X.Y.Z, whose images
# do not exist until the release. The command reads this file first.
case "$VERSION" in
    *~rc.*) printf '%s\n' "${VERSION/\~rc./-rc.}" > "$PKG/opt/smoking-pi/VERSION" ;;
esac
install -m 0755 "$ROOT/packaging/smoking-pi" "$PKG/usr/bin/smoking-pi"
install -m 0644 "$ROOT/packaging/systemd/smoking-pi.service" "$PKG/lib/systemd/system/smoking-pi.service"
cat > "$PKG/etc/default/smoking-pi" <<'ENV'
# Settings for the smoking-pi command and the systemd unit (a conffile:
# dpkg keeps your edits across upgrades).
# The command finds the tree itself (the checkout it sits in, else
# /opt/smoking-pi); set only to run the packaged command against another
# tree. Setting it here would also capture a checkout's own
# packaging/smoking-pi on this host, which sources this file.
#SMOKING_PI_HOME=/opt/smoking-pi
# The edition: `smoking-pi install` records the one it installed in
# /etc/smoking-pi/edition; set here only to override that record.
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
# The images: ghcr.io/estcarisimo/smoking-pi/<service>:<this package's
# version>, pulled instead of built (docs/packaging.md, "Published
# images"). The version is not written here on purpose: this file keeps
# your edits across upgrades, so a version line would pin every upgrade to
# the first release installed. Set it only to pin other images (a test
# build, a fork's registry with SMOKING_PI_REGISTRY).
#SMOKING_PI_VERSION=
ENV
SIZE=$(du -sk "$PKG" | cut -f1)
# The Docker dependencies, measured against each host's own repositories
# (docs/packaging.md, "Supported hosts"; packaging/tests/check-package.sh
# repeats the measurement on every release):
#  - engine: apt takes the first installable alternative, so docker-ce
#    wins wherever Docker's repository is configured (the Raspberry Pi OS
#    path, since Debian 12 ships no Compose v2 at all) and Ubuntu's
#    docker.io otherwise;
#  - CLI: Debian 13 split it out of docker.io (docker-cli, only
#    Recommended by the daemon); Ubuntu's docker.io Provides docker-cli;
#    Debian 12's docker.io still contains it, hence the version bound;
#  - Compose v2: Docker's plugin, Ubuntu's docker-compose-v2, or Debian
#    13's docker-compose 2.x -- the 1.x docker-compose of Debian 12 and
#    Ubuntu is Python, not the plugin, hence the (>= 2).
cat > "$PKG/DEBIAN/control" <<CTL
Package: smoking-pi
Version: $VERSION
Section: net
Priority: optional
Architecture: all
Depends: docker-ce | docker.io, docker-ce-cli | docker-cli | docker.io (<< 26.1.4), docker-compose-plugin | docker-compose-v2 | docker-compose (>= 2), openssl, python3, python3-yaml, git
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
    # An enabled unit keeps the links of the [Install] section it was
    # enabled with; re-enabling adds the ones a newer unit declares (it is
    # also wanted by docker.service now) without starting or stopping it.
    if systemctl is-enabled --quiet smoking-pi 2>/dev/null; then
        systemctl reenable smoking-pi >/dev/null 2>&1 || true
    fi
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
# removed; postrm says what `purge` does (docs/packaging.md, backlog #6).
PRE
cat > "$PKG/DEBIAN/postrm" <<'POSTRM'
#!/bin/sh
set -e
# The uninstall policy (docs/packaging.md, backlog #6): dpkg never deletes
# a year of measurements. `remove` keeps everything; `purge` removes what
# the stack regenerates (the seeded config, the generated output) and the
# conffile, but neither the Docker volumes nor the env file -- the file
# holds the credentials those volumes are locked with, so deleting it
# alone would make the kept data unreadable. `smoking-pi purge --config`
# is the explicit way to delete the data, before the package.
case "$1" in
    purge)
        rm -rf /etc/smoking-pi/config /var/lib/smoking-pi/output
        rmdir /var/lib/smoking-pi 2>/dev/null || true
        if [ -f /etc/smoking-pi/env ]; then
            echo "smoking-pi: kept /etc/smoking-pi/env and the Docker volumes (docker volume ls):"
            echo "  the file holds their credentials. To delete the measurements too, reinstall and"
            echo "  run 'smoking-pi purge --config', or 'docker volume rm' them and remove the file."
        else
            rmdir /etc/smoking-pi 2>/dev/null || true
        fi ;;
esac
if [ "$1" = remove ] || [ "$1" = purge ]; then
    systemctl daemon-reload >/dev/null 2>&1 || true
fi
POSTRM
chmod 0755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/prerm" "$PKG/DEBIAN/postrm"
echo "/etc/default/smoking-pi" > "$PKG/DEBIAN/conffiles"

mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/smoking-pi_${VERSION}_all.deb"
dpkg-deb --build --root-owner-group "$PKG" "$OUT" >/dev/null
echo "$OUT"
