#!/usr/bin/env bash
# Installs a built .deb on THIS host with apt -- so the host's own package
# resolver picks the Docker engine, CLI and Compose from the repositories
# it has -- and checks what an installed package must satisfy here. The
# release workflow runs it on every supported host (docs/packaging.md,
# "Supported hosts"): Ubuntu VMs, where it also starts an edition with the
# release's images, and Debian containers, where there is no daemon and it
# stops at what a package can prove without one.
#
#   sudo packaging/tests/check-package.sh dist/smoking-pi_<ver>_all.deb [options]
#     --version V        the version the package must report (a 0.0.0~ throwaway
#                        is not checked)
#     --expect-engine P  the engine package apt must have chosen (docker-ce
#                        wherever Docker's repository is configured, else
#                        docker.io)
#     --start EDITION    needs a daemon: `smoking-pi install` that edition,
#                        wait for its web UI, enable and start the unit, stop
#                        it, and only then remove the package
#     --image-tag TAG    for a throwaway build whose images are tagged
#                        differently from the package version (test-* tags)
#     --old-image-tag TAG
#                        the images the previous release runs, when they are
#                        not its own version either (a local run)
#     --upgrade-from OLD.deb
#                        with --start: install OLD first, start the edition on
#                        it, then install the package over it and
#                        `smoking-pi upgrade` -- the secrets must survive and
#                        the containers must run the new images
#
# Runs as root and leaves the host without the package. Local use: any
# distro container on the Pi (mount the .deb), or a scratch VM.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

DEB="" VERSION="" ENGINE="" START="" IMAGE_TAG="" OLD_DEB="" OLD_IMAGE_TAG=""
while [ $# -gt 0 ]; do
    case "$1" in
        --version) VERSION="$2"; shift 2 ;;
        --expect-engine) ENGINE="$2"; shift 2 ;;
        --start) START="$2"; shift 2 ;;
        --image-tag) IMAGE_TAG="$2"; shift 2 ;;
        --upgrade-from) OLD_DEB="$2"; shift 2 ;;
        --old-image-tag) OLD_IMAGE_TAG="$2"; shift 2 ;;
        -*) echo "unknown option $1" >&2; exit 2 ;;
        *) DEB="$1"; shift ;;
    esac
done
[ -n "$DEB" ] && [ -f "$DEB" ] || { echo "usage: $0 <package.deb> [--version V] [--expect-engine docker-ce|docker.io] [--start EDITION] [--image-tag TAG]" >&2; exit 2; }
[ "$(id -u)" = 0 ] || { echo "run as root: apt installs and the state directories are root's" >&2; exit 2; }
DEB="$(readlink -f "$DEB")"
[ -z "$OLD_DEB" ] || { [ -n "$START" ] && [ -f "$OLD_DEB" ] || { echo "--upgrade-from needs --start and an existing file" >&2; exit 2; }; OLD_DEB="$(readlink -f "$OLD_DEB")"; }

fail() { echo "FAIL: $*" >&2; exit 1; }
port_of() { local p; p=$(sed -n 's/^SMOKEPING_PORT=//p' /etc/smoking-pi/env); echo "${p:-80}"; }
wait_web() {
    # The edition's web UI (Basic: SmokePing on SMOKEPING_PORT) answers.
    local port code=000; port=$(port_of)
    for _ in $(seq 60); do
        code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$port/" || true)
        case "$code" in 2*|3*) echo "web UI answers on :$port ($code)"; return 0 ;; esac
        sleep 5
    done
    smoking-pi logs 2>&1 | tail -30; fail "web UI on :$port did not answer (last: $code)"
}
running_images() {
    # What the stack's containers run right now, one image ref per line.
    local project; project=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' /etc/smoking-pi/env)
    docker ps --filter "label=com.docker.compose.project=${project:-$START}" --format '{{.Image}}' | sort
}
project_volumes() {
    # The stack's Docker volumes, by name, from Compose's own label (the
    # fixed `name:`s -- smokeping-basic-data -- carry it too).
    local project; project=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' /etc/smoking-pi/env 2>/dev/null)
    docker volume ls -q --filter "label=com.docker.compose.project=${project:-$START}" | sort
}
# `|| true`: no match must reach the caller's own message, not trip set -e
# inside the assignment.
data_volume() { project_volumes | grep -- '-data$' | grep -v postgres | head -1 || true; }
read_marker() { docker run --rm -v "$1:/v:ro" alpine:3.20 cat /v/.check-package-marker 2>/dev/null || true; }
config_sum() { (cd "$(smoking-pi paths | sed -n 's/^config: *//p')" && find . -type f | sort | xargs -r sha256sum) | sha256sum | cut -d' ' -f1; }
image_tag() { smoking-pi paths | sed -n 's|^images: .*/<service>:\([^ ]*\).*|\1|p'; }
pin_images() {
    # A throwaway build: its images carry the git tag, not a Debian
    # version. Pinned through the environment (this script's commands) and
    # a unit drop-in (the unit's), never by editing the conffile: the check
    # below wants that file exactly as shipped.
    local tag="$1"
    if [ -z "$tag" ] || [ "$tag" = "$(smoking-pi version)" ]; then
        unset SMOKING_PI_VERSION; rm -rf /etc/systemd/system/smoking-pi.service.d
    else
        export SMOKING_PI_VERSION="$tag"
        mkdir -p /etc/systemd/system/smoking-pi.service.d
        printf '[Service]\nEnvironment=SMOKING_PI_VERSION=%s\n' "$tag" > /etc/systemd/system/smoking-pi.service.d/check.conf
    fi
    [ ! -d /run/systemd/system ] || systemctl daemon-reload
}
conffile_as_shipped() {
    # dpkg records the shipped conffile's md5; a byte's difference here
    # means a later upgrade stops at the conffile prompt (fatal under a
    # non-interactive apt). Nothing the package or the command does may
    # edit it -- only the user, knowingly.
    local shipped; shipped=$(dpkg-query -W -f '${Conffiles}\n' smoking-pi | awk '$1 == "/etc/default/smoking-pi" { print $2 }')
    [ "$(md5sum /etc/default/smoking-pi | cut -d' ' -f1)" = "$shipped" ] || fail "/etc/default/smoking-pi differs from what the package shipped ($1)"
}
# Not sourced: os-release sets VERSION, which is this script's option.
PRETTY_NAME=$(sed -n 's/^PRETTY_NAME="\(.*\)"/\1/p' /etc/os-release)
echo "== $PRETTY_NAME, $(uname -m), $(dpkg --print-architecture)"

apt-get update -qq

# 0. The previous release first, installed and running, so that the
# package under test arrives the way it will on every user's host: as an
# upgrade over a working install with secrets in the volumes. The
# assertions below read the old package's own output (`paths`, the
# edition file): every released package has them, because the first
# packaged release came after this check -- a release tagged from a
# branch without them would fail here, on purpose.
if [ -n "$OLD_DEB" ]; then
    apt-get install -y -qq --no-install-recommends "$OLD_DEB" >/tmp/check-package.apt-old.log 2>&1 \
        || { tail -20 /tmp/check-package.apt-old.log; fail "apt could not install the previous release $OLD_DEB"; }
    old_version=$(smoking-pi version); pin_images "$OLD_IMAGE_TAG"; old_tag=$(image_tag)
    echo "== previous release installed: $old_version (images :$old_tag)"
    docker info >/dev/null || fail "no Docker daemon to start $START with"
    smoking-pi install --edition "$START" --yes
    conffile_as_shipped "after the previous release's install"
    [ "$(cat /etc/smoking-pi/edition)" = "$START" ] || fail "the previous release did not record the edition"
    wait_web
    old_env_sum=$(sha256sum /etc/smoking-pi/env | cut -d' ' -f1)
    old_images=$(running_images); echo "$old_images" | sed 's/^/   running: /'
    echo "$old_images" | grep -q ":$old_tag\$" || fail "the previous release is not running the images it names"
fi

# 1. The host's resolver, worst case: no Recommends. A missing alternative
# (Debian 12 without Docker's repository, Debian 13's split CLI) fails here.
# --allow-downgrades: a throwaway build (0.0.0~test-*) sorts below every
# release it upgrades from; dpkg runs the same maintainer scripts and
# conffile handling either way.
apt-get install -y -qq --no-install-recommends --allow-downgrades "$DEB" >/tmp/check-package.apt.log 2>&1 \
    || { tail -20 /tmp/check-package.apt.log; fail "apt could not install the package from this host's repositories"; }
picked=$(dpkg-query -W -f '${db:Status-Status} ${Package} ${Version}\n' docker-ce docker.io docker-ce-cli docker-cli \
    docker-compose-plugin docker-compose-v2 docker-compose 2>/dev/null | awk '$1 == "installed" { print "   " $2 " " $3 }')
echo "-- apt chose:"; echo "$picked"
if [ -n "$ENGINE" ]; then
    echo "$picked" | grep -q "^   $ENGINE " || fail "expected the $ENGINE engine, apt chose otherwise"
fi
# The CLI and the Compose plugin are two packages on some hosts: both must be there.
docker --version || fail "no docker CLI after install"
docker compose version || fail "no 'docker compose' plugin after install"
python3 --version
python3 -c 'import yaml' || fail "python3-yaml not importable by $(command -v python3)"

# 2. What the package reports and where it put things. Unpinned: the
# images must follow the installed tree's version on their own.
pin_images ""
v=$(smoking-pi version); echo "smoking-pi version: $v"
# The command reports the images' tag: X.Y.Z for a release, X.Y.Z-rc.N for
# a candidate, whose package is X.Y.Z~rc.N (release-version.sh). A
# candidate that reported X.Y.Z would pull images that do not exist yet.
if [ -n "$VERSION" ]; then
    case "$VERSION" in 0.0.0~*) ;; *) [ "$v" = "${VERSION/\~rc./-rc.}" ] || fail "package reports $v, expected ${VERSION/\~rc./-rc.}" ;; esac
fi
deb_version=$(dpkg-query -W -f '${Version}' smoking-pi)
# Equal on a release (and a candidate, once ~rc. reads -rc.); a throwaway
# (0.0.0~*) carries the tree's last release.
case "$deb_version" in 0.0.0~*) ;; *) [ "$v" = "${deb_version/\~rc./-rc.}" ] || fail "smoking-pi version says $v, dpkg says $deb_version" ;; esac
# The conffile carries no version: the installed tree's is what the
# images follow, so an upgrade never stays pinned to the first release.
! grep -q '^SMOKING_PI_VERSION=' /etc/default/smoking-pi || fail "the conffile pins a version"
conffile_as_shipped "after install"
out=$(smoking-pi paths); echo "$out"
# Installed at /usr/bin, the command must find the tree in /opt -- the
# first packaged run resolved "one directory up" to / instead.
echo "$out" | grep -q 'home:     /opt/smoking-pi' || fail "home is not /opt/smoking-pi"
echo "$out" | grep -q 'mode:     packaged' || fail "not in packaged mode"
echo "$out" | grep -q 'env:      /etc/smoking-pi/env' || fail "env file not relocated"
echo "$out" | grep -q "/<service>:$v" || fail "images do not follow the installed tree's version ($v)"
[ -d /etc/smoking-pi/config ] || fail "config dir missing"
[ -d /var/lib/smoking-pi/output ] || fail "output dir missing"
[ "$(stat -c %a /etc/smoking-pi)" = 750 ] || fail "/etc/smoking-pi is not 0750"
systemd-analyze verify /lib/systemd/system/smoking-pi.service || fail "the unit does not verify"

# 3. The doctor's static checks run from /opt: the package carries
# everything they read. A skipped doctor is a failed check.
smoking-pi doctor | tee /tmp/check-package.doctor.out
grep -q ' ok, 0 warn, 0 fail' /tmp/check-package.doctor.out || fail "doctor did not pass"

# 4. Every edition renders with THIS host's Compose, in the packaged layout
# (the overlay's `!override` needs Compose >= 2.24), from a filled-in
# template -- no daemon needed. Only what a package changes is asserted:
# the state mounts must have left /opt.
mkdir -p /etc/smoking-pi
for ed in basic standard pro; do
    sed 's/^\([A-Z_]*\)=$/\1=dummy/' "/opt/smoking-pi/editions/$ed/.env.template" > /tmp/check-package.env
    files=(-f docker-compose.yml)
    [ -f "/opt/smoking-pi/editions/$ed/docker-compose.packaged.yml" ] && files+=(-f docker-compose.packaged.yml)
    # shellcheck disable=SC1091
    ( cd "/opt/smoking-pi/editions/$ed" && set -a && . /etc/default/smoking-pi && set +a \
        && docker compose --env-file /tmp/check-package.env "${files[@]}" config --format json ) > /tmp/check-package.cfg.json \
        || fail "compose config failed for $ed with $(docker compose version --short)"
    python3 - "$ed" <<'PY' || exit 1
import json, sys
cfg = json.load(open("/tmp/check-package.cfg.json")); ed = sys.argv[1]
binds = [v["source"] for s in cfg["services"].values() for v in s.get("volumes", []) if v.get("type") == "bind"]
state = [b for b in binds if "/config-manager/config" in b or "/config-manager/output" in b or b.endswith("/.env")]
if state:
    sys.exit(f"FAIL: {ed} still mounts state from the tree: {state}")
print(f"compose config {ed}: OK, {len(cfg['services'])} services")
PY
done

# 5. `install` refuses to run over an existing env file -- the guard that
# stops a reinstall from rotating live secrets.
if [ -f /etc/smoking-pi/env ]; then
    ! smoking-pi install --yes 2>/dev/null || fail "install ran over an existing env file"
else
    touch /etc/smoking-pi/env
    ! smoking-pi install --yes 2>/dev/null || fail "install ran over an existing env file"
    rm -f /etc/smoking-pi/env
fi

# 6. With a daemon: the real first install of an edition -- or, after
# step 0, the real upgrade of one -- the unit around it, a clean stop.
# The images are the release's own, pulled from GHCR.
if [ -n "$START" ]; then
    docker info >/dev/null || fail "no Docker daemon to start $START with"
    pin_images "$IMAGE_TAG"
    if [ -n "$OLD_DEB" ]; then
        # The recorded edition and the secrets survived the package upgrade.
        [ "$(cat /etc/smoking-pi/edition)" = "$START" ] || fail "the upgrade lost the recorded edition"
        [ "$(sha256sum /etc/smoking-pi/env | cut -d' ' -f1)" = "$old_env_sum" ] || fail "the package upgrade touched the env file"
        smoking-pi upgrade --skip-doctor
        wait_web
        [ "$(sha256sum /etc/smoking-pi/env | cut -d' ' -f1)" = "$old_env_sum" ] || fail "smoking-pi upgrade touched the env file"
        new_images=$(running_images); echo "$new_images" | sed 's/^/   running: /'
        want=$(image_tag)
        [ "$want" != "$old_tag" ] || fail "the new package names the same images as the old one (:$want)"
        echo "$new_images" | grep -q ":$want\$" || fail "after upgrade the stack does not run the :$want images"
        ! echo "$new_images" | grep -q ":$old_tag\$" || fail "after upgrade a container still runs :$old_tag"
    else
        smoking-pi install --edition "$START" --yes
        [ "$(cat /etc/smoking-pi/edition)" = "$START" ] || fail "install did not record the edition"
        wait_web
    fi
    smoking-pi status

    # 6b. Recovery, on the real stack: what a dead SD card costs. A marker
    # in the data volume, `backup` (offline: down, tar, up), then `purge
    # --config` -- volumes, env file and config gone; with the edition file
    # removed by hand below (purge keeps it, a recorded choice), a card with
    # only the package on it -- then `restore` from the backup. The secrets, the
    # config and the marker must come back, and the web UI must answer.
    # Until now backup/restore ran only against a stubbed docker (cli.bats).
    vol=$(data_volume); [ -n "$vol" ] || fail "no data volume among: $(project_volumes | tr '\n' ' ')"
    marker="check-package $(date -u +%s) $RANDOM"
    docker run --rm -v "$vol:/v" alpine:3.20 sh -c "echo '$marker' > /v/.check-package-marker"
    env_sum=$(sha256sum /etc/smoking-pi/env | cut -d' ' -f1); cfg_sum=$(config_sum)
    vols_before=$(project_volumes)
    backup_dir="$(mktemp -d)/backup"
    smoking-pi backup "$backup_dir"
    grep -qx "edition=$START" "$backup_dir/manifest" || fail "the backup's manifest does not name the edition"
    [ -n "$(ls "$backup_dir"/volumes/*.tgz 2>/dev/null)" ] || fail "the backup holds no volume"
    [ -n "$(running_images)" ] || fail "backup left the stack stopped"
    wait_web
    smoking-pi purge --config --yes
    [ -z "$(project_volumes)" ] || fail "purge left volumes: $(project_volumes | tr '\n' ' ')"
    [ ! -e /etc/smoking-pi/env ] || fail "purge --config left the env file"
    # A new card has no recorded edition either: restore must take the
    # backup's, not refuse a Basic backup as "this is pro".
    rm -f /etc/smoking-pi/edition
    smoking-pi restore "$backup_dir" --yes --no-start
    [ "$(cat /etc/smoking-pi/edition 2>/dev/null)" = "$START" ] || fail "restore did not record the backup's edition"
    [ "$(sha256sum /etc/smoking-pi/env | cut -d' ' -f1)" = "$env_sum" ] || fail "restore did not bring back the same env file"
    [ "$(config_sum)" = "$cfg_sum" ] || fail "restore did not bring back the same config"
    [ "$(project_volumes)" = "$vols_before" ] || fail "restore made other volumes: $(project_volumes | tr '\n' ' ') (was: $(echo "$vols_before" | tr '\n' ' '))"
    [ "$(read_marker "$vol")" = "$marker" ] || fail "the data volume did not come back from the backup"
    smoking-pi up
    wait_web
    echo "recovery OK: backup, purge --config, restore; secrets, config and data are back"
    rm -rf "$(dirname "$backup_dir")"

    if [ -d /run/systemd/system ]; then
        systemctl enable --now smoking-pi || fail "the unit did not start"
        systemctl is-active smoking-pi || fail "the unit is not active"
        # 6c. The Docker engine going away under a running stack, the two
        # ways a package upgrade of Docker does it: a restart, and a stop
        # then a start (prerm, then postinst). A reboot cannot be run on
        # these VMs; this is the part of it the unit decides. After each,
        # the stack must be measuring again by itself and the unit active.
        systemctl restart docker
        wait_web
        systemctl is-active --quiet smoking-pi || fail "after a Docker restart the unit is not active"
        echo "docker restart: the stack came back"
        systemctl stop docker.socket docker.service
        systemctl start docker.service
        wait_web
        systemctl is-active --quiet smoking-pi || fail "after a Docker stop and start the unit is not active"
        echo "docker stop + start: the stack came back"
        systemctl stop smoking-pi || fail "the unit did not stop"
    else
        smoking-pi down
    fi
    [ -z "$(running_images)" ] || fail "containers still running after stop"
    conffile_as_shipped "after the stack ran"
    pin_images ""
fi

# 7. Removal keeps the state: apt remove never deletes what a user
# configured or measured; apt purge removes the regenerable directories
# and the conffile but keeps the env file and the Docker volumes (the
# uninstall policy, docs/packaging.md backlog #6).
touch /etc/smoking-pi/env
volumes_before=$(docker volume ls -q 2>/dev/null | sort || true)
apt-get remove -y -qq smoking-pi >/dev/null
[ ! -e /usr/bin/smoking-pi ] || fail "CLI left behind"
[ -f /etc/smoking-pi/env ] || fail "removal deleted the env file"
[ -d /var/lib/smoking-pi/output ] || fail "removal deleted the output dir"
[ -f /etc/default/smoking-pi ] || fail "removal deleted the conffile (that is purge's job)"
apt-get purge -y -qq smoking-pi 2>&1 | grep -i 'kept /etc/smoking-pi/env' || fail "purge did not say what it kept"
[ ! -e /etc/default/smoking-pi ] || fail "purge left the conffile"
[ ! -e /etc/smoking-pi/config ] || fail "purge left the seeded config dir"
[ ! -e /var/lib/smoking-pi ] || fail "purge left /var/lib/smoking-pi"
[ -f /etc/smoking-pi/env ] || fail "purge deleted the env file (the volumes' credentials)"
[ "$(docker volume ls -q 2>/dev/null | sort || true)" = "$volumes_before" ] || fail "purge touched the Docker volumes"
# 8. Reinstall after the purge, as postrm's message tells the user to:
# the kept env file and volumes must bring the same stack back, with the
# same secrets and the same data, without `install` (which refuses).
if [ -n "$START" ]; then
    apt-get install -y -qq --no-install-recommends --allow-downgrades "$DEB" >/tmp/check-package.apt-again.log 2>&1 \
        || { tail -20 /tmp/check-package.apt-again.log; fail "apt could not reinstall the package after the purge"; }
    [ "$(cat /etc/smoking-pi/edition)" = "$START" ] || fail "the purge lost the recorded edition"
    [ "$(sha256sum /etc/smoking-pi/env | cut -d' ' -f1)" = "$env_sum" ] || fail "the env file changed across remove, purge and reinstall"
    pin_images "$IMAGE_TAG"
    smoking-pi up
    wait_web
    [ "$(read_marker "$vol")" = "$marker" ] || fail "the data did not survive apt purge and a reinstall"
    smoking-pi down
    pin_images ""
    apt-get purge -y -qq smoking-pi >/dev/null
    echo "reinstall OK: the kept env file and volumes brought the same stack back"
fi
echo "== package OK on $PRETTY_NAME ($(dpkg --print-architecture)): $(echo "$picked" | tr -s ' \n' ' ')"
